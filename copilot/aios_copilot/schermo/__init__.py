"""Schermo SoIA: vedere e comandare gli altri dispositivi dell'utente nella stessa rete.

Un protocollo nostro, al posto del VNC. Il VNC manda rettangoli di pixel (compressi poco e male); qui lo
schermo viaggia come video H.265/H.264, codificato dalla scheda video quando c'è (NVENC, VA-API). Si
manda solo quando qualcosa cambia (cattura «a danno»), quindi uno schermo fermo costa quasi zero. A
parità di qualità servono da 10 a 50 volte meno dati del VNC, con meno di un fotogramma di ritardo
nella codifica.

Pezzi:
- protocollo.py  stretta di mano e canale cifrato (X25519 + firme Ed25519 dei certificati dell'identità,
                 poi AES-256-GCM), messaggi e loro formato
- scoperta.py    i dispositivi dell'utente si trovano da soli nella rete (annunci cifrati: gli estranei
                 vedono solo byte casuali)
- cattura.py     lo schermo → video compresso (wf-recorder con il codificatore migliore che c'è), anteprime
- ingresso.py    mouse e tastiera ricevuti → «premuti» sul PC (Hyprland + un dispositivo virtuale uinput)
- servizio.py    il servizio di ogni PC: annuncia, accetta i dispositivi dell'utente, trasmette
- visore.py      la finestra che mostra lo schermo di un altro PC e gli passa mouse e tastiera

Chi può collegarsi: solo un dispositivo con un certificato valido della stessa identità SoIA (la stessa
frase di recupero) e non revocato. Ogni collegamento ha chiavi nuove (segretezza in avanti) e chi è
guardato lo vede subito, con una notifica.
"""

PORT = 7340          # TCP: il collegamento
DISCOVERY_PORT = 7341  # UDP: gli annunci
