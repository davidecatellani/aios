"""Catalogazione e importanza delle mail, tutto in locale.

Ogni mail riceve una categoria e un punteggio di importanza (0-100). I segnali:
- intestazioni (List-Unsubscribe = newsletter, mittenti "no-reply" = notifiche);
- relazione con il mittente (gli hai mai scritto? quante volte?);
- servizi noti (ricevute e abbonamenti);
- parole del contenuto (urgente, scadenza, fattura, offerta...);
- le correzioni dell'utente, che valgono per tutto il mittente (o il dominio).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORIES = {
    "importanti": "⭐ Importanti",
    "personali": "👤 Personali",
    "lavoro": "💼 Lavoro",
    "ricevute": "🧾 Ricevute e abbonamenti",
    "newsletter": "📰 Newsletter e promozioni",
    "notifiche": "🔔 Notifiche",
    "altro": "📥 Altro",
}
NOTIFY_THRESHOLD = 70  # da qui in su la mail viene notificata

RECEIPT = re.compile(
    r"\b(ricevuta|fattura|conferma (?:d(?:el|ell')?\s*)?(?:ordine|pagamento|acquisto)|pagamento ricevuto|abbonamento|"
    r"rinnovo|rinnovato|addebito|il tuo ordine|order confirmation|receipt|invoice|subscription|payment|renewal|billing)\b",
    re.I,
)
PROMO = re.compile(r"\b(offerta|sconto|promo|saldi|coupon|% di sconto|black friday|newsletter|offer|discount|sale|deal)\b", re.I)
URGENT = re.compile(r"\b(urgente|urgent|importante|asap|scadenza|scade|entro (?:oggi|domani|il)|sollecito|ultimo avviso|"
                    r"deadline|action required|azione richiesta|appuntamento|convocazione|udienza)\b", re.I)
WORK = re.compile(r"\b(riunione|meeting|progetto|preventivo|cliente|contratto|offerta tecnica|report|consegna|ticket)\b", re.I)
NOREPLY = re.compile(r"^(?:no[-_.]?reply|do[-_.]?not[-_.]?reply|notifications?|notifiche|alert|info|news|newsletter|marketing|mailer)\b", re.I)
SECURITY = re.compile(r"\b(codice di verifica|verification code|accesso (?:insolito|sospetto)|nuovo accesso|password|sign-in|security alert)\b", re.I)
FREE_MAIL = {"gmail.com", "outlook.com", "hotmail.com", "hotmail.it", "libero.it", "yahoo.com", "yahoo.it", "icloud.com",
             "live.com", "live.it", "virgilio.it", "tiscali.it", "alice.it"}


@dataclass
class Verdict:
    category: str
    importance: int
    reason: str


def classify(sender: str, subject: str, body: str, headers: dict[str, str], *, sent_to_count: int = 0,
             own_domain: str = "", known_service: str | None = None, override: str | None = None) -> Verdict:
    text = f"{subject}\n{body[:3000]}"
    local = sender.split("@")[0]
    domain = sender.rsplit("@", 1)[-1].lower()
    bulk = "list-unsubscribe" in headers or headers.get("precedence", "").lower() in ("bulk", "list")
    machine = bool(NOREPLY.match(local)) or bulk or "auto-submitted" in headers

    if override:
        category, reason = override, "come mi hai indicato"
    elif known_service and RECEIPT.search(text):
        category, reason = "ricevute", f"ricevuta di {known_service}"
    elif SECURITY.search(text) and machine:
        category, reason = "notifiche", "avviso di sicurezza"
    elif bulk or (machine and PROMO.search(text)):
        category, reason = "newsletter", "invio di massa"
    elif machine and RECEIPT.search(text):
        category, reason = "ricevute", "ricevuta o pagamento"
    elif machine:
        category, reason = "notifiche", "messaggio automatico"
    elif own_domain and domain == own_domain or WORK.search(text) and domain not in FREE_MAIL:
        category, reason = "lavoro", "lavoro"
    else:
        category, reason = "personali", "scritta da una persona"

    score = {"personali": 45, "lavoro": 50, "ricevute": 30, "notifiche": 20, "newsletter": 5, "altro": 25,
             "importanti": 80}.get(category, 25)
    if sent_to_count:
        score += min(30, 10 + 4 * sent_to_count)  # persone a cui scrivi: contano di più
        reason += ", gli hai già scritto"
    if URGENT.search(text) and category != "newsletter":
        score += 25
        reason += ", sembra urgente"
    if SECURITY.search(text):
        score += 50  # un accesso sospetto va visto subito, anche se il messaggio è automatico
    score = max(0, min(100, score))
    if score >= 85 and category in ("personali", "lavoro") and not override:
        category = "importanti"
    return Verdict(category, score, reason)
