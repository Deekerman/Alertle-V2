"""
Alertle-V2 — Pushover notifier.
"""
from __future__ import annotations

import logging
import httpx

from models import Endpoint, GameMatch, Subscription
from notifiers.base import build_digest_lines, build_game_lines

log = logging.getLogger(__name__)

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
SPORT_EMOJI = {"hockey": "🏒", "basketball": "🏀", "football": "🏈", "baseball": "⚾", "soccer": "⚽"}


def _emoji(sport: str) -> str:
    return SPORT_EMOJI.get(sport.lower(), "🏟️")


def _format_message(lines: dict, sport: str) -> tuple[str, str]:
    title = f"{_emoji(sport)} {lines['title']}"
    return title, lines.get("rendered", lines["time"])


async def _send(token: str, user_key: str, title: str, message: str,
                image_url: str = "") -> bool:
    payload = {
        "token": token,
        "user": user_key,
        "title": title,
        "message": message,
    }
    if image_url:
        payload["url"] = image_url
        payload["url_title"] = "Game Image"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(PUSHOVER_URL, data=payload)
            return r.status_code == 200
    except Exception as e:
        log.error("Pushover error: %s", e)
        return False


async def send_single(match: GameMatch, endpoint: Endpoint, sub: Subscription, tz_name: str,
                      mode: str = "lead_time", winner_abbrev: str = "") -> bool:
    raw = endpoint._raw
    token = raw.get("app_token", "")
    user_key = raw.get("user_key", "")
    if not token or not user_key:
        log.error("Pushover credentials not configured for endpoint %s", endpoint.id)
        return False
    lines = build_game_lines(match, endpoint, sub, tz_name, mode, winner_abbrev)
    title, message = _format_message(lines, match.game.sport)
    return await _send(token, user_key, title, message, lines["thumb_url"])


async def send_bundled(matches_subs: list[tuple[GameMatch, Subscription]],
                       endpoint: Endpoint, tz_name: str, mode: str = "lead_time") -> bool:
    raw = endpoint._raw
    token = raw.get("app_token", "")
    user_key = raw.get("user_key", "")
    if not token or not user_key:
        return False
    parts = []
    for match, sub in matches_subs:
        lines = build_game_lines(match, endpoint, sub, tz_name, mode)
        _, body = _format_message(lines, match.game.sport)
        parts.append(body)
    title = f"🐢 {len(matches_subs)} Games"
    return await _send(token, user_key, title, "\n\n".join(parts))


async def send_digest(matches_subs: list[tuple[GameMatch, Subscription]],
                      endpoint: Endpoint, tz_name: str) -> bool:
    return await send_bundled(matches_subs, endpoint, tz_name, mode="digest")


async def send_standings(event_name: str, body: str, endpoint: Endpoint) -> bool:
    raw = endpoint._raw
    token = raw.get("app_token", "")
    user_key = raw.get("user_key", "")
    if not token or not user_key:
        log.error("Pushover credentials not configured for endpoint %s", endpoint.id)
        return False
    return await _send(token, user_key, f"🏆 {event_name}", body or "No standings data available.")
