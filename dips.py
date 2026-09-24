"""Zoekt 'onverklaarde dips': momenten waarop een stalling veel leger was dan normaal,
zonder dat er een evenement in events.csv stond. Dat zijn de gemiste evenementen of storingen.

    python dips.py                 # dips van de afgelopen 14 dagen → data/onverklaarde_dips.csv
    python dips.py --dagen 30
    python dips.py --zoek          # laat Claude (met webzoeken) uitzoeken wat er toen was

'Normaal' = de mediaan van dezelfde weekdag en hetzelfde kwartier over de 4 weken ervoor (zonder regen).
"""
from __future__ import annotations

import argparse

import pandas as pd

import config
from model import laad_snapshots, op_raster


def normaal(df: pd.DataFrame, weer: dict | None = None) -> pd.Series:
    """Mediaan van dezelfde weekdag+tijd in de 4 voorgaande weken (alleen verleden, geen lekken).
    Pas vanaf 2 weken historie: één week is geen 'normaal'. Regenachtige referentiemomenten tellen niet
    mee: dan fietsen er minder mensen en lijkt een gewone dag ten onrechte een dip."""
    stap = pd.Timedelta(config.RESAMPLE)
    lags = []
    for w in (1, 2, 3, 4):
        s = df.groupby("location_code")["fietsen"].shift(int(pd.Timedelta(weeks=w) / stap))
        if weer:
            uur = (df["ts"] - pd.Timedelta(weeks=w)).dt.tz_localize(None).dt.floor("h")
            regen = pd.Series(0.0, index=df.index)
            for g, wg in weer.items():
                m = df["gebied"] == g
                regen[m] = uur[m].map(wg["regen_mm"]).fillna(0).values
            s = s.where(regen < 0.3)
        lags.append(s)
    lags = pd.concat(lags, axis=1)
    return lags.median(axis=1, skipna=True).where(lags.notna().sum(axis=1) >= 2)


def events_per_station() -> pd.DataFrame:
    try:
        ev = pd.read_csv(config.EVENTS_CSV, comment="#")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        ev = pd.DataFrame(columns=["start", "eind", "stations"])
    for c in ("start", "eind"):
        ev[c] = pd.to_datetime(ev[c], errors="coerce")
    ev["stations"] = ev["stations"].astype("object").fillna("").astype(str)
    return ev


def vind_dips(dagen: int = 14) -> pd.DataFrame:
    raw = laad_snapshots()
    df = op_raster(raw).sort_values(["location_code", "ts"]).reset_index(drop=True)
    try:
        from weer import weer
        w = weer(df["ts"].min().date() - pd.Timedelta(days=28), df["ts"].max().date(), sorted(df["gebied"].unique()))
    except Exception:
        w = {}
    df["normaal"] = normaal(df, w)
    cap = df.groupby("location_code")["fietsen"].transform(lambda s: s.quantile(0.95))
    df["tekort"] = df["normaal"] - df["fietsen"]
    df["dip"] = (df["tekort"] > (0.25 * cap).clip(lower=10)) & df["normaal"].notna()
    # kleine onderbrekingen (≤ 30 min) dichten, zodat één evenement niet in stukjes uiteenvalt
    for code, g in df.groupby("location_code"):
        rond = g["dip"].astype(int).rolling(5, center=True, min_periods=1).max().astype(bool)
        d = g["dip"].astype(bool)
        df.loc[g.index, "dip"] = d | (rond & d.shift(2, fill_value=False) & d.shift(-2, fill_value=False))
    df = df[df["ts"] >= df["ts"].max() - pd.Timedelta(days=dagen)]

    ev = events_per_station()
    namen = raw.groupby("location_code")["naam"].last()
    uit = []
    for code, g in df.groupby("location_code"):
        blok = (g["dip"] != g["dip"].shift()).cumsum()
        for _, b in g[g["dip"]].groupby(blok[g["dip"]]):
            if len(b) < 3:  # minder dan 45 minuten: ruis
                continue
            s, e = b["ts"].min().tz_localize(None), b["ts"].max().tz_localize(None)
            st = b["station_code"].iloc[0]
            verklaard = ev[ev["stations"].apply(lambda x: st in x.split(";")) &
                           (ev["start"] - pd.Timedelta(hours=3) <= e) & (ev["eind"] + pd.Timedelta(hours=1) >= s)]
            if len(verklaard):
                continue
            uit.append({"station": st, "locatie": namen.get(code, code), "start": s.strftime("%Y-%m-%d %H:%M"),
                        "eind": e.strftime("%Y-%m-%d %H:%M"), "normaal": int(b["normaal"].mean()),
                        "gemeten": int(b["fietsen"].mean()), "max_tekort": int(b["tekort"].max()), "verklaring": ""})
    return pd.DataFrame(uit)


def zoek_verklaring(rij: dict) -> str:
    import ai
    vraag = (f"Bij de OV-fietsstalling '{rij['locatie']}' (station {rij['station']}) stonden op {rij['start']} "
             f"tot {rij['eind']} veel minder fietsen dan normaal ({rij['gemeten']} i.p.v. ~{rij['normaal']}). "
             "Zoek of er in die plaats of de omgeving (tot ~10 km) toen een evenement, wedstrijd, beurs, "
             "treinstoring of iets anders was dat dit verklaart. Antwoord in één of twee zinnen in het Nederlands, "
             "met de naam van het evenement en een bron. Weet je het niet zeker, zeg dat dan.")
    try:
        return ai.vraag(vraag, max_tokens=400, web_zoeken=True).strip()
    except Exception as e:
        return f"(zoeken mislukt: {e})"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dagen", type=int, default=14)
    ap.add_argument("--zoek", action="store_true")
    a = ap.parse_args()
    dips = vind_dips(a.dagen)
    if dips.empty:
        print("Geen onverklaarde dips gevonden.")
        raise SystemExit
    if a.zoek:
        import ai
        if not ai.beschikbaar():
            raise SystemExit("--zoek heeft ANTHROPIC_API_KEY nodig.")
        dips["verklaring"] = [zoek_verklaring(r) for r in dips.to_dict("records")]
    config.DATA_DIR.mkdir(exist_ok=True)
    dips.to_csv(config.DIPS_CSV, index=False)
    print(dips.to_string(index=False))
    print(f"\n→ {config.DIPS_CSV}. Klopt een verklaring? Zet het evenement dan in events.csv, "
          "zodat het model het leert.")
