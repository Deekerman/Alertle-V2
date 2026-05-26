"""
Alertle-V2 — Discord notifier (webhook, rich embeds).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from models import Endpoint, GameMatch, Subscription
from notifiers.base import build_digest_lines, build_game_lines

log = logging.getLogger(__name__)

SPORT_EMOJI = {
    "hockey": "🏒", "basketball": "🏀", "football": "🏈",
    "baseball": "⚾", "soccer": "⚽", "default": "🏟️",
}


def _sport_emoji(sport: str) -> str:
    return SPORT_EMOJI.get(sport.lower(), SPORT_EMOJI["default"])


def _colour_for_sport(sport: str) -> int:
    colours = {
        "hockey": 0x003E7E, "basketball": 0xC9082A,
        "football": 0x013369, "baseball": 0x002D72,
        "soccer": 0x3D9B35,
    }
    return colours.get(sport.lower(), 0x5865F2)


def _build_single_embed(lines: dict, sport: str) -> dict:
    emoji = _sport_emoji(sport)
    embed: dict[str, Any] = {
        "title": f"{emoji} {lines['title']}",
        "description": lines["rendered"] or lines["time"],
        "color": _colour_for_sport(sport),
    }
    if lines["thumb_url"]:
        embed["image"] = {"url": lines["thumb_url"]}
    return embed


async def send_single(
    match: GameMatch,
    endpoint: Endpoint,
    sub: Subscription,
    tz_name: str,
    mode: str = "lead_time",
    winner_abbrev: str = "",
) -> bool:
    lines = build_game_lines(match, endpoint, sub, tz_name, mode, winner_abbrev)
    embed = _build_single_embed(lines, match.game.sport)
    return await _post_webhook(endpoint._raw.get("webhook_url", ""), {"embeds": [embed]})


async def send_bundled(
    matches_subs: list[tuple[GameMatch, Subscription]],
    endpoint: Endpoint,
    tz_name: str,
    mode: str = "lead_time",
) -> bool:
    embeds = []
    for match, sub in matches_subs:
        lines = build_game_lines(match, endpoint, sub, tz_name, mode)
        embeds.append(_build_single_embed(lines, match.game.sport))
        if len(embeds) >= 10:   # Discord max embeds per message
            break
    return await _post_webhook(endpoint._raw.get("webhook_url", ""), {"embeds": embeds})


async def send_digest(
    matches_subs: list[tuple[GameMatch, Subscription]],
    endpoint: Endpoint,
    tz_name: str,
) -> bool:
    all_lines = build_digest_lines(matches_subs, endpoint, tz_name)
    if not all_lines:
        return True

    embeds = []
    for lines, (match, _) in zip(all_lines, matches_subs):
        embeds.append(_build_single_embed(lines, match.game.sport))
        if len(embeds) >= 10:
            break

    payload = {
        "content": "🐢 **Today's Games**",
        "embeds": embeds,
    }
    return await _post_webhook(endpoint._raw.get("webhook_url", ""), payload)


async def send_standings(event_name: str, body: str, endpoint: Endpoint) -> bool:
    embed = {
        "title": f"🏆 {event_name} — Standings",
        "description": body or "No standings data available.",
        "color": 0xFFD700,
    }
    return await _post_webhook(endpoint._raw.get("webhook_url", ""), {"embeds": [embed]})


async def _post_webhook(url: str, payload: dict) -> bool:
    if not url:
        log.error("Discord webhook URL not configured")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)
            if r.status_code in (200, 204):
                return True
            log.error("Discord webhook returned %s: %s", r.status_code, r.text[:200])
            return False
    except Exception as e:
        log.error("Discord webhook error: %s", e)
        return False
