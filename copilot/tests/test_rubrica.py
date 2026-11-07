import sqlite3

from aios_copilot.rubrica import Rubrica, merge, parse_vcf, to_vcf
from aios_copilot.tools.rubrica import ContactsRouter

PHONE = """BEGIN:VCARD\r\nVERSION:2.1\r\nFN:Giulia Neri\r\nTEL;CELL:+39 333 123 4567\r\nBDAY:19900314\r\nEND:VCARD\r
BEGIN:VCARD\r\nN:Rossi;Mario;;;\r\nTEL:0039 347 7654321\r\nEND:VCARD\r\n"""


def test_vcf_round_trip_and_merge(tmp_path):
    phone = parse_vcf(PHONE, "telefono")
    assert [c.nome for c in phone] == ["Giulia Neri", "Mario Rossi"] and phone[0].compleanno == "1990-03-14"
    assert parse_vcf(to_vcf(phone), "aios")[0].telefoni == ["+39 333 123 4567"]
    db = tmp_path / "mail.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (sender_name TEXT, sender TEXT)")
    con.executemany("INSERT INTO messages VALUES (?, ?)", [("Giulia Neri", "giulia@esempio.it")] * 3 +
                    [("Banca", "noreply@banca.it")] * 5 + [("Una volta", "x@y.it")])
    con.commit()
    r = Rubrica(phone=lambda: phone, mail=lambda: __import__("aios_copilot.rubrica", fromlist=["x"]).mail_contacts(db),
                path=tmp_path / "rubrica.vcf")
    assert r.add("mario rossi", "347 765 4321", "mario@rossi.it") == "mario rossi è in rubrica."
    people = {c.nome: c for c in r.all()}
    assert set(people) == {"Giulia Neri", "mario rossi"}  # niente noreply, niente chi ha scritto una volta
    assert people["Giulia Neri"].email == ["giulia@esempio.it"] and people["Giulia Neri"].mail_scambiate == 3
    assert people["mario rossi"].telefoni == ["347 765 4321"] and set(people["mario rossi"].fonti) == {"aios", "telefono"}
    assert r.find("3477654321")[0].nome == "mario rossi" and r.find("giulia")[0].compleanno == "1990-03-14"
    assert r.remove("Mario Rossi") and not r.remove("Nessuno")
    assert r.add("X", email="non-valida").startswith("«non-valida»")
    assert len(merge(phone, phone)) == 2


def test_contacts_router():
    r = ContactsRouter()
    i = r.match("aggiungi mario rossi alla rubrica, 333 1234567")
    assert i.tool == "add_contact" and i.args == {"nome": "Mario Rossi", "telefono": "333 1234567", "email": ""}
    i = r.match("salva Anna in rubrica con la mail anna@studio.it")
    assert i.args["email"] == "anna@studio.it" and i.args["nome"] == "Anna"
    assert r.match("che numero ha Giulia?").args == {"nome": "giulia"}
    assert r.match("quando è il compleanno di sara").tool == "find_contact"


def test_calendar_routes(tmp_path):
    from datetime import datetime

    from aios_copilot.agenda import Agenda
    from aios_copilot.shell.apps import register_calendar

    routes = {}

    class App:
        def route(self, method, pattern, fn):
            routes[(method, pattern)] = fn

    agenda = Agenda(tmp_path / "a.db", clock=lambda: datetime(2026, 10, 5, 9))
    register_calendar(App(), lambda: agenda)
    new = routes[("POST", r"/api/calendario/nuovo")]
    assert new(None, {"titolo": "Dentista", "giorno": "2026-10-08", "ora": "15:00", "luogo": "Via Roma"}, {})[1]["ok"]
    new(None, {"titolo": "Compleanno", "giorno": "2026-10-12", "ripeti": "yearly"}, {})
    new(None, {"titolo": "Bolletta", "giorno": "2026-10-20", "promemoria": True}, {})
    code, d = routes[("GET", r"/api/calendario")](None, {}, {"da": "2026-09-28", "a": "2026-11-09"})
    assert [v["titolo"] for v in d["voci"]] == ["Dentista", "Compleanno", "Bolletta"]
    assert d["voci"][1]["tutto_il_giorno"] and d["voci"][2]["quando"] == "2026-10-20T09:00:00"
    assert routes[("GET", r"/api/calendario")](None, {}, {"da": "2026-01-01", "a": "2027-01-01"})[0] == 400
    dentist = d["voci"][0]
    assert routes[("POST", r"/api/calendario/togli")](None, {"tipo": "event", "id": dentist["id"]}, {})[1]["ok"]
