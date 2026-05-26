"""
Alertle-V2 — Dispatcharr API client.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from models import EPGChannel, EPGProgram

log = logging.getLogger(__name__)


def _format_channel_number(raw) -> str:
    """Format a channel number value (may be float like 101.0) to a clean string."""
    if not raw and raw != 0:
        return ""
    try:
        f = float(raw)
        return str(int(f)) if f == int(f) else str(f)
    except (ValueError, TypeError):
        return str(raw).strip()


class DispatcharrClient:
    def __init__(self, base_url: str, api_key: str, auth_scheme: str = "X-API-Key"):
        self.base_url = base_url.rstrip("/")
        # Dispatcharr primary auth: X-API-Key header
        # Also supports: Authorization: ApiKey <key>
        if auth_scheme == "X-API-Key":
            self.headers = {"X-API-Key": api_key}
        else:
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
        channels = []
        path: str | None = "/api/channels/channels/"
        params: dict = {"page_size": 10000}
        while path:
            try:
                data = await self._get(path, params)
            except Exception as e:
                log.error("Failed to fetch Dispatcharr channels: %s", e)
                break
            items = data.get("results", data) if isinstance(data, dict) else data
            for item in items:
                raw_num = item.get("channel_number") or ""
                channels.append(EPGChannel(
                    id=str(item.get("id", "")),
                    name=item.get("name", ""),
                    channel_number=_format_channel_number(raw_num),
                ))
            # Follow pagination via the next URL
            next_url: str | None = data.get("next") if isinstance(data, dict) else None
            if next_url:
                from urllib.parse import urlparse
                parsed = urlparse(next_url)
                path = parsed.path + ("?" + parsed.query if parsed.query else "")
                params = {}
            else:
                path = None
        return channels

    # ── EPG Programs ──────────────────────────────────────────────────────────

    async def get_programs(
        self,
        start: datetime | None = None,
        stop: datetime | None = None,
    ) -> list[EPGProgram]:
        """Fetch EPG programs within a time window (UTC datetimes)."""
        params: dict[str, str] = {}
        if start:
            params["start_time"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        if stop:
            params["end_time"] = stop.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            data = await self._get("/api/epg/programs/", params)
        except Exception as e:
            log.error("Failed to fetch Dispatcharr EPG programs: %s", e)
            return []

        programs = []
        items = data if isinstance(data, list) else data.get("results", [])
        for item in items:
            try:
                # Dispatcharr uses start_time/end_time; fall back to start/stop for compat
                start_str = item.get("start_time") or item.get("start", "")
                stop_str  = item.get("end_time")   or item.get("stop", "")
                start_dt = datetime.fromisoformat(
                    start_str.replace("Z", "+00:00")
                ).replace(tzinfo=timezone.utc)
                stop_dt = datetime.fromisoformat(
                    stop_str.replace("Z", "+00:00")
                ).replace(tzinfo=timezone.utc)
                # Dispatcharr links programs to channels via tvg_id
                channel_id = str(
                    item.get("tvg_id") or item.get("channel_id") or item.get("channel") or ""
                )
                programs.append(EPGProgram(
                    channel_id=channel_id,
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
        try:
            return await self._post("/api/recordings/", program_data)
        except Exception as e:
            log.error("Failed to create recording: %s", e)
            return None

    async def is_already_recording(self, channel_id: str, start: datetime) -> bool:
        recordings = await self.get_recordings()
        start_str = start.strftime("%Y-%m-%dT%H:%M:%S")
        for r in recordings:
            if str(r.get("channel_id", r.get("channel", ""))) == channel_id:
                if start_str in r.get("start_time", r.get("start", "")):
                    return True
        return False

    # ── Output profiles ───────────────────────────────────────────────────────

    async def get_output_profiles(self) -> list[dict]:
        """List Dispatcharr channel profiles (used in /output/epg/{name}/)."""
        try:
            data = await self._get("/api/channels/profiles/")
            items = data if isinstance(data, list) else data.get("results", [])
            return [{"id": p.get("id"), "name": p.get("name", "")} for p in items if p.get("name")]
        except Exception as e:
            log.warning("Failed to fetch Dispatcharr channel profiles: %s", e)
            return []

    # ── Health check ──────────────────────────────────────────────────────────

    async def ping(self) -> tuple[bool, str]:
        try:
            await self._get("/api/channels/channels/")
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
    scheme = d.get("auth_scheme", "X-API-Key")
    return DispatcharrClient(base_url=url, api_key=key, auth_scheme=scheme)
