"""
Alertle-V2 — Scanner.

Runs daily (or on demand), pulls ESPN games for all subscriptions,
matches them against Dispatcharr EPG, and hands off to the scheduler.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import config as cfg_module
from dispatcharr.client import get_client as get_dispatcharr
from epg.matcher import find_channels_for_event, find_channels_for_game, find_event_earliest_start, find_event_time_groups
from epg.xmltv import fetch_xmltv
from espn.client import get_active_event, get_games_for_subscription
from models import ESPNGame, ESPNTeam, GameMatch
from scheduler import AlertScheduler

log = logging.getLogger(__name__)

# How many days ahead to scan
LOOKAHEAD_DAYS = 7

# Base EPG search terms per event-series league (augmented with event name at runtime).
# These must match the exact phrasing used in EPG title/subtitle fields.
# Verified against user's actual XMLTV feed (Golf Channel uses "PGA Tour Golf" as title;
# Sky Sports Golf uses event name first, e.g. "Charles Schwab Challenge, PGA Tour Golf").
EVENT_BASE_TERMS: dict[tuple[str, str], list[str]] = {
    ("golf",   "pga"):  ["PGA Tour Golf", "PGA Tour"],
    ("golf",   "lpga"): ["LPGA Tour Golf", "LPGA"],
    ("golf",   "eur"):  ["DP World Tour Golf", "DP World Tour"],
    ("racing", "f1"):   ["Formula 1", "F1", "Formula One", "Grand Prix"],
    ("mma",    "ufc"):  ["UFC", "MMA"],
    ("tennis", "atp"):  ["ATP Tennis", "ATP Tour"],
    ("tennis", "wta"):  ["WTA Tennis", "WTA Tour"],
}

# Programs whose title/subtitle match any of these terms are excluded even if they
# also match the base terms — used to filter studio shows, analysis, and highlight
# programmes that are technically "live" but are not actual event coverage.
EVENT_EXCLUDE_TERMS: dict[tuple[str, str], list[str]] = {
    ("golf", "pga"):  [
        # Studio / analysis shows
        "the cut", "inside the pga", "golf academy", "school of golf",
        "golf central", "morning drive", "live from", "highlights",
        "best of", "on the range", "the drop", "the return",
        # LPGA programs contain "lpga tour golf" which includes "pga tour" as a substring
        "lpga",
    ],
    ("golf", "lpga"): ["highlights", "best of", "lpga tour golf academy"],
    ("golf", "eur"):  ["highlights", "best of"],
    ("racing", "f1"): ["highlights", "best of", "classic"],
    ("mma",  "ufc"):  ["highlights", "best of", "embedded"],
    ("tennis", "atp"): ["highlights", "best of"],
    ("tennis", "wta"): ["highlights", "best of"],
}


async def run_scan(scheduler: AlertScheduler) -> dict:
    """
    Full scan:
    1. Pull games from ESPN for every subscription
    2. Match each game against Dispatcharr EPG
    3. Schedule alerts via the scheduler
    Returns a summary dict for the UI.
    """
    raw = cfg_module.load_config()
    subs = cfg_module.get_subscriptions(raw)
    endpoints = cfg_module.get_endpoints(raw)
    dispatcharr = get_dispatcharr(raw)

    now = datetime.now(timezone.utc)
    scan_end = now + timedelta(days=LOOKAHEAD_DAYS)

    log.info("Starting scan: %d subscriptions, lookahead %d days", len(subs), LOOKAHEAD_DAYS)

    # Build date range string for ESPN (YYYYMMDD-YYYYMMDD)
    dates = f"{now.strftime('%Y%m%d')}-{scan_end.strftime('%Y%m%d')}"

    # Fetch EPG programs once for the whole window
    epg_programs = []
    dispatcharr_cfg = raw.get("dispatcharr", {})
    output_profile = dispatcharr_cfg.get("output_profile", "").strip()

    if dispatcharr and output_profile:
        # Use Dispatcharr's filtered XMLTV output for the selected profile
        xmltv_url = f"{dispatcharr.base_url}/output/epg/{output_profile}/"
        log.info("Fetching EPG from Dispatcharr profile '%s': %s", output_profile, xmltv_url)
        try:
            epg_programs = await fetch_xmltv(xmltv_url)
            log.info("Fetched %d EPG programs from Dispatcharr XMLTV (profile: %s)",
                     len(epg_programs), output_profile)
        except Exception as e:
            log.error("Failed to fetch Dispatcharr XMLTV (profile '%s'): %s", output_profile, e)
    elif dispatcharr:
        log.info("Fetching EPG programs from Dispatcharr REST API...")
        epg_programs = await dispatcharr.get_programs(start=now, stop=scan_end)
        log.info("Fetched %d EPG programs", len(epg_programs))
        # Enrich programs with channel numbers from the channels API
        try:
            channels = await dispatcharr.get_channels()
            ch_num_map = {ch.id: ch.channel_number for ch in channels if ch.channel_number}
            for prog in epg_programs:
                prog.channel_number = ch_num_map.get(prog.channel_id, "")
            log.info("Enriched programs with channel numbers (%d channels)", len(ch_num_map))
        except Exception as e:
            log.warning("Could not enrich channel data: %s", e)
    else:
        log.warning("Dispatcharr not configured — channel matching disabled")

    # Merge programs from any configured XMLTV sources
    for source in cfg_module.get_epg_sources(raw):
        url = source.get("url", "").strip()
        name = source.get("name", url)
        if not url:
            continue
        try:
            xmltv_progs = await fetch_xmltv(url)
            epg_programs.extend(xmltv_progs)
            log.info("XMLTV source '%s': %d programs fetched", name, len(xmltv_progs))
        except Exception as e:
            log.error("XMLTV source '%s' failed: %s", name, e)

    # Track what we scheduled to report back to the UI
    scheduled_count = 0
    no_channel_count = 0
    games_seen: set[str] = set()

    for sub in subs:
        if sub.scope == "event_series":
            continue  # handled in the event-series loop below

        try:
            games = await get_games_for_subscription(
                sport=sub.espn_sport,
                league=sub.espn_league,
                team_id=sub.espn_team_id,
                scope=sub.scope,
                dates=dates,
            )
        except Exception as e:
            log.error("ESPN fetch failed for %s: %s", sub.label, e)
            continue

        log.info("[%s] %d games found from ESPN", sub.label, len(games))

        for game in games:
            # Skip games that have already ended
            if game.status == "final":
                continue

            channels, description = find_channels_for_game(game, epg_programs)
            if not channels:
                no_channel_count += 1
                log.debug("No EPG channel found for %s vs %s", game.away_team.name, game.home_team.name)

            match = GameMatch(game=game, channels=channels, program_description=description)

            # Schedule for each endpoint this subscription targets
            endpoint_ids = sub.endpoints
            for ep in endpoints:
                if ep.id not in endpoint_ids:
                    continue
                scheduler.schedule_game(match, sub, ep)
                scheduled_count += 1

            games_seen.add(game.id)

    # ── Event Series ──────────────────────────────────────────────────────────
    tz_name = cfg_module.get_timezone(raw)

    # Prune event series alerts for subscriptions that no longer exist,
    # then wipe and rebuild alerts for active ones so the DB stays fresh.
    active_event_subs = {
        (s.espn_sport, s.espn_league)
        for s in subs if s.scope == "event_series"
    }
    scheduler.prune_orphaned_event_series_alerts(active_event_subs)
    for sport, league in active_event_subs:
        scheduler.cleanup_alerts_for_sport_league(sport, league)

    for sub in subs:
        if sub.scope != "event_series":
            continue
        try:
            base_terms = EVENT_BASE_TERMS.get((sub.espn_sport, sub.espn_league), [])
            if not base_terms:
                log.warning("[%s] No EPG terms defined for %s/%s", sub.label, sub.espn_sport, sub.espn_league)
                continue

            # Try ESPN for current event name — used for standings labels and alert ID.
            # NOT used for EPG term matching: ESPN sometimes returns the wrong event
            # for a league endpoint (e.g. golf/eur returning a PGA Tour event), which
            # would add that name to event_terms and match wrong EPG programs.
            event = await get_active_event(sub.espn_sport, sub.espn_league)
            event_name = event["name"] if event else sub.label
            event_id = event["id"] if event else sub.espn_league

            # EPG matching uses only the verified base terms for this league.
            event_terms = list(base_terms)
            exclude_terms = EVENT_EXCLUDE_TERMS.get((sub.espn_sport, sub.espn_league), [])

            # Scan the lookahead window starting from TOMORROW.
            # Today's events are already covered by yesterday's scheduled scan;
            # starting at 1 prevents a manual scan (or first-run) from firing
            # same-day push alerts before the user's configured lead time.
            days_with_coverage = 0

            # Standings alert fires today — only schedule it if the event has
            # live EPG coverage today (i.e. the tournament is actually running today).
            today_has_coverage = bool(find_event_time_groups(
                event_terms, epg_programs, now.date(), exclude_terms=exclude_terms
            ))
            standings_scheduled = False

            for day_offset in range(1, LOOKAHEAD_DAYS + 1):
                check_dt = now + timedelta(days=day_offset)
                check_date = check_dt.date()
                date_str = check_date.isoformat()

                # Each distinct broadcast window on this day becomes a separate alert.
                # Channels that start within 60 minutes of each other are grouped;
                # early AU/featured-group coverage and the main primary-market broadcast
                # are kept separate so the user gets a notification per airtime.
                time_groups = find_event_time_groups(
                    event_terms, epg_programs, check_date, exclude_terms=exclude_terms
                )
                if not time_groups:
                    continue

                day_scheduled = False

                for event_start, channels, description, epg_title, epg_subtitle in time_groups:
                    # Derive the display name from EPG program data.
                    #
                    # Strategy:
                    #   1. Prefer the title when it's "specific" — i.e. it contains content
                    #      beyond the bare league/tour phrase (e.g. "Austrian Open, DP World
                    #      Tour Golf" is specific; "DP World Tour Golf" alone is generic).
                    #      Strip any leading "Live:" broadcast prefix first.
                    #   2. Fall back to subtitle when the title is generic AND the subtitle
                    #      looks like an actual event name (not a round/day indicator).
                    #   3. Fall back to the generic title, then ESPN event_name, then sub.label.
                    #
                    # This prevents a mislabelled subtitle (e.g. a PGA program subtitle
                    # appearing on a DP World Tour channel) from overriding a correct title.
                    _title_clean = re.sub(r'^Live:\s*', '', epg_title, flags=re.IGNORECASE).strip()
                    _sub = epg_subtitle.strip()
                    _is_round = bool(re.match(
                        r'^(round|day|session|hole|week)\s*(\d+|one|two|three|four|final)$',
                        _sub, re.IGNORECASE
                    ))
                    # Title is "specific" if it contains more than just the bare league term
                    _title_is_generic = not _title_clean or any(
                        _title_clean.lower() == bt.lower() for bt in base_terms
                    )
                    if not _title_is_generic:
                        display_name = _title_clean
                    elif _sub and not _is_round:
                        display_name = _sub
                    else:
                        display_name = _title_clean
                    display_name = display_name or event_name or sub.label
                    log.debug("[%s] EPG match — title=%r subtitle=%r → display=%r",
                              sub.label, epg_title, epg_subtitle, display_name)

                    hhmm = f"{event_start.hour:02d}{event_start.minute:02d}"
                    fake_game = ESPNGame(
                        id=f"event:{sub.espn_sport}:{sub.espn_league}:{event_id}:{date_str}:{hhmm}",
                        sport=sub.espn_sport,
                        league=sub.espn_league,
                        home_team=ESPNTeam(
                            id="event", name=display_name, abbreviation="",
                            short_name=display_name, location="",
                        ),
                        away_team=ESPNTeam(
                            id="event", name="", abbreviation="", short_name="", location="",
                        ),
                        start_time=event_start,
                    )

                    match = GameMatch(game=fake_game, channels=channels, program_description=description)

                    for ep in endpoints:
                        if ep.id not in sub.endpoints:
                            continue
                        scheduler.schedule_game(match, sub, ep)
                        # Schedule today's standings alert only when event is live today;
                        # only once per subscription regardless of how many time groups exist.
                        if sub.standings_alert and event and today_has_coverage and not standings_scheduled:
                            scheduler.schedule_standings(sub, ep, tz_name, {
                                "sport": sub.espn_sport,
                                "league": sub.espn_league,
                                "label": sub.label,
                                "event_name": event_name,
                                "event_id": event_id,
                            })
                        scheduled_count += 1

                    if sub.standings_alert and event and today_has_coverage:
                        standings_scheduled = True

                    games_seen.add(fake_game.id)
                    day_scheduled = True

                if day_scheduled:
                    days_with_coverage += 1

            if days_with_coverage:
                log.info("[%s] %s — %d days of EPG coverage found", sub.label, event_name, days_with_coverage)
            else:
                no_channel_count += 1
                log.info("[%s] No EPG coverage found for %s in next %d days",
                         sub.label, event_name, LOOKAHEAD_DAYS)

        except Exception as e:
            log.error("Event series scan failed for %s: %s", sub.label, e)

    # Arm any newly added tasks
    scheduler.arm_all_pending()

    summary = {
        "scanned_at": now.isoformat(),
        "unique_games": len(games_seen),
        "alerts_scheduled": scheduled_count,
        "games_without_channel": no_channel_count,
        "epg_programs_fetched": len(epg_programs),
        "subscriptions": len(subs),
    }
    log.info("Scan complete: %s", summary)
    return summary


async def daily_scan_loop(scheduler: AlertScheduler) -> None:
    """
    Runs the scan once at startup, then again each day at the configured scan_time.
    """
    # Run immediately on startup
    await run_scan(scheduler)

    while True:
        raw = cfg_module.load_config()
        scan_time_str = cfg_module.get_scan_time(raw)  # e.g. "06:00"
        tz_name = cfg_module.get_timezone(raw)

        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz_name)
        except Exception:
            from datetime import timezone as _tz
            tz = _tz.utc

        now_local = datetime.now(tz)
        hour, minute = (int(x) for x in scan_time_str.split(":"))
        next_run = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now_local:
            next_run += timedelta(days=1)

        wait_seconds = (next_run - now_local).total_seconds()
        log.info("Next scan at %s (in %.0f min)", next_run.strftime("%H:%M %Z"), wait_seconds / 60)
        await asyncio.sleep(wait_seconds)
        await run_scan(scheduler)
