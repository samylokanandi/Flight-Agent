"""Central settings, loaded once from environment variables / .env."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    aviationstack_api_key: str | None = os.getenv("AVIATIONSTACK_API_KEY")
    opensky_client_id: str | None = os.getenv("OPENSKY_CLIENT_ID")
    opensky_client_secret: str | None = os.getenv("OPENSKY_CLIENT_SECRET")
    max_agent_iterations: int = int(os.getenv("MAX_AGENT_ITERATIONS", "6"))
    ntfy_topic: str | None = os.getenv("NTFY_TOPIC")
    ntfy_server: str = os.getenv("NTFY_SERVER", "https://ntfy.sh")
    database_url: str | None = os.getenv("DATABASE_URL") or None  # Postgres (Neon). Empty means local SQLite.
    db_path: str = os.getenv("DB_PATH", "flight_agent.db")
    max_checks_per_flight: int = int(os.getenv("MAX_CHECKS_PER_FLIGHT", "30"))


settings = Settings()
