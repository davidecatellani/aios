# Energia: la batteria è un requisito di progetto (docs/ARCHITECTURE.md › Energia).
# Qui solo le impostazioni di base; le decisioni giorno per giorno le prende Nova.

# Obiettivo verificato a ogni versione (scripts/misura-batteria.sh): consumo in standby
PRODUCT_SYSTEM_EXT_PROPERTIES += \
    ro.aios.energia.standby_max_per_ora=1.0

# Valori predefiniti applicati al primo avvio da aios-energia.sh (init/aios.rc):
# risparmio adattivo, standby delle app, Doze anticipato per le app mai usate.
