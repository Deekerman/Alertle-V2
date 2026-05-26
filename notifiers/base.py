"""
Alertle-V2 — Notification renderer.

Builds the content for each notification type using a {variable} template.
All fields are always computed; the template controls what appears.
Lines where every {var} resolves to an empty string are auto-skipped.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from models import Endpoint, ESPNGame, GameMatch, Subscription
from game_thumbs.builder import build_url, build_league_url

_GAME_THUMBS_DEFAULT = "https://game-thumbs.swvn.io"

DEFAULT_TEMPLATE = """{time}
📺 {channels}
📍 {venue}
{context}
{odds}
{score}"""

DEFAULT_GAME_SUMMARY_TEMPLATE = """🏆 {score}
{time}
📺 {channels}
{description}"""


def _get_template(endpoint: Endpoint, mode: str = "") -> str:
    """Return the active template for the given mode.
    Checks endpoint override first, then global config, then built-in default.
    """
    if mode == "game_summary":
        ep_t = endpoint._raw.get("game_summary_template", "")
        if ep_t:
            return ep_t
        import config as _cfg
        raw = _cfg.load_config()
        nd = raw.get("notification_defaults", {})
        return nd.get("game_summary_template", DEFAULT_GAME_SUMMARY_TEMPLATE)
    else:
        ep_t = endpoint._raw.get("notification_template", "")
        if ep_t:
            return ep_t
        import config as _cfg
        raw = _cfg.load_config()
        nd = raw.get("notification_defaults", {})
        return nd.get("template", DEFAULT_TEMPLATE)


def render_template(template: str, vars: dict) -> str:
    """
    Substitute {var} markers in template.
    Lines where every marker resolves to empty are dropped entirely.
    """
    result_lines = []
    for line in template.split("\n"):
        markers = re.findall(r'\{(\w+)\}', line)
        if markers and all(not vars.get(m, "") for m in markers):
            continue
        rendered = line
        for k, v in vars.items():
            rendered = rendered.replace("{" + k + "}", v)
        result_lines.append(rendered)
    return "\n".join(result_lines)


def format_game_time(dt: datetime, tz_name: str) -> str:
    """Format a UTC datetime into the user's configured timezone."""
    try:
        tz = ZoneInfo(tz_name)
        local = dt.astimezone(tz)
        return local.strftime("%A, %B %-d at %-I:%M %p %Z")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M UTC")


def build_game_lines(
    match: GameMatch,
    endpoint: Endpoint,
    sub: Subscription,
    tz_name: str,
    mode: str = "lead_time",
    winner_abbrev: str = "",
) -> dict:
    """
    Build a structured dict of notification content for one game.
    Returns all raw fields plus 'rendered' (template output) and 'template' (active template).
    """
    game = match.game

    title = f"{game.away_team.name} vs {game.home_team.name}"
    time_str = format_game_time(game.start_time, tz_name)
    channels_str = "\n".join(match.channels) if match.channels else ""

    venue = ""
    if game.venue:
        venue = game.venue
        if game.venue_city:
            venue += f", {game.venue_city}"

    context_parts = []
    if game.season_context:
        context_parts.append(game.season_context)
    if game.series_summary:
        context_parts.append(game.series_summary)
    context = " · ".join(context_parts)

    odds_parts = []
    if game.odds_spread:
        odds_parts.append(game.odds_spread)
    if game.odds_over_under:
        odds_parts.append(f"O/U {game.odds_over_under}")
    odds = " · ".join(odds_parts)

    score = ""
    if mode == "game_summary" and game.home_score is not None:
        score = (
            f"{game.away_team.abbreviation} {game.away_score} – "
            f"{game.home_team.abbreviation} {game.home_score} (Final)"
        )

    broadcast = ", ".join(game.broadcast_networks) if game.broadcast_networks else ""

    # Game-Thumbs — read base_url from config at runtime
    thumb_url = ""
    try:
        import config as cfg_module
        raw = cfg_module.load_config()
        gt_settings = cfg_module.get_game_thumbs(raw)
        base_url = (
            gt_settings.get("base_url", _GAME_THUMBS_DEFAULT)
            if gt_settings
            else _GAME_THUMBS_DEFAULT
        )
        if not gt_settings or gt_settings.get("enabled", True):
            gt_cfg = endpoint.game_thumbs
            thumb_url = build_url(game, gt_cfg, base_url=base_url, winner_abbrev=winner_abbrev)
    except Exception:
        pass

    template = _get_template(endpoint, mode)
    vars_map = {
        "time":         time_str,
        "channels":     channels_str,
        "broadcast":    broadcast,
        "venue":        venue,
        "context":      context,
        "odds":         odds,
        "score":        score,
        "description":  match.program_description,
        "home":         game.home_team.name,
        "away":         game.away_team.name,
        "home_abbrev":  game.home_team.abbreviation,
        "away_abbrev":  game.away_team.abbreviation,
        "league":       game.league.upper() if game.league else "",
        "sport":        game.sport,
    }
    rendered = render_template(template, vars_map)

    return {
        "title":     title,
        "time":      time_str,
        "channels":  channels_str,
        "broadcast": broadcast,
        "venue":     venue,
        "context":   context,
        "odds":      odds,
        "score":     score,
        "thumb_url": thumb_url,
        "rendered":  rendered,
        "template":  template,
    }


def build_digest_lines(
    matches: list[tuple[GameMatch, Subscription]],
    endpoint: Endpoint,
    tz_name: str,
) -> list[dict]:
    """Build content dicts for all games in a digest."""
    return [
        build_game_lines(match, endpoint, sub, tz_name, mode="digest")
        for match, sub in matches
    ]
