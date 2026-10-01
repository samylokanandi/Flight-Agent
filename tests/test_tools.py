"""Offline tests: no API keys or network needed."""

from app.tools.aviationstack import normalize_flight_number, trim_flight
from app.tools.opensky import bounding_box, haversine_km, trim_state
from app.tools.registry import TOOLS, TOOL_FUNCTIONS, run_tool

EWR = (40.6895, -74.1745)


def test_haversine_ewr_to_jfk():
    jfk = (40.6413, -73.7781)
    assert 32 < haversine_km(*EWR, *jfk) < 36


def test_bounding_box_contains_center():
    lamin, lomin, lamax, lomax = bounding_box(*EWR, 25)
    assert lamin < EWR[0] < lamax and lomin < EWR[1] < lomax


def test_trim_state():
    state = ["a1b2c3", "UAL123  ", "United States", 0, 0, -74.10, 40.70,
             10000.0, False, 230.0, 87.6, -2.5, None, 10100.0, "1200", False, 0]
    out = trim_state(state, *EWR)
    assert out["callsign"] == "UAL123"
    assert out["speed_kmh"] == 828
    assert out["altitude_m"] == 10000
    assert out["distance_km"] < 10


def test_trim_flight_drops_noise():
    raw = {
        "flight_date": "2026-10-01",
        "flight_status": "active",
        "departure": {"airport": "Newark", "iata": "EWR", "gate": "C71", "delay": 15,
                      "timezone": "America/New_York", "icao": "KEWR"},
        "arrival": {"airport": "Atlanta", "iata": "ATL"},
        "airline": {"name": "Delta Air Lines", "iata": "DL"},
        "flight": {"iata": "DL123", "number": "123", "codeshared": None},
        "aircraft": {"registration": "N123DL"},
        "live": None,
    }
    out = trim_flight(raw)
    assert out["departure"]["gate"] == "C71"
    assert "icao" not in out["departure"]
    assert "aircraft" not in out


def test_normalize_flight_number():
    assert normalize_flight_number("dl 123") == "DL123"


def test_every_schema_has_a_function():
    assert {t["name"] for t in TOOLS} == set(TOOL_FUNCTIONS)


def test_run_tool_unknown():
    content, is_error = run_tool("nope", {})
    assert is_error and "Unknown tool" in content


def test_run_tool_bad_args():
    content, is_error = run_tool("get_aircraft_near", {"lat": 1})
    assert is_error and "Bad arguments" in content
