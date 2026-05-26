"""
Alertle-V2 — Notification renderer.

Builds the content for each notification type, respecting
endpoint content_defaults and subscription content_overrides.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from models import ContentDefaults, Endpoint, ESPNGame, GameMatch, Subscription
from game_thumbs.builder import build_url, build_league_url

_GAME_THUMBS_DEFAULT = "https://game-thumbs.swvn.io"


def _effective_content(endpoint: Endpoint, sub: Subscription) -> ContentDefaults:
    """Merge endpoint defaults with subscription overrides."""
    cd = ContentDefaults(
        show_venue=endpoint.content_defaults.show_venue,
        show_broadcast=endpoint.content_defaults.show_broadcast,
        show_odds=endpoint.content_defaults.show_odds,
        show_series=endpoint.content_defaults.show_series,
        show_week_context=endpoint.content_defaults.show_week_context,
        show_key_stats=endpoint.content_defaults.show_key_stats,
    )
    overrides = sub.content_overrides
    if "show_venue" in overrides:        cd.show_venue = overrides["show_venue"]
    if "show_broadcast" in overrides:    cd.show_broadcast = overrides["show_broadcast"]
    if "show_odds" in overrides:         cd.show_odds = overrides["show_odds"]
    if "show_series" in overrides:       cd.show_series = overrides["show_series"]
    if "show_week_context" in overrides: cd.show_week_context = overrides["show_week_context"]
    if "show_key_stats" in overrides:    cd.show_key_stats = overrides["show_key_stats"]
    return cd


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
    Notifiers use this to render their specific format.

    Returns:
        {
          "title":    str,   # "Away vs Home"
          "time":     str,   # formatted local time
          "channels": str,   # "Sportsnet, ESPN+"
          "venue":    str,   # "Scotiabank Arena, Toronto" (or "")
          "context":  str,   # "Week 14" / "Series tied 2-2" (or "")
          "odds":     str,   # "TOR -1.5 · O/U 6.0" (or "")
          "score":    str,   # "3 - 1 (Final)" (or "")
          "thumb_url": str,  # Game-Thumbs URL (or "")
        }
    """
    game = match.game
    cd = _effective_content(endpoint, sub)

    title = f"{game.away_team.name} vs {game.home_team.name}"
    time_str = format_game_time(game.start_time, tz_name)
    channels_str = ", ".join(match.channels) if match.channels else "Channel TBD"

    venue = ""
    if cd.show_venue and game.venue:
        venue = game.venue
        if game.venue_city:
            venue += f", {game.venue_city}"

    context_parts = []
    if cd.show_week_context and game.season_context:
        context_parts.append(game.season_context)
    if cd.show_series and game.series_summary:
        context_parts.append(game.series_summary)
    context = " · ".join(context_parts)

    odds = ""
    if cd.show_odds:
        parts = []
        if game.odds_spread:
            parts.append(game.odds_spread)
        if game.odds_over_under:
            parts.append(f"O/U {game.odds_over_under}")
        odds = " · ".join(parts)

    score = ""
    if mode == "game_summary" and game.home_score is not None:
        score = (
            f"{game.away_team.abbreviation} {game.away_score} – "
            f"{game.home_team.abbreviation} {game.home_score} (Final)"
        )

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

    return {
        "title": title,
        "time": time_str,
        "channels": channels_str,
        "venue": venue,
        "context": context,
        "odds": odds,
        "score": score,
        "thumb_url": thumb_url,
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
