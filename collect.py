"""Haalt de actuele OV-fietsvoorraad op en schrijft één snapshot weg.

Gebruik:
    export NS_API_KEY=...        # je key van apiportal.ns.nl
    python collect.py            # één meting (bedoeld voor cron / GitHub Actions)
    python collect.py --dry-run  # alleen tonen, niet opslaan

We meten in de gebieden uit stations.csv: Regio Den Bosch (25 km) en de grootste stations van Nederland.
Per maand ontstaat data/snapshots/YYYY-MM.csv met één regel per stalling per meting.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore", message=".*OpenSSL.*")  # onschuldige melding van de Python op macOS
import requests  # noqa: E402

import config  # noqa: E402
from gebieden import gebied_van, stations  # noqa: E402

VELDEN = ["ts_utc", "station_code", "location_code", "naam", "fietsen", "open", "lat", "lng", "gebied"]


def _get(params: dict | None = None) -> list[dict]:
    key = os.environ.get(config.NS_KEY_ENV)
    if not key:
        sys.exit(f"Geen API-key: zet de omgevingsvariabele {config.NS_KEY_ENV}.")
    resp = requests.get(
        config.NS_API_BASE + config.NS_OVFIETS_PATH,
        headers={"Ocp-Apim-Subscription-Key": key},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    locaties = []
    for blok in resp.json().get("payload", []):
        locaties.extend(blok.get("locations", []))
    return locaties


def haal_locaties() -> list[dict]:
    """Eerst alles in één call; lukt dat niet, dan per station uit stations.csv."""
    try:
        locs = _get()
        if locs:
            return locs
    except requests.HTTPError as e:
        print(f"Bulk-call faalde ({e}); val terug op per-station.", file=sys.stderr)
    locs = []
    for s in stations():
        try:
            locs.extend(_get({"station_code": s["station_code"]}))
        except requests.HTTPError as e:
            print(f"  {s['station_code']}: {e}", file=sys.stderr)
    return locs


def nette_naam(naam: str) -> str:
    """'UT - OV -fiets - Utrecht Centraal Stationsplein' → 'Utrecht Centraal Stationsplein'."""
    return re.sub(r"^\s*[A-Z]+\s*-\s*OV\s*-?\s*fiets\s*-\s*", "", naam or "", flags=re.I).strip() or naam


def naar_rijen(locs: list[dict], ts: datetime) -> list[dict]:
    rijen, gezien = [], set()
    for loc in locs:
        gebied = gebied_van(loc.get("lat"), loc.get("lng"), loc.get("stationCode"))
        if gebied is None:
            continue
        extra = loc.get("extra") or {}
        code = extra.get("locationCode") or loc.get("name")
        if code in gezien:
            continue
        gezien.add(code)
        try:
            fietsen = int(extra.get("rentalBikes"))
        except (TypeError, ValueError):
            fietsen = ""  # onbekend (API geeft soms leeg/None)
        rijen.append({
            "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "station_code": loc.get("stationCode", ""),
            "location_code": code,
            "naam": nette_naam(loc.get("name", "")),
            "fietsen": fietsen,
            "open": loc.get("open", ""),
            "lat": loc["lat"],
            "lng": loc["lng"],
            "gebied": gebied,
        })
    return rijen


def schrijf(rijen: list[dict], ts: datetime) -> str:
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    pad = config.SNAPSHOT_DIR / f"{ts:%Y-%m}.csv"
    nieuw = not pad.exists()
    with open(pad, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=VELDEN)
        if nieuw:
            w.writeheader()
        w.writerows(rijen)
    return str(pad)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ts = datetime.now(timezone.utc).replace(microsecond=0)
    rijen = naar_rijen(haal_locaties(), ts)
    if not rijen:
        sys.exit("Geen stallingen in de gebieden gevonden — check key/endpoint.")
    huidig = None
    for r in sorted(rijen, key=lambda r: (r["gebied"], r["naam"])):
        if r["gebied"] != huidig:
            huidig = r["gebied"]
            print(f"\n{huidig}")
        print(f"  {r['naam'][:48]:48s} {r['fietsen']!s:>4}")
    print(f"\n{len(rijen)} stallingen in {len({r['gebied'] for r in rijen})} gebieden")
    if not args.dry_run:
        print("→", schrijf(rijen, ts))


if __name__ == "__main__":
    main()
