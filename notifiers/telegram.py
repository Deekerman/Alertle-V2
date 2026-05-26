"""
Alertle-V2 — Telegram notifier (Bot API).
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

def _tg_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"

def _format_message(lines: dict, sport: str) -> str:
    header = f"<b>{_emoji(sport)} {lines['title']}</b>"
    body = lines.get("rendered", "")
    return f"{header}\n{body}" if body else header

async def _send_message(token: str, chat_id: str, text: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(_tg_url(token, "sendMessage"),
                                  json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            return r.status_code == 200
    except Exception as e:
        log.error("Telegram sendMessage error: %s", e)
        return False

async def _send_photo(token: str, chat_id: str, photo_url: str, caption: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(_tg_url(token, "sendPhoto"),
                                  json={"chat_id": chat_id, "photo": photo_url,
                                        "caption": caption, "parse_mode": "HTML"})
            return r.status_code == 200
    except Exception as e:
        log.error("Telegram sendPhoto error: %s", e)
        return False

async def send_single(match: GameMatch, endpoint: Endpoint, sub: Subscription, tz_name: str,
                      mode: str = "lead_time", winner_abbrev: str = "") -> bool:
    raw = endpoint._raw
    token = raw.get("bot_token", "")
    chat_id = raw.get("chat_id", "")
    if not token or not chat_id:
        log.error("Telegram credentials not configured for endpoint %s", endpoint.id)
        return False
    lines = build_game_lines(match, endpoint, sub, tz_name, mode, winner_abbrev)
    text = _format_message(lines, match.game.sport)
    if lines["thumb_url"]:
        return await _send_photo(token, chat_id, lines["thumb_url"], text)
    return await _send_message(token, chat_id, text)

async def send_bundled(matches_subs: list[tuple[GameMatch, Subscription]],
                       endpoint: Endpoint, tz_name: str, mode: str = "lead_time") -> bool:
    raw = endpoint._raw
    token = raw.get("bot_token", "")
    chat_id = raw.get("chat_id", "")
    if not token or not chat_id:
        return False
    parts = []
    for match, sub in matches_subs:
        lines = build_game_lines(match, endpoint, sub, tz_name, mode)
        parts.append(_format_message(lines, match.game.sport))
    text = "\n\n─────────────\n\n".join(parts)
    return await _send_message(token, chat_id, text)

async def send_standings(event_name: str, body: str, endpoint: Endpoint) -> bool:
    raw = endpoint._raw
    token = raw.get("bot_token", "")
    chat_id = raw.get("chat_id", "")
    if not token or not chat_id:
        return False
    text = f"<b>🏆 {event_name} — Standings</b>\n{body or 'No standings data available.'}"
    return await _send_message(token, chat_id, text)

async def send_digest(matches_subs: list[tuple[GameMatch, Subscription]],
                      endpoint: Endpoint, tz_name: str) -> bool:
    raw = endpoint._raw
    token = raw.get("bot_token", "")
    chat_id = raw.get("chat_id", "")
    if not token or not chat_id:
        return False
    all_lines = build_digest_lines(matches_subs, endpoint, tz_name)
    parts = ["🐢 <b>Today's Games</b>"]
    for lines, (match, _) in zip(all_lines, matches_subs):
        parts.append(_format_message(lines, match.game.sport))
    text = "\n\n".join(parts)
    return await _send_message(token, chat_id, text)
