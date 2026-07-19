"""Server-side PostHog capture for events the browser can't see
(alert emails, scheduler failures, lookup failures). No-op unless
POSTHOG_API_KEY is set; must never break a request or the scheduler."""

from app import config

_client = None


def _get_client():
    global _client
    if _client is None and config.POSTHOG_API_KEY:
        from posthog import Posthog

        _client = Posthog(config.POSTHOG_API_KEY, host=config.POSTHOG_HOST)
    return _client


def capture(event: str, properties: dict | None = None) -> None:
    client = _get_client()
    if not client:
        return
    try:
        client.capture(
            distinct_id="server",
            event=event,
            # personless: server events describe system health, not a person
            properties={"$process_person_profile": False, **(properties or {})},
        )
    except Exception:
        pass


def shutdown() -> None:
    if _client:
        try:
            _client.flush()
        except Exception:
            pass
