"""
Alertle-V2 — ESPN API client.

All public ESPN endpoints — no API key required.
Base: https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from models import ESPNGame, ESPNLeague, ESPNTeam
from espn.cache import games_cache, leagues_cache, teams_cache

log = logging.getLogger(__name__)

BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Curated list of supported sport/league combos shown in the UI dropdown.
# Format: (sport_path, league_path, display_label)
SUPPORTED_LEAGUES: list[tuple[str, str, str]] = [
    # North American team sports
    ("football",   "nfl",                    "NFL"),
    ("football",   "college-football",        "NCAA Football"),
    ("basketball", "nba",                    "NBA"),
    ("basketball", "wnba",                   "WNBA"),
    ("basketball", "mens-college-basketball", "NCAA Men's Basketball"),
    ("basketball", "womens-college-basketball","NCAA Women's Basketball"),
    ("baseball",   "mlb",                    "MLB"),
    ("hockey",     "nhl",                    "NHL"),
    ("football",   "ufl",                    "UFL"),
    # Soccer — England
    ("soccer",     "eng.1",                  "English Premier League"),
    ("soccer",     "eng.2",                  "EFL Championship"),
    ("soccer",     "eng.3",                  "EFL League One"),
    ("soccer",     "eng.4",                  "EFL League Two"),
    ("soccer",     "eng.fa_cup",             "FA Cup"),
    ("soccer",     "eng.league_cup",         "EFL Cup"),
    # Soccer — Scotland
    ("soccer",     "sco.1",                  "Scottish Premiership"),
    # Soccer — Europe
    ("soccer",     "esp.1",                  "La Liga"),
    ("soccer",     "ger.1",                  "Bundesliga"),
    ("soccer",     "ita.1",                  "Serie A"),
    ("soccer",     "fra.1",                  "Ligue 1"),
    # Soccer — Americas
    ("soccer",     "usa.1",                  "MLS"),
    ("soccer",     "nwsl",                   "NWSL"),
    # Soccer — UEFA & FIFA
    ("soccer",     "uefa.champions",         "UEFA Champions League"),
    ("soccer",     "uefa.europa",            "UEFA Europa League"),
    ("soccer",     "uefa.conference",        "UEFA Conference League"),
    ("soccer",     "fifa.world",             "FIFA World Cup"),
    ("soccer",     "fifa.wwc",               "FIFA Women's World Cup"),
]

# Event-series sports: no home/away teams, just events (tournaments, races, cards)
EVENT_SERIES_LEAGUES: list[tuple[str, str, str]] = [
    ("golf",    "pga",  "PGA Tour"),
    ("golf",    "lpga", "LPGA Tour"),
    ("golf",    "eur",  "DP World Tour"),
    ("racing",  "f1",   "Formula 1"),
    ("mma",     "ufc",  "UFC"),
    ("tennis",  "atp",  "ATP Tennis"),
    ("tennis",  "wta",  "WTA Tennis"),
]


async def _get(url: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


# ── Leagues ───────────────────────────────────────────────────────────────────

def get_supported_leagues() -> list[ESPNLeague]:
    """Return the curated list of supported leagues for the UI dropdown."""
    team_leagues = [
        ESPNLeague(sport=sport, league=league, label=label, is_event_series=False)
        for sport, league, label in SUPPORTED_LEAGUES
    ]
    event_leagues = [
        ESPNLeague(sport=sport, league=league, label=label, is_event_series=True)
        for sport, league, label in EVENT_SERIES_LEAGUES
    ]
    return team_leagues + event_leagues


# ── Teams ─────────────────────────────────────────────────────────────────────

async def get_teams(sport: str, league: str) -> list[ESPNTeam]:
    """Fetch all teams for a league. Cached for 1 hour."""
    cache_key = f"teams:{sport}:{league}"
    cached = teams_cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{BASE}/{sport}/{league}/teams"
    try:
        data = await _get(url, {"limit": 200})
    except Exception as e:
        log.error("ESPN teams fetch failed (%s/%s): %s", sport, league, e)
        return []

    teams = []
    for item in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        t = item.get("team", {})
        teams.append(ESPNTeam(
            id=str(t.get("id", "")),
            name=t.get("displayName", ""),
            abbreviation=t.get("abbreviation", ""),
            short_name=t.get("shortDisplayName", ""),
            location=t.get("location", ""),
            logo_url=(t.get("logos") or [{}])[0].get("href", ""),
        ))

    teams.sort(key=lambda t: t.name)
    teams_cache.set(cache_key, teams)
    return teams


# ── Schedule / Scoreboard ─────────────────────────────────────────────────────

def _parse_team(t: dict) -> ESPNTeam:
    team = t.get("team", {})
    return ESPNTeam(
        id=str(team.get("id", "")),
        name=team.get("displayName", ""),
        abbreviation=team.get("abbreviation", ""),
        short_name=team.get("shortDisplayName", ""),
        location=team.get("location", ""),
        logo_url=(team.get("logos") or [{}])[0].get("href", ""),
    )


def _parse_game(event: dict, sport: str, league: str) -> ESPNGame | None:
    """Parse a single ESPN scoreboard event into an ESPNGame."""
    try:
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors", [])
        if len(competitors) < 2:
            return None

        # home / away
        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        # start time
        date_str = event.get("date", "")
        try:
            start_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            start_time = datetime.now(timezone.utc)

        # status
        status_type = competition.get("status", {}).get("type", {})
        raw_status = status_type.get("name", "STATUS_SCHEDULED").lower()
        if "final" in raw_status:
            status = "final"
        elif "progress" in raw_status or "in_progress" in raw_status:
            status = "in_progress"
        else:
            status = "scheduled"

        # scores
        home_score = None
        away_score = None
        winner_abbrev = ""
        if status in ("final", "in_progress"):
            try:
                home_score = int(home.get("score", 0))
                away_score = int(away.get("score", 0))
            except (ValueError, TypeError):
                pass
        if status == "final":
            if home_score is not None and away_score is not None:
                if home_score > away_score:
                    winner_abbrev = home.get("team", {}).get("abbreviation", "")
                else:
                    winner_abbrev = away.get("team", {}).get("abbreviation", "")

        # venue
        venue_data = competition.get("venue", {})
        venue_name = venue_data.get("fullName", "")
        venue_city = venue_data.get("address", {}).get("city", "")

        # broadcasts
        broadcasts = []
        for b in competition.get("broadcasts", []):
            name = (b.get("names") or [""])[0] or b.get("media", {}).get("shortName", "")
            if name:
                broadcasts.append(name)

        # odds
        odds_list = competition.get("odds", [{}])
        odds = odds_list[0] if odds_list else {}
        _ou = odds.get("overUnder")
        over_under = str(_ou) if _ou not in (None, "") else ""

        # Spread: prefer structured pointSpread.home.close.line (reliable across sports).
        # ESPN puts the moneyline in details for hockey, so details alone is unreliable.
        ps_line = odds.get("pointSpread", {}).get("home", {}).get("close", {}).get("line", "")
        if ps_line:
            home_abbrev = home.get("team", {}).get("abbreviation", "")
            spread = f"{home_abbrev} {ps_line}" if home_abbrev else ps_line
        else:
            spread = odds.get("details", "")

        # Moneyline: ESPN nests this under odds["moneyline"]["home/away"]["close"]["odds"]
        ml = odds.get("moneyline", {})
        home_ml = (ml.get("home", {}).get("close", {}).get("odds", "")
                   or ml.get("home", {}).get("open", {}).get("odds", ""))
        away_ml = (ml.get("away", {}).get("close", {}).get("odds", "")
                   or ml.get("away", {}).get("open", {}).get("odds", ""))

        # series / season notes
        series_summary = competition.get("series", {}).get("summary", "")
        note = event.get("season", {}).get("slug", "")
        week = event.get("week", {}).get("text", "")
        season_context = week or note

        return ESPNGame(
            id=str(event.get("id", "")),
            sport=sport,
            league=league,
            home_team=_parse_team(home),
            away_team=_parse_team(away),
            start_time=start_time,
            venue=venue_name,
            venue_city=venue_city,
            status=status,
            home_score=home_score,
            away_score=away_score,
            broadcast_networks=broadcasts,
            odds_spread=spread,
            odds_over_under=over_under,
            odds_home_ml=home_ml,
            odds_away_ml=away_ml,
            series_summary=series_summary,
            season_context=season_context,
            winner_abbrev=winner_abbrev,
        )
    except Exception as e:
        log.warning("Failed to parse ESPN event %s: %s", event.get("id"), e)
        return None


async def get_scoreboard(sport: str, league: str, dates: str | None = None) -> list[ESPNGame]:
    """
    Fetch scoreboard for a sport/league.
    dates: optional YYYYMMDD or YYYYMMDD-YYYYMMDD range string.
    Cached for 5 minutes (bypass cache if dates is None = live/today).
    """
    cache_key = f"scoreboard:{sport}:{league}:{dates or 'today'}"
    cached = games_cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{BASE}/{sport}/{league}/scoreboard"
    params: dict[str, Any] = {"limit": 200}
    if dates:
        params["dates"] = dates

    try:
        data = await _get(url, params)
    except Exception as e:
        log.error("ESPN scoreboard fetch failed (%s/%s): %s", sport, league, e)
        return []

    games = []
    for event in data.get("events", []):
        game = _parse_game(event, sport, league)
        if game:
            games.append(game)

    games_cache.set(cache_key, games)
    return games


async def get_games_for_subscription(
    sport: str,
    league: str,
    team_id: str | None,
    scope: str,
    dates: str | None = None,
) -> list[ESPNGame]:
    """
    Return games relevant to a subscription.
    scope="league"  → all games
    scope="team"    → only games where team_id is home or away
    """
    all_games = await get_scoreboard(sport, league, dates)
    if scope == "league" or not team_id:
        return all_games
    return [
        g for g in all_games
        if g.home_team.id == team_id or g.away_team.id == team_id
    ]


async def get_game_status(sport: str, league: str, game_id: str) -> ESPNGame | None:
    """Re-fetch a single game's current status — used by the summary poller."""
    # Bypass cache — we need fresh status
    url = f"{BASE}/{sport}/{league}/scoreboard"
    try:
        data = await _get(url)
    except Exception as e:
        log.error("ESPN status check failed for game %s: %s", game_id, e)
        return None

    for event in data.get("events", []):
        if str(event.get("id")) == game_id:
            return _parse_game(event, sport, league)
    return None


