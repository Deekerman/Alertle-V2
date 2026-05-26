"""
Alertle-V2 — Game-Thumbs URL builder.
Uses https://game-thumbs.swvn.io
"""
from __future__ import annotations
import re
from models import ESPNGame, GameThumbsEndpointConfig

DEFAULT_BASE = "https://game-thumbs.swvn.io"


def _to_slug(name: str) -> str:
    """
    Convert a team name to a Game-Thumbs compatible slug.
    Game-Thumbs is flexible (accepts names, cities, abbreviations)
    but lowercase-hyphenated works reliably.
    e.g. "Toronto Maple Leafs" -> "toronto-maple-leafs"
         "TOR" -> "tor"
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


def build_url(
    game: ESPNGame,
    cfg: GameThumbsEndpointConfig,
    base_url: str = DEFAULT_BASE,
    winner_abbrev: str = "",
) -> str:
    """
    Build a Game-Thumbs URL for a matchup.

    For summary notifications, pass winner_abbrev to grey out the loser.
    """
    base = base_url.rstrip("/")
    league = game.league.lower()

    away_slug = _to_slug(game.away_team.name)
    home_slug = _to_slug(game.home_team.name)

    image_type = cfg.type  # thumb | logo | cover

    url = f"{base}/{league}/{away_slug}/{home_slug}/{image_type}"

    # Query params
    params: list[str] = []

    if image_type == "thumb":
        if cfg.style and cfg.style != 1:
            params.append(f"style={cfg.style}")
        if cfg.aspect and cfg.aspect != "4-3":
            params.append(f"aspect={cfg.aspect}")

    if image_type in ("thumb", "logo"):
        if not cfg.show_logo:
            params.append("logo=false")
        if cfg.style and cfg.style != 1 and image_type == "logo":
            params.append(f"style={cfg.style}")

    if cfg.badge:
        params.append(f"badge={cfg.badge}")

    if cfg.fallback:
        params.append("fallback=true")

    if winner_abbrev:
        winner_slug = _to_slug(winner_abbrev)
        params.append(f"winner={winner_slug}")

    if params:
        url += "?" + "&".join(params)

    return url


def build_league_url(
    league: str,
    image_type: str = "thumb",
    base_url: str = DEFAULT_BASE,
) -> str:
    """League-only image (no teams) — used for digest headers etc."""
    return f"{base_url.rstrip('/')}/{league.lower()}/{image_type}?fallback=true"
