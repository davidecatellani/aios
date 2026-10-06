#!/bin/sh
# Controllo di greenboot dopo un aggiornamento di SoIA: copiare in
# /etc/greenboot/check/required.d/. Se fallisce più volte, greenboot riporta il
# sistema alla versione precedente da solo.
exec aios-aggiornamenti verifica
