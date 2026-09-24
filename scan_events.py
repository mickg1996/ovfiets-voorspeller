"""Evenementen-scanner: zoekt automatisch evenementen rond de stations en schat de impact.

    python scan_events.py            # scannen; nieuwe vondsten → data/event_kandidaten.csv
    python scan_events.py --auto     # idem, en zekere vondsten meteen in events.csv (voor de dagelijkse run)
    python scan_events.py --review   # openstaande vondsten één voor één goedkeuren → events.csv
    python scan_events.py --lijst    # openstaande vondsten tonen

Bronnen: Ticketmaster (API-key), evenementenvergunningen uit het Gemeenteblad en agenda-pagina's
(die laatste twee worden door Claude gelezen; ANTHROPIC_API_KEY). Wat ontbreekt wordt overgeslagen.

Impact-inschatting (0–3) is een startwaarde: het model leert uit de metingen hoe zwaar een event echt telt.
Afstand telt mee: vlak bij het station lopen mensen, op 1–12 km pakken ze juist de OV-fiets.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import math
import re
import sys
from datetime import datetime
from functools import lru_cache

import config

VELDEN = ["id", "start", "eind", "naam", "locatie", "plaats", "lat", "lng", "station", "stations", "afstand_km",
          "bezoekers", "type", "impact", "zekerheid", "bron", "url", "status", "gevonden_op"]
GROTE_ZALEN = {"brabanthallen": 2, "autotron": 2, "de vliert": 2, "theater aan de parade": 1,
               "willem ii": 1, "mezz": 1, "tramkade": 1}
ZEKERHEID = {"laag": 0, "middel": 1, "hoog": 2}


def afstand_km(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


@lru_cache(maxsize=1)
def stations() -> tuple:
    with open(config.STATIONS_CSV, newline="", encoding="utf-8") as f:
        return tuple({**r, "lat": float(r["lat"]), "lng": float(r["lng"])} for r in csv.DictReader(f) if r.get("lat"))


def dichtstbij(lat, lng):
    best = min(stations(), key=lambda s: afstand_km(lat, lng, s["lat"], s["lng"]))
    return best["station_code"], round(afstand_km(lat, lng, best["lat"], best["lng"]), 1)


def stations_bij(lat, lng) -> list[str]:
    """Alle stations waar bezoekers realistisch vandaan fietsen: binnen 2 km, of hooguit 1 km verder
    dan het dichtstbijzijnde station (max. 3). Een event tussen twee stations raakt ze allebei."""
    afst = sorted((afstand_km(lat, lng, s["lat"], s["lng"]), s["station_code"]) for s in stations())
    grens = max(2.0, afst[0][0] + 1.0)
    return [c for d, c in afst if d <= grens][:3]


def schat_impact(ev: dict) -> int:
    """1 = merkbaar, 2 = flink, 3 = fietsen gaan zeker op. 0 = waarschijnlijk geen effect."""
    b = ev.get("bezoekers")
    try:
        b = int(float(b)) if b not in (None, "") else None
    except (TypeError, ValueError):
        b = None
    if b is not None:
        score = 0 if b < 1000 else 1 if b < 5000 else 2 if b < 20000 else 3
    else:
        naam = f"{ev.get('locatie', '')} {ev.get('naam', '')}".lower()
        score = max([v for k, v in GROTE_ZALEN.items() if k in naam], default=1)
        if ev.get("type") in ("festival", "optocht"):
            score = max(score, 2)
    d = ev.get("afstand_km")
    if d not in (None, ""):
        d = float(d)
        if d < 0.8:
            score -= 1   # op loopafstand: weinig OV-fietsgebruik
        elif d > 12:
            score -= 2   # te ver: auto/pendelbus
    return max(0, min(3, score))


def _norm(naam: str) -> str:
    naam = re.sub(r"\b(20\d\d|live|tour|concert|in|de|het|the)\b", " ", naam.lower())
    return re.sub(r"[^a-z0-9]", "", naam)


def sleutel(ev: dict) -> str:
    return hashlib.md5(f"{_norm(ev.get('naam', ''))[:40]}|{str(ev.get('start', ''))[:10]}".encode()).hexdigest()[:10]


def zelfde_event(a: dict, b: dict) -> bool:
    """Dezelfde dag én (bijna dezelfde naam, of zelfde locatie en starttijd)."""
    if not a.get("start") or str(a["start"])[:10] != str(b.get("start", ""))[:10]:
        return False
    na, nb = _norm(a.get("naam", "")), _norm(b.get("naam", ""))
    if na and nb and (na in nb or nb in na or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.7):
        return True
    return (str(a.get("start"))[:13] == str(b.get("start"))[:13]
            and _norm(a.get("locatie", "")) and _norm(a.get("locatie", "")) == _norm(b.get("locatie", "")))


def lees(pad) -> list[dict]:
    if not pad.exists():
        return []
    with open(pad, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#")))


def schrijf_kandidaten(rijen: list[dict]):
    config.KANDIDATEN_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(config.KANDIDATEN_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=VELDEN, extrasaction="ignore")
        w.writeheader()
        w.writerows(rijen)


def naar_events_csv(rijen: list[dict]):
    with open(config.EVENTS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rijen:
            w.writerow([r["start"], r["eind"], r["naam"], r.get("stations") or r["station"], r["impact"], r["bron"]])


def verrijk(ev: dict) -> dict:
    from bronnen import geocode
    if ev.get("lat") in (None, "") and (ev.get("locatie") or ev.get("plaats")):
        pos = geocode(f"{ev.get('locatie', '')} {ev.get('plaats', '')}".strip())
        if pos:
            ev["lat"], ev["lng"] = pos
    if ev.get("lat") not in (None, ""):
        lat, lng = float(ev["lat"]), float(ev["lng"])
        ev["station"], ev["afstand_km"] = dichtstbij(lat, lng)
        ev["stations"] = ";".join(stations_bij(lat, lng))
    ev["impact"] = schat_impact(ev)
    ev["id"] = sleutel(ev)
    return ev


def samenvoegen(oud: dict, nieuw: dict):
    """Vul ontbrekende gegevens aan en houd de zekerste bron."""
    for k in ("bezoekers", "lat", "lng", "station", "stations", "afstand_km", "eind", "locatie"):
        if oud.get(k) in (None, "") and nieuw.get(k) not in (None, ""):
            oud[k] = nieuw[k]
    if ZEKERHEID.get(nieuw.get("zekerheid"), 0) > ZEKERHEID.get(oud.get("zekerheid"), 0):
        oud["zekerheid"], oud["bron"], oud["url"] = nieuw["zekerheid"], nieuw["bron"], nieuw.get("url", "")
    if " + " not in oud.get("bron", "") and nieuw.get("bron") and nieuw["bron"] != oud.get("bron"):
        oud["bron"] = f"{oud['bron']} + {nieuw['bron']}"
    oud["impact"] = schat_impact(oud)


def scan(auto: bool = False):
    import bronnen
    ruw = []
    for bron in (bronnen.ticketmaster, bronnen.vergunningen, bronnen.agendas):
        try:
            ruw += bron()
        except Exception as e:  # één kapotte bron mag de rest niet tegenhouden
            print(f"{bron.__name__} faalde: {e}", file=sys.stderr)
    bestaand = lees(config.KANDIDATEN_CSV)
    in_events = lees(config.EVENTS_CSV)
    nu = datetime.now().strftime("%Y-%m-%d %H:%M")
    nieuw, dubbel = [], 0
    for ev in ruw:
        ev = verrijk(ev)
        if ev.get("start") and ev["start"] < nu[:10]:
            continue
        match = next((o for o in bestaand + nieuw if o["id"] == ev["id"] or zelfde_event(o, ev)), None)
        if match is not None:
            if match.get("status") == "nieuw" or match in nieuw:
                samenvoegen(match, ev)
            dubbel += 1
            continue
        if any(zelfde_event(e, ev) for e in in_events):
            dubbel += 1
            continue
        ev["status"] = "nieuw" if ev["impact"] > 0 or not ev.get("start") else "genegeerd"
        ev["gevonden_op"] = nu
        nieuw.append(ev)

    auto_goed = []
    if auto:  # zeker genoeg om zonder review toe te voegen
        for ev in nieuw:
            if (ev["status"] == "nieuw" and ev.get("start") and ev.get("stations")
                    and ev.get("zekerheid") == "hoog" and int(ev["impact"]) >= 1):
                ev["status"] = "auto"
                auto_goed.append(ev)
        naar_events_csv(auto_goed)
    schrijf_kandidaten(bestaand + nieuw)
    open_ = [e for e in nieuw if e["status"] == "nieuw"]
    print(f"\n{len(ruw)} gevonden · {len(nieuw)} nieuw · {dubbel} al bekend · "
          f"{len(auto_goed)} automatisch toegevoegd · {len(open_)} wachten op review")
    toon(auto_goed + open_)


def toon(rijen):
    for e in sorted(rijen, key=lambda r: r.get("start") or "9"):
        imp = "●" * int(e.get("impact") or 0) or "·"
        waar = f"{e.get('stations') or e.get('station') or '?'} {e.get('afstand_km', '?')} km"
        tag = " (auto)" if e.get("status") == "auto" else ""
        print(f"  {e.get('start') or '(datum onbekend)':16s} {imp:3s} {e['naam'][:44]:44s} {waar:16s} [{e['bron']}]{tag}")


def review():
    rijen = lees(config.KANDIDATEN_CSV)
    open_ = [r for r in rijen if r["status"] == "nieuw"]
    if not open_:
        print("Niets te beoordelen.")
        return
    goed = []
    print("Per vondst: [j] toevoegen, [n] afwijzen, [0-3] toevoegen met die impact, [enter] later, [q] stoppen\n")
    for r in sorted(open_, key=lambda r: r.get("start") or "9"):
        print(f"{r['naam']}\n  {r['start']} – {r['eind']} · {r['locatie']} {r['plaats']}\n"
              f"  stations: {r['stations'] or r['station']} ({r['afstand_km']} km) · bezoekers: {r['bezoekers'] or '?'} · "
              f"impact-schatting: {r['impact']} · {r['bron']}\n  {r['url']}")
        k = input("  > ").strip().lower()
        if k == "q":
            break
        if k in ("j", "0", "1", "2", "3"):
            if not (r["start"] and (r["stations"] or r["station"])):
                print("  Datum of locatie onbekend — vul hem zelf in events.csv in.")
                continue
            if k != "j":
                r["impact"] = k
            r["status"] = "goedgekeurd"
            goed.append(r)
        elif k == "n":
            r["status"] = "afgewezen"
    schrijf_kandidaten(rijen)
    naar_events_csv(goed)
    if goed:
        print(f"{len(goed)} toegevoegd aan {config.EVENTS_CSV.name}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--lijst", action="store_true")
    ap.add_argument("--auto", action="store_true")
    a = ap.parse_args()
    if a.review:
        review()
    elif a.lijst:
        toon([r for r in lees(config.KANDIDATEN_CSV) if r["status"] == "nieuw"])
    else:
        scan(auto=a.auto)
