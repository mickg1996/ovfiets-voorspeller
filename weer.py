"""Uurlijks weer per gebied via Open-Meteo (gratis, geen key).

Regen, kou en harde wind verlagen het fietsgebruik (er blijven meer fietsen staan); mooi weer
verhoogt het. Het model leert zelf hoe sterk dat effect is.

Voor tests en de demo kun je een CSV gebruiken in plaats van de API:
    OVFIETS_WEER_CSV=pad/naar/weer.csv   (kolommen: [gebied,] time, regen_mm, temp_c, wind_kmh)
Zonder kolom 'gebied' geldt hetzelfde weer voor alle gebieden.
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import requests

import config

VARS = "precipitation,temperature_2m,wind_speed_10m"
KOLOMMEN = ["regen_mm", "temp_c", "wind_kmh"]


def _ophalen(url: str, lat: float, lng: float, params: dict) -> pd.DataFrame:
    p = {"latitude": lat, "longitude": lng, "hourly": VARS, "timezone": "Europe/Amsterdam", **params}
    r = requests.get(url, params=p, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["hourly"])
    df["time"] = pd.to_datetime(df["time"])
    return df.rename(columns={"precipitation": "regen_mm", "temperature_2m": "temp_c",
                              "wind_speed_10m": "wind_kmh"}).set_index("time")


def _een_gebied(lat, lng, start: date, eind: date) -> pd.DataFrame:
    delen = []
    try:
        delen.append(_ophalen(config.WEER_ARCHIEF_URL, lat, lng, {"start_date": str(start), "end_date": str(eind)}))
    except requests.RequestException:
        pass
    try:  # recente dagen (archief loopt paar dagen achter) + verwachting
        delen.append(_ophalen(config.WEER_FORECAST_URL, lat, lng, {"past_days": 7, "forecast_days": 3}))
    except requests.RequestException:
        pass
    if not delen:
        return pd.DataFrame(columns=KOLOMMEN)
    df = pd.concat(delen)
    return df[~df.index.duplicated(keep="last")].sort_index()[KOLOMMEN]


def weer(start: date, eind: date, gebieden: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """{gebied: DataFrame per uur (lokale tijd, naive) met regen_mm/temp_c/wind_kmh}. Leeg dict als niets lukt."""
    from gebieden import gebieden as alle
    info = {g["naam"]: g for g in alle()}
    gebieden = gebieden or list(info)
    csv = os.environ.get("OVFIETS_WEER_CSV")
    if csv:
        df = pd.read_csv(csv, parse_dates=["time"])
        if "gebied" in df.columns:
            return {g: d.set_index("time")[KOLOMMEN].sort_index() for g, d in df.groupby("gebied") if g in gebieden}
        w = df.set_index("time")[KOLOMMEN].sort_index()
        return {g: w for g in gebieden}
    uit = {}
    for g in gebieden:
        if g in info:
            w = _een_gebied(info[g]["lat"], info[g]["lng"], start, eind)
            if not w.empty:
                uit[g] = w
    return uit
