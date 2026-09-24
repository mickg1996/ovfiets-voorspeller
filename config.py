"""Centrale instellingen voor de OV-Fietsvoorraad voorspeller."""
import os
from pathlib import Path

APP_NAAM = "OV-Fietsvoorraad voorspeller"

ROOT = Path(__file__).resolve().parent

# Sleutels mogen ook in een bestand .env in deze map staan (NAAM=waarde per regel). Staat in .gitignore.
if (ROOT / ".env").exists():
    for _regel in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in _regel and not _regel.lstrip().startswith("#"):
            _k, _v = _regel.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
DATA_DIR = Path(os.environ.get("OVFIETS_DATA_DIR", ROOT / "data"))  # override voor tests
SNAPSHOT_DIR = DATA_DIR / "snapshots"
EVENTS_CSV = Path(os.environ.get("OVFIETS_EVENTS_CSV", ROOT / "events.csv"))
STATIONS_CSV = ROOT / "stations.csv"   # alle stations + gebied + schoolvakantieregio
MODEL_PATH = DATA_DIR / "model.joblib"

# NS API (https://apiportal.ns.nl) — product "Ns-App", bevat de Places API.
NS_API_BASE = "https://gateway.apiportal.ns.nl"
NS_OVFIETS_PATH = "/places-api/v2/ovfiets"
NS_KEY_ENV = "NS_API_KEY"  # zet je subscription key in deze omgevingsvariabele

# Gebieden (zie stations.csv en gebieden.py): een regio met eigen straal, of één groot station.
GEBIED_STRAAL_KM = {"Regio Den Bosch": 25,   # alle stallingen binnen 25 km van 's-Hertogenbosch
                    "Tilburg": 7}            # Tilburg heeft binnen die 25 km een eigen gebied (dichtstbijzijnde wint)
GROOT_STATION_STRAAL_KM = 1.5                  # grote stations: alle stallingen van dat station, niet de buren
EVENT_STRAAL_KM = 6                            # evenementen zoeken tot zo ver rond een groot station

# Modelinstellingen
RESAMPLE = "15min"            # tijdraster waarop we alles uitlijnen
HORIZONS_MIN = [30, 60, 120]  # hoever vooruit we voorspellen
LAAG_DREMPEL = 3              # "krap" = minder dan zoveel fietsen

# Weer (Open-Meteo, gratis, geen key) — per gebied
WEER_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEER_ARCHIEF_URL = "https://archive-api.open-meteo.com/v1/archive"

# ---------- Pushmeldingen voor fietsmomenten ----------
FIETSMOMENTEN_CSV = Path(os.environ.get("OVFIETS_FIETSMOMENTEN_CSV", ROOT / "fietsmomenten.csv"))
MELDINGEN_STATUS = DATA_DIR / "meldingen_status.json"
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC_ENV = "NTFY_TOPIC"   # een lange, willekeurige naam; iedereen die hem kent kan meelezen
VOORAF_MIN = 120                # 'vooraf'-melding zo lang voor je fietsmoment
EVENT_VOORAF_UUR = 6            # evenement-melding tot zoveel uur van tevoren
ALTERNATIEF_KM = 5              # alternatieve stallingen binnen deze afstand

# ---------- Evenementen-scanner ----------
KANDIDATEN_CSV = DATA_DIR / "event_kandidaten.csv"
DIPS_CSV = DATA_DIR / "onverklaarde_dips.csv"
TICKETMASTER_KEY_ENV = "TICKETMASTER_API_KEY"   # gratis via developer.ticketmaster.com
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"         # voor het lezen van agenda's/vergunningen (optioneel)
ANTHROPIC_MODEL_ENV = "ANTHROPIC_MODEL"         # leeg = automatisch het nieuwste Haiku-model
SCAN_DAGEN_VOORUIT = 90
SCAN_DAGEN_TERUG_VERGUNNINGEN = 14              # vergunningen die de afgelopen x dagen gepubliceerd zijn

# Gemeenten waarvan we evenementenvergunningen volgen (Gemeenteblad via officielebekendmakingen)
GEMEENTEN = ["'s-Hertogenbosch", "Vught", "Oss", "Boxtel", "Sint-Michielsgestel", "Heusden",
             "Maasdriel", "Zaltbommel", "Bernheze",
             "Tilburg", "Utrecht", "Amsterdam", "Rotterdam", "'s-Gravenhage", "Haarlemmermeer", "Leiden",
             "Eindhoven", "Nijmegen", "Amersfoort"]

# Agenda-pagina's die de AI leest. Voeg gerust bronnen toe (bv. het programma van FC Den Bosch,
# de agenda van de Jaarbeurs, RAI, Ahoy of de Johan Cruijff ArenA).
AGENDA_PAGINAS = [
    ("Brabanthallen", "https://www.brabanthallen.nl/agenda/"),
    ("Autotron", "https://www.autotron.nl/evenementen/"),
    ("Zin in Den Bosch", "https://www.zinindenbosch.nl/nl/page/events"),
    ("Uit Den Bosch", "https://www.uitdenbosch.com/"),
]
