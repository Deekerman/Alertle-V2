"""
Alertle-V2 — Scheduler.

Manages precise per-game alert triggers with restart-safe SQLite persistence.
Groups games within the bundle window before firing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models import (
    Endpoint, ESPNGame, ESPNTeam,
    GameMatch, ScheduledAlert, Subscription
)
import config as cfg_module

log = logging.getLogger(__name__)

DB_PATH = Path("alertle_state.db")

# Estimated game durations in minutes per sport — used for summary trigger
GAME_DURATIONS: dict[str, int] = {
    "hockey":     150,
    "basketball": 150,
    "football":   210,
    "baseball":   180,
    "soccer":     120,
}

MAX_SUMMARY_RETRIES = 12   # 12 × 10 min = 2 hours past estimated end


# ── SQLite state ──────────────────────────────────────────────────────────────

def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_alerts (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL,
            endpoint_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            fire_at TEXT NOT NULL,
            game_match_json TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _upsert_alert(conn: sqlite3.Connection, alert: ScheduledAlert) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO scheduled_alerts
        (id, game_id, endpoint_id, mode, fire_at, game_match_json, sent, retry_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        alert.id, alert.game_id, alert.endpoint_id, alert.mode,
        alert.fire_at.isoformat(), alert.game_match_json,
        int(alert.sent), alert.retry_count,
    ))
    conn.commit()


def _mark_sent(conn: sqlite3.Connection, alert_id: str) -> None:
    conn.execute("UPDATE scheduled_alerts SET sent=1 WHERE id=?", (alert_id,))
    conn.commit()


def _get_pending(conn: sqlite3.Connection) -> list[ScheduledAlert]:
    rows = conn.execute(
        "SELECT id,game_id,endpoint_id,mode,fire_at,game_match_json,retry_count "
        "FROM scheduled_alerts WHERE sent=0"
    ).fetchall()
    alerts = []
    for row in rows:
        alerts.append(ScheduledAlert(
            id=row[0], game_id=row[1], endpoint_id=row[2],
            mode=row[3],
            fire_at=datetime.fromisoformat(row[4]).replace(tzinfo=timezone.utc),
            game_match_json=row[5],
            sent=False, retry_count=row[6],
        ))
    return alerts


# ── Fire time calculation ─────────────────────────────────────────────────────

def _jitter(precision_minutes: int) -> timedelta:
    if precision_minutes <= 0:
        return timedelta(0)
    seconds = random.randint(-precision_minutes * 60, precision_minutes * 60)
    return timedelta(seconds=seconds)


def _fire_time_for_mode(game: ESPNGame, endpoint: Endpoint, mode: str) -> datetime | None:
    if mode == "lead_time":
        base = game.start_time - timedelta(minutes=endpoint.lead_time_minutes)
        return base + _jitter(endpoint.precision_minutes)
    if mode == "game_start":
        return game.start_time + _jitter(endpoint.precision_minutes)
    if mode == "game_summary":
        duration = GAME_DURATIONS.get(game.sport, 180)
        return game.start_time + timedelta(minutes=duration)
    return None


# ── Notifier dispatch ─────────────────────────────────────────────────────────

async def _dispatch(
    endpoint: Endpoint,
    mode: str,
    matches_subs: list[tuple[GameMatch, Subscription]],
    tz_name: str,
    winner_abbrev: str = "",
) -> None:
    from notifiers import discord, telegram, pushover, ntfy
    notifier_map = {"discord": discord, "telegram": telegram,
                    "pushover": pushover, "ntfy": ntfy}
    mod = notifier_map.get(endpoint.type)
    if not mod:
        log.error("Unknown endpoint type: %s", endpoint.type)
        return
    if len(matches_subs) == 1:
        match, sub = matches_subs[0]
        await mod.send_single(match, endpoint, sub, tz_name, mode=mode,
                              winner_abbrev=winner_abbrev)
    elif mode == "digest":
        await mod.send_digest(matches_subs, endpoint, tz_name)
    else:
        await mod.send_bundled(matches_subs, endpoint, tz_name, mode=mode)


async def _dispatch_standings(endpoint: Endpoint, event_name: str, body: str) -> None:
    from notifiers import discord, telegram, pushover, ntfy
    notifier_map = {"discord": discord, "telegram": telegram,
                    "pushover": pushover, "ntfy": ntfy}
    mod = notifier_map.get(endpoint.type)
    if not mod:
        log.error("Unknown endpoint type: %s", endpoint.type)
        return
    await mod.send_standings(event_name, body, endpoint)


# ── Public API ────────────────────────────────────────────────────────────────

class AlertScheduler:
    def __init__(self):
        self._conn = _init_db()
        self._tasks: dict[str, asyncio.Task] = {}

    def schedule_game(
        self,
        match: GameMatch,
        sub: Subscription,
        endpoint: Endpoint,
    ) -> None:
        """Calculate trigger times for all enabled modes and persist them."""
        game = match.game
        match_json = _serialise_match(match)
        is_event = game.id.startswith("event:")

        for mode in endpoint.modes:
            if mode == "digest":
                continue  # digest is handled by the daily digest task
            if mode == "game_summary" and is_event:
                continue  # final-score summary doesn't apply to event-series

            fire_at = _fire_time_for_mode(game, endpoint, mode)
            if fire_at is None:
                continue

            now = datetime.now(timezone.utc)
            if fire_at < now - timedelta(minutes=5):
                log.debug("Skipping past alert %s/%s/%s", game.id, endpoint.id, mode)
                continue

            alert_id = f"{game.id}:{endpoint.id}:{mode}"
            alert = ScheduledAlert(
                id=alert_id,
                game_id=game.id,
                endpoint_id=endpoint.id,
                mode=mode,
                fire_at=fire_at,
                game_match_json=match_json,
            )
            _upsert_alert(self._conn, alert)
            log.info(
                "Scheduled %s for %s at %s",
                mode,
                f"{game.away_team.name} vs {game.home_team.name}",
                fire_at,
            )

    async def run(self) -> None:
        """
        Main loop. Replays any pending alerts from DB on startup,
        then fires scheduled tasks as their time comes.
        """
        log.info("Scheduler starting — replaying pending alerts from DB")
        self.cleanup_stale_event_series_alerts()
        pending = _get_pending(self._conn)
        log.info("%d pending alerts found", len(pending))

        for alert in pending:
            self._arm_task(alert)

        # Keep the loop alive — new alerts are armed as they're scheduled
        while True:
            await asyncio.sleep(30)

    def _arm_task(self, alert: ScheduledAlert) -> None:
        if alert.id in self._tasks:
            return
        task = asyncio.create_task(self._fire_when_ready(alert))
        self._tasks[alert.id] = task

    async def _fire_when_ready(self, alert: ScheduledAlert) -> None:
        now = datetime.now(timezone.utc)
        delay = (alert.fire_at - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        raw = cfg_module.load_config()
        tz_name = cfg_module.get_timezone(raw)
        endpoint = cfg_module.get_endpoint_by_id(alert.endpoint_id, raw)
        if not endpoint:
            log.warning("Endpoint %s not found — skipping alert %s", alert.endpoint_id, alert.id)
            _mark_sent(self._conn, alert.id)
            return

        if alert.mode == "standings":
            await self._fire_standings(alert, endpoint, tz_name)
            _mark_sent(self._conn, alert.id)
            self._tasks.pop(alert.id, None)
            return

        match = _deserialise_match(alert.game_match_json)
        sub = _find_sub_for_game(match.game, cfg_module.get_subscriptions(raw), alert.endpoint_id)

        if alert.mode == "game_summary":
            await self._fire_summary_with_retry(alert, match, sub, endpoint, tz_name)
        else:
            await _dispatch(endpoint, alert.mode, [(match, sub)], tz_name)
            _mark_sent(self._conn, alert.id)
            self._tasks.pop(alert.id, None)

    async def _fire_standings(
        self,
        alert: ScheduledAlert,
        endpoint: Endpoint,
        tz_name: str,
    ) -> None:
        from espn.client import get_standings_summary
        event_data = json.loads(alert.game_match_json)
        sport = event_data.get("sport", "")
        league = event_data.get("league", "")
        event_name = event_data.get("event_name", "")
        body = await get_standings_summary(sport, league)
        await _dispatch_standings(endpoint, event_name, body)

    def schedule_standings(
        self,
        sub: Subscription,
        endpoint: Endpoint,
        tz_name: str,
        event_info: dict,
    ) -> None:
        """Schedule a daily standings alert for an event-series event."""
        from zoneinfo import ZoneInfo
        raw = cfg_module.load_config()
        standings_time_str = cfg_module.get_standings_time(raw)
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc

        now_local = datetime.now(tz)
        h, m = (int(x) for x in standings_time_str.split(":"))
        fire_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
        if fire_local <= now_local:
            log.debug("Standings time already past today for %s — skipping", event_info.get("event_name"))
            return
        fire_at = fire_local.astimezone(timezone.utc)

        today = datetime.now(timezone.utc).date().isoformat()
        alert_id = f"standings:{event_info['sport']}:{event_info['league']}:{today}:{endpoint.id}"

        alert = ScheduledAlert(
            id=alert_id,
            game_id=event_info.get("event_id", ""),
            endpoint_id=endpoint.id,
            mode="standings",
            fire_at=fire_at,
            game_match_json=json.dumps({
                "type": "standings",
                "sport": event_info["sport"],
                "league": event_info["league"],
                "label": event_info["label"],
                "event_name": event_info["event_name"],
            }),
        )
        _upsert_alert(self._conn, alert)
        log.info("Scheduled standings alert for %s at %s", event_info["event_name"], fire_at)

    async def _fire_summary_with_retry(
        self,
        alert: ScheduledAlert,
        match: GameMatch,
        sub: Subscription,
        endpoint: Endpoint,
        tz_name: str,
    ) -> None:
        from espn.client import get_game_status

        game = match.game
        for attempt in range(MAX_SUMMARY_RETRIES):
            fresh = await get_game_status(game.sport, game.league, game.id)
            if fresh and fresh.status == "final":
                match.game = fresh
                await _dispatch(endpoint, "game_summary", [(match, sub)], tz_name,
                                winner_abbrev=fresh.winner_abbrev)
                _mark_sent(self._conn, alert.id)
                return
            log.debug("Game %s not final yet (attempt %d), retrying in 10 min", game.id, attempt + 1)
            await asyncio.sleep(600)

        log.warning("Game %s never went final after %d retries", game.id, MAX_SUMMARY_RETRIES)
        _mark_sent(self._conn, alert.id)
        self._tasks.pop(alert.id, None)

    def arm_all_pending(self) -> None:
        """Called after new games are scheduled to arm any unarmed tasks."""
        for alert in _get_pending(self._conn):
            self._arm_task(alert)

    def cleanup_stale_event_series_alerts(self) -> None:
        """Delete unsent event-series and standings alerts whose fire_at has passed."""
        now_str = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "DELETE FROM scheduled_alerts WHERE sent=0 "
            "AND (game_id LIKE 'event:%' OR id LIKE 'standings:%') "
            "AND fire_at <= ?",
            (now_str,)
        )
        self._conn.commit()
        if cur.rowcount:
            log.info("Cleaned up %d stale event series alerts", cur.rowcount)

    def list_pending(self) -> list[dict]:
        """Return serializable pending alerts sorted by fire time — used by the dashboard."""
        result = []
        for alert in _get_pending(self._conn):
            try:
                if alert.mode == "standings":
                    data = json.loads(alert.game_match_json)
                    result.append({
                        "id": alert.id,
                        "endpoint_id": alert.endpoint_id,
                        "mode": "standings",
                        "fire_at": alert.fire_at.isoformat(),
                        "game_id": alert.game_id,
                        "away_team": "",
                        "home_team": data.get("event_name", ""),
                        "sport": data.get("sport", ""),
                        "league": data.get("league", "").upper(),
                        "channels": [],
                        "game_start": alert.fire_at.isoformat(),
                    })
                else:
                    match = _deserialise_match(alert.game_match_json)
                    g = match.game
                    result.append({
                        "id": alert.id,
                        "endpoint_id": alert.endpoint_id,
                        "mode": alert.mode,
                        "fire_at": alert.fire_at.isoformat(),
                        "game_id": alert.game_id,
                        "away_team": g.away_team.name,
                        "home_team": g.home_team.name,
                        "sport": g.sport,
                        "league": g.league.upper(),
                        "channels": match.channels,
                        "game_start": g.start_time.isoformat(),
                    })
            except Exception as e:
                log.warning("Failed to deserialise alert %s: %s", alert.id, e)
        return sorted(result, key=lambda x: x["fire_at"])

    async def test_fire(self, alert_id: str) -> bool:
        """Dispatch an alert immediately without marking it sent (test mode)."""
        row = self._conn.execute(
            "SELECT endpoint_id, mode, game_match_json FROM scheduled_alerts WHERE id=?",
            (alert_id,)
        ).fetchone()
        if not row:
            return False
        endpoint_id, mode, match_json = row

        raw = cfg_module.load_config()
        endpoint = cfg_module.get_endpoint_by_id(endpoint_id, raw)
        if not endpoint:
            log.warning("Endpoint %s not found — cannot test alert %s", endpoint_id, alert_id)
            return False

        tz_name = cfg_module.get_timezone(raw)

        if mode == "standings":
            temp_alert = ScheduledAlert(
                id=alert_id, game_id="", endpoint_id=endpoint_id,
                mode="standings", fire_at=datetime.now(timezone.utc),
                game_match_json=match_json,
            )
            await self._fire_standings(temp_alert, endpoint, tz_name)
            return True

        match = _deserialise_match(match_json)
        sub = _find_sub_for_game(match.game, cfg_module.get_subscriptions(raw), endpoint_id)
        await _dispatch(endpoint, mode, [(match, sub)], tz_name)
        return True


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _serialise_match(match: GameMatch) -> str:
    g = match.game
    data = {
        "game": {
            "id": g.id, "sport": g.sport, "league": g.league,
            "start_time": g.start_time.isoformat(),
            "venue": g.venue, "venue_city": g.venue_city,
            "status": g.status,
            "home_score": g.home_score, "away_score": g.away_score,
            "broadcast_networks": g.broadcast_networks,
            "odds_spread": g.odds_spread, "odds_over_under": g.odds_over_under,
            "odds_moneyline": g.odds_moneyline,
            "series_summary": g.series_summary, "season_context": g.season_context,
            "winner_abbrev": g.winner_abbrev,
            "home_team": _team_dict(g.home_team),
            "away_team": _team_dict(g.away_team),
        },
        "channels": match.channels,
        "program_description": match.program_description,
    }
    return json.dumps(data)


def _team_dict(t: ESPNTeam) -> dict:
    return {"id": t.id, "name": t.name, "abbreviation": t.abbreviation,
            "short_name": t.short_name, "location": t.location, "logo_url": t.logo_url}


def _deserialise_match(json_str: str) -> GameMatch:
    data = json.loads(json_str)
    g = data["game"]

    def make_team(d: dict) -> ESPNTeam:
        return ESPNTeam(id=d["id"], name=d["name"], abbreviation=d["abbreviation"],
                        short_name=d["short_name"], location=d["location"], logo_url=d["logo_url"])

    game = ESPNGame(
        id=g["id"], sport=g["sport"], league=g["league"],
        start_time=datetime.fromisoformat(g["start_time"]).replace(tzinfo=timezone.utc),
        home_team=make_team(g["home_team"]),
        away_team=make_team(g["away_team"]),
        venue=g.get("venue", ""), venue_city=g.get("venue_city", ""),
        status=g.get("status", "scheduled"),
        home_score=g.get("home_score"), away_score=g.get("away_score"),
        broadcast_networks=g.get("broadcast_networks", []),
        odds_spread=g.get("odds_spread", ""), odds_over_under=g.get("odds_over_under", ""),
        odds_moneyline=g.get("odds_moneyline", ""),
        series_summary=g.get("series_summary", ""), season_context=g.get("season_context", ""),
        winner_abbrev=g.get("winner_abbrev", ""),
    )
    return GameMatch(
        game=game,
        channels=data.get("channels", []),
        program_description=data.get("program_description", ""),
    )


def _find_sub_for_game(game: ESPNGame, subs: list[Subscription], endpoint_id: str) -> Subscription:
    """Find the best matching subscription for a game + endpoint combo."""
    for sub in subs:
        if endpoint_id not in sub.endpoints:
            continue
        if sub.espn_sport != game.sport or sub.espn_league != game.league:
            continue
        if sub.scope == "team":
            if sub.espn_team_id in (game.home_team.id, game.away_team.id):
                return sub
        else:
            return sub
    # Fallback — return first matching league sub
    for sub in subs:
        if sub.espn_sport == game.sport and sub.espn_league == game.league:
            return sub
    return Subscription(label="Unknown", espn_sport=game.sport, espn_league=game.league, scope="league")
