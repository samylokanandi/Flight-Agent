"""AviationStack: flight status by flight number (schedule, gates, delays).

Docs: https://aviationstack.com/documentation
Note: the free tier only supports http, not https.
"""

import httpx

from app.config import settings
from app.tools.errors import ToolError

FLIGHTS_URL = "http://api.aviationstack.com/v1/flights"

LEG_FIELDS = (
    "airport", "iata", "timezone", "terminal", "gate",
    "scheduled", "estimated", "actual", "delay",
)


def _trim_leg(leg: dict | None) -> dict:
    leg = leg or {}
    return {k: leg.get(k) for k in LEG_FIELDS}


def trim_flight(f: dict) -> dict:
    """Keep only what the model needs. The raw payload is huge and noisy."""
    live = f.get("live")
    return {
        "flight": (f.get("flight") or {}).get("iata"),
        "airline": (f.get("airline") or {}).get("name"),
        "date": f.get("flight_date"),
        "status": f.get("flight_status"),
        "departure": _trim_leg(f.get("departure")),
        "arrival": _trim_leg(f.get("arrival")),
        "live_position": (
            {
                "latitude": live.get("latitude"),
                "longitude": live.get("longitude"),
                "altitude": live.get("altitude"),
                "is_ground": live.get("is_ground"),
            }
            if live
            else None
        ),
    }


def normalize_flight_number(flight_iata: str) -> str:
    return flight_iata.replace(" ", "").upper()


def get_flight_status(flight_iata: str) -> dict:
    if not settings.aviationstack_api_key:
        raise ToolError("AVIATIONSTACK_API_KEY is not configured on the server.")

    flight_iata = normalize_flight_number(flight_iata)
    try:
        resp = httpx.get(
            FLIGHTS_URL,
            params={"access_key": settings.aviationstack_api_key, "flight_iata": flight_iata},
            timeout=15,
        )
    except httpx.TimeoutException:
        raise ToolError("AviationStack timed out. Try again.")
    resp.raise_for_status()

    body = resp.json()
    if "error" in body:
        raise ToolError(f"AviationStack error: {body['error'].get('message', 'unknown error')}")

    flights = body.get("data") or []
    if not flights:
        return {
            "flight": flight_iata,
            "found": False,
            "message": "No flights found. Check the IATA code (e.g. DL123, not DAL123).",
        }

    # The API can return several dates for the same flight number. Newest first.
    flights.sort(key=lambda f: f.get("flight_date") or "", reverse=True)
    return {"flight": flight_iata, "found": True, "results": [trim_flight(f) for f in flights[:3]]}
