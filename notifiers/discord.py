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

_GAME_THUMBS_DEFAULT = "https://game-thumbs.swvn.io"
_color_cache: dict[str, int] = {}

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


async def _fetch_winner_color(base_url: str, league: str, winner_abbrev: str) -> int | None:
    """Fetch the winner's primary brand color from the game-thumbs /raw endpoint."""
    from game_thumbs.builder import _to_slug
    slug = _to_slug(winner_abbrev)
    cache_key = f"{league}:{slug}"
    if cache_key in _color_cache:
        return _color_cache[cache_key]
    url = f"{base_url.rstrip('/')}/{league.lower()}/{slug}/raw"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            if r.status_code == 200:
                hex_color = r.json().get("color", "")
                if hex_color:
                    color = int(hex_color.lstrip("#"), 16)
                    _color_cache[cache_key] = color
                    return color
    except Exception as e:
        log.debug("Failed to fetch winner color for %s/%s: %s", league, winner_abbrev, e)
    return None


def _build_single_embed(lines: dict, sport: str, color: int | None = None) -> dict:
    emoji = _sport_emoji(sport)
    embed: dict[str, Any] = {
        "title": f"{emoji} {lines['title']}",
        "description": lines["rendered"] or lines["time"],
        "color": color if color is not None else _colour_for_sport(sport),
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

    color: int | None = None
    if winner_abbrev:
        try:
            import config as cfg_module
            raw_cfg = cfg_module.load_config()
            gt = cfg_module.get_game_thumbs(raw_cfg)
            if not gt or gt.get("enabled", True):
                base_url = gt.get("base_url", _GAME_THUMBS_DEFAULT) if gt else _GAME_THUMBS_DEFAULT
                color = await _fetch_winner_color(base_url, match.game.league, winner_abbrev)
        except Exception as e:
            log.debug("Winner color lookup failed: %s", e)

    embed = _build_single_embed(lines, match.game.sport, color)
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
    show_channels: bool = True,
    mode: str = "digest",
) -> bool:
    from notifiers.base import build_league_digest
    embeds = []
    for group in build_league_digest(matches_subs, endpoint, tz_name,
                                     show_channels=show_channels, mode=mode):
        emoji = _sport_emoji(group["sport"])
        parts = [g["rendered"] for g in group["games"] if g.get("rendered")]
        embed: dict[str, Any] = {
            "title": f"{emoji} {group['title']}",
            "description": "\n\n".join(parts) or "No games.",
            "color": _colour_for_sport(group["sport"]),
        }
        if group["thumb_url"]:
            embed["thumbnail"] = {"url": group["thumb_url"]}
        embeds.append(embed)
        if len(embeds) >= 10:
            break

    if not embeds:
        return True
    day_label = "Today's" if mode == "digest" else "This Week's"
    return await _post_webhook(endpoint._raw.get("webhook_url", ""),
                               {"content": f"🐢 **{day_label} Games**", "embeds": embeds})


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
