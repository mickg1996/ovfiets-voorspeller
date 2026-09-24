"""Genereert 5 weken synthetische snapshots (17 aug t/m 20 sep 2026) voor alle gebieden, om model.py,
predict.py en het dashboard zonder API-key te testen. Inclusief evenementen en regen per gebied.

    python tests/nepdata.py --map /tmp/nep
    export OVFIETS_DATA_DIR=/tmp/nep OVFIETS_EVENTS_CSV=/tmp/nep/events_demo.csv OVFIETS_WEER_CSV=/tmp/nep/weer_demo.csv
    python model.py

Schrijft snapshots/2026-08.csv, events_demo.csv en weer_demo.csv in die map. Niet in data/snapshots zetten.
Alle aantallen zijn verzonnen. Into the Woods (18–19 sep 2026, Amersfoort) is een echt festival; de tijden en
het effect op de voorraad zijn verzonnen. Events met "(fictief)" bestaan niet.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (station, locatie, naam, capaciteit, ochtendspits (0-1), weekendgebruik (0-1), lat, lng)
LOCS = [
    ("HT", "HT001", "'s-Hertogenbosch - Stationsplein", 180, 0.80, 0.15, 51.6905, 5.2935),
    ("HT", "HT002", "'s-Hertogenbosch - Zuidzijde", 90, 0.90, 0.15, 51.6890, 5.2940),
    ("RS", "RS001", "Rosmalen", 40, 0.85, 0.15, 51.7165, 5.3700),
    ("VG", "VG001", "Vught", 30, 1.00, 0.15, 51.6530, 5.2890),
    ("TB", "TB001", "Tilburg Zuid", 200, 0.80, 0.20, 51.5605, 5.0835),
    ("UT", "UT001", "Utrecht Centraal", 900, 0.85, 0.25, 52.0894, 5.1101),
    ("ASD", "ASD001", "Amsterdam Centraal", 600, 0.75, 0.45, 52.3789, 4.9003),
    ("RTD", "RTD001", "Rotterdam Centraal", 400, 0.80, 0.25, 51.9249, 4.4690),
    ("GVC", "GVC001", "Den Haag Centraal", 350, 0.85, 0.20, 52.0808, 4.3245),
    ("SHL", "SHL001", "Schiphol Airport", 60, 0.40, 0.25, 52.3094, 4.7617),
    ("LEDN", "LEDN001", "Leiden Centraal", 450, 0.95, 0.20, 52.1664, 4.4817),
    ("EHV", "EHV001", "Eindhoven Centraal", 300, 0.85, 0.15, 51.4430, 5.4813),
    ("ASDZ", "ASDZ001", "Amsterdam Zuid", 350, 0.95, 0.10, 52.3389, 4.8730),
    ("ASS", "ASS001", "Amsterdam Sloterdijk", 200, 0.90, 0.10, 52.3889, 4.8378),
    ("NM", "NM001", "Nijmegen", 250, 0.80, 0.20, 51.8433, 5.8528),
    ("AMF", "AMF001", "Amersfoort Centraal", 250, 0.70, 0.20, 52.1530, 5.3740),
]

# start, eind, naam, stations, impact (1-3), hoe sterk het de stalling leegtrekt (0-1)
EVENTS = [
    ("2026-08-21 15:00", "2026-08-21 23:00", "Festival Amersfoort (fictief)", "AMF", 3, 0.9),
    ("2026-08-22 10:00", "2026-08-22 17:00", "Beurs Brabanthallen (fictief)", "HT", 2, 0.55),
    ("2026-08-28 20:00", "2026-08-28 22:00", "Thuiswedstrijd De Vliert (fictief)", "HT", 2, 0.45),
    ("2026-08-28 19:00", "2026-08-28 23:00", "Concert Ahoy (fictief)", "RTD", 2, 0.5),
    ("2026-08-29 12:00", "2026-08-29 22:00", "Evenement Amersfoort (fictief)", "AMF", 3, 0.85),
    ("2026-08-29 10:00", "2026-08-29 18:00", "Beurs Jaarbeurs (fictief)", "UT", 2, 0.45),
    ("2026-09-03 08:00", "2026-09-03 18:00", "Congres RAI (fictief)", "ASDZ", 2, 0.5),
    ("2026-09-04 19:00", "2026-09-04 23:00", "Concert Ahoy (fictief)", "RTD", 2, 0.5),
    ("2026-09-05 10:00", "2026-09-05 17:00", "Beurs Brabanthallen (fictief)", "HT", 2, 0.55),
    ("2026-09-05 10:00", "2026-09-05 18:00", "Beurs Jaarbeurs (fictief)", "UT", 2, 0.45),
    ("2026-09-06 13:00", "2026-09-06 20:00", "Evenement Amersfoort (fictief)", "AMF", 2, 0.6),
    ("2026-09-10 08:00", "2026-09-10 18:00", "Congres RAI (fictief)", "ASDZ", 2, 0.5),
    ("2026-09-11 20:00", "2026-09-11 22:00", "Thuiswedstrijd De Vliert (fictief)", "HT", 2, 0.45),
    ("2026-09-12 12:00", "2026-09-12 22:00", "Festival Amersfoort (fictief)", "AMF", 3, 0.9),
    # testdagen 17–20 sep
    ("2026-09-17 08:00", "2026-09-17 18:00", "Congres RAI (fictief)", "ASDZ", 2, 0.5),
    ("2026-09-18 14:00", "2026-09-18 23:00", "Into the Woods", "AMF", 3, 0.95),
    ("2026-09-18 19:00", "2026-09-18 23:00", "Concert Ahoy (fictief)", "RTD", 2, 0.5),
    ("2026-09-18 20:00", "2026-09-18 22:00", "Thuiswedstrijd De Vliert (fictief)", "HT", 2, 0.45),
    ("2026-09-19 10:00", "2026-09-19 17:00", "Beurs Brabanthallen (fictief)", "HT", 2, 0.55),
    ("2026-09-19 10:00", "2026-09-19 18:00", "Beurs Jaarbeurs (fictief)", "UT", 2, 0.45),
    ("2026-09-19 12:00", "2026-09-19 23:00", "Into the Woods", "AMF", 3, 0.95),
]

# Regenbuien per landsdeel (start, eind, mm per uur). Bij regen pakken minder mensen de fiets.
REGEN = {
    "zuid": [("2026-08-20 07:00", "2026-08-20 11:00", 1.5), ("2026-08-26 14:00", "2026-08-26 19:00", 2.0),
             ("2026-09-01 06:00", "2026-09-01 12:00", 1.0), ("2026-09-03 16:00", "2026-09-03 20:00", 2.5),
             ("2026-09-09 07:00", "2026-09-09 10:00", 2.0), ("2026-09-14 12:00", "2026-09-14 18:00", 1.5),
             ("2026-09-17 15:00", "2026-09-17 19:00", 2.0), ("2026-09-20 08:00", "2026-09-20 12:00", 1.0)],
    "west": [("2026-08-19 06:00", "2026-08-19 10:00", 2.0), ("2026-08-25 15:00", "2026-08-25 20:00", 1.5),
             ("2026-09-02 07:00", "2026-09-02 11:00", 2.5), ("2026-09-08 16:00", "2026-09-08 21:00", 1.0),
             ("2026-09-15 06:00", "2026-09-15 12:00", 1.5), ("2026-09-18 06:00", "2026-09-18 10:00", 2.5),
             ("2026-09-19 14:00", "2026-09-19 17:00", 1.0)],
}
ZUID = {"Regio Den Bosch", "Tilburg", "Eindhoven Centraal", "Nijmegen"}


def event_effect(ts: pd.DatetimeIndex, station: str) -> np.ndarray:
    """Extra 'leegtrekken' door events: 2 uur aanloop, vol effect, 1 uur herstel na afloop."""
    t = ts.tz_localize(None)
    eff = np.zeros(len(ts))
    for start, eind, _, st, _, kracht in EVENTS:
        if st != station:
            continue
        s, e = pd.Timestamp(start), pd.Timestamp(eind)
        uur = (t - s) / pd.Timedelta(hours=1)
        duur = (e - s) / pd.Timedelta(hours=1)
        curve = np.clip(np.minimum((uur + 2) / 2, (duur + 1 - uur) / 1.5), 0, 1)
        eff = np.maximum(eff, kracht * curve)
    return eff


def maak_weer(rng, gebied: str) -> pd.DataFrame:
    uren = pd.date_range("2026-08-17", "2026-09-21 23:00", freq="h")
    dagen = uren.normalize().unique()
    dagoffset = pd.Series(rng.uniform(-3, 4, len(dagen)), index=dagen)
    kust = 0 if gebied in ZUID else -1  # iets koeler aan de kust
    temp = 17 + kust + 5 * np.sin((uren.hour - 9) / 24 * 2 * np.pi) + dagoffset.reindex(uren.normalize()).values
    regen = np.zeros(len(uren))
    for s, e, mm in REGEN["zuid" if gebied in ZUID else "west"]:
        regen[(uren >= pd.Timestamp(s)) & (uren < pd.Timestamp(e))] = mm
    temp = temp - (regen > 0) * 3
    wind = rng.uniform(8, 22, len(uren)) + (regen > 0) * 8 + (0 if gebied in ZUID else 5)
    return pd.DataFrame({"gebied": gebied, "time": uren, "regen_mm": regen.round(1), "temp_c": temp.round(1),
                         "wind_kmh": wind.round(0)})


def weer_factor(ts: pd.DatetimeIndex, weer: pd.DataFrame) -> np.ndarray:
    """Hoeveel van het 'normale' fietsgebruik doorgaat: regen verlaagt, warm droog weer verhoogt."""
    w = weer.set_index("time")
    uur = ts.tz_localize(None).floor("h")
    regen, temp = w["regen_mm"].reindex(uur).fillna(0).values, w["temp_c"].reindex(uur).fillna(17).values
    return (1 - 0.55 * np.minimum(regen / 2, 1)) * np.where((temp >= 21) & (regen == 0), 1.08, 1.0)


def main():
    from gebieden import gebied_van
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True)
    a = ap.parse_args()
    rng = np.random.default_rng(1)
    ts = pd.date_range("2026-08-17", periods=35 * 96, freq="15min", tz="Europe/Amsterdam")
    uur = ts.hour + ts.minute / 60
    werkdag = ts.weekday < 5
    weer_per_gebied, rijen = {}, []
    for st, code, naam, cap, druk, weekend, lat, lng in LOCS:
        gebied = gebied_van(lat, lng, st)
        if gebied not in weer_per_gebied:
            weer_per_gebied[gebied] = maak_weer(rng, gebied)
        wf = weer_factor(ts, weer_per_gebied[gebied])
        # werkdag: ochtendspits trekt fietsen weg, na 17u komen ze terug; weekend: rustiger middagpiek
        leeg = np.where(werkdag, np.exp(-((uur - 9) ** 2) / 4) * druk + np.exp(-((uur - 13) ** 2) / 10) * 0.3,
                        0.1 + weekend * np.exp(-((uur - 14) ** 2) / 8)) * wf
        leeg = np.clip(leeg + event_effect(ts, st), 0, 1)
        n = cap * (1 - leeg) + rng.normal(0, cap * 0.035, len(ts))
        rijen.append(pd.DataFrame({"ts_utc": ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "station_code": st, "location_code": code, "naam": naam,
                                   "fietsen": np.clip(n, 0, cap).round().astype(int), "open": "Yes",
                                   "lat": lat, "lng": lng, "gebied": gebied}))
    uit = Path(a.map)
    (uit / "snapshots").mkdir(parents=True, exist_ok=True)
    pd.concat(rijen).to_csv(uit / "snapshots" / "2026-08.csv", index=False)
    pd.DataFrame([e[:5] + ("nepdata",) for e in EVENTS],
                 columns=["start", "eind", "naam", "stations", "impact", "bron"]).to_csv(uit / "events_demo.csv", index=False)
    pd.concat(weer_per_gebied.values()).to_csv(uit / "weer_demo.csv", index=False)
    print(f"nepdata → {uit} ({len(LOCS)} stallingen in {len(weer_per_gebied)} gebieden)")


if __name__ == "__main__":
    main()
