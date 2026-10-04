"""Le frasi da cui imparano gli adattatori del nucleo (aios_copilot/nucleo.py).

Per ogni azione di Nova: dei modelli di frase con le parti variabili tra graffe e i campi attesi.
Le parti variabili si riempiono con i valori di VALORI; le date con QUANDO, calcolate rispetto a «adesso».
Un campo "{quando}" vale la data ISO della frase scelta; gli altri "{x}" il testo inserito.
Aggiungere frasi qui (soprattutto quelle che Nova ha capito male) e rilanciare l'addestramento.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

VALORI: dict[str, list[str]] = {
    "persona": ["Marco", "la mamma", "Giulia", "Luca", "papà", "Aurora", "il dottor Bianchi", "Sara", "nonna", "Francesco"],
    "cosa": ["chiamare il dentista", "comprare il latte", "pagare la bolletta", "portare fuori il cane",
             "prendere le medicine", "mandare il preventivo", "ritirare il pacco", "fare la spesa",
             "innaffiare le piante", "rinnovare l'assicurazione", "prenotare la revisione", "studiare per l'esame"],
    "evento": ["cena da Giulia", "riunione con il commercialista", "visita dal dentista", "partita di calcetto",
               "colloquio di lavoro", "compleanno di Aurora", "corso di inglese", "aperitivo con Luca"],
    "luogo": ["in ufficio", "a casa di Marco", "in centro", "allo studio medico", "in palestra"],
    "app": ["Firefox", "LibreOffice", "VLC", "GIMP", "Spotify", "Telegram", "Steam", "Thunderbird", "la calcolatrice",
            "il terminale", "Writer", "Blender"],
    "file": ["la tesi", "il contratto d'affitto", "la bolletta della luce", "il CV", "le ricevute del medico",
             "il preventivo della cucina", "il libretto della caldaia", "la dichiarazione dei redditi"],
    "foto": ["Aurora", "agosto", "il mare dell'anno scorso", "Natale", "la montagna", "il matrimonio di Sara",
             "Parigi", "il cane"],
    "ricerca": ["il meteo di domani a Torino", "gli orari dei treni per Milano", "la ricetta della carbonara",
                "le ultime notizie", "il risultato della Juve", "quanto costa un iPhone", "i film al cinema stasera"],
    "testo": ["arrivo tra dieci minuti", "ok, ci vediamo dopo", "sto guidando, ti richiamo", "grazie mille!",
              "a che ora ci vediamo?"],
    "mittente": ["Amazon", "la banca", "Netflix", "l'ufficio", "@newsletter.it", "Marco"],
    "tema": ["marino", "autunnale", "alto contrasto", "notturno", "pastello", "foresta"],
    "servizio": ["Netflix", "Spotify", "Disney+", "Prime Video", "DAZN"],
    "titolo": ["Stranger Things", "Il Signore degli Anelli", "Breaking Bad", "Inside Out", "Dune"],
    "citta": ["Londra", "New York", "Tokyo", "Parigi", "Roma"],
    "cartella": ["Scaricati", "la scrivania", "Documenti"],
    "dispositivo": ["il portatile", "il telefono vecchio", "il tablet"],
    "giorno": ["ieri", "lunedì", "tre giorni fa", "venerdì scorso", "il 3 ottobre"],
    "argomento": ["la bici", "il contratto", "le vacanze in Grecia", "il mutuo", "la lavatrice"],
    "tastiera": ["inglese", "americana", "tedesca", "italiana", "francese"],
    "ore": ["due", "tre", "un'ora", "mezza giornata"],
}


def _at(days: int, hour: int | None, minute: int = 0) -> Callable[[datetime], str]:
    def f(now: datetime) -> str:
        d = now + timedelta(days=days)
        return d.strftime("%Y-%m-%d") if hour is None else d.replace(hour=hour, minute=minute).strftime("%Y-%m-%dT%H:%M")
    return f


def _weekday(target: int, hour: int | None) -> Callable[[datetime], str]:
    def f(now: datetime) -> str:
        days = (target - now.weekday()) % 7 or 7
        return _at(days, hour)(now)
    return f


def _in(minutes: int) -> Callable[[datetime], str]:
    return lambda now: (now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M")


QUANDO: list[tuple[str, Callable[[datetime], str]]] = [
    ("domani alle 9", _at(1, 9)), ("domani mattina alle 8", _at(1, 8)), ("oggi alle 18", _at(0, 18)),
    ("stasera alle 21", _at(0, 21)), ("dopodomani alle 15", _at(2, 15)), ("alle 17:30", _at(0, 17, 30)),
    ("tra mezz'ora", _in(30)), ("tra un'ora", _in(60)), ("tra 10 minuti", _in(10)),
    ("lunedì alle 10", _weekday(0, 10)), ("venerdì alle 20", _weekday(4, 20)), ("sabato", _weekday(5, None)),
    ("domani", _at(1, None)), ("mercoledì pomeriggio alle 16", _weekday(2, 16)),
]

# (azione, modello di frase, campi attesi)
ESEMPI: list[tuple[str, str, dict[str, str]]] = [
    # agenda
    ("add_reminder", "ricordami di {cosa} {quando}", {"what": "{cosa}", "when": "{quando}"}),
    ("add_reminder", "{quando} ricordami di {cosa}", {"what": "{cosa}", "when": "{quando}"}),
    ("add_reminder", "mettimi un promemoria per {cosa} {quando}", {"what": "{cosa}", "when": "{quando}"}),
    ("add_reminder", "non farmi dimenticare di {cosa}", {"what": "{cosa}"}),
    ("add_reminder", "devo {cosa}, segnalo", {"what": "{cosa}"}),
    ("add_reminder", "aggiungi alle cose da fare {cosa}", {"what": "{cosa}"}),
    ("add_event", "segna in agenda {evento} {quando}", {"title": "{evento}", "when": "{quando}"}),
    ("add_event", "ho {evento} {quando} {luogo}", {"title": "{evento}", "when": "{quando}", "location": "{luogo}"}),
    ("add_event", "metti in calendario {evento} {quando}", {"title": "{evento}", "when": "{quando}"}),
    ("add_event", "fissa un appuntamento: {evento} {quando}", {"title": "{evento}", "when": "{quando}"}),
    ("list_agenda", "cosa ho da fare oggi?", {"period": "oggi"}),
    ("list_agenda", "che impegni ho domani", {"period": "domani"}),
    ("list_agenda", "fammi vedere gli appuntamenti della settimana", {"period": "settimana"}),
    ("list_agenda", "sono libero questo weekend?", {"period": "weekend"}),
    ("list_agenda", "quali promemoria ho?", {"period": "promemoria"}),
    ("daily_briefing", "com'è la mia giornata?", {}),
    ("daily_briefing", "fammi il riepilogo di oggi", {}),
    ("daily_briefing", "buongiorno, cosa c'è oggi?", {}),
    ("complete_reminder", "ho fatto: {cosa}", {"query": "{cosa}"}),
    ("complete_reminder", "segna come fatto {cosa}", {"query": "{cosa}"}),
    ("delete_agenda_item", "cancella l'appuntamento {evento}", {"query": "{evento}"}),
    ("delete_agenda_item", "togli il promemoria di {cosa}", {"query": "{cosa}"}),
    # posta
    ("mail_overview", "ho mail nuove?", {}),
    ("mail_overview", "com'è messa la posta?", {}),
    ("mail_overview", "c'è qualcosa di importante nella mail?", {}),
    ("search_mail", "cerca le mail di {mittente}", {"query": "{mittente}"}),
    ("search_mail", "trovami l'email con la fattura di {mittente}", {"query": "fattura {mittente}"}),
    ("send_email", "scrivi una mail a {persona} per dire che {testo}", {"to": "{persona}", "body": "{testo}"}),
    ("send_email", "manda un'email a {persona}: {testo}", {"to": "{persona}", "body": "{testo}"}),
    ("categorize_mail", "le mail di {mittente} mettile nelle newsletter", {"who": "{mittente}", "category": "newsletter"}),
    ("read_mail", "leggimi la mail numero 3", {"mail_id": "3"}),
    # file
    ("search_files", "cerca {file}", {"query": "{file}"}),
    ("search_files", "dov'è finito {file}?", {"query": "{file}"}),
    ("search_files", "trova i documenti che parlano di {argomento}", {"query": "{argomento}"}),
    ("show_document", "fammi vedere {file}", {"query": "{file}"}),
    ("show_document", "aprimi {file}", {"query": "{file}"}),
    ("show_photos", "mostrami le foto di {foto}", {"query": "{foto}"}),
    ("show_photos", "fammi vedere le foto di {foto}", {"query": "{foto}"}),
    ("show_photos", "dove sono le foto di {foto}?", {"query": "{foto}"}),
    ("diet_today", "cosa mangio oggi a pranzo secondo la dieta?", {"when": "oggi a pranzo"}),
    ("diet_today", "cosa prevede la dieta per domani a cena", {"when": "domani a cena"}),
    ("shopping_list", "fammi la lista della spesa", {}),
    ("photo_recognition", "riconosci le persone nelle mie foto", {"attiva": "si"}),
    ("photo_recognition", "smetti di analizzare le foto", {"attiva": "no"}),
    ("photo_recognition_status", "a che punto sei con le foto?", {}),
    ("exclude_folder", "non guardare nella cartella {cartella}", {"path": "{cartella}"}),
    # riordino
    ("tidy_plan", "metti in ordine {cartella}", {"folder": "{cartella}"}),
    ("tidy_plan", "c'è un gran disordine in {cartella}, sistemala", {"folder": "{cartella}"}),
    ("tidy_apply", "sì, procedi con il riordino", {}),
    ("tidy_undo", "rimetti i file com'erano", {}),
    ("cleanup_suggestions", "il disco è pieno, cosa posso cancellare?", {}),
    ("cleanup_suggestions", "libera un po' di spazio", {}),
    ("list_collections", "che raccolte di file ho?", {}),
    ("show_collection", "mostrami la raccolta {file}", {"what": "{file}"}),
    # app
    ("launch_app", "apri {app}", {"name": "{app}"}),
    ("launch_app", "avvia {app}", {"name": "{app}"}),
    ("launch_app", "mi apri {app} per favore?", {"name": "{app}"}),
    ("launch_app", "voglio usare {app}", {"name": "{app}"}),
    ("close_window", "chiudi {app}", {"name": "{app}"}),
    ("close_window", "esci da {app}", {"name": "{app}"}),
    ("close_all_windows", "chiudi tutto", {}),
    ("close_all_windows", "chiudi tutti i programmi", {}),
    ("switch_window", "torna a {app}", {"name": "{app}"}),
    ("switch_window", "passa a {app}", {"name": "{app}"}),
    ("list_windows", "che programmi ho aperti?", {}),
    ("go_home", "torna alla schermata principale", {}),
    ("go_home", "fammi vedere la home", {}),
    ("search_apps", "c'è un programma per montare i video?", {"query": "video editor"}),
    ("search_apps", "cerca un'app per disegnare", {"query": "drawing"}),
    ("search_apps", "mi serve un programma per leggere i pdf", {"query": "pdf reader"}),
    ("install_app", "installa {app}", {"app_id": "{app}"}),
    ("remove_app", "disinstalla {app}", {"app_id": "{app}"}),
    ("restore_session", "riapri quello che avevo aperto", {}),
    ("restore_session", "riprendi da dove ero rimasto su {dispositivo}", {"dispositivo": "{dispositivo}"}),
    ("other_devices_session", "cosa avevo aperto sull'altro computer?", {}),
    # impostazioni
    ("set_volume", "alza il volume", {"action": "up"}),
    ("set_volume", "non si sente niente, alza", {"action": "up"}),
    ("set_volume", "abbassa un po' l'audio", {"action": "down"}),
    ("set_volume", "è troppo forte", {"action": "down"}),
    ("set_volume", "togli l'audio", {"action": "mute"}),
    ("set_volume", "rimetti l'audio", {"action": "unmute"}),
    ("set_brightness", "lo schermo è troppo scuro", {"action": "up"}),
    ("set_brightness", "alza la luminosità", {"action": "up"}),
    ("set_brightness", "abbassa la luminosità che mi acceca", {"action": "down"}),
    ("set_radio", "accendi il wifi", {"device": "wifi", "state": "on"}),
    ("set_radio", "spegni il bluetooth", {"device": "bluetooth", "state": "off"}),
    ("set_radio", "attiva il bluetooth", {"device": "bluetooth", "state": "on"}),
    ("set_theme", "metti il tema scuro", {"mode": "dark"}),
    ("set_theme", "passa al tema chiaro", {"mode": "light"}),
    ("media_control", "metti in pausa", {"action": "pause"}),
    ("media_control", "prossima canzone", {"action": "next"}),
    ("take_screenshot", "fai uno screenshot", {}),
    ("take_screenshot", "cattura lo schermo", {}),
    ("lock_screen", "blocca lo schermo", {}),
    ("power", "spegni il computer", {"action": "poweroff"}),
    ("power", "riavvia il pc", {"action": "reboot"}),
    ("set_keyboard", "metti la tastiera {tastiera}", {"lingua": "{tastiera}"}),
    ("bluetooth_devices", "quali cuffie bluetooth sono collegate?", {}),
    # computer
    ("current_time", "che ore sono?", {"what": "ora"}),
    ("current_time", "che giorno è oggi?", {"what": "data"}),
    ("system_info", "quanta memoria ha questo computer?", {}),
    ("system_info", "quanto spazio libero ho sul disco?", {}),
    ("energy_status", "come sta la batteria?", {}),
    ("energy_choice", "risparmia la batteria per {ore} ore", {"mode": "risparmio", "hours": "{ore}"}),
    ("energy_choice", "dammi il massimo delle prestazioni", {"mode": "prestazioni"}),
    ("open_location", "apri la cartella {cartella}", {"target": "{cartella}"}),
    ("voice_listening", "smetti di ascoltarmi", {"on": "no"}),
    ("voice_listening", "riprendi ad ascoltare", {"on": "sì"}),
    ("voice_status", "mi stai ascoltando?", {}),
    ("set_timezone", "imposta il fuso orario di {citta}", {"luogo": "{citta}"}),
    ("get_timezone", "che fuso orario ho?", {}),
    # aggiornamenti
    ("update_now", "aggiorna il sistema", {}),
    ("update_now", "ci sono aggiornamenti?", {}),
    ("update_status", "il sistema è aggiornato?", {}),
    ("restart_to_update", "riavvia per aggiornare", {}),
    ("rollback_system", "torna alla versione di prima, questa non va", {}),
    ("update_from_usb", "aggiorna dalla chiavetta", {}),
    ("auto_updates", "disattiva gli aggiornamenti automatici", {"on": "no"}),
    ("connect_github_updates", "collega gli aggiornamenti da GitHub", {}),
    # chiamate
    ("answer_call", "rispondi al telefono", {}),
    ("reject_call", "rifiuta la chiamata", {}),
    ("read_sms", "leggimi i messaggi di {persona}", {"who": "{persona}"}),
    ("send_sms", "manda un sms a {persona}: {testo}", {"to": "{persona}", "text": "{testo}"}),
    ("reply_message", "rispondi a {persona} che {testo}", {"who": "{persona}", "text": "{testo}"}),
    ("ring_phone", "non trovo il telefono, fallo squillare", {}),
    ("phone_notifications", "cosa è arrivato sul telefono?", {}),
    ("setup_calls", "voglio rispondere alle chiamate dal pc", {}),
    # telefono
    ("connect_phone", "collega il mio telefono", {}),
    ("send_to_phone", "manda {file} al telefono", {"path": "{file}"}),
    ("sync_photos", "scarica le foto dal telefono", {}),
    ("copy_code", "copiami il codice che mi è arrivato", {}),
    ("phone_status", "il telefono è collegato?", {}),
    ("improve_photos", "migliora le ultime foto", {"which": "ultime"}),
    ("forget_phone", "scollega {dispositivo}", {"name": "{dispositivo}"}),
    ("install_aios_phone", "installa AIOS sul telefono", {}),
    # identita
    ("identity_status", "quali dispositivi ho collegati al mio account?", {}),
    ("sync_now", "sincronizza adesso con gli altri dispositivi", {}),
    ("revoke_device", "mi hanno rubato {dispositivo}, revocalo", {"name": "{dispositivo}"}),
    ("show_recovery_phrase", "fammi vedere la frase di recupero", {}),
    ("create_identity", "crea la mia identità AIOS", {}),
    # memoria
    ("where_left_off", "dove mi ero fermato?", {}),
    ("where_left_off", "cosa stavo facendo prima?", {}),
    ("recall_day", "cosa ho fatto {giorno}?", {"giorno": "{giorno}"}),
    ("recall_day", "su cosa ho lavorato {giorno}", {"giorno": "{giorno}"}),
    ("search_memory", "quel sito su {argomento} che ho visto {giorno}", {"testo": "{argomento}", "giorno": "{giorno}"}),
    ("search_memory", "ti ricordi quando ti ho parlato di {argomento}?", {"testo": "{argomento}"}),
    ("resume_conversation", "riprendiamo il discorso di {giorno}", {"giorno": "{giorno}"}),
    ("diary_switch", "spegni il diario delle attività", {"attivo": "no"}),
    ("forget_diary", "cancella tutto quello che ricordi di me", {}),
    # web
    ("search_web", "cerca {ricerca}", {"query": "{ricerca}"}),
    ("search_web", "dimmi {ricerca}", {"query": "{ricerca}"}),
    ("search_web", "guarda su internet {ricerca}", {"query": "{ricerca}"}),
    # ai
    ("models_status", "quali modelli AI sto usando?", {}),
    ("suggest_models", "che modelli posso installare su questo pc?", {}),
    ("memory_status", "la memoria è compressa?", {}),
    ("optimize_memory", "ottimizza la memoria", {}),
    ("install_models", "installa il modello per la dettatura", {"which": "dettatura"}),
    # gusti
    ("recommend", "consigliami una serie da vedere", {"kind": "serie"}),
    ("recommend", "che film guardo stasera?", {"kind": "film"}),
    ("recommend", "un cartone per i bambini", {"kind": "cartone"}),
    ("list_subscriptions", "quanto spendo di abbonamenti?", {}),
    ("set_subscription", "ho disdetto {servizio}", {"service": "{servizio}", "active": "no"}),
    ("set_subscription", "ho fatto l'abbonamento a {servizio}", {"service": "{servizio}", "active": "sì"}),
    ("rate", "{titolo} mi è piaciuto tantissimo", {"title": "{titolo}", "liked": "sì"}),
    ("rate", "{titolo} non mi è piaciuto", {"title": "{titolo}", "liked": "no"}),
    # aspetto
    ("create_theme", "fammi un tema {tema}", {"description": "{tema}"}),
    ("create_theme", "voglio un aspetto {tema}", {"description": "{tema}"}),
    ("remix_theme", "rendi il tema più caldo", {"change": "più caldo"}),
    ("previous_theme", "rimetti il tema di prima", {}),
    ("list_themes", "che temi ho?", {}),
    ("market_search", "cerca temi {tema} nel market", {"query": "{tema}"}),
    ("set_text_style", "ingrandisci il testo", {"dimensione": "più grande"}),
    ("set_text_style", "usa un carattere più leggibile", {"carattere": "leggibile"}),
]

# Frasi che non sono richieste al computer: ambito «chiacchiera», nessuna azione.
CHIACCHIERA = [
    "ciao Nova", "grazie mille", "come stai?", "chi ha scritto i Promessi sposi?", "quanto fa 25 per 4",
    "traduci buongiorno in inglese", "come si fa la carbonara", "spiegami la fotosintesi", "raccontami una barzelletta",
    "scrivimi una poesia sul mare", "che differenza c'è tra un virus e un batterio?", "aiutami a scrivere una lettera di "
    "presentazione", "dammi un consiglio per dormire meglio", "chi era Leonardo da Vinci?", "ok perfetto",
    "non importa, lascia stare", "riassumimi la trama dei Promessi sposi", "qual è la capitale dell'Australia?",
    "perché il cielo è blu?", "correggi questa frase: io ho andato al mare", "buonanotte", "sei bravissima",
    "cosa significa resilienza?", "fammi un esempio di curriculum", "quanti giorni ha febbraio?",
]
