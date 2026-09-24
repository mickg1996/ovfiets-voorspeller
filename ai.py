"""Kleine helper rond de Claude API (Anthropic) — optioneel.

Zonder ANTHROPIC_API_KEY werkt de scanner nog steeds, maar dan alleen met gestructureerde
bronnen (Ticketmaster) en zonder het lezen van agenda-pagina's en vergunningen.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

import requests

import config

API = "https://api.anthropic.com/v1"


def beschikbaar() -> bool:
    return bool(os.environ.get(config.ANTHROPIC_KEY_ENV))


def _headers() -> dict:
    return {"x-api-key": os.environ[config.ANTHROPIC_KEY_ENV], "anthropic-version": "2023-06-01",
            "content-type": "application/json"}


@lru_cache(maxsize=1)
def model() -> str:
    """Model uit ANTHROPIC_MODEL, anders het nieuwste Haiku-model (goedkoop, ruim voldoende)."""
    if os.environ.get(config.ANTHROPIC_MODEL_ENV):
        return os.environ[config.ANTHROPIC_MODEL_ENV]
    r = requests.get(f"{API}/models", headers=_headers(), params={"limit": 50}, timeout=30)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", [])]  # nieuwste eerst
    for voorkeur in ("haiku", "sonnet"):
        for i in ids:
            if voorkeur in i:
                return i
    return ids[0]


def vraag(prompt: str, systeem: str = "", max_tokens: int = 2000, web_zoeken: bool = False) -> str:
    body = {"model": model(), "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if systeem:
        body["system"] = systeem
    if web_zoeken:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    r = requests.post(f"{API}/messages", headers=_headers(), json=body, timeout=120)
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


def json_uit(tekst: str):
    """Haalt het eerste JSON-blok uit een antwoord (ook als het in ```json staat)."""
    m = re.search(r"```(?:json)?\s*(.*?)```", tekst, re.S)
    kandidaat = m.group(1) if m else tekst
    start = min([i for i in (kandidaat.find("["), kandidaat.find("{")) if i >= 0], default=-1)
    if start < 0:
        return None
    try:
        return json.loads(kandidaat[start:])
    except json.JSONDecodeError:
        # knip af op de laatste sluitende haak
        for eind in range(len(kandidaat), start, -1):
            try:
                return json.loads(kandidaat[start:eind])
            except json.JSONDecodeError:
                continue
    return None


EXTRACT_SYSTEEM = """Je haalt evenementen uit Nederlandse teksten (agenda-pagina's, evenementenvergunningen).
Geef ALLEEN een JSON-lijst terug. Per evenement:
{"naam": str, "start": "YYYY-MM-DD HH:MM", "eind": "YYYY-MM-DD HH:MM", "locatie": str, "plaats": str,
 "bezoekers": int|null, "type": "concert|festival|sport|beurs|markt|theater|optocht|overig", "zekerheid": "hoog|middel|laag"}
Regels: verzin niets. Onbekende tijd: start 10:00 en eind 23:00 en zekerheid hoogstens "middel".
Meerdaagse evenementen: één item per dag. Bezoekers alleen invullen als het in de tekst staat of
redelijk af te leiden is uit de zaal/het terrein (dan zekerheid "laag"). Negeer evenementen in het verleden
en evenementen met minder dan ~300 bezoekers. Geen evenementen gevonden: geef []."""


def extraheer_events(tekst: str, bron: str, vandaag: str) -> list[dict]:
    prompt = f"Vandaag is {vandaag}. Bron: {bron}.\n\nTekst:\n{tekst[:15000]}"
    antwoord = json_uit(vraag(prompt, systeem=EXTRACT_SYSTEEM))
    return antwoord if isinstance(antwoord, list) else []
