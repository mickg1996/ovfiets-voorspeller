"""Bouwt het demo-dashboard van de OV-Fietsvoorraad voorspeller (demo/fietsvoorraad-demo.html) op nepdata.

    python demo/bouw_demo.py

Stappen: nepdata maken (5 weken, 16 stallingen in 13 gebieden, met evenementen en weer) → model trainen tot
17 sep → voorspellen voor 17–20 sep (met en zonder evenementen) → scanner- en dips-voorbeelden → alles als
JSON in template.html. Het dashboard is één los HTML-bestand; open het in je browser.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HIER = Path(__file__).resolve().parent
ROOT = HIER.parent
TMP = Path(tempfile.mkdtemp(prefix="ovfiets-demo-"))

# nepdata + omgevingsvariabelen vóór het importeren van config
subprocess.run([sys.executable, str(ROOT / "tests" / "nepdata.py"), "--map", str(TMP)], check=True)
(TMP / "leeg.csv").write_text("start,eind,naam,stations,impact,bron\n")
os.environ.update(OVFIETS_DATA_DIR=str(TMP), OVFIETS_EVENTS_CSV=str(TMP / "events_demo.csv"),
                  OVFIETS_WEER_CSV=str(TMP / "weer_demo.csv"))
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import config  # noqa: E402
import model as M  # noqa: E402
from gebieden import gebieden  # noqa: E402

TZ = "Europe/Amsterdam"
GRENS = pd.Timestamp("2026-09-17 00:00", tz=TZ)
sys.path.insert(0, str(ROOT / "tests"))
from nepdata import LOCS  # noqa: E402

CAP = {code: cap for _, code, _, cap, *_ in LOCS}
H = config.HORIZONS_MIN


def r1(x):
    return round(float(x), 1)


def groep(gebied: str) -> str:
    if gebied in config.GEBIED_STRAAL_KM:   # regio's (Den Bosch, Tilburg) zijn een eigen groep
        return gebied
    if gebied == "Amersfoort Centraal":
        return "Overig"
    return "Grote stations"


def main():
    # 1. model trainen op alles vóór 17 sep (dus zonder de testdagen te zien)
    bundel, rapport, df = M.train(met_weer=True, grens=GRENS, definitief=False, stil=True)
    raw = M.laad_snapshots()
    namen = raw.groupby("location_code")["naam"].last()
    met = M.voorspel(df, bundel, df["ts"] >= GRENS)

    # 2. dezelfde voorspelling zonder evenementen (om het effect van een melding te laten zien)
    config.EVENTS_CSV = TMP / "leeg.csv"
    df0 = M.maak_features(M.op_raster(raw), met_weer=True)
    zonder = M.voorspel(df0, bundel, df0["ts"] >= GRENS)
    config.EVENTS_CSV = TMP / "events_demo.csv"

    volgorde = [g["naam"] for g in gebieden()]
    data = {"drempel": config.LAAG_DREMPEL, "horizons": H, "locs": [], "score": [],
            "gebieden": [{"naam": g["naam"], "regio": g["vakantieregio"], "groep": groep(g["naam"])} for g in gebieden()]}
    for r in rapport:
        data["score"].append({"h": r["horizon_min"], "model": r["MAE_model"], "gelijk": r["MAE_blijft_gelijk"],
                              "week": r["MAE_vorige_week"], "herkend": r.get("krap_herkend_%"),
                              "terecht": r.get("krap_terecht_%")})
    t0 = GRENS - pd.Timedelta(hours=12)
    z = df0.loc[zonder.index, ["location_code", "ts"]].join(zonder)
    zmap = {(r.location_code, r.ts.strftime("%Y-%m-%dT%H:%M")): [r1(getattr(r, f"pred_{h}")) for h in H]
            for r in z.itertuples()}
    for code, g in df[df["ts"] >= t0].groupby("location_code"):
        g = g.sort_values("ts")
        pred, kans, pred0 = {}, {}, {}
        for i, rij in g[g["ts"] >= GRENS].iterrows():
            k = rij["ts"].strftime("%Y-%m-%dT%H:%M")
            pred[k] = [r1(met.loc[i, f"pred_{h}"]) for h in H]
            kans[k] = [round(float(met.loc[i, f"kans_{h}"]), 2) for h in H]
            pred0[k] = zmap.get((code, k), pred[k])
        geb = g["gebied"].iloc[0]
        data["locs"].append({"code": code, "station": g["station_code"].iloc[0], "naam": namen[code], "gebied": geb,
                             "groep": groep(geb), "orde": volgorde.index(geb) if geb in volgorde else 99,
                             "cap": CAP.get(code, int(g["fietsen"].max())),
                             "serie": [[t.strftime("%Y-%m-%dT%H:%M"), int(v)] for t, v in zip(g["ts"], g["fietsen"])],
                             "pred": pred, "kans": kans, "pred0": pred0})
    data["locs"].sort(key=lambda l: (l["orde"], l["naam"]))

    ev = pd.read_csv(TMP / "events_demo.csv")
    data["events"] = [{"start": e.start.replace(" ", "T"), "eind": e.eind.replace(" ", "T"), "naam": e.naam,
                       "stations": e.stations, "impact": int(e.impact)} for e in ev.itertuples() if e.start >= "2026-09-16"]
    w = pd.read_csv(TMP / "weer_demo.csv", parse_dates=["time"])
    w = w[(w["time"] >= t0.tz_localize(None)) & (w["time"] <= pd.Timestamp("2026-09-21 03:00"))]
    data["weer"] = {geb: [[t.strftime("%Y-%m-%dT%H:%M"), float(r), float(tc)]
                          for t, r, tc in zip(d["time"], d["regen_mm"], d["temp_c"])] for geb, d in w.groupby("gebied")}

    # 3. kalender: echte adviesdata (rijksoverheid.nl), carnaval berekend uit de paasdatum
    data["kalender"] = [
        {"kind": "cal", "naam": "Herfstvakantie", "wanneer": "Noord 10–18 okt · Midden en Zuid 17–25 okt 2026", "s": "2026-10-10",
         "tags": ["Amsterdam, Schiphol: Noord", "Utrecht, Randstad-Zuid: Midden", "Brabant, Nijmegen: Zuid"]},
        {"kind": "cal", "naam": "Kerstvakantie", "wanneer": "hele land · za 19 dec 2026 – zo 3 jan 2027", "s": "2026-12-19", "tags": ["alle stations", "minder forensen"]},
        {"kind": "ev", "naam": "Carnaval", "wanneer": "za 6 – di 9 feb 2027 · vooral in het zuiden", "s": "2027-02-06", "tags": ["Den Bosch (Oeteldonk)", "Eindhoven", "Nijmegen"]},
        {"kind": "cal", "naam": "Voorjaarsvakantie", "wanneer": "Zuid 13–21 feb · Noord en Midden 20–28 feb 2027", "s": "2027-02-13", "tags": ["per regio verschillend"]},
    ]

    # 4. scanner: voorbeeldvondsten door de echte verrijking (stations, afstand, impact) + dips op de nepdata
    import dips
    import scan_events as se
    voorbeelden = [
        dict(naam="Publieksbeurs", start="2026-10-10 10:00", eind="2026-10-10 17:00", locatie="Brabanthallen", plaats="'s-Hertogenbosch", lat=51.7003, lng=5.3048, bezoekers=15000, type="beurs", zekerheid="middel", bron="Brabanthallen agenda"),
        dict(naam="Stadionconcert", start="2026-10-24 19:00", eind="2026-10-24 23:30", locatie="De Kuip", plaats="Rotterdam", lat=51.8939, lng=4.5231, bezoekers=45000, type="concert", zekerheid="hoog", bron="Ticketmaster"),
        dict(naam="Vakbeurs", start="2026-11-03 09:00", eind="2026-11-03 18:00", locatie="Jaarbeurs", plaats="Utrecht", lat=52.0875, lng=5.1035, bezoekers=20000, type="beurs", zekerheid="middel", bron="Vergunning Utrecht"),
        dict(naam="Dancefestival", start="2026-10-17 13:00", eind="2026-10-17 23:00", locatie="Autotron", plaats="Rosmalen", lat=51.7127, lng=5.3604, bezoekers=25000, type="festival", zekerheid="hoog", bron="Ticketmaster"),
        dict(naam="Thuiswedstrijd", start="2026-10-23 20:00", eind="2026-10-23 22:00", locatie="De Vliert", plaats="'s-Hertogenbosch", lat=51.6893, lng=5.2808, bezoekers=None, type="sport", zekerheid="hoog", bron="Ticketmaster + Uit Den Bosch"),
        dict(naam="Kerstmarkt", start="2026-12-12 11:00", eind="2026-12-12 20:00", locatie="Kasteel Maurick", plaats="Vught", lat=51.6477, lng=5.3139, bezoekers=4000, type="markt", zekerheid="laag", bron="Vergunning Vught"),
        dict(naam="Klein concert", start="2026-10-30 20:30", eind="2026-10-30 23:00", locatie="Theaterzaal", plaats="'s-Hertogenbosch", lat=51.6890, lng=5.3000, bezoekers=450, type="concert", zekerheid="hoog", bron="Uit Den Bosch"),
    ]
    kand = [se.verrijk(dict(v)) for v in voorbeelden]
    velden = ("id", "naam", "start", "eind", "locatie", "plaats", "station", "stations", "afstand_km", "bezoekers",
              "type", "impact", "zekerheid", "bron")
    config.EVENTS_CSV = TMP / "leeg.csv"
    d = dips.vind_dips(35)
    d = d[d["start"] >= "2026-09-10"].sort_values("max_tekort", ascending=False).head(9).sort_values("start")
    # 'verklaring' zoals dips.py --zoek die zou vinden: hier het evenement uit de nepdata
    evs = pd.read_csv(TMP / "events_demo.csv", parse_dates=["start", "eind"])
    verklaring = []
    for r in d.to_dict("records"):
        s_, e_ = pd.Timestamp(r["start"]), pd.Timestamp(r["eind"])
        hit = evs[(evs["stations"] == r["station"]) & (evs["start"] - pd.Timedelta(hours=3) <= e_) & (evs["eind"] + pd.Timedelta(hours=1) >= s_)]
        if len(hit):
            e = hit.iloc[0]
            naam = e["naam"].replace(" (fictief)", "")
            extra = "" if "fictief" in e["naam"] else " Bezoekers fietsen van het station naar het terrein."
            v = f"{naam} ({e['start']:%d-%m} {e['start']:%H:%M}–{e['eind']:%H:%M})." + (" Verzonnen evenement in de nepdata." if "fictief" in e["naam"] else extra)
        else:
            v = "Geen evenement of storing gevonden. Houd deze plek in de gaten."
        verklaring.append({**r, "verklaring": v})
    data["scan"] = {"kandidaten": [{k: c.get(k) for k in velden} for c in kand], "dips": verklaring}

    html = (HIER / "template.html").read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, separators=(",", ":")))
    uit = HIER / "fietsvoorraad-demo.html"
    uit.write_text(html, encoding="utf-8")
    print(f"→ {uit} ({len(html) // 1024} kB)")
    print(pd.DataFrame(rapport).to_string(index=False))


if __name__ == "__main__":
    main()
