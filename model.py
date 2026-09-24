"""Bouwt de trainingstabel, traint per horizon een model en vergelijkt met simpele baselines.

Per horizon (30/60/120 min) twee modellen:
  - een regressiemodel: hoeveel fietsen staan er dan?
  - een klassificatiemodel: hoe groot is de kans dat het krap is (< LAAG_DREMPEL fietsen)?

Kenmerken gaan over *nu* (voorraad, trend, lags) én over het *doeltijdstip* (tijd van de dag,
evenement, weer, zelfde moment vorige week). Juist dat laatste maakt het verschil: om 11:00
voorspellen voor 13:00 vraagt om het weer en de evenementen van 13:00.

Gebruik:
    python model.py              # trainen + evaluatie, model → data/model.joblib
    python model.py --geen-weer  # zonder weer (offline)
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import config
from gebieden import gebied_van, vakantieregio
from kalender import kalender_features

TZ = "Europe/Amsterdam"
STAP = pd.Timedelta(config.RESAMPLE)
_WEER: dict[str, pd.DataFrame] = {}  # laatst geladen weer per gebied (ook verwachting), voor doeltijd-kenmerken
WEER_KOLOMMEN = ["regen_mm", "temp_c", "wind_kmh"]


# ---------- data ----------
def laad_snapshots() -> pd.DataFrame:
    paden = sorted(config.SNAPSHOT_DIR.glob("*.csv"))
    if not paden:
        raise SystemExit("Nog geen snapshots in data/snapshots — draai eerst collect.py (een paar weken).")
    df = pd.concat((pd.read_csv(p) for p in paden), ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts_utc"], utc=True).dt.tz_convert(TZ)
    df["fietsen"] = pd.to_numeric(df["fietsen"], errors="coerce")
    if "gebied" not in df.columns or df["gebied"].isna().any():  # oudere snapshots: gebied afleiden
        pos = df.groupby("location_code")[["lat", "lng", "station_code"]].last()
        per_loc = {c: gebied_van(r.lat, r.lng, r.station_code) for c, r in pos.iterrows()}
        df["gebied"] = df.get("gebied", pd.Series(index=df.index, dtype=object)).fillna(df["location_code"].map(per_loc))
    return df.dropna(subset=["fietsen", "gebied"])


def op_raster(df: pd.DataFrame) -> pd.DataFrame:
    """Per locatie op een vast 15-minutenraster; kleine gaten opvullen, grote gaten leeg laten."""
    uit = []
    for code, g in df.groupby("location_code"):
        s = g.set_index("ts")["fietsen"].sort_index()
        s = s[~s.index.duplicated(keep="last")].resample(config.RESAMPLE).last().ffill(limit=2)
        f = s.to_frame("fietsen")
        f["location_code"] = code
        f["station_code"] = g["station_code"].iloc[-1]
        f["gebied"] = g["gebied"].iloc[-1]
        uit.append(f)
    return pd.concat(uit).reset_index().rename(columns={"index": "ts"})


def _weer_op(df: pd.DataFrame, tijden: pd.Series) -> pd.DataFrame:
    """Weer per rij: gebied van de rij, uur van `tijden`."""
    out = pd.DataFrame(np.nan, index=df.index, columns=WEER_KOLOMMEN)
    uur = tijden.dt.tz_localize(None).dt.floor("h")
    for g, w in _WEER.items():
        m = (df["gebied"] == g).values
        if m.any():
            for c in WEER_KOLOMMEN:
                out.loc[m, c] = uur[m].map(w[c]).values
    return out


# ---------- kenmerken ----------
def _shift(df: pd.DataFrame, delta: pd.Timedelta) -> pd.Series:
    """Waarde van 'fietsen' delta geleden (positief) of vooruit (negatief), per locatie."""
    return df.groupby("location_code")["fietsen"].shift(int(delta / STAP))


def maak_features(df: pd.DataFrame, met_weer: bool = True) -> pd.DataFrame:
    """Kenmerken over het huidige moment. Laadt ook het weer voor doeltijd-kenmerken."""
    global _WEER
    df = df.sort_values(["location_code", "ts"]).reset_index(drop=True)
    t = df["ts"]
    mod = t.dt.hour * 60 + t.dt.minute
    df["tod_sin"] = np.sin(2 * np.pi * mod / 1440)
    df["tod_cos"] = np.cos(2 * np.pi * mod / 1440)
    df["weekdag"] = t.dt.weekday
    df["weekend"] = (df["weekdag"] >= 5).astype(int)
    df["regio"] = df["gebied"].map(vakantieregio)
    kal = kalender_features(pd.DatetimeIndex(t), df["station_code"], df["regio"])
    for c in kal.columns:
        df[c] = kal[c].astype(int).values
    for m in (15, 30, 60, 120):
        df[f"lag_{m}"] = _shift(df, pd.Timedelta(minutes=m))
    df["delta_1u"] = df["fietsen"] - df["lag_60"]
    df["max_vandaag"] = df.groupby([df["location_code"], t.dt.date])["fietsen"].cummax()

    _WEER = {}
    if met_weer:
        from weer import weer
        _WEER = weer(t.min().date(), (t.max() + pd.Timedelta(days=1)).date(), sorted(df["gebied"].unique()))
        if _WEER:
            w = _weer_op(df, t)
            for c in WEER_KOLOMMEN:
                df[c] = w[c].values
    return df


BASIS = ["fietsen", "tod_sin", "tod_cos", "weekdag", "weekend", "feestdag", "carnaval", "schoolvakantie",
         "event_impact", "event_aanloop", "lag_15", "lag_30", "lag_60", "lag_120", "delta_1u", "max_vandaag", "loc"]


def doel_features(df: pd.DataFrame, h: int) -> pd.DataFrame:
    """Kenmerken van het doeltijdstip (nu + h minuten)."""
    td = df["ts"] + pd.Timedelta(minutes=h)
    out = pd.DataFrame(index=df.index)
    mod = td.dt.hour * 60 + td.dt.minute
    out["d_tod_sin"] = np.sin(2 * np.pi * mod / 1440)
    out["d_tod_cos"] = np.cos(2 * np.pi * mod / 1440)
    out["d_weekdag"] = td.dt.weekday
    kal = kalender_features(pd.DatetimeIndex(td), df["station_code"], df["regio"])
    for c in kal.columns:
        out[f"d_{c}"] = kal[c].astype(int).values
    # hetzelfde doelmoment gisteren en vorige week (dat ligt in het verleden, dus bekend)
    out["d_gisteren"] = _shift(df, pd.Timedelta(days=1) - pd.Timedelta(minutes=h))
    out["d_vorige_week"] = _shift(df, pd.Timedelta(days=7) - pd.Timedelta(minutes=h))
    if _WEER:
        w = _weer_op(df, td)
        for c in WEER_KOLOMMEN:
            out[f"d_{c}"] = w[c].values
    return out


def X_voor(df: pd.DataFrame, h: int) -> pd.DataFrame:
    basis = BASIS + [c for c in ("regen_mm", "temp_c", "wind_kmh") if c in df.columns]
    return pd.concat([df[basis], doel_features(df, h)], axis=1)


def doel(df: pd.DataFrame, h: int) -> pd.Series:
    return _shift(df, -pd.Timedelta(minutes=h))


# ---------- trainen ----------
def train(met_weer: bool = True, grens=None, definitief: bool = True, stil: bool = False):
    """Traint op alles vóór `grens` en test op de rest. Met definitief=True daarna opnieuw op alles.

    Geeft (bundel, rapport, df) terug. bundel bevat alles wat voorspel() nodig heeft.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error

    df = maak_features(op_raster(laad_snapshots()), met_weer)
    loc_codes = {c: i for i, c in enumerate(sorted(df["location_code"].unique()))}
    df["loc"] = df["location_code"].map(loc_codes)
    grens = df["ts"].quantile(0.8) if grens is None else grens
    bundel = {"horizons": list(config.HORIZONS_MIN), "kolommen": {}, "reg": {}, "clf": {},
              "loc_codes": loc_codes, "met_weer": bool(_WEER), "drempel": config.LAAG_DREMPEL}
    rapport = []
    for h in config.HORIZONS_MIN:
        X, y = X_voor(df, h), doel(df, h)
        ok = y.notna() & df["fietsen"].notna()
        X, y = X[ok], y[ok]
        tr = (df.loc[ok, "ts"] < grens).values
        cat = [X.columns.get_loc("loc")]
        reg = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, categorical_features=cat, random_state=0)
        reg.fit(X[tr], y[tr])
        krap = (y < config.LAAG_DREMPEL).astype(int)
        clf = None
        if krap[tr].nunique() == 2:
            clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, categorical_features=cat,
                                                 random_state=0)
            clf.fit(X[tr], krap[tr])

        Xt, yt = X[~tr], y[~tr]
        r = {"horizon_min": h, "n_test": len(yt)}
        if len(yt):
            p = np.clip(reg.predict(Xt), 0, None)
            r["MAE_model"] = round(mean_absolute_error(yt, p), 2)
            r["MAE_blijft_gelijk"] = round(mean_absolute_error(yt, Xt["fietsen"]), 2)
            vw = Xt["d_vorige_week"].notna()
            r["MAE_vorige_week"] = round(mean_absolute_error(yt[vw], Xt.loc[vw, "d_vorige_week"]), 2) if vw.any() else None
            if clf is not None:
                echt, voorsp = krap[~tr].values == 1, clf.predict_proba(Xt)[:, 1] >= 0.5
                r["krap_herkend_%"] = round(100 * (echt & voorsp).sum() / max(echt.sum(), 1), 1)
                r["krap_terecht_%"] = round(100 * (echt & voorsp).sum() / max(voorsp.sum(), 1), 1)
        rapport.append(r)
        if definitief:
            reg.fit(X, y)
            if krap.nunique() == 2:
                if clf is None:
                    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
                                                         categorical_features=cat, random_state=0)
                clf.fit(X, krap)
        bundel["kolommen"][h], bundel["reg"][h], bundel["clf"][h] = list(X.columns), reg, clf
    if not stil:
        print(pd.DataFrame(rapport).to_string(index=False))
    return bundel, rapport, df


