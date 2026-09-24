# OV-Fietsvoorraad voorspeller

Voorspelt met AI en slimme algoritmes hoeveel OV-fietsen er over 30, 60 en 120 minuten staan en hoe groot de kans is dat het krap wordt (minder dan 3 fietsen). Voor de 10 grootste stations van Nederland, Regio Den Bosch, Tilburg en Amersfoort. Het model kijkt naar tijd van de dag, weekdag, feestdagen, schoolvakanties (per regio), carnaval, weer en evenementen. Evenementen zoekt het systeem grotendeels zelf. Heb je op een vast moment een fiets nodig, dan krijg je pushmeldingen op je telefoon.

## Morgen: de API's koppelen (checklist)

1. `pip install -r requirements.txt`
2. Kopieer `.env.voorbeeld` naar `.env` en vul in wat je hebt. Minimaal `NS_API_KEY` (apiportal.ns.nl → product *Ns-App* → primary key). Optioneel: `NTFY_TOPIC`, `TICKETMASTER_API_KEY` (developer.ticketmaster.com), `ANTHROPIC_API_KEY` (console.anthropic.com). `.env` staat in `.gitignore`.
3. `python check_setup.py` — controleert pakketten, sleutels en elke verbinding, en vertelt wat er nog mist.
4. `python collect.py --dry-run` — lijst met stallingen per gebied en hun aantallen.
5. Installeer de app ntfy, abonneer je op je `NTFY_TOPIC` en draai `python check_setup.py --push`.
6. Zet de meting aan via GitHub (zie hieronder). Vanaf dan loopt de klok: na 3–4 weken kun je trainen.

## Welke stations

`stations.csv` bepaalt waar we meten. Elk station hoort bij een *gebied* en een *schoolvakantieregio*:

| Gebied | Wat telt mee |
|---|---|
| Regio Den Bosch | alle stallingen binnen 25 km van 's-Hertogenbosch |
| 10 grootste stations (NS, in- en uitstappers 2022): Utrecht Centraal, Amsterdam Centraal, Rotterdam Centraal, Den Haag Centraal, Schiphol Airport, Leiden Centraal, Eindhoven Centraal, Amsterdam Zuid, Amsterdam Sloterdijk, Nijmegen | alle stallingen binnen 1,5 km van het station |
| Tilburg | eigen gebied binnen de regio: stallingen binnen 7 km van Tilburg (Zuid, Noord, Reeshof, Universiteit) |
| Amersfoort Centraal | extra, o.a. voor Into the Woods |

Een station toevoegen = één regel in `stations.csv`. Straal per gebied staat in `config.py`.

## Hoe het in elkaar zit

```
 NS API ──► collect.py ──► data/snapshots/*.csv ──► model.py ──► data/model.joblib
 (elke 15 min)                     │                  ▲    ▲
                                   │     weer.py ─────┘    │     (Open-Meteo, per gebied)
                                   ▼                       │
                               dips.py            events.csv ◄── scan_events.py
                     (onverklaarde lege stallingen)            (Ticketmaster, Gemeenteblad, agenda's + AI)

 data/model.joblib + fietsmomenten.csv ──► meldingen.py ──► ntfy ──► je telefoon
                                          predict.py   ──► overzicht in de terminal
```

| Bestand | Wat het doet |
|---|---|
| `collect.py` | Haalt elke 15 min de voorraad op (NS API) voor alle stallingen in de gebieden. |
| `model.py` | Machine learning (gradient boosting): per horizon een model voor het aantal fietsen en een model voor de kans op krapte. Kenmerken over *nu* én over het *doeltijdstip*. |
| `predict.py` | Voorspelling voor nu, per gebied: `python predict.py`, `python predict.py HT`, `python predict.py amsterdam`. |
| `meldingen.py` | Pushmeldingen voor je fietsmomenten (`fietsmomenten.csv`). |
| `scan_events.py` | Zoekt evenementen rond alle stations en schat de impact. |
| `dips.py` | Vindt momenten waarop een stalling veel leger was dan normaal zonder bekende reden. |
| `gebieden.py`, `kalender.py`, `weer.py` | Gebieden, feestdagen/vakanties per regio/carnaval/evenementen, weer per gebied. |
| `check_setup.py` | Controleert of alles klaarstaat. |
| `demo/` | Het dashboard (`fietsvoorraad-demo.html`) en `bouw_demo.py` om het opnieuw te bouwen. |

