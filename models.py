"""
Alertle-V2 — Data models
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ── ESPN models ───────────────────────────────────────────────────────────────

@dataclass
class ESPNTeam:
    id: str
    name: str           # "Toronto Maple Leafs"
    abbreviation: str   # "TOR"
    short_name: str     # "Maple Leafs"
    location: str       # "Toronto"
    logo_url: str = ""


@dataclass
class ESPNLeague:
    sport: str          # "hockey"
    league: str         # "nhl"
    label: str          # "NHL"
    is_event_series: bool = False  # True for golf, F1, UFC, tennis


@dataclass
class ESPNGame:
    id: str
    sport: str
    league: str
    home_team: ESPNTeam
    away_team: ESPNTeam
    start_time: datetime            # UTC
    venue: str = ""
    venue_city: str = ""
    status: str = "scheduled"       # scheduled | in_progress | final
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    broadcast_networks: list[str] = field(default_factory=list)
    odds_spread: str = ""
    odds_over_under: str = ""
    odds_moneyline: str = ""
    series_summary: str = ""        # "Series tied 2-2"
    season_context: str = ""        # "Week 14" / "Matchday 22"
    winner_abbrev: str = ""         # set when status == final


# ── EPG models ────────────────────────────────────────────────────────────────

@dataclass
class EPGChannel:
    id: str
    name: str
    channel_number: str = ""


@dataclass
class EPGProgram:
    channel_id: str
    channel_name: str
    title: str
    subtitle: str
    description: str
    start: datetime     # UTC
    stop: datetime      # UTC
    channel_number: str = ""
    is_live: bool = False  # True when XMLTV <live/> element is present


# ── Match — ESPN game + EPG channels it was found on ─────────────────────────

@dataclass
class GameMatch:
    game: ESPNGame
    channels: list[str] = field(default_factory=list)   # channel names from EPG
    program_description: str = ""                        # first EPG program description
    schedule: list[dict] = field(default_factory=list)
    # schedule: [{"start": "<ISO datetime UTC>", "channels": [...]}, ...]
    # populated for event_series; empty for regular team/league games


# ── Config models ─────────────────────────────────────────────────────────────

@dataclass
class GameThumbsEndpointConfig:
    type: str = "thumb"         # thumb | logo | cover
    style: int = 1
    aspect: str = "16-9"        # thumb only: 4-3 | 16-9 | 1-1
    show_logo: bool = True
    badge: Optional[str] = None # HD | FHD | 4K | UHD | ALT | None
    fallback: bool = True


@dataclass
class ContentDefaults:
    show_venue: bool = True
    show_broadcast: bool = True
    show_odds: bool = False
    show_series: bool = True
    show_week_context: bool = True
    show_key_stats: bool = False


@dataclass
class Endpoint:
    id: str
    type: str                   # discord | telegram | pushover | ntfy
    modes: list[str] = field(default_factory=list)  # digest | lead_time | game_start | game_summary
    lead_time_minutes: int = 30
    precision_minutes: int = 0  # 0 = exact
    digest_time: str = "08:00"
    digest_team_days: int = 1
    digest_event_days: int = 7
    bundle_window_minutes: int = 10
    auto_record: bool = False
    content_defaults: ContentDefaults = field(default_factory=ContentDefaults)
    game_thumbs: GameThumbsEndpointConfig = field(default_factory=GameThumbsEndpointConfig)
    # notifier-specific fields stored in raw dict
    _raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Subscription:
    label: str
    espn_sport: str
    espn_league: str
    scope: str                  # "team" | "league" | "event_series"
    espn_team_id: Optional[str] = None
    espn_team_name: Optional[str] = None
    espn_team_abbrev: Optional[str] = None
    game_thumbs_league: str = ""
    content_overrides: dict = field(default_factory=dict)
    endpoints: list[str] = field(default_factory=list)
    standings_alert: bool = False  # event_series only: send daily leaderboard


# ── Scheduled alert state (persisted to SQLite) ───────────────────────────────

@dataclass
class ScheduledAlert:
    id: str                     # "{game_id}:{endpoint_id}:{mode}"
    game_id: str
    endpoint_id: str
    mode: str                   # digest | lead_time | game_start | game_summary
    fire_at: datetime           # UTC — when to send
    game_match_json: str        # serialised GameMatch
    sent: bool = False
    retry_count: int = 0
