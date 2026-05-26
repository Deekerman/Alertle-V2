"""
Alertle-V2 — Dispatcharr API client.
Replaces the old raw XMLTV fetcher with proper API calls.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from models import EPGChannel, EPGProgram

log = logging.getLogger(__name__)


class DispatcharrClient:
    def __init__(self, base_url: str, api_key: str, auth_scheme: str = "Token"):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"{auth_scheme} {api_key}"}

    async def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers=self.headers, params=params)
            r.raise_for_status()
            return r.json()

    async def _post(self, path: str, json: dict) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, headers=self.headers, json=json)
            r.raise_for_status()
            return r.json()

    # ── Channels ──────────────────────────────────────────────────────────────

    async def get_channels(self) -> list[EPGChannel]:
        try:
            data = await self._get("/api/channels/")
            channels = []
            for item in (data if isinstance(data, list) else data.get("results", [])):
                channels.append(EPGChannel(
                    id=str(item.get("id", "")),
                    name=item.get("name", item.get("channel_name", "")),
                    channel_number=str(
                        item.get("number") or item.get("channel_number") or
                        item.get("lcn") or item.get("stream_profile_number") or ""
                    ).strip(),
                ))
            return channels
        except Exception as e:
            log.error("Failed to fetch Dispatcharr channels: %s", e)
            return []

    # ── EPG Programs ──────────────────────────────────────────────────────────

    async def get_programs(
        self,
        start: datetime | None = None,
        stop: datetime | None = None,
    ) -> list[EPGProgram]:
        """
        Fetch EPG programs within a time window.
        start/stop are UTC datetimes.
        """
        params: dict[str, str] = {}
        if start:
            params["start"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        if stop:
            params["stop"] = stop.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            data = await self._get("/api/epg/programs/", params)
        except Exception as e:
            log.error("Failed to fetch Dispatcharr EPG programs: %s", e)
            return []

        programs = []
        items = data if isinstance(data, list) else data.get("results", [])
        for item in items:
            try:
                start_dt = datetime.fromisoformat(
                    item["start"].replace("Z", "+00:00")
                ).replace(tzinfo=timezone.utc)
                stop_dt = datetime.fromisoformat(
                    item["stop"].replace("Z", "+00:00")
                ).replace(tzinfo=timezone.utc)
                programs.append(EPGProgram(
                    channel_id=str(item.get("channel_id", item.get("channel", ""))),
                    channel_name=item.get("channel_name", ""),
                    title=item.get("title", ""),
                    subtitle=item.get("sub_title", item.get("subtitle", "")),
                    description=item.get("description", item.get("desc", "")),
                    start=start_dt,
                    stop=stop_dt,
                ))
            except Exception as e:
                log.debug("Skipping malformed EPG program: %s", e)
        return programs

    # ── Recordings ────────────────────────────────────────────────────────────

    async def get_recordings(self) -> list[dict]:
        try:
            data = await self._get("/api/recordings/")
            return data if isinstance(data, list) else data.get("results", [])
        except Exception as e:
            log.error("Failed to fetch recordings: %s", e)
            return []

    async def create_recording(self, program_data: dict) -> dict | None:
        """
        Queue a recording. program_data should match Dispatcharr's recording schema.
        Returns the created recording object, or None on failure.
        """
        try:
            return await self._post("/api/recordings/", program_data)
        except Exception as e:
            log.error("Failed to create recording: %s", e)
            return None

    async def is_already_recording(self, channel_id: str, start: datetime) -> bool:
        """Check if a recording already exists for this channel + start time."""
        recordings = await self.get_recordings()
        start_str = start.strftime("%Y-%m-%dT%H:%M:%S")
        for r in recordings:
            if str(r.get("channel_id", r.get("channel", ""))) == channel_id:
                if start_str in r.get("start", ""):
                    return True
        return False

    # ── Health check ──────────────────────────────────────────────────────────

    async def ping(self) -> tuple[bool, str]:
        try:
            await self._get("/api/channels/")
            return True, ""
        except httpx.HTTPStatusError as e:
            return False, f"HTTP {e.response.status_code} — {e.response.reason_phrase}"
        except Exception as e:
            return False, str(e)


def get_client(cfg: dict) -> DispatcharrClient | None:
    """Build a DispatcharrClient from config dict. Returns None if not configured."""
    d = cfg.get("dispatcharr", {})
    url = d.get("url", "").strip()
    key = d.get("api_key", "").strip()
    if not url or not key:
        return None
    scheme = d.get("auth_scheme", "Token")
    return DispatcharrClient(base_url=url, api_key=key, auth_scheme=scheme)