def voorspel(df: pd.DataFrame, bundel: dict, mask=None) -> pd.DataFrame:
    """Voorspelling (aantal) en krapkans per horizon voor de rijen in mask (standaard: alle).

    df moet de uitvoer van maak_features zijn over een aaneengesloten periode (voor de lags).
    """
    df = df.copy()
    df["loc"] = df["location_code"].map(bundel["loc_codes"])
    idx = df.index if mask is None else df.index[np.asarray(mask)]
    out = pd.DataFrame(index=idx)
    for h in bundel["horizons"]:
        X = X_voor(df, h).reindex(columns=bundel["kolommen"][h]).loc[idx]
        out[f"pred_{h}"] = np.clip(bundel["reg"][h].predict(X), 0, None)
        clf = bundel["clf"][h]
        out[f"kans_{h}"] = (clf.predict_proba(X)[:, 1] if clf is not None
                            else (out[f"pred_{h}"] < bundel["drempel"]).astype(float))
    return out


def laad_model() -> dict:
    import joblib
    if not config.MODEL_PATH.exists():
        raise SystemExit("Nog geen model — draai eerst: python model.py")
    return joblib.load(config.MODEL_PATH)


if __name__ == "__main__":
    import joblib
    ap = argparse.ArgumentParser()
    ap.add_argument("--geen-weer", action="store_true")
    bundel, _, _ = train(met_weer=not ap.parse_args().geen_weer)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundel, config.MODEL_PATH)
    print("→ model opgeslagen:", config.MODEL_PATH)