# ── Event Series ──────────────────────────────────────────────────────────────

async def get_active_event(sport: str, league: str) -> dict | None:
    """
    Return today's active event for an event-series sport.
    Returns {id, name, date, status} or None if no event today.
    """
    url = f"{BASE}/{sport}/{league}/scoreboard"
    try:
        data = await _get(url)
    except Exception as e:
        log.error("ESPN active event fetch failed (%s/%s): %s", sport, league, e)
        return None

    events = data.get("events", [])
    if not events:
        return None
    event = events[0]
    event_id = str(event.get("id", ""))
    if not event_id:
        return None
    return {
        "id": event_id,
        "name": event.get("name", event.get("shortName", "")),
        "date": event.get("date", ""),
        "status": event.get("status", {}).get("type", {}).get("name", ""),
    }


async def get_standings_summary(sport: str, league: str) -> str:
    """
    Fetch and format a top-10 leaderboard/standings for an event-series sport.
    Returns a plain-text string for embedding in a notification.
    """
    url = f"{BASE}/{sport}/{league}/scoreboard"
    try:
        data = await _get(url)
    except Exception as e:
        log.error("ESPN standings fetch failed (%s/%s): %s", sport, league, e)
        return "Failed to fetch standings."

    events = data.get("events", [])
    if not events:
        return "No active event found."

    event = events[0]
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors", [])
    if not competitors:
        return "No standings available yet."

    lines = []
    for comp in competitors[:10]:
        pos = (comp.get("status") or {})
        if isinstance(pos, dict):
            pos_text = pos.get("position", {}).get("displayText", "")
        else:
            pos_text = ""
        athlete = comp.get("athlete") or comp.get("team") or {}
        name = athlete.get("displayName", athlete.get("name", ""))
        score = str(comp.get("score", "") or comp.get("displayScore", ""))
        if name:
            line = f"{pos_text + '. ' if pos_text else ''}{name}"
            if score and score not in ("", "0"):
                line += f"  {score}"
            lines.append(line)

    return "\n".join(lines) if lines else "No standings data available."
