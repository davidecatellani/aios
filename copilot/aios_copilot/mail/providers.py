"""Impostazioni dei principali servizi di posta.

I nomi dei server sono quelli pubblicati dai provider; se uno cambia, l'account si
può sempre configurare a mano (host e porte). OAuth: Gmail e Outlook lo richiedono
(Outlook.com non accetta più password); serve un "client id" registrato da SoIA
presso Google/Microsoft, letto da ~/.config/aios/oauth.json o dalle variabili
AIOS_GOOGLE_CLIENT_ID / AIOS_GOOGLE_CLIENT_SECRET / AIOS_MICROSOFT_CLIENT_ID.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OAuthConfig:
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    extra_params: tuple[tuple[str, str], ...] = ()
    env_prefix: str = ""


@dataclass(frozen=True)
class Provider:
    name: str
    imap_host: str
    smtp_host: str
    imap_port: int = 993
    smtp_port: int = 465  # 465 = SSL implicito, 587 = STARTTLS
    domains: tuple[str, ...] = ()
    oauth: OAuthConfig | None = None
    password_help: str = ""
    saves_sent: bool = False  # il server salva da solo le mail inviate (Gmail)


GOOGLE = OAuthConfig(
    "https://accounts.google.com/o/oauth2/v2/auth",
    "https://oauth2.googleapis.com/token",
    ("https://mail.google.com/",),
    (("access_type", "offline"), ("prompt", "consent")),
    "AIOS_GOOGLE",
)
MICROSOFT = OAuthConfig(
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    ("https://outlook.office.com/IMAP.AccessAsUser.All", "https://outlook.office.com/SMTP.Send", "offline_access"),
    (),
    "AIOS_MICROSOFT",
)

PROVIDERS: dict[str, Provider] = {
    "gmail": Provider("Gmail", "imap.gmail.com", "smtp.gmail.com", domains=("gmail.com", "googlemail.com"),
                      oauth=GOOGLE, saves_sent=True,
                      password_help="Con la verifica in due passaggi attiva, crea una «password per le app» "
                                    "in myaccount.google.com → Sicurezza."),
    "outlook": Provider("Outlook", "outlook.office365.com", "smtp-mail.outlook.com", smtp_port=587,
                        domains=("outlook.com", "outlook.it", "hotmail.com", "hotmail.it", "live.com", "live.it", "msn.com"),
                        oauth=MICROSOFT, saves_sent=True,
                        password_help="Outlook.com richiede l'accesso con Microsoft (OAuth): le password non sono accettate."),
    "yahoo": Provider("Yahoo", "imap.mail.yahoo.com", "smtp.mail.yahoo.com", domains=("yahoo.com", "yahoo.it", "ymail.com"),
                      password_help="Crea una «password per le app» nelle impostazioni di sicurezza dell'account Yahoo."),
    "icloud": Provider("iCloud", "imap.mail.me.com", "smtp.mail.me.com", smtp_port=587, domains=("icloud.com", "me.com", "mac.com"),
                       password_help="Crea una «password specifica per l'app» su account.apple.com."),
    "libero": Provider("Libero", "imapmail.libero.it", "smtp.libero.it", domains=("libero.it", "inwind.it", "iol.it", "blu.it")),
    "virgilio": Provider("Virgilio", "in.virgilio.it", "out.virgilio.it", domains=("virgilio.it", "tim.it")),
}


def provider_for(address: str) -> tuple[str, Provider] | None:
    domain = address.rsplit("@", 1)[-1].lower().strip()
    for key, p in PROVIDERS.items():
        if domain in p.domains:
            return key, p
    return None
