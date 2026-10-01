"""Track one flight all day and push a notification when something changes.

The split of responsibilities:
  plain Python decides WHEN to check and WHAT changed (cheap, predictable)
  Claude only writes the notification text (the part language models are good at)
"""

import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic

from app import store
from app.config import settings
from app.notify import send_push
from app.tools.aviationstack import get_flight_status
from app.tools.errors import ToolError

DELAY_THRESHOLD_MIN = 10  # ignore delay wiggles smaller than this
FINAL_STATUSES = {"landed", "cancelled", "diverted"}
MAX_CONSECUTIVE_ERRORS = 5


# ---------- turning API data into a snapshot ----------

def pick_flight(results: list[dict], flight_date: str | None) -> dict:
    """The API can return several days for one flight number. Stick to one date."""
    if flight_date:
        for r in results:
            if r.get("date") == flight_date:
                return r
    return results[0]


def to_snapshot(flight: dict) -> dict:
    dep, arr = flight.get("departure") or {}, flight.get("arrival") or {}
    return {
        "status": flight.get("status"),
        "dep_delay": dep.get("delay"),
        "dep_gate": dep.get("gate"),
        "dep_terminal": dep.get("terminal"),
        "dep_scheduled": dep.get("scheduled"),
        "dep_estimated": dep.get("estimated"),
        "arr_gate": arr.get("gate"),
        "arr_estimated": arr.get("estimated"),
        "dep_timezone": dep.get("timezone"),
        "dep_airport": dep.get("iata"),
        "arr_airport": arr.get("iata"),
    }


def parse_airport_time(ts: str | None, tz_name: str | None) -> datetime | None:
    """AviationStack reports times in the airport's LOCAL time, even though the string
    ends in +00:00. So drop the offset and attach the airport's real timezone.
    Sanity check this against the airport's departure board on your first run."""
    if not ts:
        return None
    try:
        naive = datetime.fromisoformat(ts).replace(tzinfo=None)
    except ValueError:
        return None
    try:
        return naive.replace(tzinfo=ZoneInfo(tz_name)) if tz_name else naive.replace(tzinfo=timezone.utc)
    except Exception:
        return naive.replace(tzinfo=timezone.utc)


def fmt_time(ts: str | None, tz_name: str | None) -> str | None:
    dt = parse_airport_time(ts, tz_name)
    return dt.strftime("%I:%M %p").lstrip("0") if dt else None


# ---------- deciding what changed ----------

def detect_changes(prev: dict | None, curr: dict) -> list[dict]:
    if prev is None:
        return [{"kind": "initial"}]

    changes = []
    if curr["status"] != prev["status"]:
        changes.append({"kind": "status", "from": prev["status"], "to": curr["status"]})

    old_delay, new_delay = prev["dep_delay"] or 0, curr["dep_delay"] or 0
    if abs(new_delay - old_delay) >= DELAY_THRESHOLD_MIN:
        changes.append({"kind": "delay", "from": old_delay, "to": new_delay})

    for field, label in (("dep_gate", "departure gate"), ("dep_terminal", "departure terminal"),
                         ("arr_gate", "arrival gate")):
        if curr[field] and curr[field] != prev[field]:
            changes.append({"kind": "gate", "what": label, "from": prev[field], "to": curr[field]})
    return changes


def priority_for(changes: list[dict]) -> int:
    """ntfy priority: 5 urgent, 4 high, 3 normal. Code decides, not the model."""
    for c in changes:
        if c["kind"] == "status" and c["to"] in ("cancelled", "diverted"):
            return 5
    for c in changes:
        if c["kind"] == "delay" and c["to"] - c["from"] >= 30:
            return 4
        if c["kind"] == "gate" and c["what"] == "departure gate":
            return 4
    return 3


def next_check_minutes(snap: dict, now: datetime | None = None) -> int | None:
    """How long to wait before checking again. None means stop tracking.
    Spends the API quota where it matters: close to departure."""
    if snap["status"] in FINAL_STATUSES:
        return None
    if snap["status"] == "active":  # in the air: just waiting for landing
        return 30
    dep = parse_airport_time(snap["dep_estimated"] or snap["dep_scheduled"], snap.get("dep_timezone"))
    if dep is None:
        return 60
    now = now or datetime.now(timezone.utc)
    minutes_until = (dep - now).total_seconds() / 60
    return 60 if minutes_until > 180 else 15


# ---------- writing the notification ----------

COMPOSE_PROMPT = """You write push notifications about a traveler's flight.
You get the flight number, what changed, and the current status.
Return ONLY a JSON object: {"title": "...", "message": "..."}
* title: under 40 characters, lead with the most important fact
* message: 1 or 2 short sentences, plain and calm, include new times and gates when known
* never invent information that isn't in the data
* times are already in the airport's local time"""


