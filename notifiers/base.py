"""
Alertle-V2 — Notification renderer.

Builds the content for each notification type using a {variable} template.
All fields are always computed; the template controls what appears.
Lines where every {var} resolves to an empty string are auto-skipped.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

from models import Endpoint, ESPNGame, GameMatch, Subscription
from game_thumbs.builder import build_url, build_league_url

_GAME_THUMBS_DEFAULT = "https://game-thumbs.swvn.io"

DEFAULT_TEMPLATE = """{description}
{time}
{venue}
{channels}"""

DEFAULT_GAME_SUMMARY_TEMPLATE = """{time}
{score}"""

DEFAULT_LEAD_TIME_TEMPLATE = DEFAULT_TEMPLATE

DEFAULT_DIGEST_GAME_TEMPLATE = """{away} at {home}
{time}
{context}
{channels_line}"""

DEFAULT_DIGEST_EVENT_TEMPLATE = """{home}
{time}
{channels_line}"""


def _get_template(endpoint: Endpoint, mode: str = "") -> str:
    """Return the active template for the given mode.
    Checks endpoint override first, then global config, then built-in default.
    """
    if mode == "game_summary":
        ep_t = endpoint._raw.get("game_summary_template", "")
        if ep_t:
            log.debug("Template source: endpoint override (game_summary) for %s", endpoint.id)
            return ep_t
        import config as _cfg
        raw = _cfg.load_config()
        nd = raw.get("notification_defaults", {})
        gs_t = nd.get("game_summary_template", "")
        if gs_t:
            log.debug("Template source: global game_summary_template for %s", endpoint.id)
            return gs_t
        log.debug("Template source: built-in DEFAULT_GAME_SUMMARY_TEMPLATE for %s", endpoint.id)
        return DEFAULT_GAME_SUMMARY_TEMPLATE
    elif mode == "digest":
        ep_t = endpoint._raw.get("digest_game_template", "")
        if ep_t:
            log.debug("Template source: endpoint override (digest) for %s", endpoint.id)
            return ep_t
        import config as _cfg
        nd = _cfg.load_config().get("notification_defaults", {})
        d_t = nd.get("digest_game_template", "")
        if d_t:
            log.debug("Template source: global digest_game_template for %s", endpoint.id)
            return d_t
        log.debug("Template source: built-in DEFAULT_DIGEST_GAME_TEMPLATE for %s", endpoint.id)
        return DEFAULT_DIGEST_GAME_TEMPLATE
    elif mode == "digest_event":
        ep_t = endpoint._raw.get("digest_event_template", "")
        if ep_t:
            log.debug("Template source: endpoint override (digest_event) for %s", endpoint.id)
            return ep_t
        import config as _cfg
        nd = _cfg.load_config().get("notification_defaults", {})
        d_t = nd.get("digest_event_template", "")
        if d_t:
            log.debug("Template source: global digest_event_template for %s", endpoint.id)
            return d_t
        log.debug("Template source: built-in DEFAULT_DIGEST_EVENT_TEMPLATE for %s", endpoint.id)
        return DEFAULT_DIGEST_EVENT_TEMPLATE
    elif mode == "weekly_digest":
        ep_t = endpoint._raw.get("weekly_digest_game_template", "")
        if ep_t:
            log.debug("Template source: endpoint override (weekly_digest) for %s", endpoint.id)
            return ep_t
        import config as _cfg
        nd = _cfg.load_config().get("notification_defaults", {})
        wt = nd.get("weekly_digest_game_template", "")
        if wt:
            log.debug("Template source: global weekly_digest_game_template for %s", endpoint.id)
            return wt
        # Fall back to digest_game_template then default
        ep_d = endpoint._raw.get("digest_game_template", "")
        if ep_d:
            return ep_d
        d_t = nd.get("digest_game_template", "")
        if d_t:
            return d_t
        log.debug("Template source: built-in DEFAULT_DIGEST_GAME_TEMPLATE for %s (weekly)", endpoint.id)
        return DEFAULT_DIGEST_GAME_TEMPLATE
    elif mode == "weekly_digest_event":
        ep_t = endpoint._raw.get("weekly_digest_event_template", "")
        if ep_t:
            log.debug("Template source: endpoint override (weekly_digest_event) for %s", endpoint.id)
            return ep_t
        import config as _cfg
        nd = _cfg.load_config().get("notification_defaults", {})
        wt = nd.get("weekly_digest_event_template", "")
        if wt:
            log.debug("Template source: global weekly_digest_event_template for %s", endpoint.id)
            return wt
        # Fall back to daily digest event template chain
        ep_d = endpoint._raw.get("digest_event_template", "")
        if ep_d:
            return ep_d
        d_t = nd.get("digest_event_template", "")
        if d_t:
            return d_t
        log.debug("Template source: built-in DEFAULT_DIGEST_EVENT_TEMPLATE for %s (weekly event)", endpoint.id)
        return DEFAULT_DIGEST_EVENT_TEMPLATE
    elif mode == "lead_time":
        ep_t = endpoint._raw.get("lead_time_template", "")
        if ep_t:
            log.debug("Template source: endpoint override (lead_time) for %s", endpoint.id)
            return ep_t
        import config as _cfg
        nd = _cfg.load_config().get("notification_defaults", {})
        lt_t = nd.get("lead_time_template", "")
        if lt_t:
            log.debug("Template source: global lead_time_template for %s", endpoint.id)
            return lt_t
        # Fall back to the shared notification_template before using the built-in default
        ep_shared = endpoint._raw.get("notification_template", "")
        if ep_shared:
            log.debug("Template source: endpoint notification_template fallback (lead_time) for %s", endpoint.id)
            return ep_shared
        nd_shared = nd.get("template", "")
        if nd_shared:
            log.debug("Template source: global notification_defaults.template fallback (lead_time) for %s", endpoint.id)
            return nd_shared
        log.debug("Template source: built-in DEFAULT_LEAD_TIME_TEMPLATE for %s", endpoint.id)
        return DEFAULT_LEAD_TIME_TEMPLATE
    else:
        ep_t = endpoint._raw.get("notification_template", "")
        if ep_t:
            log.debug("Template source: endpoint override for %s", endpoint.id)
            return ep_t
        import config as _cfg
        raw = _cfg.load_config()
        nd = raw.get("notification_defaults", {})
        g_t = nd.get("template", "")
        if g_t:
            log.debug("Template source: global notification_defaults.template for %s", endpoint.id)
            return g_t
        log.debug("Template source: built-in DEFAULT_TEMPLATE for %s", endpoint.id)
        return DEFAULT_TEMPLATE


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


def _format_schedule(schedule: list[dict], tz_name: str) -> str:
    """
    Format a broadcast schedule for event series notifications.
    Each window becomes one line: "8:00 AM  Channel A, Channel B"
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = None
    blocks = []
    for entry in schedule:
        try:
            start = datetime.fromisoformat(entry["start"])
            if tz:
                local = start.astimezone(tz)
                time_part = local.strftime("%-I:%M %p")
            else:
                time_part = start.strftime("%H:%M UTC")
            block_lines = [time_part] + list(entry.get("channels", []))
            blocks.append("\n".join(block_lines))
        except Exception:
            continue
    return "\n\n".join(blocks)


