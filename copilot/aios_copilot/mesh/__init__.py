"""Collegamento tra i dispositivi dell'utente: telefono e PC.

- phone.py   telefono vicino (KDE Connect): scoperta, abbinamento, squillo, invio file
- calls.py   chiamate dal PC: il PC fa da vivavoce Bluetooth del telefono (HFP + oFono)
- files.py   i file del PC dal telefono: pagina sicura in rete locale, attiva solo
             quando il telefono è vicino
- service.py servizio che collega e scollega tutto da solo
"""
