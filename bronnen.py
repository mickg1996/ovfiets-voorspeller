"""Bronnen voor de evenementen-scanner. Elke bron geeft een lijst dicts terug met minimaal
naam, start, eind (YYYY-MM-DD HH:MM, lokale tijd), locatie, plaats, bron, url en waar mogelijk lat/lng.
"""
from __future__ import annotations

import os
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

import requests

import ai
import config

UA = {"User-Agent": "ovfiets-voorspeller/1.0 (hobbyproject)"}


def _log(msg: str):
    print(msg, file=sys.stderr)


# ---------- hulpjes ----------
class _Tekst(HTMLParser):
    def __init__(self):
        super().__init__()
        self.delen, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag in ("br", "p", "li", "h1", "h2", "h3", "h4", "div", "tr", "article", "time"):
            self.delen.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.delen.append(data.strip() + " ")


def html_naar_tekst(html: str) -> str:
    p = _Tekst()
    p.feed(html)
    return re.sub(r"\n\s*\n+", "\n", "".join(p.delen)).strip()


@lru_cache(maxsize=512)
def geocode(zoek: str) -> tuple[float, float] | None:
    """Adres/locatie → (lat, lng) via PDOK Locatieserver (gratis, geen key)."""
    try:
        r = requests.get("https://api.pdok.nl/bzk/locatieserver/search/v3_1/free",
                         params={"q": zoek, "rows": 1, "fl": "centroide_ll,weergavenaam"}, timeout=20, headers=UA)
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
        if docs and docs[0].get("centroide_ll"):
            lng, lat = map(float, re.findall(r"[-\d.]+", docs[0]["centroide_ll"])[:2])
            return lat, lng
    except (requests.RequestException, ValueError) as e:
        _log(f"  geocode '{zoek}' mislukt: {e}")
    return None


# ---------- 1. Ticketmaster (gestructureerd, geen AI nodig) ----------
def parse_ticketmaster(js: dict) -> list[dict]:
    uit = []
    for ev in js.get("_embedded", {}).get("events", []):
        st = ev.get("dates", {}).get("start", {})
        dag, tijd = st.get("localDate"), (st.get("localTime") or "20:00:00")[:5]
        if not dag:
            continue
        begin = datetime.fromisoformat(f"{dag} {tijd}")
        eind_info = ev.get("dates", {}).get("end", {})
        if eind_info.get("localDate"):
            eind = datetime.fromisoformat(f"{eind_info['localDate']} {(eind_info.get('localTime') or '23:00')[:5]}")
        else:
            eind = begin + timedelta(hours=3)
        venue = (ev.get("_embedded", {}).get("venues") or [{}])[0]
        loc = venue.get("location") or {}
        seg = ((ev.get("classifications") or [{}])[0].get("segment") or {}).get("name", "")
        uit.append({
            "naam": ev.get("name", ""), "start": begin.strftime("%Y-%m-%d %H:%M"), "eind": eind.strftime("%Y-%m-%d %H:%M"),
            "locatie": venue.get("name", ""), "plaats": (venue.get("city") or {}).get("name", ""),
            "lat": float(loc["latitude"]) if loc.get("latitude") else None,
            "lng": float(loc["longitude"]) if loc.get("longitude") else None,
            "bezoekers": None, "type": {"Music": "concert", "Sports": "sport", "Arts & Theatre": "theater"}.get(seg, "overig"),
            "zekerheid": "hoog", "bron": "Ticketmaster", "url": ev.get("url", ""),
        })
    return uit


