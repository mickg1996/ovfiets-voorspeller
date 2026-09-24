"""Stations en gebieden waar we meten (uit stations.csv).

Een gebied is een regio (Regio Den Bosch, straal 25 km rond 's-Hertogenbosch) of één groot station
(straal GROOT_STATION_STRAAL_KM, zodat alle OV-fietslocaties van dat station meedoen maar de buren niet).
Elk gebied hoort bij een schoolvakantieregio (Noord, Midden, Zuid).
"""
from __future__ import annotations

import csv
import math
from functools import lru_cache

import config


def afstand_km(a, b, c, d) -> float:
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


@lru_cache(maxsize=1)
def stations() -> tuple:
    with open(config.STATIONS_CSV, newline="", encoding="utf-8") as f:
        return tuple({**r, "lat": float(r["lat"]), "lng": float(r["lng"])} for r in csv.DictReader(f) if r.get("lat"))


@lru_cache(maxsize=1)
def gebieden() -> tuple:
    """(naam, lat, lng, straal_km, vakantieregio) per gebied; middelpunt = eerste station van dat gebied."""
    uit = {}
    for s in stations():
        if s["gebied"] not in uit:
            straal = config.GEBIED_STRAAL_KM.get(s["gebied"], config.GROOT_STATION_STRAAL_KM)
            uit[s["gebied"]] = {"naam": s["gebied"], "lat": s["lat"], "lng": s["lng"], "straal": straal,
                                "vakantieregio": s["vakantieregio"]}
    return tuple(uit.values())


@lru_cache(maxsize=1)
def codes_per_gebied() -> dict:
    uit = {}
    for s in stations():
        uit.setdefault(s["gebied"], set()).add(s["station_code"])
    return uit


def gebied_van(lat, lng, station_code: str | None = None) -> str | None:
    """Het gebied waar een stalling bij hoort, anders None.

    Regio's (zoals Regio Den Bosch) nemen alles binnen hun straal. Een groot station neemt alleen stallingen
    van dat station zelf: Den Haag HS of Rotterdam Blaak liggen dichtbij, maar zijn andere stations.
    """
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    binnen = []
    for g in gebieden():
        d = afstand_km(lat, lng, g["lat"], g["lng"])
        if d > g["straal"]:
            continue
        is_regio = g["naam"] in config.GEBIED_STRAAL_KM
        if not is_regio and station_code and station_code.strip().upper() not in codes_per_gebied()[g["naam"]]:
            continue
        binnen.append((d, g["naam"]))
    return min(binnen)[1] if binnen else None


def vakantieregio(gebied: str) -> str:
    return next((g["vakantieregio"] for g in gebieden() if g["naam"] == gebied), "Midden")


def stationsnaam(code: str) -> str:
    return next((s["naam"] for s in stations() if s["station_code"] == code), code)
