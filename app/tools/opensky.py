"""OpenSky Network: live aircraft positions (no schedules, gates, or delays).

Docs: https://openskynetwork.github.io/opensky-api/rest.html
"""

import math
import time

import httpx

from app.config import settings
from app.tools.errors import ToolError

STATES_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network"
    "/protocol/openid-connect/token"
)

_token_cache: dict = {"token": None, "expires_at": 0.0}


def _auth_headers() -> dict:
    """OAuth2 client credentials if configured, otherwise anonymous."""
    if not (settings.opensky_client_id and settings.opensky_client_secret):
        return {}
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 30:
        return {"Authorization": f"Bearer {_token_cache['token']}"}

    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.opensky_client_id,
            "client_secret": settings.opensky_client_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    _token_cache["token"] = body["access_token"]
    _token_cache["expires_at"] = time.time() + body.get("expires_in", 1800)
    return {"Authorization": f"Bearer {_token_cache['token']}"}


def bounding_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Rough box around a point. Good enough to pre filter before exact distance."""
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def trim_state(s: list, center_lat: float, center_lon: float) -> dict:
    """Turn OpenSky's positional array into a small, readable dict.

    Index reference: 0 icao24, 1 callsign, 2 origin_country, 5 lon, 6 lat,
    7 baro_altitude, 8 on_ground, 9 velocity (m/s), 10 true_track,
    11 vertical_rate, 13 geo_altitude.
    """
    lon, lat = s[5], s[6]
    altitude = s[7] if s[7] is not None else s[13]
    return {
        "icao24": s[0],
        "callsign": (s[1] or "").strip() or None,
        "origin_country": s[2],
        "latitude": lat,
        "longitude": lon,
        "altitude_m": round(altitude) if altitude is not None else None,
        "on_ground": s[8],
        "speed_kmh": round(s[9] * 3.6) if s[9] is not None else None,
        "heading_deg": round(s[10]) if s[10] is not None else None,
        "vertical_rate_ms": s[11],
        "distance_km": round(haversine_km(center_lat, center_lon, lat, lon), 1),
    }


def get_aircraft_near(latitude: float, longitude: float, radius_km: float = 25, limit: int = 15) -> dict:
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ToolError("latitude must be within [-90, 90] and longitude within [-180, 180]")
    radius_km = min(max(radius_km, 1), 150)

    lamin, lomin, lamax, lomax = bounding_box(latitude, longitude, radius_km)
    try:
        resp = httpx.get(
            STATES_URL,
            params={"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax},
            headers=_auth_headers(),
            timeout=15,
        )
    except httpx.TimeoutException:
        raise ToolError("OpenSky timed out. Try again or use a smaller radius.")
    if resp.status_code == 429:
        raise ToolError("OpenSky rate limit reached. Wait a bit before asking again.")
    resp.raise_for_status()

    states = resp.json().get("states") or []
    aircraft = [
        trim_state(s, latitude, longitude)
        for s in states
        if s[5] is not None and s[6] is not None
    ]
    aircraft = [a for a in aircraft if a["distance_km"] <= radius_km]
    aircraft.sort(key=lambda a: a["distance_km"])

    return {
        "center": {"latitude": latitude, "longitude": longitude},
        "radius_km": radius_km,
        "total_found": len(aircraft),
        "aircraft": aircraft[:limit],
    }
