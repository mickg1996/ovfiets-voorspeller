"""Controleert of alles klaarstaat: Python-pakketten, sleutels en de verbinding met elke dienst.

    python check_setup.py          # alles controleren (verstuurt niets)
    python check_setup.py --push   # stuurt ook een testmelding naar je telefoon (ntfy)

Sleutels zet je als omgevingsvariabele, bijvoorbeeld in je terminal:
    export NS_API_KEY=...
of in een bestand .env in deze map (regels NAAM=waarde); dat bestand wordt automatisch gelezen
en staat in .gitignore, zodat het nooit op GitHub belandt.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import warnings

warnings.filterwarnings("ignore", message=".*OpenSSL.*")  # onschuldige melding van de Python op macOS

GROEN, ROOD, GRIJS, RESET = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
if not sys.stdout.isatty():
    GROEN = ROOD = GRIJS = RESET = ""
uitslag = {"ok": 0, "fout": 0, "over": 0}


def ok(tekst):
    uitslag["ok"] += 1
    print(f"  {GROEN}✓{RESET} {tekst}")


def fout(tekst, tip=""):
    uitslag["fout"] += 1
    print(f"  {ROOD}✗{RESET} {tekst}" + (f"\n      → {tip}" if tip else ""))


def over(tekst):
    uitslag["over"] += 1
    print(f"  {GRIJS}–{RESET} {tekst}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    print("\n1. Python-pakketten")
    for mod, pip in [("requests", "requests"), ("pandas", "pandas"), ("numpy", "numpy"), ("sklearn", "scikit-learn"),
                     ("joblib", "joblib"), ("dateutil", "python-dateutil")]:
        try:
            importlib.import_module(mod)
            ok(pip)
        except ImportError:
            fout(pip, "pip install -r requirements.txt")
    try:
        import requests
    except ImportError:
        sys.exit("Installeer eerst de pakketten.")
    import config
    from gebieden import gebied_van, gebieden

    print("\n2. NS API (verplicht)")
    if not os.environ.get(config.NS_KEY_ENV):
        fout("NS_API_KEY ontbreekt", "apiportal.ns.nl → product Ns-App → primary key")
    else:
        try:
            import collect
            locs = collect._get()
            if not locs:
                over("de NS API geeft zonder station_code niets terug; collect.py vraagt dan per station op (werkt ook)")
                locs = collect._get({"station_code": "HT"})
            ok(f"verbinding werkt: {len(locs)} OV-fietslocaties ontvangen")
            per_gebied = {}
            for l in locs:
                g = gebied_van(l.get("lat"), l.get("lng"), l.get("stationCode"))
                if g:
                    per_gebied[g] = per_gebied.get(g, 0) + 1
            ok(f"{sum(per_gebied.values())} stallingen in {len(per_gebied)} van de {len(gebieden())} gebieden")
            missend = [g["naam"] for g in gebieden() if g["naam"] not in per_gebied]
            if missend:
                fout("geen stalling gevonden bij: " + ", ".join(missend),
                     "controleer de coördinaten in stations.csv of vergroot GROOT_STATION_STRAAL_KM in config.py")
            voorbeeld = next((l for l in locs if (l.get("extra") or {}).get("rentalBikes") not in (None, "")), None)
            if voorbeeld:
                ok(f"aantal fietsen staat in extra.rentalBikes (bv. {voorbeeld.get('name')}: {voorbeeld['extra']['rentalBikes']})")
            else:
                fout("geen 'rentalBikes' in de antwoorden gevonden", "stuur mij (Claude) een voorbeeldantwoord, dan pas ik collect.py aan")
        except requests.HTTPError as e:
            fout(f"NS API weigert: {e}", "klopt de key en heb je je geabonneerd op het product Ns-App?")
        except requests.RequestException as e:
            fout(f"NS API niet bereikbaar: {e}")

    print("\n3. Gratis diensten zonder sleutel")
    for naam, url, params in [
        ("Open-Meteo (weer)", config.WEER_FORECAST_URL, {"latitude": 51.69, "longitude": 5.29, "hourly": "precipitation", "forecast_days": 1}),
        ("PDOK (adres → coördinaten)", "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free", {"q": "Brabanthallen", "rows": 1}),
        ("Officiële bekendmakingen (vergunningen)", "https://repository.overheid.nl/sru",
         {"version": "1.2", "operation": "searchRetrieve", "maximumRecords": 1,
          "query": '(c.product-area==officielepublicaties)and(w.publicatienaam=="Gemeenteblad")'}),
    ]:
        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            ok(naam)
        except requests.RequestException as e:
            fout(f"{naam}: niet bereikbaar ({type(e).__name__})", "controleer je internetverbinding of firewall")

    print("\n4. Pushmeldingen (ntfy)")
    topic = os.environ.get(config.NTFY_TOPIC_ENV)
    if not topic:
        over("NTFY_TOPIC niet gezet (optioneel) — installeer de app ntfy en kies een lange, eigen naam")
    elif len(topic) < 12:
        fout(f"NTFY_TOPIC '{topic}' is kort en makkelijk te raden", "kies iets als ovfiets-<naam>-<willekeurige letters>")
    elif a.push:
        try:
            import meldingen
            meldingen.verstuur({"titel": f"Test van je {config.APP_NAAM}", "prio": 3, "tags": ["bike"],
                                "tekst": "Werkt! Hier komen straks je fietsmeldingen binnen."}, droog=False)
            ok("testmelding verstuurd — komt hij binnen op je telefoon?")
        except requests.RequestException as e:
            fout(f"ntfy: {e}")
    else:
        ok(f"NTFY_TOPIC is gezet ({topic[:10]}…); draai met --push om een testmelding te sturen")

    print("\n5. Evenementen-scanner (optioneel)")
    tm = os.environ.get(config.TICKETMASTER_KEY_ENV)
    if not tm:
        over("TICKETMASTER_API_KEY niet gezet — gratis via developer.ticketmaster.com")
    else:
        try:
            r = requests.get("https://app.ticketmaster.com/discovery/v2/events.json",
                             params={"apikey": tm, "latlong": "52.0894,5.1101", "radius": 5, "unit": "km", "size": 1},
                             timeout=20)
            r.raise_for_status()
            ok(f"Ticketmaster werkt ({r.json().get('page', {}).get('totalElements', '?')} evenementen rond Utrecht Centraal)")
        except requests.RequestException as e:
            fout(f"Ticketmaster: {e}")
    if not os.environ.get(config.ANTHROPIC_KEY_ENV):
        over("ANTHROPIC_API_KEY niet gezet — nodig om agenda's en vergunningen te laten lezen (console.anthropic.com)")
    else:
        try:
            import ai
            ok(f"Claude API werkt (model: {ai.model()})")
        except Exception as e:
            fout(f"Claude API: {e}")

    print(f"\n{uitslag['ok']} in orde · {uitslag['fout']} aandachtspunten · {uitslag['over']} optioneel overgeslagen")
    if uitslag["fout"] == 0:
        print("Klaar om te meten: python collect.py --dry-run")


if __name__ == "__main__":
    main()
