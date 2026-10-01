"""Tool schemas the model sees, plus the dispatcher that runs them.

To add a tool later (RAG search, SQL query, etc.):
  1. write the function
  2. add its schema to TOOLS
  3. add it to TOOL_FUNCTIONS
"""

import json

import httpx

from app.tools.aviationstack import get_flight_status
from app.tools.errors import ToolError
from app.tools.opensky import get_aircraft_near

TOOLS = [
    {
        "name": "get_flight_status",
        "description": (
            "Look up the schedule and live status of a commercial flight by its IATA flight "
            "number (airline code + number, e.g. 'DL123', 'UA1532'). Returns departure and "
            "arrival airports, terminals, gates, scheduled/estimated/actual times, delay in "
            "minutes, and status (scheduled, active, landed, cancelled, diverted). Use this "
            "whenever the user mentions a specific flight number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "flight_iata": {
                    "type": "string",
                    "description": "IATA flight number, e.g. 'DL123'.",
                }
            },
            "required": ["flight_iata"],
        },
    },
    {
        "name": "get_aircraft_near",
        "description": (
            "List aircraft currently near a location, using live ADS-B positions. Returns "
            "callsign, altitude, speed, heading, and distance for each aircraft, closest "
            "first. Use this for questions like 'what's flying over me' or 'what planes are "
            "near JFK'. Does NOT know schedules, gates, or delays. If the user names a place, "
            "convert it to approximate coordinates yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "radius_km": {
                    "type": "number",
                    "description": "Search radius in km, 1 to 150. Default 25.",
                },
            },
            "required": ["latitude", "longitude"],
        },
    },
]

TOOL_FUNCTIONS = {
    "get_flight_status": get_flight_status,
    "get_aircraft_near": get_aircraft_near,
}


def run_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """Run a tool and return (content_for_model, is_error).

    Never raises: every failure becomes a message the model can read and react to.
    """
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return f"Unknown tool: {name}", True
    try:
        result = fn(**tool_input)
        return json.dumps(result), False
    except ToolError as e:
        return str(e), True
    except TypeError as e:
        return f"Bad arguments for {name}: {e}", True
    except httpx.HTTPStatusError as e:
        return f"Upstream API returned HTTP {e.response.status_code}.", True
    except httpx.HTTPError as e:
        return f"Network error calling upstream API: {e.__class__.__name__}", True
