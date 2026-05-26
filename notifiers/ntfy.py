"""
Alertle-V2 — ntfy notifier.
"""
from __future__ import annotations
import logging
import httpx
from models import Endpoint, GameMatch, Subscription
from notifiers.base import build_digest_lines, build_game_lines

log = logging.getLogger(__name__)
SPORT_EMOJI = {"hockey":"🏒","basketball":"🏀","football":"🏈","baseball":"⚾","soccer":"⚽"}

def _emoji(sport: str) -> str:
    return SPORT_EMOJI.get(sport.lower(), "🏟️")

async def _publish(url: str, topic: str, title: str, message: str,
                   thumb_url: str = "") -> bool:
    full_url = f"{url.rstrip('/')}/{topic}"
    headers = {"Title": title, "Markdown": "yes"}
    if thumb_url:
        headers["Attach"] = thumb_url
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(full_url, content=message, headers=headers)
            return r.status_code in (200, 201)
    except Exception as e:
        log.error("ntfy error: %s", e)
        return False

def _lines_to_text(lines: dict, sport: str) -> tuple[str, str]:
    title = f"{_emoji(sport)} {lines['title']}"
    parts = [lines["time"]]
    if lines["channels"]:  parts.append(f"📺 {lines['channels']}")
    if lines["venue"]:     parts.append(f"📍 {lines['venue']}")
    if lines["context"]:   parts.append(lines["context"])
    if lines["odds"]:      parts.append(f"📊 {lines['odds']}")
    if lines["score"]:     parts.append(f"🏆 {lines['score']}")
    return title, "\n".join(parts)

async def send_single(match: GameMatch, endpoint: Endpoint, sub: Subscription, tz_name: str,
                      mode: str = "lead_time", winner_abbrev: str = "") -> bool:
    raw = endpoint._raw
    url = raw.get("url", "https://ntfy.sh")
    topic = raw.get("topic", "")
    if not topic:
        log.error("ntfy topic not configured for endpoint %s", endpoint.id)
        return False
    lines = build_game_lines(match, endpoint, sub, tz_name, mode, winner_abbrev)
    title, message = _lines_to_text(lines, match.game.sport)
    return await _publish(url, topic, title, message, lines["thumb_url"])

async def send_bundled(matches_subs: list[tuple[GameMatch, Subscription]],
                       endpoint: Endpoint, tz_name: str, mode: str = "lead_time") -> bool:
    raw = endpoint._raw
    url = raw.get("url", "https://ntfy.sh")
    topic = raw.get("topic", "")
    if not topic:
        return False
    bodies = []
    for match, sub in matches_subs:
        lines = build_game_lines(match, endpoint, sub, tz_name, mode)
        _, body = _lines_to_text(lines, match.game.sport)
        bodies.append(body)
    title = f"🐢 {len(matches_subs)} Games"
    message = "\n\n".join(bodies)
    return await _publish(url, topic, title, message)

async def send_digest(matches_subs: list[tuple[GameMatch, Subscription]],
                      endpoint: Endpoint, tz_name: str) -> bool:
    return await send_bundled(matches_subs, endpoint, tz_name, mode="digest")
