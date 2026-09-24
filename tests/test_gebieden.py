"""Offline tests voor gebieden, verzamelen en kalender per regio.

    python tests/test_gebieden.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

import collect  # noqa: E402
from gebieden import gebied_van, gebieden, stationsnaam, vakantieregio  # noqa: E402
from kalender import kalender_features  # noqa: E402

# --- welke stallingen horen erbij
assert gebied_van(51.6906, 5.2936) == "Regio Den Bosch"
assert gebied_van(51.7649, 5.5230) == "Regio Den Bosch"          # Oss, binnen 25 km
assert gebied_van(52.0900, 5.1110) == "Utrecht Centraal"          # stalling naast het station
assert gebied_van(52.3122, 4.9474) is None                        # Amsterdam Bijlmer ArenA: geen top-10
assert gebied_van(52.5049, 6.0916) is None                        # Zwolle
assert gebied_van(52.3389, 4.8730) == "Amsterdam Zuid"            # niet verward met Centraal of Sloterdijk
assert len(gebieden()) == 13 and vakantieregio("Amsterdam Centraal") == "Noord"
assert stationsnaam("LEDN") == "Leiden Centraal"
# grote stations nemen alleen hun eigen stallingen, niet die van buurstations
assert gebied_van(52.0697, 4.3225, "GV") is None                  # Den Haag HS ligt vlak bij Den Haag Centraal
assert gebied_van(52.3375, 4.8900, "RAI") is None                 # station RAI naast Amsterdam Zuid
assert gebied_van(51.9197, 4.4893, "RTB") is None                 # Rotterdam Blaak
assert gebied_van(52.1550, 5.3800, "AMF") == "Amersfoort Centraal"  # andere kant van hetzelfde station
assert gebied_van(51.7004, 5.3183, "HT") == "Regio Den Bosch"     # regio: alles binnen de straal telt
assert gebied_van(51.5733, 4.9929, "TB") == "Tilburg"              # Reeshof: binnen 25 km van Den Bosch, maar Tilburg is dichterbij
assert gebied_van(51.5790, 5.1890, "OT") == "Regio Den Bosch"     # Oisterwijk
import collect  # noqa: E402
assert collect.nette_naam("UT - OV -fiets - Utrecht Centraal Stationsplein") == "Utrecht Centraal Stationsplein"
assert collect.nette_naam("TB-OV-fiets - Tilburg Noord") == "Tilburg Noord"
assert collect.nette_naam("HT - OV-fiets - s-Hertogenbosch") == "s-Hertogenbosch"

locs = [{"name": n, "stationCode": c, "lat": la, "lng": ln, "open": "Yes",
         "extra": {"rentalBikes": "12", "locationCode": c.lower() + "1"}}
        for n, c, la, ln in [("HT", "HT", 51.6906, 5.2936), ("UT", "UT", 52.0895, 5.1100),
                             ("Bijlmer", "ASB", 52.3122, 4.9474), ("Zwolle", "ZL", 52.5049, 6.0916)]]
rijen = collect.naar_rijen(locs, datetime.now(timezone.utc))
assert [(r["station_code"], r["gebied"]) for r in rijen] == [("HT", "Regio Den Bosch"), ("UT", "Utrecht Centraal")]

# --- schoolvakantie verschilt per regio: 12 okt 2026 is herfstvakantie in Noord, niet in Zuid
idx = pd.DatetimeIndex([pd.Timestamp("2026-10-12 08:00"), pd.Timestamp("2026-10-12 08:00")])
k = kalender_features(idx, None, pd.Series(["Noord", "Zuid"]))
assert list(k["schoolvakantie"]) == [True, False]
# carnaval alleen in Zuid
idx = pd.DatetimeIndex([pd.Timestamp("2027-02-08 12:00")] * 2)
k = kalender_features(idx, None, pd.Series(["Zuid", "Midden"]))
assert list(k["carnaval"]) == [True, False]
print("alle gebieden-tests geslaagd")
