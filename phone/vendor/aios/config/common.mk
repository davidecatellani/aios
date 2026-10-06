# Ciò che SoIA aggiunge a qualsiasi base (AOSP o LineageOS).

# Nova (app di sistema) e la sovrapposizione delle impostazioni di sistema
PRODUCT_PACKAGES += \
    Nova \
    AiosFrameworkOverlay \
    aios-energia.sh

PRODUCT_COPY_FILES += \
    vendor/aios/init/aios.rc:$(TARGET_COPY_OUT_SYSTEM_EXT)/etc/init/aios.rc

# Nova è l'assistente predefinito (pressione lunga del tasto di accensione)
PRODUCT_SYSTEM_EXT_PROPERTIES += \
    ro.aios.version=0.1 \
    ro.aios.assistant=org.aios.nova

# Energia: vedi energy.mk
$(call inherit-product, vendor/aios/config/energy.mk)

# Regole SELinux di SoIA
SYSTEM_EXT_PRIVATE_SEPOLICY_DIRS += vendor/aios/sepolicy
