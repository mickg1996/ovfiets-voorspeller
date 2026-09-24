"""Offline tests voor de meldingenlogica (geen model of netwerk nodig).

    python tests/test_meldingen.py
"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
tmp = Path(tempfile.mkdtemp())
os.environ["OVFIETS_DATA_DIR"] = str(tmp)
os.environ["OVFIETS_FIETSMOMENTEN_CSV"] = str(tmp / "fm.csv")
(tmp / "events.csv").write_text("start,eind,naam,stations,impact,bron\n2026-09-18 14:00,2026-09-18 23:00,Festival,AMF,3,test\n")
os.environ["OVFIETS_EVENTS_CSV"] = str(tmp / "events.csv")
(tmp / "fm.csv").write_text("# uitleg\nnaam,stalling,dagen,tijd,meldingen\n"
                            "Festival,AMF001,2026-09-18,16:00,vooraf;update;krap;event\n"
                            "Werk,HT,werkdagen,08:30,vooraf;krap\n")

import pandas as pd  # noqa: E402

import meldingen as ml  # noqa: E402

TZ = "Europe/Amsterdam"
# --- dagen
assert ml.geldt_op("werkdagen", date(2026, 9, 18)) and not ml.geldt_op("werkdagen", date(2026, 9, 19))
assert ml.geldt_op("ma;wo", date(2026, 9, 23)) and not ml.geldt_op("ma;wo", date(2026, 9, 24))
assert ml.geldt_op("2026-09-18", date(2026, 9, 18)) and ml.geldt_op("dagelijks", date(2026, 9, 20))

nu = pd.Timestamp("2026-09-18 10:00", tz=TZ)
k = ml.komende(ml.lees_momenten(), nu, pd.Timedelta(hours=6))
assert [m["naam"] for m, _ in k] == ["Festival"], k


# --- tabel zoals predict.actueel() hem teruggeeft
def tabel(pred, kans, fietsen=150):
    return pd.DataFrame([
        {"ts": nu, "location_code": "AMF001", "station_code": "AMF", "gebied": "Amersfoort Centraal", "naam": "Amersfoort Centraal", "fietsen": fietsen,
         "lat": 52.153, "lng": 5.374, **{f"pred_{h}": p for h, p in zip((30, 60, 120), pred)},
         **{f"kans_{h}": q for h, q in zip((30, 60, 120), kans)}},
        {"ts": nu, "location_code": "HT001", "station_code": "HT", "gebied": "Regio Den Bosch", "naam": "Den Bosch", "fietsen": 100,
         "lat": 51.69, "lng": 5.29, "pred_30": 100, "pred_60": 100, "pred_120": 100,
         "kans_30": 0, "kans_60": 0, "kans_120": 0}])


# --- interpolatie tussen horizons
tm = ml.op_moment(tabel([100, 60, 20], [0, .2, .8]), 90, [30, 60, 120])
assert abs(tm.loc[0, "p"] - 40) < 1e-9 and abs(tm.loc[0, "k"] - 0.5) < 1e-9
assert ml.op_moment(tabel([1, 1, 1], [1, 1, 1]), 200, [30, 60, 120]) is None
assert ml.status(50, 0.6) == "krap" and ml.status(50, 0.25) == "weinig" and ml.status(50, 0.05) == "genoeg"
assert ml.status(30, 0.05, cap=900) == "weinig" and ml.status(30, 0.05, cap=40) == "genoeg"

# --- verloop: event 6 uur van tevoren, dan vooraf, dan (na 2 metingen) een update
state = {}
b = ml.beslis(nu, tabel([150, 150, 150], [0, 0, 0]), [30, 60, 120], None, state)
assert [x["titel"][:9] for x in b] == ["Evenement"], b
t2 = pd.Timestamp("2026-09-18 13:55", tz=TZ)
b = ml.beslis(t2, tabel([150, 120, 90], [0, 0, 0.05]), [30, 60, 120], None, state)
assert len(b) == 1 and b[0]["titel"].startswith("Je fiets om 16:00"), b
t3 = pd.Timestamp("2026-09-18 14:30", tz=TZ)
slecht = tabel([40, 10, 2], [.1, .3, .9], fietsen=60)
b = ml.beslis(t3, slecht, [30, 60, 120], None, state)
assert len(b) == 1 and b[0]["titel"].startswith("Let op: krap"), b      # krap: meteen
assert "Geen stalling in de buurt" in b[0]["tekst"]                       # Den Bosch is te ver weg
assert ml.beslis(t3, slecht, [30, 60, 120], None, state) == []            # niet dubbel

# --- versturen via ntfy (JSON naar de server)
verstuurd = []
ml.requests.post = lambda url, json, timeout: verstuurd.append((url, json)) or type("R", (), {"raise_for_status": lambda s: None})()
os.environ["NTFY_TOPIC"] = "test-onderwerp"
ml.verstuur({"titel": "T", "tekst": "x", "prio": 3, "tags": ["bike"]}, droog=False)
assert verstuurd[0][1]["topic"] == "test-onderwerp" and verstuurd[0][0].startswith("https://ntfy.sh")
print("alle meldingen-tests geslaagd")