## Laten meten via GitHub

Maak een repository, push deze map, verplaats de drie `github-workflow-*.yml` naar `.github/workflows/` en zet de secrets `NS_API_KEY` (en later `NTFY_TOPIC`, `TICKETMASTER_API_KEY`, `ANTHROPIC_API_KEY`).

| Workflow | Wanneer | Wat |
|---|---|---|
| `collect` | elke 15 min | meten, daarna pushmeldingen als er een model is |
| `scan` | elke ochtend | evenementen zoeken (`--auto`) en dips opsporen |
| `train` | elke maandag | model opnieuw trainen op alle metingen |

Kies een publieke repo: de sleutels staan veilig in de secrets en publieke repo's hebben onbeperkt gratis Actions-minuten. Met enkele tientallen stallingen elke 15 minuten groeit de data met minder dan 1 MB per dag. Alternatief op je Mac (alleen als hij aanstaat), via `crontab -e`:

```
*/15 * * * * cd "/Users/mick/Desktop/Claude General/OVFiets" && /usr/bin/python3 collect.py >> data/collect.log 2>&1
```

## Pushmeldingen

Zet je momenten in `fietsmomenten.csv` (uitleg bovenin het bestand); het dashboard maakt de regel voor je aan. `meldingen.py` draait na elke meting. Per moment: `vooraf` (2 uur van tevoren), `update` (als de verwachting twee metingen op rij van status verandert), `krap` (direct, met een alternatieve stalling binnen 5 km), `event` (tot 6 uur van tevoren) en `weer` (regen rond je tijd). "Weinig" schaalt mee met de stalling: 10 fietsen is genoeg in Vught, weinig in Utrecht. Een stationscode zoals `HT` telt alle stallingen van dat station samen.

```bash
python meldingen.py --simuleer 2026-10-05T06:00 2026-10-05T09:00   # afspelen op oude data, niets versturen
```

## Evenementen (automatisch)

```bash
python scan_events.py            # zoeken → data/event_kandidaten.csv
python scan_events.py --auto     # idem, en zekere vondsten direct in events.csv
python scan_events.py --review   # de rest zelf goed- of afkeuren
python dips.py --zoek            # onverklaarde lege stallingen laten uitzoeken door Claude
```

Bronnen: Ticketmaster (per gebied), evenementenvergunningen uit het Gemeenteblad (open data) voor de gemeenten in `config.py`, en agenda-pagina's (lijst in `config.py`; vul gerust aan met Jaarbeurs, RAI, Ahoy). Vergunningen en agenda's worden gelezen door Claude. Afstand telt mee: vlak bij het station lopen mensen, op 1–12 km pakken ze juist de OV-fiets. `dips.py` telt regenachtige referentieweken niet mee, zodat een gewone droge dag niet als dip wordt gezien.

## Testen zonder sleutels

```bash
python tests/test_gebieden.py
python tests/test_scanner.py
python tests/test_meldingen.py
python demo/bouw_demo.py          # nepdata → model → dashboard in demo/
```

Met nepdata werken (5 weken, 16 stallingen in 13 gebieden, met evenementen en weer):

```bash
python tests/nepdata.py --map /tmp/nep
export OVFIETS_DATA_DIR=/tmp/nep OVFIETS_EVENTS_CSV=/tmp/nep/events_demo.csv OVFIETS_WEER_CSV=/tmp/nep/weer_demo.csv
python model.py && python predict.py && python dips.py --dagen 35
```

## Ideeën voor later

Treinstoringen (NS disruptions-API) als kenmerk en melding; een echte webapp (installeerbaar op je telefoon) op basis van het dashboard, met live data en fietsmomenten instellen in de app.
