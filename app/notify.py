"""Send push notifications to your phone with ntfy (https://ntfy.sh).

Setup: install the ntfy app, subscribe to the topic in NTFY_TOPIC. That's it.
Sending is a single HTTP POST: the body is the message, headers set title etc.
"""

import httpx

from app.config import settings


def send_push(title: str, message: str, priority: int = 3, tags: list[str] | None = None) -> bool:
    """Returns True if sent. Never raises, so a notification failure can't kill the tracker."""
    if not settings.ntfy_topic:
        print(f"[notify skipped, NTFY_TOPIC not set] {title}: {message}")
        return False
    try:
        resp = httpx.post(
            f"{settings.ntfy_server.rstrip('/')}/{settings.ntfy_topic}",
            content=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": str(max(1, min(priority, 5))),  # 1 = min, 3 = default, 5 = urgent
                "Tags": ",".join(tags or ["airplane"]),
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPError as e:
        print(f"[notify failed] {e.__class__.__name__}: {title}: {message}")
        return False
