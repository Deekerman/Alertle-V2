"""
Alertle-V2 — EPG matcher.

Given an ESPNGame, finds matching EPG programs by:
1. Time proximity  — program starts within ±45 min of ESPN game time
2. Text confirmation — terms for BOTH teams must appear in subtitle
   (title is typically generic like "NHL Hockey"; subtitle has the teams)
"""
from __future__ import annotations

import re
from datetime import timedelta

from models import EPGProgram, ESPNGame, ESPNTeam

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


def _terms_for_team(team: ESPNTeam) -> list[str]:
    """Search terms for one team: full name, short name, location, abbreviation."""
    return [t for t in [team.name, team.short_name, team.location, team.abbreviation]
            if t and t.strip()]


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
      - At least one term for the HOME team appears in title+subtitle AND
        at least one term for the AWAY team appears in title+subtitle
        (requiring both teams eliminates false positives from channels that
        happen to mention one city/team name for an unrelated reason)
    """
    home_terms = _terms_for_team(game.home_team)
    away_terms = _terms_for_team(game.away_team)
    window = timedelta(minutes=MATCH_WINDOW_MINUTES)
    # Map channel_name → display string (to deduplicate by channel, keep first number seen)
    matched: dict[str, str] = {}

    for prog in programs:
        # 1. Time window check
        delta = abs(prog.start - game.start_time)
        if delta > window:
            continue

        # 2. Both teams must appear — title + subtitle only (description excluded)
        haystack = f"{prog.title} {prog.subtitle}"
        if not (_text_contains_any(haystack, home_terms) and
                _text_contains_any(haystack, away_terms)):
            continue

        if prog.channel_name and prog.channel_name not in matched:
            if prog.channel_number:
                matched[prog.channel_name] = f"{prog.channel_number} - {prog.channel_name}"
            else:
                matched[prog.channel_name] = prog.channel_name

    return sorted(matched.values())
