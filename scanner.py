"""
Alertle-V2 — Scanner.

Runs daily (or on demand), pulls ESPN games for all subscriptions,
matches them against Dispatcharr EPG, and hands off to the scheduler.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import config as cfg_module
from dispatcharr.client import get_client as get_dispatcharr
from epg.matcher import find_channels_for_game
from epg.xmltv import fetch_xmltv
from espn.client import get_games_for_subscription
from models import GameMatch
from scheduler import AlertScheduler

log = logging.getLogger(__name__)

# How many days ahead to scan
LOOKAHEAD_DAYS = 7


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
    if dispatcharr:
        log.info("Fetching EPG programs from Dispatcharr...")
        epg_programs = await dispatcharr.get_programs(start=now, stop=scan_end)
        log.info("Fetched %d EPG programs", len(epg_programs))
        # Enrich programs with channel numbers from the channels endpoint
        try:
            channels = await dispatcharr.get_channels()
            ch_num_map = {ch.id: ch.channel_number for ch in channels if ch.channel_number}
            if ch_num_map:
                for prog in epg_programs:
                    prog.channel_number = ch_num_map.get(prog.channel_id, "")
                log.info("Enriched programs with channel numbers (%d channels with numbers)", len(ch_num_map))
        except Exception as e:
            log.warning("Could not fetch channel numbers: %s", e)
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

            channels = find_channels_for_game(game, epg_programs)
            if not channels:
                no_channel_count += 1
                log.debug("No EPG channel found for %s vs %s", game.away_team.name, game.home_team.name)

            match = GameMatch(game=game, channels=channels)

            # Schedule for each endpoint this subscription targets
            endpoint_ids = sub.endpoints
            for ep in endpoints:
                if ep.id not in endpoint_ids:
                    continue
                scheduler.schedule_game(match, sub, ep)
                scheduled_count += 1

            games_seen.add(game.id)

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
