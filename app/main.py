"""FastAPI wrapper. Run with: uvicorn app.main:app --reload"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.agent import run_agent
from app.tools.aviationstack import get_flight_status
from app.tools.errors import ToolError
from app.tools.opensky import get_aircraft_near

app = FastAPI(title="Flight Agent")


class AskRequest(BaseModel):
    question: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ask")
def ask(req: AskRequest):
    result = run_agent(req.question)
    return {
        "answer": result.answer,
        "iterations": result.iterations,
        "trace": result.trace,
        "tokens": {"input": result.input_tokens, "output": result.output_tokens},
    }


# Debug endpoints: call tools directly, no LLM involved.
# If something is wrong, check here first so you know whether it's the API or the agent.


@app.get("/debug/flight/{flight_iata}")
def debug_flight(flight_iata: str):
    try:
        return get_flight_status(flight_iata)
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/debug/nearby")
def debug_nearby(lat: float, lon: float, radius_km: float = 25):
    try:
        return get_aircraft_near(lat, lon, radius_km)
    except ToolError as e:
        raise HTTPException(status_code=400, detail=str(e))
