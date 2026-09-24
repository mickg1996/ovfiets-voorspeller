"""Offline tests voor de evenementen-scanner (geen netwerk nodig).

    python tests/test_scanner.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
tmp = Path(tempfile.mkdtemp())
os.environ["OVFIETS_DATA_DIR"] = str(tmp)
(tmp / "events.csv").write_text("# test\nstart,eind,naam,stations,impact,bron\n")
os.environ["OVFIETS_EVENTS_CSV"] = str(tmp / "events.csv")
for k in ("TICKETMASTER_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)

import ai  # noqa: E402
import bronnen  # noqa: E402
import config  # noqa: E402
import scan_events as se  # noqa: E402

# --- Ticketmaster-parser
tm = {"_embedded": {"events": [{
    "name": "Voorbeeldshow", "url": "https://example.org/x",
    "dates": {"start": {"localDate": "2026-11-07", "localTime": "20:00:00"}},
    "classifications": [{"segment": {"name": "Music"}}],
    "_embedded": {"venues": [{"name": "Brabanthallen", "city": {"name": "'s-Hertogenbosch"},
                              "location": {"latitude": "51.7003", "longitude": "5.3048"}}]}}]}}
evs = bronnen.parse_ticketmaster(tm)
assert evs[0]["start"] == "2026-11-07 20:00" and evs[0]["eind"] == "2026-11-07 23:00" and evs[0]["type"] == "concert"

# --- SRU-parser
xml = b"""<?xml version="1.0"?>
<sru:searchRetrieveResponse xmlns:sru="http://docs.oasis-open.org/ns/search-ws/sruResponse">
 <sru:numberOfRecords>1</sru:numberOfRecords><sru:records><sru:record><sru:recordData>
 <gzd:gzd xmlns:gzd="http://standaarden.overheid.nl/sru"><gzd:originalData>
  <overheidwetgeving:meta xmlns:overheidwetgeving="http://standaarden.overheid.nl/wetgeving/">
   <overheidwetgeving:owmskern xmlns:dcterms="http://purl.org/dc/terms/">
    <dcterms:identifier>gmb-2026-123456</dcterms:identifier>
    <dcterms:title>Verleende evenementenvergunning Voorbeeldfestival</dcterms:title>
    <dcterms:creator>'s-Hertogenbosch</dcterms:creator>
    <dcterms:modified>2026-09-20</dcterms:modified>
   </overheidwetgeving:owmskern></overheidwetgeving:meta></gzd:originalData></gzd:gzd>
 </sru:recordData></sru:record></sru:records></sru:searchRetrieveResponse>"""
recs = bronnen.parse_sru(xml)
assert recs[0]["gemeente"] == "'s-Hertogenbosch" and "evenementenvergunning" in recs[0]["titel"].lower()
assert recs[0]["url"].endswith("gmb-2026-123456.html")

# --- HTML → tekst
assert "Concert" in bronnen.html_naar_tekst("<html><script>x=1</script><h2>Concert</h2><p>7 nov</p></html>")

# --- JSON uit AI-antwoord
assert ai.json_uit('Hier:\n```json\n[{"naam":"A"}]\n```')[0]["naam"] == "A"

# --- impact-heuristiek: afstand telt mee
assert se.schat_impact({"bezoekers": 30000, "afstand_km": 3}) == 3
assert se.schat_impact({"bezoekers": 30000, "afstand_km": 0.5}) == 2      # loopafstand
assert se.schat_impact({"bezoekers": 30000, "afstand_km": 20}) == 1       # te ver
assert se.schat_impact({"bezoekers": 400, "afstand_km": 3}) == 0
assert se.schat_impact({"locatie": "Brabanthallen", "afstand_km": 1.6}) == 2
assert se.dichtstbij(51.7003, 5.3048)[0] in ("HT", "HTO")
assert se.dichtstbij(51.6536, 5.2890)[0] == "VG"

# --- een event tussen twee stations raakt ze allebei
st = se.stations_bij(51.7003, 5.3048)
assert "HT" in st and "HTO" in st, st
assert se.stations_bij(51.6536, 5.2890)[0] == "VG"

# --- ontdubbelen: zelfde dag + (bijna) zelfde naam, of zelfde locatie en starttijd
a = {"naam": "Voorbeeldshow Live 2026", "start": "2026-11-07 20:00", "locatie": "Brabanthallen"}
assert se.zelfde_event(a, {"naam": "Voorbeeldshow", "start": "2026-11-07 19:30", "locatie": ""})
assert se.zelfde_event(a, {"naam": "Iets anders", "start": "2026-11-07 20:00", "locatie": "Brabanthallen"})
assert not se.zelfde_event(a, {"naam": "Voorbeeldshow", "start": "2026-11-08 20:00", "locatie": "Brabanthallen"})

# --- volledige scan met nep-bronnen
agenda_dubbel = {**evs[0], "naam": "VOORBEELDSHOW (live)", "bron": "Brabanthallen", "zekerheid": "middel",
                 "bezoekers": 9000, "lat": None, "lng": None, "locatie": "Brabanthallen"}
bronnen.geocode = lambda q: (51.7003, 5.3048)
bronnen.ticketmaster = lambda: [dict(e) for e in evs]
bronnen.vergunningen = lambda: []
bronnen.agendas = lambda: [dict(agenda_dubbel)]
se.scan()
rijen = se.lees(config.KANDIDATEN_CSV)
assert len(rijen) == 1, rijen                      # twee bronnen, één evenement
r = rijen[0]
assert r["status"] == "nieuw" and "HT" in r["stations"] and r["bezoekers"] == "9000"
assert "Ticketmaster" in r["bron"] and "Brabanthallen" in r["bron"]
se.scan()  # tweede keer: geen dubbelen
assert len(se.lees(config.KANDIDATEN_CSV)) == 1

# --- --auto: zekere vondst gaat direct naar events.csv
bronnen.ticketmaster = lambda: [{**evs[0], "naam": "Grote show", "start": "2026-12-05 20:00", "eind": "2026-12-05 23:00"}]
bronnen.agendas = lambda: []
se.scan(auto=True)
events = se.lees(config.EVENTS_CSV)
assert len(events) == 1 and events[0]["naam"] == "Grote show" and "HT" in events[0]["stations"]
print("\nalle scanner-tests geslaagd")
