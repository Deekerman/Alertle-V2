"""
Alertle-V2 — EPG matcher.

Given an ESPNGame, finds matching EPG programs by:
1. Time proximity  — program starts within ±45 min of ESPN game time
2. Text confirmation — team names or league keywords appear in title/subtitle/desc
"""
from __future__ import annotations

import re
from datetime import timedelta

from models import EPGProgram, ESPNGame

MATCH_WINDOW_MINUTES = 45


def _normalise(s: str) -> str:
    return s.lower().strip()


def _text_contains_any(text: str, terms: list[str]) -> bool:
    t = _normalise(text)
    return any(_normalise(term) in t for term in terms if term)


def _search_terms_for_game(game: ESPNGame) -> list[str]:
    """Build a list of strings to look for in EPG title/subtitle/description."""
    terms = []
    for team in (game.home_team, game.away_team):
        terms.append(team.name)          # "Toronto Maple Leafs"
        terms.append(team.short_name)    # "Maple Leafs"
        terms.append(team.location)      # "Toronto"
        terms.append(team.abbreviation)  # "TOR"
    # Remove empties
    return [t for t in terms if t.strip()]


def find_channels_for_game(
    game: ESPNGame,
    programs: list[EPGProgram],
) -> list[str]:
    """
    Return a deduplicated, sorted list of channel names whose EPG programs
    match this game.

    A program matches when:
      - Its start time is within ±MATCH_WINDOW_MINUTES of the game's start_time
      - At least one team name (or location/abbreviation) appears in
        title, subtitle, or description
    """
    search_terms = _search_terms_for_game(game)
    window = timedelta(minutes=MATCH_WINDOW_MINUTES)
    matched_channels: set[str] = set()

    for prog in programs:
        # 1. Time window check
        delta = abs(prog.start - game.start_time)
        if delta > window:
            continue

        # 2. Text confirmation
        haystack = " ".join([prog.title, prog.subtitle, prog.description])
        if _text_contains_any(haystack, search_terms):
            if prog.channel_name:
                matched_channels.add(prog.channel_name)

    return sorted(matched_channels)
