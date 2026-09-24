"""OV-Fietsvoorraad voorspeller — pushmeldingen voor je fietsmomenten (fietsmomenten.csv), via ntfy.

ntfy is een gratis, open-source pushdienst: installeer de app 'ntfy' (iOS/Android), abonneer je op
een zelfbedachte, lange onderwerpnaam en zet die naam in de omgevingsvariabele NTFY_TOPIC.
Wie de naam kent kan meelezen, dus maak hem lang en willekeurig (bv. ovfiets-mick-7f3k9q2x).

    python meldingen.py                  # kijkt of er iets te melden is en verstuurt het (na elke meting)
    python meldingen.py --droog          # alleen tonen, niets versturen of onthouden
    python meldingen.py --test           # stuurt een testmelding naar je telefoon
    python meldingen.py --simuleer 2026-09-18T12:00 2026-09-18T16:00   # 'afspelen' op oude data (droog)

Wat je krijgt, per fietsmoment (instelbaar per regel):
    vooraf  ~2 uur van tevoren: verwachte voorraad en kans op krapte
    update  als de verwachting van status verandert (genoeg ↔ weinig ↔ krap)
    krap    zodra krapte dreigt, met een alternatieve stalling in de buurt
    event   een evenement rond je tijd (tot 6 uur van tevoren)
    weer    regen rond je tijd
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore", message=".*OpenSSL.*")  # onschuldige melding van de Python op macOS
import pandas as pd  # noqa: E402
import requests  # noqa: E402

import config
from gebieden import stationsnaam

DAGEN = {"ma": 0, "di": 1, "wo": 2, "do": 3, "vr": 4, "za": 5, "zo": 6}
TZ = "Europe/Amsterdam"


# ---------- fietsmomenten ----------
def lees_momenten() -> list[dict]:
    if not config.FIETSMOMENTEN_CSV.exists():
        return []
    with open(config.FIETSMOMENTEN_CSV, newline="", encoding="utf-8") as f:
        rijen = list(csv.DictReader(r for r in f if r.strip() and not r.lstrip().startswith("#")))
    return [r for r in rijen if r.get("stalling") and r.get("tijd")]


def geldt_op(dagen: str, d) -> bool:
    dagen = dagen.strip().lower()
    if dagen == "dagelijks":
        return True
    if dagen == "werkdagen":
        return d.weekday() < 5
    delen = [x.strip() for x in dagen.split(";") if x.strip()]
    return any(x == d.isoformat() or DAGEN.get(x[:2]) == d.weekday() for x in delen)


def komende(momenten: list[dict], nu: pd.Timestamp, vooruit: timedelta) -> list[tuple[dict, pd.Timestamp]]:
    uit = []
    for m in momenten:
        for dd in (0, 1):
            d = (nu + pd.Timedelta(days=dd)).date()
            if not geldt_op(m.get("dagen", "dagelijks"), d):
                continue
            T = pd.Timestamp(f"{d} {m['tijd']}").tz_localize(TZ)
            if nu < T <= nu + vooruit:
                uit.append((m, T))
    return uit


# ---------- verwachting voor een stalling of station ----------
def km(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def op_moment(tabel: pd.DataFrame, minuten: float, horizons: list[int]) -> pd.DataFrame | None:
    """Verwachting (p) en krapkans (k) over `minuten`, lineair tussen de horizons in (nu = horizon 0)."""
    if minuten > max(horizons) + 15:
        return None
    punten = [0] + sorted(horizons)
    p = {0: tabel["fietsen"].astype(float), **{h: tabel[f"pred_{h}"] for h in horizons}}
    k = {0: (tabel["fietsen"] < config.LAAG_DREMPEL).astype(float), **{h: tabel[f"kans_{h}"] for h in horizons}}
    m = min(max(minuten, 0), punten[-1])
    h2 = next(h for h in punten if h >= m)
    h1 = punten[max(punten.index(h2) - 1, 0)]
    w = 0 if h2 == h1 else (m - h1) / (h2 - h1)
    return tabel.assign(p=(1 - w) * p[h1] + w * p[h2], k=(1 - w) * k[h1] + w * k[h2])


def verwachting(tm: pd.DataFrame, stalling: str) -> dict | None:
    """Voor een stalling, of een heel station (stallingen opgeteld; krap = allemaal krap)."""
    rij = tm[(tm["location_code"] == stalling) | (tm["station_code"] == stalling)]
    if rij.empty:
        return None
    station = rij["station_code"].iloc[0]
    return {"pred": float(rij["p"].sum()), "kans": float(rij["k"].min()),
            "cap": float(rij["cap"].sum()) if "cap" in rij else None,
            "naam": rij["naam"].iloc[0] if len(rij) == 1 else f"station {stationsnaam(station)}",
            "lat": float(rij["lat"].mean()), "lng": float(rij["lng"].mean()),
            "codes": set(rij["location_code"]), "station": rij["station_code"].iloc[0]}


def weinig_grens(cap: float | None) -> float:
    """'Weinig' schaalt mee met de stalling: 9 fietsen is veel in Vught, weinig in Utrecht."""
    return max(8.0, 0.05 * float(cap)) if cap and cap == cap else 8.0


def status(pred: float, kans: float, cap: float | None = None) -> str:
    if kans >= 0.5 or pred < config.LAAG_DREMPEL:
        return "krap"
    if kans >= 0.2 or pred < weinig_grens(cap):
        return "weinig"
    return "genoeg"


def alternatief(tm: pd.DataFrame, v: dict) -> str:
    kand = tm[~tm["location_code"].isin(v["codes"])].copy()
    if not kand.empty:
        kand["km"] = [km(v["lat"], v["lng"], a, b) for a, b in zip(kand["lat"], kand["lng"])]
        grens = kand["cap"].apply(weinig_grens) if "cap" in kand else 8
        kand = kand[(kand["km"] <= config.ALTERNATIEF_KM) & (kand["k"] < 0.2) & (kand["p"] >= grens)]
    if kand.empty:
        return " Geen stalling in de buurt met genoeg fietsen; ga eerder of kies ander vervoer."
    best = kand.assign(zelf=kand["station_code"] == v["station"]).sort_values(["zelf", "km"], ascending=[False, True]).iloc[0]
    return f" Alternatief: {best['naam']} (~{best['p']:.0f} verwacht, {best['km']:.1f} km)."


# ---------- beslissen wat we sturen ----------
def events_rond(station: str, T: pd.Timestamp) -> list[dict]:
    try:
        ev = pd.read_csv(config.EVENTS_CSV, comment="#", parse_dates=["start", "eind"])
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return []
    t = T.tz_localize(None)
    uit = []
    for e in ev.to_dict("records"):
        if station in str(e.get("stations", "")).split(";") and e["start"] - pd.Timedelta(hours=2) <= t <= e["eind"] + pd.Timedelta(hours=1):
            uit.append(e)
    return uit


def beslis(nu: pd.Timestamp, tabel: pd.DataFrame, horizons: list[int], weer: dict | None,
           state: dict) -> list[dict]:
    berichten = []
    for m, T in komende(lees_momenten(), nu, timedelta(hours=config.EVENT_VOORAF_UUR)):
        soorten = {x.strip() for x in (m.get("meldingen") or "vooraf;update;krap;event;weer").split(";")}
        sleutel = f"{m['naam']}|{T.isoformat()}"
        st = state.setdefault(sleutel, {"verzonden": [], "status": None})
        tijd, minuten = T.strftime("%H:%M"), (T - nu).total_seconds() / 60
        wie = m["naam"] or m["stalling"]

        if "event" in soorten and "event" not in st["verzonden"]:
            station = tabel.loc[(tabel["location_code"] == m["stalling"]) | (tabel["station_code"] == m["stalling"]), "station_code"]
            for e in events_rond(station.iloc[0] if len(station) else m["stalling"], T):
                berichten.append({"titel": f"Evenement rond je fietsmoment ({tijd})", "prio": 3, "tags": ["tada"],
                                  "tekst": f"{e['naam']} ({e['start']:%H:%M}–{e['eind']:%H:%M}). De voorraad kan hard dalen; "
                                           f"we houden je op de hoogte."})
                st["verzonden"].append("event")
                break

        rijen = tabel[(tabel["location_code"] == m["stalling"]) | (tabel["station_code"] == m["stalling"])]
        w = (weer or {}).get(rijen["gebied"].iloc[0]) if len(rijen) else None
        if "weer" in soorten and "weer" not in st["verzonden"] and minuten <= 180 and w is not None:
            uur = T.tz_localize(None).floor("h")
            if uur in w.index and w.loc[uur, "regen_mm"] >= 0.5:
                mm = f"{w.loc[uur, 'regen_mm']:.1f}".replace(".", ",")
                berichten.append({"titel": f"Regen rond {tijd}", "prio": 3, "tags": ["umbrella"],
                                  "tekst": f"Verwacht {mm} mm regen rond {tijd}. Neem een regenjas mee."})
                st["verzonden"].append("weer")

        tm = op_moment(tabel, minuten, horizons)
        if tm is None:
            continue
        v = verwachting(tm, m["stalling"])
        if v is None:
            print(f"  onbekende stalling '{m['stalling']}' in fietsmomenten.csv", file=sys.stderr)
            continue
        s, gemeld = status(v["pred"], v["kans"], v["cap"]), st.get("status")
        kans = f"{v['kans']:.0%} kans op krapte"
        alt = alternatief(tm, v) if s != "genoeg" else ""
        krap_tekst = f"{v['naam']}: waarschijnlijk minder dan {config.LAAG_DREMPEL} fietsen ({kans}).{alt}"
        if "vooraf" in soorten and "vooraf" not in st["verzonden"] and minuten <= config.VOORAF_MIN + 15:
            if s == "krap":
                berichten.append({"titel": f"Let op: krap verwacht om {tijd}", "prio": 4, "tags": ["warning"],
                                  "tekst": krap_tekst})
                st["verzonden"].append("krap")
            else:
                berichten.append({"titel": f"Je fiets om {tijd} · {wie}", "prio": 3, "tags": ["bike"],
                                  "tekst": f"{v['naam']}: ~{v['pred']:.0f} fietsen verwacht ({s}, {kans}).{alt}"})
            st["verzonden"].append("vooraf")
            st["status"], st["kandidaat"] = s, None
        elif "krap" in soorten and s == "krap" and "krap" not in st["verzonden"]:
            berichten.append({"titel": f"Let op: krap verwacht om {tijd}", "prio": 4, "tags": ["warning"],
                              "tekst": krap_tekst})
            st["verzonden"].append("krap")
            st["status"], st["kandidaat"] = s, None
        elif "update" in soorten and gemeld and s != gemeld:
            # alleen melden als de nieuwe verwachting twee metingen op rij standhoudt (geen gewiebel)
            if st.get("kandidaat") == s:
                berichten.append({"titel": f"Update voor {tijd}", "prio": 3, "tags": ["bike"],
                                  "tekst": f"{v['naam']}: nu ~{v['pred']:.0f} fietsen verwacht ({s}, eerder {gemeld}).{alt}"})
                st["status"], st["kandidaat"] = s, None
            else:
                st["kandidaat"] = s
        else:
            st["kandidaat"] = None
            if st.get("status") is None:
                st["status"] = s
    return berichten


# ---------- versturen ----------
def verstuur(b: dict, droog: bool):
    topic = os.environ.get(config.NTFY_TOPIC_ENV)
    if droog or not topic:
        print(f"  [{b['titel']}] {b['tekst']}")
        return
    r = requests.post(config.NTFY_SERVER.rstrip("/") + "/", timeout=20, json={
        "topic": topic, "title": b["titel"], "message": b["tekst"], "priority": b["prio"], "tags": b["tags"]})
    r.raise_for_status()


def lees_state() -> dict:
    try:
        return json.loads(config.MELDINGEN_STATUS.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def schrijf_state(state: dict, nu: pd.Timestamp):
    grens = (nu - pd.Timedelta(days=2)).isoformat()
    state = {k: v for k, v in state.items() if k.split("|")[-1] >= grens}
    config.MELDINGEN_STATUS.parent.mkdir(parents=True, exist_ok=True)
    config.MELDINGEN_STATUS.write_text(json.dumps(state, indent=1))


def run(nu=None, droog=False, state=None, bundel=None):
    import model  # model._WEER wordt gevuld door actueel() → maak_features()
    from predict import actueel
    if not lees_momenten():
        print("Geen fietsmomenten in fietsmomenten.csv.")
        return []
    bundel = bundel or model.laad_model()
    tabel = actueel(bundel, nu)
    nu = tabel["ts"].iloc[0]
    eigen_state = state is None
    state = lees_state() if eigen_state else state
    berichten = beslis(nu, tabel, bundel["horizons"], model._WEER, state)
    for b in berichten:
        verstuur(b, droog)
    if eigen_state and not droog:
        schrijf_state(state, nu)
    return berichten


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--droog", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--nu", help="doe alsof het nu dit tijdstip is, bv. 2026-09-18T14:00")
    ap.add_argument("--simuleer", nargs=2, metavar=("VAN", "TOT"))
    a = ap.parse_args()
    if a.test:
        if not os.environ.get(config.NTFY_TOPIC_ENV):
            sys.exit("Zet eerst NTFY_TOPIC (zie uitleg bovenin dit bestand).")
        verstuur({"titel": "Test van je OV-Fietsvoorraad voorspeller", "prio": 3, "tags": ["bike"],
                  "tekst": "Werkt! Hier komen straks je fietsmeldingen binnen."}, droog=False)
        print("Testmelding verstuurd.")
    elif a.simuleer:
        import model
        bundel, state = model.laad_model(), {}
        t = pd.Timestamp(a.simuleer[0]).tz_localize(TZ)
        eind = pd.Timestamp(a.simuleer[1]).tz_localize(TZ)
        while t <= eind:
            print(t.strftime("%a %d %b %H:%M"))
            run(nu=t, droog=True, state=state, bundel=bundel)
            t += pd.Timedelta(config.RESAMPLE)
    else:
        nu = pd.Timestamp(a.nu).tz_localize(TZ) if a.nu else None
        run(nu=nu, droog=a.droog)