def build_game_lines(
    match: GameMatch,
    endpoint: Endpoint,
    sub: Subscription,
    tz_name: str,
    mode: str = "lead_time",
    winner_abbrev: str = "",
    show_channels: bool = True,
) -> dict:
    """
    Build a structured dict of notification content for one game.
    Returns all raw fields plus 'rendered' (template output) and 'template' (active template).
    """
    game = match.game

    is_event = game.id.startswith("event:") or sub.scope == "event_series"
    if is_event:
        title = game.home_team.name or f"{game.sport.title()} Event"
    else:
        title = f"{game.away_team.name} vs {game.home_team.name}"
    time_str = format_game_time(game.start_time, tz_name)
    if is_event and match.schedule:
        channels_str = _format_schedule(match.schedule, tz_name)
        channels_line = channels_str  # schedule block, no prefix
    else:
        channels_str = "\n".join(match.channels) if match.channels else ""
        channels_line = f"📺 {channels_str}" if channels_str else ""

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

    if not show_channels:
        channels_str = ""
        channels_line = ""

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

    if is_event:
        template_mode = "weekly_digest_event" if mode == "weekly_digest" else "digest_event"
    else:
        template_mode = mode
    template = _get_template(endpoint, template_mode)
    vars_map = {
        "time":         time_str,
        "channels":     channels_str,
        "channels_line": channels_line,
        "broadcast":    broadcast,
        "venue":        venue,
        "context":      context,
        "odds":         odds,
        "odds_spread":       game.odds_spread,
        "odds_home_spread":  game.odds_home_spread,
        "odds_away_spread":  game.odds_away_spread,
        "odds_ou":           f"O/U {game.odds_over_under}" if game.odds_over_under else "",
        "odds_home_ml":      game.odds_home_ml,
        "odds_away_ml":      game.odds_away_ml,
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
    log.debug("Rendered notification [%s/%s]: %r", endpoint.id, mode, rendered[:300])

    return {
        "title":         title,
        "time":          time_str,
        "channels":      channels_str,
        "channels_line": channels_line,
        "broadcast":     broadcast,
        "venue":         venue,
        "context":       context,
        "odds":          odds,
        "score":         score,
        "thumb_url":     thumb_url,
        "rendered":      rendered,
        "template":      template,
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


def build_league_digest(
    matches_subs: list[tuple[GameMatch, Subscription]],
    endpoint: Endpoint,
    tz_name: str,
    show_channels: bool = True,
    mode: str = "digest",
) -> list[dict]:
    """
    Group games by league and build one content dict per league group.
    Returns list of dicts with keys: sport, league, label, title, thumb_url, games.
    Used by all notifiers for the grouped per-league digest format.
    """
    groups: defaultdict[tuple[str, str], list[tuple[GameMatch, Subscription]]] = defaultdict(list)
    for match, sub in matches_subs:
        groups[(match.game.sport, match.game.league)].append((match, sub))

    # Load game-thumbs config once
    thumb_base = _GAME_THUMBS_DEFAULT
    thumbs_enabled = True
    try:
        import config as cfg_module
        gt = cfg_module.get_game_thumbs(cfg_module.load_config())
        if gt:
            thumb_base = gt.get("base_url", _GAME_THUMBS_DEFAULT)
            thumbs_enabled = gt.get("enabled", True)
    except Exception:
        pass

    day_label = "Today's" if mode == "digest" else "This Week's"

    result = []
    for (sport, league), group in groups.items():
        game_lines = [
            build_game_lines(m, endpoint, sub, tz_name, mode=mode, show_channels=show_channels)
            for m, sub in group
        ]
        label = group[0][1].label or league.upper()
        is_event_group = any(m.game.id.startswith("event:") for m, _ in group)
        if is_event_group:
            title = f"{day_label} {label} Event"
        else:
            n = len(group)
            title = f"{day_label} {label} {'Games' if n != 1 else 'Game'}"

        thumb_url = ""
        if thumbs_enabled:
            try:
                thumb_url = build_league_url(league, base_url=thumb_base)
            except Exception:
                pass

        result.append({
            "sport":     sport,
            "league":    league,
            "label":     label,
            "title":     title,
            "thumb_url": thumb_url,
            "games":     game_lines,
        })
    return result
