"""
Alertle-V2 — XMLTV EPG fetcher.

Fetches and parses an XMLTV-format XML file from a URL.
Returns EPGProgram objects compatible with the EPG matcher.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import httpx

from models import EPGProgram

log = logging.getLogger(__name__)


def _parse_xmltv_time(s: str) -> Optional[datetime]:
    """
    Parse XMLTV timestamp format: "YYYYMMDDHHmmss +HHMM" or "YYYYMMDDHHmmss +HH:MM".
    Returns UTC datetime, or None on failure.
    """
    s = s.strip()
    try:
        # Normalise offset: remove colon if present ("20240601193000 +05:30" -> "+0530")
        if " " in s:
            dt_part, tz_part = s.rsplit(" ", 1)
            tz_part = tz_part.replace(":", "")
            s = f"{dt_part} {tz_part}"
        dt = datetime.strptime(s, "%Y%m%d%H%M%S %z")
        return dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    except Exception:
        try:
            # Fallback: treat as UTC
            dt = datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None


async def fetch_xmltv(url: str) -> list[EPGProgram]:
    """
    Download and parse an XMLTV URL.
    Returns a list of EPGProgram objects.
    """
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            content = r.content
    except Exception as e:
        log.error("XMLTV fetch failed for %s: %s", url, e)
        return []

    return _parse_xmltv_content(content)


def _parse_xmltv_content(content: bytes) -> list[EPGProgram]:
    """Parse raw XMLTV XML bytes into EPGProgram list."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        log.error("XMLTV XML parse error: %s", e)
        return []

    # Build channel id → display name + number maps
    channel_names:   dict[str, str] = {}
    channel_numbers: dict[str, str] = {}
    for ch in root.findall("channel"):
        ch_id   = ch.get("id", "")
        display = ch.findtext("display-name") or ch_id
        lcn     = (ch.findtext("lcn") or "").strip()
        if ch_id:
            channel_names[ch_id] = display
            if lcn:
                channel_numbers[ch_id] = lcn        # explicit <lcn> wins
            elif ch_id.isdigit():
                channel_numbers[ch_id] = ch_id      # numeric id IS the channel number

    programs: list[EPGProgram] = []
    for prog in root.findall("programme"):
        start_str = prog.get("start", "")
        stop_str  = prog.get("stop", "")
        channel_id = prog.get("channel", "")

        start = _parse_xmltv_time(start_str)
        stop  = _parse_xmltv_time(stop_str)

        if start is None or stop is None:
            continue

        channel_name = channel_names.get(channel_id, channel_id)

        title    = prog.findtext("title") or ""
        subtitle = prog.findtext("sub-title") or ""
        desc     = prog.findtext("desc") or ""
        is_live  = prog.find("live") is not None

        programs.append(EPGProgram(
            channel_id=channel_id,
            channel_name=channel_name,
            channel_number=channel_numbers.get(channel_id, ""),
            title=title,
            subtitle=subtitle,
            description=desc,
            start=start,
            stop=stop,
            is_live=is_live,
        ))

    log.debug("XMLTV parsed %d programs", len(programs))
    return programs
