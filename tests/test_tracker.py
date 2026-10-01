"""Offline tests for the tracker: no API keys, no network, no sleeping."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app import store
from app.tracker import (
    detect_changes, fallback_text, next_check_minutes, parse_airport_time, priority_for,
)

NY = "America/New_York"


def snap(**overrides):
    base = {
        "status": "scheduled", "dep_delay": None, "dep_gate": "C71", "dep_terminal": "C",
        "dep_scheduled": "2026-10-01T18:00:00+00:00", "dep_estimated": "2026-10-01T18:00:00+00:00",
        "arr_gate": None, "arr_estimated": None, "dep_timezone": NY,
        "dep_airport": "EWR", "arr_airport": "ATL",
    }
    return {**base, **overrides}


def test_first_check_is_initial():
    assert detect_changes(None, snap()) == [{"kind": "initial"}]


def test_no_change():
    assert detect_changes(snap(), snap()) == []


def test_small_delay_ignored_big_delay_caught():
    assert detect_changes(snap(), snap(dep_delay=5)) == []
    assert detect_changes(snap(), snap(dep_delay=40))[0]["kind"] == "delay"


def test_gate_change():
    changes = detect_changes(snap(), snap(dep_gate="C80"))
    assert changes == [{"kind": "gate", "what": "departure gate", "from": "C71", "to": "C80"}]


def test_priorities():
    assert priority_for([{"kind": "status", "from": "scheduled", "to": "cancelled"}]) == 5
    assert priority_for([{"kind": "delay", "from": 0, "to": 45}]) == 4
    assert priority_for([{"kind": "delay", "from": 0, "to": 15}]) == 3


def test_airport_time_is_local():
    dt = parse_airport_time("2026-10-01T18:00:00+00:00", NY)
    assert dt.hour == 18 and dt.tzinfo == ZoneInfo(NY)


def test_check_interval():
    morning = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo(NY))
    close = datetime(2026, 10, 1, 16, 30, tzinfo=ZoneInfo(NY))
    assert next_check_minutes(snap(), morning) == 60
    assert next_check_minutes(snap(), close) == 15
    assert next_check_minutes(snap(status="active")) == 30
    assert next_check_minutes(snap(status="landed")) is None


def test_fallback_text():
    title, msg = fallback_text("UA1532", [{"kind": "delay", "from": 0, "to": 40}], snap())
    assert "UA1532" in title and "40" in msg


import os
from datetime import timedelta

import pytest

from app import tracker

PG_URL = os.getenv("TEST_DATABASE_URL")  # set this to also run the tests against real Postgres


@pytest.fixture(params=["sqlite", "postgres"])
def db(request, tmp_path):
    if request.param == "sqlite":
        conn = store.connect(url="", sqlite_path=str(tmp_path / "test.db"))
    else:
        if not PG_URL:
            pytest.skip("TEST_DATABASE_URL not set")
        conn = store.connect(url=PG_URL)
        conn.run("TRUNCATE snapshots, tracked_flights RESTART IDENTITY")
    yield conn
    conn.close()


def test_store_roundtrip(db):
    tid = store.add_tracked_flight(db, "UA1532")
    assert store.latest_snapshot(db, tid) is None
    store.save_snapshot(db, tid, snap(dep_delay=20), {"raw": True})
    assert store.latest_snapshot(db, tid)["dep_delay"] == 20
    assert store.count_snapshots(db, tid) == 1
    assert store.record_error(db, tid) == 1
    assert store.stop_flight(db, "UA1532") == 1
    assert store.active_flights(db) == []


def api_result(**dep):
    departure = {"iata": "EWR", "timezone": NY, "gate": "C71", "terminal": "C",
                 "scheduled": "2026-10-01T18:00:00+00:00", "estimated": "2026-10-01T18:00:00+00:00",
                 "delay": None, **dep}
    status = dep.pop("status", "scheduled") if "status" in dep else "scheduled"
    departure.pop("status", None)
    return {"found": True, "results": [{"flight": "UA1532", "date": "2026-10-01", "status": status,
                                        "departure": departure, "arrival": {"iata": "ATL"}}]}


def test_full_day_through_due_checks(db, monkeypatch):
    responses = iter([
        api_result(),                                                   # first check
        api_result(gate="C80", delay=45, estimated="2026-10-01T18:45:00+00:00"),
        api_result(status="landed", gate="C80", delay=45),
    ])
    monkeypatch.setattr(tracker, "get_flight_status", lambda f: next(responses))
    monkeypatch.setattr(tracker, "compose_notification", lambda f, c, s: tracker.fallback_text(f, c, s))
    sent = []
    monkeypatch.setattr(tracker, "send_push", lambda t, m, p=3, tags=None: sent.append((t, m, p)))

    morning = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo(NY))
    tid = store.add_tracked_flight(db, "UA1532", now=morning)
    assert tracker.run_due_checks(db, morning) == 1          # initial check
    assert tracker.run_due_checks(db, morning) == 0          # nothing due yet

    later = morning + timedelta(hours=1, minutes=1)
    assert tracker.run_due_checks(db, later) == 1            # delay + gate change
    assert sent[-1][2] == 4 and "C80" in sent[-1][1]

    evening = later + timedelta(hours=10)
    tracker.run_due_checks(db, evening)                      # landed
    assert store.active_flights(db) == []
    assert store.count_snapshots(db, tid) == 3
    assert len(sent) == 3


def test_failures_stop_tracking(db, monkeypatch):
    def boom(f):
        raise tracker.ToolError("Flight not found")
    monkeypatch.setattr(tracker, "get_flight_status", boom)
    monkeypatch.setattr(tracker, "send_push", lambda *a, **k: True)
    now = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo(NY))
    store.add_tracked_flight(db, "ZZ9999", now=now)
    for i in range(tracker.MAX_CONSECUTIVE_ERRORS):
        tracker.run_due_checks(db, now + timedelta(minutes=16 * i))
    assert store.active_flights(db) == []
