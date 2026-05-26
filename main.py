"""
Alertle-V2 — Main FastAPI application.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config as cfg_module
from dispatcharr.client import DispatcharrClient, get_client as get_dispatcharr
from epg.xmltv import fetch_xmltv
from espn.client import get_supported_leagues, get_teams
from scanner import daily_scan_loop, run_scan
from scheduler import AlertScheduler

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ── Lifespan ──────────────────────────────────────────────────────────────────

scheduler: AlertScheduler | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    scheduler = AlertScheduler()
    asyncio.create_task(scheduler.run())
    asyncio.create_task(daily_scan_loop(scheduler))
    yield

app = FastAPI(title="Alertle-V2", lifespan=lifespan)

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    raw = cfg_module.load_config()
    return templates.TemplateResponse(request, "settings.html", {"cfg": raw})


@app.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request):
    raw = cfg_module.load_config()
    subs = cfg_module.get_subscriptions(raw)
    endpoints = cfg_module.get_endpoints(raw)
    return templates.TemplateResponse(request, "subscriptions.html", {
        "subscriptions": subs,
        "endpoints": endpoints,
    })


@app.get("/endpoints", response_class=HTMLResponse)
async def endpoints_page(request: Request):
    raw = cfg_module.load_config()
    endpoints = cfg_module.get_endpoints(raw)
    return templates.TemplateResponse(request, "endpoints.html", {"endpoints": endpoints})


# ── ESPN API helpers (used by subscription UI dropdowns) ──────────────────────

@app.get("/api/espn/leagues")
async def api_leagues():
    leagues = get_supported_leagues()
    return [{"sport": l.sport, "league": l.league, "label": l.label} for l in leagues]


@app.get("/api/espn/teams/{sport}/{league}")
async def api_teams(sport: str, league: str):
    teams = await get_teams(sport, league)
    return [
        {"id": t.id, "name": t.name, "abbreviation": t.abbreviation,
         "short_name": t.short_name, "location": t.location}
        for t in teams
    ]


# ── Settings API ──────────────────────────────────────────────────────────────

@app.post("/api/settings")
async def save_settings(request: Request):
    form = await request.form()
    raw = cfg_module.load_config()

    raw.setdefault("settings", {})
    raw["settings"]["timezone"] = form.get("timezone", "UTC")
    raw["settings"]["scan_time"] = form.get("scan_time", "06:00")

    raw.setdefault("dispatcharr", {})
    raw["dispatcharr"]["url"] = form.get("dispatcharr_url", "").strip()
    raw["dispatcharr"]["api_key"] = form.get("dispatcharr_api_key", "").strip()
    raw["dispatcharr"]["auth_scheme"] = form.get("dispatcharr_auth_scheme", "Token")

    raw.setdefault("game_thumbs", {})
    raw["game_thumbs"]["base_url"] = form.get("game_thumbs_url", "https://game-thumbs.swvn.io").strip()
    raw["game_thumbs"]["enabled"] = form.get("game_thumbs_enabled") == "on"

    # Global notification defaults
    raw.setdefault("notification_defaults", {})
    for field in ("show_venue", "show_broadcast", "show_odds",
                  "show_series", "show_week_context", "show_key_stats"):
        raw["notification_defaults"][field] = form.get(field) == "on"

    cfg_module.save_config(raw)
    return JSONResponse({"ok": True})


@app.get("/api/settings/test-dispatcharr")
async def test_dispatcharr(url: str = "", api_key: str = "", auth_scheme: str = ""):
    """
    Test Dispatcharr connectivity.
    Accepts url/api_key/auth_scheme as query params (from the unsaved form) or
    falls back to the saved config if params are empty.
    """
    if url and api_key:
        scheme = auth_scheme or "Token"
        client: DispatcharrClient | None = DispatcharrClient(base_url=url, api_key=api_key, auth_scheme=scheme)
    else:
        raw = cfg_module.load_config()
        client = get_dispatcharr(raw)

    if not client:
        return JSONResponse({"ok": False, "error": "Not configured — fill in URL and API key first"})

    try:
        ok, err = await client.ping()
        return JSONResponse({"ok": ok, "error": err or None})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ── Endpoint API ──────────────────────────────────────────────────────────────

@app.post("/api/endpoints")
async def save_endpoint(request: Request):
    data = await request.json()
    raw = cfg_module.load_config()
    raw.setdefault("endpoints", [])
    # Replace by id — handles both create and edit
    raw["endpoints"] = [e for e in raw["endpoints"] if e.get("id") != data.get("id")]
    raw["endpoints"].append(data)
    cfg_module.save_config(raw)
    return JSONResponse({"ok": True})


@app.delete("/api/endpoints/{endpoint_id}")
async def delete_endpoint(endpoint_id: str):
    raw = cfg_module.load_config()
    raw["endpoints"] = [e for e in raw.get("endpoints", []) if e.get("id") != endpoint_id]
    cfg_module.save_config(raw)
    return JSONResponse({"ok": True})


# ── Subscription API ──────────────────────────────────────────────────────────

@app.post("/api/subscriptions")
async def save_subscription(request: Request):
    data = await request.json()
    raw = cfg_module.load_config()
    raw.setdefault("subscriptions", [])
    # Use label as key — replace if exists
    raw["subscriptions"] = [s for s in raw["subscriptions"] if s.get("label") != data.get("label")]
    raw["subscriptions"].append(data)
    cfg_module.save_config(raw)
    return JSONResponse({"ok": True})


@app.delete("/api/subscriptions/{label}")
async def delete_subscription(label: str):
    raw = cfg_module.load_config()
    raw["subscriptions"] = [s for s in raw.get("subscriptions", []) if s.get("label") != label]
    cfg_module.save_config(raw)
    return JSONResponse({"ok": True})


# ── EPG Sources API ───────────────────────────────────────────────────────────

@app.get("/api/epg-sources")
async def list_epg_sources():
    raw = cfg_module.load_config()
    return cfg_module.get_epg_sources(raw)


@app.post("/api/epg-sources")
async def add_epg_source(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    if not name or not url:
        return JSONResponse({"ok": False, "error": "name and url are required"}, status_code=400)
    raw = cfg_module.load_config()
    raw.setdefault("epg_sources", [])
    # Replace by name
    raw["epg_sources"] = [s for s in raw["epg_sources"] if s.get("name") != name]
    raw["epg_sources"].append({"name": name, "url": url})
    cfg_module.save_config(raw)
    return JSONResponse({"ok": True})


@app.delete("/api/epg-sources/{name}")
async def delete_epg_source(name: str):
    raw = cfg_module.load_config()
    raw["epg_sources"] = [s for s in raw.get("epg_sources", []) if s.get("name") != name]
    cfg_module.save_config(raw)
    return JSONResponse({"ok": True})


@app.get("/api/epg-sources/test/{name}")
async def test_epg_source(name: str):
    raw = cfg_module.load_config()
    source = next((s for s in cfg_module.get_epg_sources(raw) if s.get("name") == name), None)
    if not source:
        return JSONResponse({"ok": False, "error": "Source not found"}, status_code=404)
    try:
        programs = await fetch_xmltv(source["url"])
        return JSONResponse({"ok": True, "count": len(programs)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ── Scanner API ───────────────────────────────────────────────────────────────

@app.post("/api/scan")
async def trigger_scan():
    global scheduler
    if not scheduler:
        return JSONResponse({"ok": False, "error": "Scheduler not ready"})
    try:
        summary = await run_scan(scheduler)
        return JSONResponse({"ok": True, "summary": summary})
    except Exception as e:
        log.exception("Manual scan failed")
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/pending-alerts")
async def pending_alerts():
    if not scheduler:
        return JSONResponse([])
    return JSONResponse(scheduler.list_pending())


@app.post("/api/pending-alerts/{alert_id:path}/test")
async def test_pending_alert(alert_id: str):
    if not scheduler:
        return JSONResponse({"ok": False, "error": "Scheduler not ready"})
    try:
        ok = await scheduler.test_fire(alert_id)
        return JSONResponse({"ok": ok, "error": None if ok else "Alert not found"})
    except Exception as e:
        log.exception("Test alert failed")
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/status")
async def status():
    raw = cfg_module.load_config()
    dispatcharr = get_dispatcharr(raw)
    dispatcharr_ok = False
    if dispatcharr:
        dispatcharr_ok, _ = await dispatcharr.ping()
    return JSONResponse({
        "dispatcharr_connected": dispatcharr_ok,
        "subscriptions": len(cfg_module.get_subscriptions(raw)),
        "endpoints": len(cfg_module.get_endpoints(raw)),
        "epg_sources": len(cfg_module.get_epg_sources(raw)),
        "timezone": cfg_module.get_timezone(raw),
        "scan_time": cfg_module.get_scan_time(raw),
    })