def ticketmaster() -> list[dict]:
    """Per gebied één zoekvraag: Regio Den Bosch met zijn eigen straal, grote stations met EVENT_STRAAL_KM."""
    from gebieden import gebieden
    key = os.environ.get(config.TICKETMASTER_KEY_ENV)
    if not key:
        _log("Ticketmaster overgeslagen (geen TICKETMASTER_API_KEY).")
        return []
    nu = datetime.now(timezone.utc)
    uit, gezien = [], set()
    for g in gebieden():
        params = {"apikey": key, "latlong": f"{g['lat']},{g['lng']}", "radius": max(g["straal"], config.EVENT_STRAAL_KM),
                  "unit": "km", "size": 200, "locale": "*", "sort": "date,asc",
                  "startDateTime": nu.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "endDateTime": (nu + timedelta(days=config.SCAN_DAGEN_VOORUIT)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        pagina, n, pogingen = 0, 0, 0
        while True:
            params["page"] = pagina
            r = requests.get("https://app.ticketmaster.com/discovery/v2/events.json", params=params, timeout=30)
            if r.status_code == 429 and pogingen < 5:  # te snel achter elkaar: even wachten
                import time
                pogingen += 1
                time.sleep(2)
                continue
            r.raise_for_status()
            js = r.json()
            for ev in parse_ticketmaster(js):
                k = (ev["naam"], ev["start"], ev["locatie"])
                if k not in gezien:
                    gezien.add(k)
                    uit.append(ev)
                    n += 1
            pagina += 1
            if pagina >= js.get("page", {}).get("totalPages", 1) or pagina >= 5:
                break
        _log(f"Ticketmaster {g['naam']}: {n} evenementen")
    return uit


# ---------- 2. Evenementenvergunningen (Gemeenteblad, officielebekendmakingen) ----------
SRU = "https://repository.overheid.nl/sru"
NS = {"sru": "http://docs.oasis-open.org/ns/search-ws/sruResponse",
      "diag": "http://docs.oasis-open.org/ns/search-ws/diagnostic",
      "gzd": "http://standaarden.overheid.nl/sru", "ow": "http://standaarden.overheid.nl/wetgeving/",
      "dcterms": "http://purl.org/dc/terms/"}


def parse_sru(xml: bytes) -> list[dict]:
    root = ET.fromstring(xml)
    diag = root.find(".//diag:diagnostic", NS)
    if diag is not None:
        raise ValueError(diag.findtext("diag:message", default="SRU-fout", namespaces=NS))
    uit = []
    for rec in root.findall(".//gzd:gzd", NS):
        kern = rec.find(".//ow:owmskern", NS)
        if kern is None:
            continue
        ident = kern.findtext("dcterms:identifier", default="", namespaces=NS)
        url = ""
        hv = rec.find(".//ow:owmsmantel/dcterms:hasVersion", NS)
        if hv is not None:
            url = hv.get("resourceIdentifier", "")
        uit.append({"id": ident, "titel": kern.findtext("dcterms:title", default="", namespaces=NS),
                    "gemeente": kern.findtext("dcterms:creator", default="", namespaces=NS),
                    "datum": kern.findtext("dcterms:modified", default="", namespaces=NS),
                    "url": url or f"https://zoek.officielebekendmakingen.nl/{ident}.html"})
    return uit


def vergunningen_publicaties() -> list[dict]:
    """Alle recente Gemeenteblad-publicaties over evenementen in de gekozen gemeenten."""
    gevonden, vandaag = [], date.today()
    titelfilter = True  # server-side filteren op titel; valt terug op client-side als de server dat weigert
    for d in range(config.SCAN_DAGEN_TERUG_VERGUNNINGEN):
        dag = (vandaag - timedelta(days=d)).isoformat()
        start = 1
        while True:
            q = (f'(c.product-area==officielepublicaties)and(dt.available=={dag})'
                 f'and(w.publicatienaam=="Gemeenteblad")' + ('and(dt.title=evenement*)' if titelfilter else ''))
            params = urllib.parse.urlencode({"version": "1.2", "operation": "searchRetrieve", "query": q,
                                             "maximumRecords": 100, "startRecord": start})
            try:
                r = requests.get(f"{SRU}?{params}", timeout=30, headers=UA)
                r.raise_for_status()
                recs = parse_sru(r.content)
            except ValueError as e:
                if titelfilter:
                    _log(f"  titelfilter niet ondersteund ({e}); filter voortaan zelf")
                    titelfilter = False
                    continue
                _log(f"  vergunningen {dag}: {e}")
                break
            except (requests.RequestException, ET.ParseError) as e:
                _log(f"  vergunningen {dag}: {e}")
                break
            gevonden += recs
            if len(recs) < 100 or start > 3000:
                break
            start += 100
    def gemeente_klopt(naam: str) -> bool:  # 'Oss' mag niet op 'Losser' matchen
        naam = naam.lower().strip()
        return any(re.search(rf"(^|\s){re.escape(g.lower())}$", naam) for g in config.GEMEENTEN)
    relevant = [p for p in gevonden if gemeente_klopt(p["gemeente"]) and "evenement" in p["titel"].lower()]
    _log(f"Vergunningen: {len(gevonden)} publicaties over evenementen, {len(relevant)} in de regio")
    return relevant


def vergunningen() -> list[dict]:
    pubs = vergunningen_publicaties()
    if not pubs:
        return []
    if not ai.beschikbaar():
        _log("  (zonder ANTHROPIC_API_KEY worden vergunningen alleen als titel doorgegeven)")
        return [{"naam": p["titel"][:120], "start": "", "eind": "", "locatie": "", "plaats": p["gemeente"],
                 "bron": "Vergunning (niet gelezen)", "url": p["url"], "zekerheid": "laag"} for p in pubs]
    uit = []
    for p in pubs:
        try:
            html = requests.get(p["url"], timeout=30, headers=UA).text
            for ev in ai.extraheer_events(html_naar_tekst(html), f"evenementenvergunning {p['gemeente']}",
                                          date.today().isoformat()):
                uit.append({**ev, "bron": f"Vergunning {p['gemeente']}", "url": p["url"]})
        except requests.RequestException as e:
            _log(f"  {p['url']}: {e}")
    return uit


# ---------- 3. Agenda-pagina's (AI leest de pagina) ----------
def agendas() -> list[dict]:
    if not ai.beschikbaar():
        _log("Agenda-pagina's overgeslagen (geen ANTHROPIC_API_KEY).")
        return []
    uit = []
    for naam, url in config.AGENDA_PAGINAS:
        try:
            r = requests.get(url, timeout=30, headers=UA)
            r.raise_for_status()
            evs = ai.extraheer_events(html_naar_tekst(r.text), naam, date.today().isoformat())
            for ev in evs:
                ev.setdefault("locatie", naam)
                uit.append({**ev, "bron": naam, "url": url})
            _log(f"{naam}: {len(evs)} evenementen")
        except requests.RequestException as e:
            _log(f"{naam}: pagina niet bereikbaar ({e}) — controleer de URL in config.py")
    return uit