def fallback_text(flight_iata: str, changes: list[dict], snap: dict) -> tuple[str, str]:
    """Used if Claude is unavailable. The tracker should never go silent because of the LLM."""
    if changes[0]["kind"] == "initial":
        dep = fmt_time(snap["dep_estimated"] or snap["dep_scheduled"], snap.get("dep_timezone"))
        return (f"Tracking {flight_iata}",
                f"{snap.get('dep_airport')} to {snap.get('arr_airport')}, departs {dep or 'time unknown'}, "
                f"status {snap['status']}.")
    parts = []
    for c in changes:
        if c["kind"] == "status":
            parts.append(f"status is now {c['to']}")
        elif c["kind"] == "delay":
            parts.append(f"delay now {c['to']} min")
        elif c["kind"] == "gate":
            parts.append(f"{c['what']} now {c['to']}")
    text = "; ".join(parts)
    return f"{flight_iata} update", text[:1].upper() + text[1:] + "."


def compose_notification(flight_iata: str, changes: list[dict], snap: dict) -> tuple[str, str]:
    readable = dict(snap)
    tz = snap.get("dep_timezone")
    for f in ("dep_scheduled", "dep_estimated"):
        readable[f] = fmt_time(snap[f], tz)
    readable["arr_estimated"] = fmt_time(snap["arr_estimated"], None)  # arrival tz may differ; shown as reported

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.model,
            max_tokens=300,
            system=COMPOSE_PROMPT,
            messages=[{"role": "user", "content": json.dumps(
                {"flight": flight_iata, "changes": changes, "current": readable})}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        return data["title"], data["message"]
    except Exception as e:  # any failure: fall back to a template, keep tracking
        print(f"[compose fallback] {e.__class__.__name__}")
        return fallback_text(flight_iata, changes, snap)


# ---------- checking flights ----------

def check_once(db, tracked_id: int, flight_iata: str, flight_date: str | None):
    """One check: fetch, compare with last time, save. Returns (snapshot, changes, flight_date)."""
    data = get_flight_status(flight_iata)
    if not data.get("found"):
        raise ToolError(data.get("message", "Flight not found"))
    flight = pick_flight(data["results"], flight_date)
    if flight_date is None:
        flight_date = flight.get("date")
        store.set_flight_date(db, tracked_id, flight_date)

    snap = to_snapshot(flight)
    prev = store.latest_snapshot(db, tracked_id)
    changes = detect_changes(prev, snap)
    store.save_snapshot(db, tracked_id, snap, flight)
    return snap, changes, flight_date


def process_flight(db, row: dict, now: datetime | None = None) -> str:
    """Check one tracked flight, notify if needed, and schedule its next check.
    Returns a short outcome: ok, done, error, failed, or limit."""
    now = now or store.utc_now()
    tid, flight_iata = row["id"], row["flight_iata"]

    if store.count_snapshots(db, tid) >= settings.max_checks_per_flight:
        send_push(f"Stopped tracking {flight_iata}", "Hit the check limit for this flight.", 2)
        store.deactivate(db, tid)
        return "limit"

    try:
        snap, changes, _ = check_once(db, tid, flight_iata, row["flight_date"])
    except ToolError as e:
        errors = store.record_error(db, tid)
        print(f"[{flight_iata}] check failed ({errors}/{MAX_CONSECUTIVE_ERRORS}): {e}")
        if errors >= MAX_CONSECUTIVE_ERRORS:
            send_push(f"Stopped tracking {flight_iata}", f"Kept failing: {e}", 3)
            store.deactivate(db, tid)
            return "failed"
        store.set_next_check(db, tid, now + timedelta(minutes=15))
        return "error"

    store.reset_errors(db, tid)
    print(f"[{flight_iata}] status={snap['status']} delay={snap['dep_delay']} gate={snap['dep_gate']} "
          f"changes={[c['kind'] for c in changes] or 'none'}")

    if changes:
        title, message = compose_notification(flight_iata, changes, snap)
        print(f"  -> {title}: {message}")
        if row["notify"]:
            send_push(title, message, priority_for(changes))

    wait = next_check_minutes(snap, now)
    if wait is None:
        print(f"[{flight_iata}] {snap['status']}, done tracking.")
        store.deactivate(db, tid)
        return "done"
    store.set_next_check(db, tid, now + timedelta(minutes=wait))
    return "ok"


def run_due_checks(db=None, now: datetime | None = None) -> int:
    """Check every flight whose next check time has passed, then exit.
    This is what the cloud scheduler runs every 15 minutes."""
    db = db or store.connect()
    now = now or store.utc_now()
    due = store.due_flights(db, now)
    for row in due:
        process_flight(db, row, now)
    return len(due)


def start_tracking(flight_iata: str, flight_date: str | None = None, notify: bool = True, db=None) -> int:
    """Add a flight and do its first check right away, so you get a confirmation immediately."""
    db = db or store.connect()
    flight_iata = flight_iata.replace(" ", "").upper()
    tid = store.add_tracked_flight(db, flight_iata, flight_date, notify)
    process_flight(db, store.get_tracked(db, tid))
    return tid


def watch(db=None) -> None:
    """Local mode: keep running and check flights as they come due.
    Sleeps until the next due time instead of polling, so it doesn't keep the database awake.
    Don't run this at the same time as the cloud scheduler, or flights get checked twice."""
    db = db or store.connect()
    while True:
        run_due_checks(db)
        nxt = store.next_due_time(db)
        if nxt is None:
            print("No active flights left. Done.")
            return
        wait = max(30, (nxt - store.utc_now()).total_seconds())
        print(f"Next check in {round(wait / 60)} min. Ctrl+C to stop.")
        time.sleep(wait)
