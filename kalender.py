"""Kalenderkenmerken: feestdagen, schoolvakanties per regio, carnaval (Zuid) en evenementen."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from dateutil.easter import easter

import config

# Adviesdata schoolvakanties 2026-2027 (rijksoverheid.nl; scholen mogen afwijken). Elk schooljaar aanvullen.
# Noord: o.a. Noord-Holland (Amsterdam, Schiphol) · Midden: o.a. Utrecht, Zuid-Holland, Amersfoort ·
# Zuid: o.a. Noord-Brabant (Den Bosch, Eindhoven), Nijmegen.
SCHOOLVAKANTIES = {
    "Noord": [("2026-10-10", "2026-10-18"), ("2026-12-19", "2027-01-03"), ("2027-02-20", "2027-02-28"),
              ("2027-04-24", "2027-05-02"), ("2027-07-10", "2027-08-22")],
    "Midden": [("2026-10-17", "2026-10-25"), ("2026-12-19", "2027-01-03"), ("2027-02-20", "2027-02-28"),
               ("2027-04-24", "2027-05-02"), ("2027-07-17", "2027-08-29")],
    "Zuid": [("2026-10-17", "2026-10-25"), ("2026-12-19", "2027-01-03"), ("2027-02-13", "2027-02-21"),
             ("2027-04-24", "2027-05-02"), ("2027-07-24", "2027-09-05")],
}
CARNAVAL_REGIO = "Zuid"  # carnaval speelt vooral in het zuiden (Oeteldonk, Eindhoven, Nijmegen)


def feestdagen(jaar: int) -> dict[date, str]:
    p = easter(jaar)
    koningsdag = date(jaar, 4, 27)
    if koningsdag.weekday() == 6:  # zondag → zaterdag ervoor
        koningsdag -= timedelta(days=1)
    d = {
        date(jaar, 1, 1): "nieuwjaarsdag",
        p - timedelta(days=2): "goede vrijdag",
        p: "eerste paasdag",
        p + timedelta(days=1): "tweede paasdag",
        koningsdag: "koningsdag",
        p + timedelta(days=39): "hemelvaart",
        p + timedelta(days=49): "eerste pinksterdag",
        p + timedelta(days=50): "tweede pinksterdag",
        date(jaar, 12, 25): "eerste kerstdag",
        date(jaar, 12, 26): "tweede kerstdag",
    }
    if jaar % 5 == 0:
        d[date(jaar, 5, 5)] = "bevrijdingsdag"
    return d


def carnaval(jaar: int) -> tuple[date, date]:
    """Zaterdag t/m dinsdag vóór Aswoensdag (Pasen − 46 dagen)."""
    aswoensdag = easter(jaar) - timedelta(days=46)
    return aswoensdag - timedelta(days=4), aswoensdag - timedelta(days=1)


def laad_events() -> pd.DataFrame:
    try:
        ev = pd.read_csv(config.EVENTS_CSV, comment="#")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=["start", "eind", "naam", "stations", "impact"])
    for c in ("start", "eind"):
        ev[c] = pd.to_datetime(ev[c], errors="coerce")
    return ev.dropna(subset=["start", "eind"])


def kalender_features(index: pd.DatetimeIndex, station_codes: pd.Series | None = None,
                      regio: pd.Series | None = None) -> pd.DataFrame:
    """Kalenderkolommen per rij. regio = schoolvakantieregio per rij (Noord/Midden/Zuid); standaard Zuid."""
    n = len(index)
    naive = index.tz_localize(None) if getattr(index, "tz", None) is not None else index
    dagen = pd.DatetimeIndex(naive).normalize()
    regio = np.asarray(regio.values if regio is not None else ["Zuid"] * n, dtype=object)

    jaren = sorted({d.year for d in dagen})
    fd = set()
    for j in jaren:
        fd |= set(feestdagen(j))
    out = pd.DataFrame(index=range(n))
    out["feestdag"] = dagen.isin(pd.to_datetime(sorted(fd)))

    vak = np.zeros(n, dtype=bool)
    for r, perioden in SCHOOLVAKANTIES.items():
        m = regio == r
        if m.any():
            for a, b in perioden:
                vak |= m & (dagen >= pd.Timestamp(a)) & (dagen <= pd.Timestamp(b))
    out["schoolvakantie"] = vak

    carn = np.zeros(n, dtype=bool)
    for j in jaren:
        a, b = carnaval(j)
        carn |= (dagen >= pd.Timestamp(a)) & (dagen <= pd.Timestamp(b))
    out["carnaval"] = carn & (regio == CARNAVAL_REGIO)

    # Evenementen: impact (0–3) als het event loopt, en 'event binnen 3 uur' als aanloop.
    impact, aanloop = np.zeros(n, dtype=int), np.zeros(n, dtype=int)
    codes = np.asarray(station_codes.values, dtype=object) if station_codes is not None else None
    for e in laad_events().to_dict("records"):
        geldt = np.ones(n, dtype=bool)
        if codes is not None and isinstance(e.get("stations"), str) and e["stations"].strip():
            geldt = np.isin(codes, [c.strip() for c in e["stations"].split(";")])
        loopt = geldt & (naive >= e["start"]) & (naive <= e["eind"])
        voor = geldt & (naive >= e["start"] - pd.Timedelta(hours=3)) & (naive < e["start"])
        impact = np.where(loopt, np.maximum(impact, int(e.get("impact", 1))), impact)
        aanloop = np.where(voor, 1, aanloop)
    out["event_impact"], out["event_aanloop"] = impact, aanloop
    return out


if __name__ == "__main__":
    for j in (2026, 2027):
        a, b = carnaval(j)
        print(f"carnaval {j}: {a} t/m {b}")
