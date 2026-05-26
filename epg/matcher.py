"""
Alertle-V2 — EPG matcher.

Given an ESPNGame, finds matching EPG programs by:
1. Time proximity  — program starts within ±45 min of ESPN game time
2. Text confirmation — team names appear in title or subtitle (NOT description,
   to avoid false positives from news/rerun channels that mention city names)
"""
from __future__ import annotations

import re
from datetime import timedelta

from models import EPGProgram, ESPNGame

MATCH_WINDOW_MINUTES = 45


def _normalise(s: str) -> str:
    return s.lower().strip()


def _text_contains_any(text: str, terms: list[str]) -> bool:
    """
    Return True if any term appears in text.
    Short terms (≤4 chars, e.g. abbreviations) require a word boundary so
    "VGK" doesn't match inside unrelated words.
    """
    t = _normalise(text)
    for term in terms:
        if not term:
            continue
        term_norm = _normalise(term)
        if len(term_norm) <= 4:
            if re.search(r'\b' + re.escape(term_norm) + r'\b', t):
                return True
        else:
            if term_norm in t:
                return True
    return False


def _search_terms_for_game(game: ESPNGame) -> list[str]:
    """Build a list of strings to look for in EPG title/subtitle."""
    terms = []
    for team in (game.home_team, game.away_team):
        terms.append(team.name)          # "Toronto Maple Leafs"
        terms.append(team.short_name)    # "Maple Leafs"
        terms.append(team.location)      # "Toronto"
        terms.append(team.abbreviation)  # "TOR"
    return [t for t in terms if t.strip()]


def find_channels_for_game(
    game: ESPNGame,
    programs: list[EPGProgram],
) -> list[str]:
    """
    Return a deduplicated, sorted list of channel strings whose EPG programs
    match this game. Each string is formatted as "{number} - {name}" when a
    channel number is available, otherwise just "{name}".

    A program matches when:
      - Its start time is within ±MATCH_WINDOW_MINUTES of the game's start_time
      - At least one team name (or location/abbreviation) appears in
        title or subtitle (description is intentionally excluded)
    """
    search_terms = _search_terms_for_game(game)
    window = timedelta(minutes=MATCH_WINDOW_MINUTES)
    # Map channel_name → display string (to deduplicate by channel, keep first number seen)
    matched: dict[str, str] = {}

    for prog in programs:
        # 1. Time window check
        delta = abs(prog.start - game.start_time)
        if delta > window:
            continue

        # 2. Text confirmation — title + subtitle only
        haystack = f"{prog.title} {prog.subtitle}"
        if _text_contains_any(haystack, search_terms):
            if prog.channel_name and prog.channel_name not in matched:
                if prog.channel_number:
                    matched[prog.channel_name] = f"{prog.channel_number} - {prog.channel_name}"
                else:
                    matched[prog.channel_name] = prog.channel_name

    return sorted(matched.values())
