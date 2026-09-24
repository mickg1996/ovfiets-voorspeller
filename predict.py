"""OV-Fietsvoorraad voorspeller — voorspelling voor nu + 30/60/120 min per stalling.

Gebruik:
    python predict.py            # alle stallingen, per gebied
    python predict.py HT         # alleen station 's-Hertogenbosch
    python predict.py utrecht    # alles in een gebied (deel van de naam)
"""
from __future__ import annotations

import sys

import pandas as pd

import config
from model import laad_model, laad_snapshots, maak_features, op_raster, voorspel


def actueel(bundel: dict, nu: pd.Timestamp | None = None):
    """Laatste meting + voorspelling per locatie (optioneel 'alsof het nu <nu> is', voor tests)."""
    raw = laad_snapshots()
    raster = op_raster(raw)
    nu = raster["ts"].max() if nu is None else nu
    raster = raster[(raster["ts"] <= nu) & (raster["ts"] >= nu - pd.Timedelta(days=8))]
    df = maak_features(raster, bundel["met_weer"])
    laatste = df["ts"] == nu
    uit = df.loc[laatste, ["ts", "location_code", "station_code", "gebied", "fietsen"]].join(voorspel(df, bundel, laatste))
    namen = raw.groupby("location_code")["naam"].last()
    pos = raw.groupby("location_code")[["lat", "lng"]].last()
    uit["naam"] = uit["location_code"].map(namen)
    uit["cap"] = uit["location_code"].map(raw.groupby("location_code")["fietsen"].quantile(0.95))  # geschatte capaciteit
    return uit.join(pos, on="location_code").reset_index(drop=True)


def main():
    """Filter optioneel op stationscode(s) of een deel van een gebiedsnaam, bv. HT of utrecht."""
    bundel = laad_model()
    tabel = actueel(bundel)
    if len(sys.argv) > 1:
        zoek = [a.lower() for a in sys.argv[1:]]
        tabel = tabel[tabel["station_code"].str.lower().isin(zoek)
                      | tabel["gebied"].str.lower().apply(lambda g: any(z in g for z in zoek))]
    H = bundel["horizons"]
    print(f"{'stalling':42s} {'nu':>4} " + " ".join(f"{'+' + str(h) + 'm':>11}" for h in H))
    for gebied, g in tabel.sort_values(["gebied", "naam"]).groupby("gebied", sort=False):
        print(f"\n{gebied}")
        for _, r in g.iterrows():
            cellen = [f"{r[f'pred_{h}']:>4.0f} ({r[f'kans_{h}']:>3.0%})" for h in H]
            vlag = "  ⚠ kans op krap" if max(r[f"kans_{h}"] for h in H) >= 0.5 else ""
            print(f"  {str(r['naam'])[:40]:40s} {r['fietsen']:>4.0f} " + " ".join(f"{c:>11}" for c in cellen) + vlag)
    print(f"\n(tussen haakjes: kans dat er minder dan {config.LAAG_DREMPEL} fietsen staan)")


if __name__ == "__main__":
    main()
