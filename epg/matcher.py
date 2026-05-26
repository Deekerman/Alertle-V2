"""
Alertle-V2 — EPG matcher.

Given an ESPNGame, finds matching EPG programs by:
1. Time proximity  — program starts within ±45 min of ESPN game time
2. Text confirmation — terms for BOTH teams must appear in subtitle
   (title is typically generic like "NHL Hockey"; subtitle has the teams)
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

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
) -> tuple[list[str], str]:
    """
    Return (channels, description) where:
    - channels: deduplicated sorted list of "{number} - {name}" strings
    - description: first non-empty EPG program description from matched programs

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
    first_description = ""

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

        if not first_description and prog.description:
            first_description = prog.description

    def _sort_key(display: str) -> tuple[int, str]:
        m = re.match(r'^(\d+)', display)
        return (int(m.group(1)), display) if m else (10 ** 9, display)

    return sorted(matched.values(), key=_sort_key), first_description


def find_channels_for_event(
    event_terms: list[str],
    programs: list[EPGProgram],
    event_date: date,
    exclude_terms: list[str] | None = None,
) -> tuple[list[str], str, str, str]:
    """
    Match EPG programs for an event-series sport (golf, F1, UFC, tennis, etc.).
    Full-day window: any program starting on event_date (UTC) whose title or
    subtitle contains at least one of the event_terms AND is flagged <live/>.
    Requires is_live=True to exclude replays and highlight shows.
    Programs matching any exclude_terms are skipped (studio/analysis shows).

    Returns (channels, description, first_title, first_subtitle).
    """
    matched: dict[str, str] = {}
    first_description = ""
    first_title = ""
    first_subtitle = ""

    for prog in programs:
        if prog.start.date() != event_date:
            continue
        if not prog.is_live:
            continue
        haystack = f"{prog.title} {prog.subtitle}"
        if not _text_contains_any(haystack, event_terms):
            continue
        if exclude_terms and _text_contains_any(haystack, exclude_terms):
            continue
        if prog.channel_name and prog.channel_name not in matched:
            if prog.channel_number:
                matched[prog.channel_name] = f"{prog.channel_number} - {prog.channel_name}"
            else:
                matched[prog.channel_name] = prog.channel_name
        if not first_description and prog.description:
            first_description = prog.description
        if not first_title:
            first_title = prog.title
            first_subtitle = prog.subtitle

    def _sort_key(display: str) -> tuple[int, str]:
        m = re.match(r'^(\d+)', display)
        return (int(m.group(1)), display) if m else (10 ** 9, display)

    return sorted(matched.values(), key=_sort_key), first_description, first_title, first_subtitle


def find_event_earliest_start(
    event_terms: list[str],
    programs: list[EPGProgram],
    event_date: date,
    exclude_terms: list[str] | None = None,
) -> "Optional[object]":
    """
    Find the earliest live EPG program start time matching event_terms on event_date.
    Returns a datetime (UTC) or None if no live programs matched.
    """
    starts = []
    for prog in programs:
        if prog.start.date() != event_date:
            continue
        if not prog.is_live:
            continue
        haystack = f"{prog.title} {prog.subtitle}"
        if _text_contains_any(haystack, event_terms):
            if not exclude_terms or not _text_contains_any(haystack, exclude_terms):
                starts.append(prog.start)
    return min(starts) if starts else None


def find_event_time_groups(
    event_terms: list[str],
    programs: list[EPGProgram],
    event_date: date,
    exclude_terms: list[str] | None = None,
    group_window_minutes: int = 60,
) -> list[tuple]:
    """
    Return one entry per distinct broadcast window on event_date:
      (start_time, channels, description, first_title, first_subtitle)

    Live programs whose starts fall within group_window_minutes of the group's
    anchor start are clustered together. This separates early AU/featured-group
    coverage from the main primary-market broadcast later the same day, producing
    one notification per distinct airtime rather than one monolithic alert.
    """
    from datetime import timedelta as _td

    matching = []
    for prog in programs:
        if prog.start.date() != event_date:
            continue
        if not prog.is_live:
            continue
        haystack = f"{prog.title} {prog.subtitle}"
        if not _text_contains_any(haystack, event_terms):
            continue
        if exclude_terms and _text_contains_any(haystack, exclude_terms):
            continue
        matching.append(prog)

    if not matching:
        return []

    matching.sort(key=lambda p: p.start)

    # Cluster into time windows anchored on the first program in each group
    groups: list[tuple] = []  # (anchor_start, [EPGProgram, ...])
    window = _td(minutes=group_window_minutes)
    for prog in matching:
        if groups and (prog.start - groups[-1][0]) <= window:
            groups[-1][1].append(prog)
        else:
            groups.append((prog.start, [prog]))

    def _sort_key(display: str) -> tuple[int, str]:
        m = re.match(r'^(\d+)', display)
        return (int(m.group(1)), display) if m else (10 ** 9, display)

    result = []
    for group_start, group_progs in groups:
        seen: set[str] = set()
        channels: list[str] = []
        description = ""
        first_title = ""
        first_subtitle = ""
        for prog in group_progs:
            if prog.channel_name and prog.channel_name not in seen:
                seen.add(prog.channel_name)
                if prog.channel_number:
                    channels.append(f"{prog.channel_number} - {prog.channel_name}")
                else:
                    channels.append(prog.channel_name)
            if not description and prog.description:
                description = prog.description
            if not first_title:
                first_title = prog.title
                first_subtitle = prog.subtitle
        result.append((
            group_start,
            sorted(channels, key=_sort_key),
            description,
            first_title,
            first_subtitle,
        ))

    return result
