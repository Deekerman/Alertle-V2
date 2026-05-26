"""
Alertle-V2 — Config loader
Reads config.yaml and returns typed objects.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any

import yaml

from models import (
    ContentDefaults, Endpoint, GameThumbsEndpointConfig, Subscription
)

CONFIG_PATH = Path(os.environ.get("ALERTLE_CONFIG", "config.yaml"))


def _load_raw() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f) or {}


def _save_raw(data: dict) -> None:
    with CONFIG_PATH.open("w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def load_config() -> dict:
    return _load_raw()


def save_config(data: dict) -> None:
    _save_raw(data)


# ── Typed accessors ───────────────────────────────────────────────────────────

def get_settings(raw: dict | None = None) -> dict:
    raw = raw or _load_raw()
    return raw.get("settings", {})


def get_timezone(raw: dict | None = None) -> str:
    return get_settings(raw).get("timezone", "UTC")


def get_scan_time(raw: dict | None = None) -> str:
    return get_settings(raw).get("scan_time", "06:00")


def get_dispatcharr(raw: dict | None = None) -> dict:
    raw = raw or _load_raw()
    return raw.get("dispatcharr", {})


def get_game_thumbs(raw: dict | None = None) -> dict:
    raw = raw or _load_raw()
    return raw.get("game_thumbs", {})


def get_epg_sources(raw: dict | None = None) -> list[dict]:
    raw = raw or _load_raw()
    return raw.get("epg_sources", [])


def get_channel_overrides(raw: dict | None = None) -> dict:
    """Returns {channel_id: {number: str, enabled: bool}}."""
    raw = raw or _load_raw()
    return raw.get("channel_overrides", {})


def get_notification_defaults(raw: dict | None = None) -> "ContentDefaults":
    raw = raw or _load_raw()
    return _parse_content_defaults(raw.get("notification_defaults", {}))


def _parse_content_defaults(d: dict) -> ContentDefaults:
    return ContentDefaults(
        show_venue=d.get("show_venue", True),
        show_broadcast=d.get("show_broadcast", True),
        show_odds=d.get("show_odds", False),
        show_series=d.get("show_series", True),
        show_week_context=d.get("show_week_context", True),
        show_key_stats=d.get("show_key_stats", False),
    )


def _parse_game_thumbs_config(d: dict) -> GameThumbsEndpointConfig:
    return GameThumbsEndpointConfig(
        type=d.get("type", "thumb"),
        style=d.get("style", 1),
        aspect=d.get("aspect", "16-9"),
        show_logo=d.get("show_logo", True),
        badge=d.get("badge"),
        fallback=d.get("fallback", True),
    )


def get_endpoints(raw: dict | None = None) -> list[Endpoint]:
    raw = raw or _load_raw()
    endpoints = []
    for e in raw.get("endpoints", []):
        ep = Endpoint(
            id=e["id"],
            type=e["type"],
            modes=e.get("modes", ["lead_time"]),
            lead_time_minutes=e.get("lead_time_minutes", 30),
            precision_minutes=e.get("precision_minutes", 0),
            digest_time=e.get("digest_time", "08:00"),
            bundle_window_minutes=e.get("bundle_window_minutes", 10),
            auto_record=e.get("auto_record", False),
            content_defaults=_parse_content_defaults(e.get("content_defaults", {})),
            game_thumbs=_parse_game_thumbs_config(e.get("game_thumbs", {})),
            _raw=e,
        )
        endpoints.append(ep)
    return endpoints


def get_endpoint_by_id(endpoint_id: str, raw: dict | None = None) -> Endpoint | None:
    for ep in get_endpoints(raw):
        if ep.id == endpoint_id:
            return ep
    return None


def get_subscriptions(raw: dict | None = None) -> list[Subscription]:
    raw = raw or _load_raw()
    subs = []
    for s in raw.get("subscriptions", []):
        subs.append(Subscription(
            label=s.get("label", ""),
            espn_sport=s.get("espn_sport", ""),
            espn_league=s.get("espn_league", ""),
            scope=s.get("scope", "league"),
            espn_team_id=s.get("espn_team_id"),
            espn_team_name=s.get("espn_team_name"),
            espn_team_abbrev=s.get("espn_team_abbrev"),
            game_thumbs_league=s.get("game_thumbs_league", s.get("espn_league", "")),
            content_overrides=s.get("content_overrides", {}),
            endpoints=s.get("endpoints", []),
        ))
    return subs
