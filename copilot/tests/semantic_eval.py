"""Banco di prova del livello 1, con frasi che NON compaiono nel catalogo.

    python tests/semantic_eval.py      stampa precisione, copertura e latenza
"""

# (frase, azione attesa)
IN_SCOPE = [
    ("alzami un po' il volume", "volume_up"),
    ("il volume è troppo basso", "volume_up"),
    ("aumenta l'audio per favore", "volume_up"),
    ("metti il volume più alto", "volume_up"),
    ("abbassa un po' l'audio", "volume_down"),
    ("diminuisci l'audio", "volume_down"),
    ("volume più basso", "volume_down"),
    ("metti tutto in silenzio", "mute"),
    ("azzera l'audio", "mute"),
    ("disattiva i suoni", "mute"),
    ("rimetti il sonoro", "unmute"),
    ("più luminosità", "brightness_up"),
    ("aumenta un po' la luminosità dello schermo", "brightness_up"),
    ("riduci la luminosità", "brightness_down"),
    ("schermo troppo luminoso, abbassa", "brightness_down"),
    ("metti la modalità scura", "theme_dark"),
    ("voglio il tema scuro", "theme_dark"),
    ("attiva la dark mode", "theme_dark"),
    ("rimetti il tema chiaro", "theme_light"),
    ("disattiva la modalità scura", "theme_light"),
    ("accendimi il wifi", "wifi_on"),
    ("riattiva il wi-fi", "wifi_on"),
    ("spegnimi il wifi", "wifi_off"),
    ("disattiva la connessione wifi", "wifi_off"),
    ("attivami il bluetooth", "bluetooth_on"),
    ("disattivami il bluetooth", "bluetooth_off"),
    ("fammi sentire un po' di musica", "media_play"),
    ("metti della musica", "media_play"),
    ("fai ripartire la canzone", "media_play"),
    ("ferma la canzone", "media_pause"),
    ("metti in pausa la musica", "media_pause"),
    ("passa alla prossima canzone", "media_next"),
    ("salta il brano", "media_next"),
    ("torna al brano precedente", "media_previous"),
    ("fammi uno screenshot", "screenshot"),
    ("cattura una schermata", "screenshot"),
    ("blocca lo schermo del pc", "lock"),
    ("blocca il computer che sto uscendo", "lock"),
    ("metti il pc in sospensione", "suspend"),
    ("manda in standby il computer", "suspend"),
    ("spegni il computer adesso", "poweroff"),
    ("arresta il pc", "poweroff"),
    ("riavvia il computer per favore", "reboot"),
    ("fai ripartire il sistema", "reboot"),
    ("quanto spazio ho sul disco", "system_info"),
    ("quanta memoria ram ho", "system_info"),
    ("fammi vedere i download", "open_downloads"),
    ("dove ho messo i file scaricati", "open_downloads"),
    ("mostrami le mie immagini", "open_pictures"),
    ("apri le mie foto", "open_pictures"),
    ("fammi vedere i miei documenti", "open_documents"),
    ("apri i miei video", "open_videos"),
    ("turn up the volume", "volume_up"),
    ("turn off the wifi", "wifi_off"),
    ("lock my screen", "lock"),
    # Secondo set, scritto dopo la messa a punto e misurato alla cieca una volta
    # (precisione 95%, copertura 81%, 0/12 falsi positivi) prima di aggiungerlo qui.
    ("non si sente niente, alza", "volume_up"), ("volume al massimo", "volume_up"),
    ("aumentami il volume", "volume_up"), ("abbassami il volume che è tardi", "volume_down"),
    ("troppo alto il volume", "volume_down"), ("zitto", "mute"), ("togli i suoni", "mute"),
    ("schermo più scuro", "brightness_down"), ("alzami la luminosità", "brightness_up"),
    ("passa alla modalità notte", "theme_dark"), ("metti i colori scuri", "theme_dark"),
    ("tema chiaro per favore", "theme_light"), ("attiva il wireless", "wifi_on"),
    ("stacca il wifi", "wifi_off"), ("spegni bluetooth", "bluetooth_off"),
    ("mettimi un po' di musica", "media_play"), ("stop alla musica", "media_pause"),
    ("prossimo brano", "media_next"), ("schermata dello schermo", "screenshot"),
    ("blocca tutto", "lock"), ("standby", "suspend"), ("spegni il dispositivo", "poweroff"),
    ("riavvia", "reboot"), ("quanta memoria libera c'è", "system_info"),
    ("dov'è la roba che ho scaricato", "open_downloads"), ("le mie foto", "open_pictures"),
    ("documenti", "open_documents"),
]

# Frasi che il livello 1 NON deve eseguire: vanno lasciate all'LLM.
OUT_OF_SCOPE = [
    "scrivi una mail a marco",
    "che tempo fa domani a torino",
    "chi ha vinto la partita ieri",
    "riassumi questo documento",
    "quanto fa 25 per 4",
    "traduci ciao in inglese",
    "prenota un tavolo per due stasera",
    "come si fa la carbonara",
    "ricordami di chiamare la mamma alle cinque",
    "apri il file della tesi e correggi gli errori",
    "installa un programma per i pdf",
    "cerca un volo per londra",
    "non spegnere il computer",
    "non alzare il volume",
    "non attivare il wifi",
    "perché il wifi è così lento",
    "qual è la password del wifi",
    "che canzone è questa",
    "quanto costa un computer nuovo",
    "spegni il computer di marco",
    "spegni la luce in cucina",
    "alza la tapparella",
    "abbassa il prezzo",
    "metti la sveglia alle 7",
    "blocca il numero di marco",
    "riavvia il router di casa",
    "che musica ascolta mia figlia",
    "foto di gatti su internet",
    "non mettere in pausa",
    "scarica le foto dal telefono",
    "manda le foto a giulia",
    "il wifi non funziona",
]


def evaluate(router):
    import time

    correct = wrong = abstained = 0
    errors = []
    start = time.perf_counter()
    for text, expected in IN_SCOPE:
        intent = router.match(text)
        if intent is None:
            abstained += 1
            errors.append(("astenuto", text, expected))
            continue
        spec = next(s for s in router.catalog if s.name == expected).build()
        if intent == spec:
            correct += 1
        else:
            wrong += 1
            errors.append(("SBAGLIATO", text, f"{expected} → {intent}"))
    false_accepts = [t for t in OUT_OF_SCOPE if router.match(t) is not None]
    elapsed = time.perf_counter() - start
    answered = correct + wrong
    return {
        "precision": correct / answered if answered else 1.0,
        "coverage": answered / len(IN_SCOPE),
        "false_accepts": false_accepts,
        "ms_per_query": elapsed * 1000 / (len(IN_SCOPE) + len(OUT_OF_SCOPE)),
        "errors": errors,
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from aios_copilot.semantic import default_router

    r = evaluate(default_router())
    for kind, text, detail in r["errors"]:
        print(f"  {kind:9} «{text}»  ({detail})")
    for text in r["false_accepts"]:
        print(f"  FALSO SÌ  «{text}»")
    print(
        f"\nprecisione {r['precision']:.0%} · copertura {r['coverage']:.0%} · "
        f"falsi positivi {len(r['false_accepts'])}/{len(OUT_OF_SCOPE)} · {r['ms_per_query']:.2f} ms/frase"
    )
