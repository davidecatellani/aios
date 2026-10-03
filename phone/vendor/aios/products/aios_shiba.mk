# AIOS per Google Pixel 8 (shiba). Servono i binari del fornitore di Google per la
# stessa versione (https://developers.google.com/android/drivers), in vendor/google_devices.
$(call inherit-product, device/google/shusky/aosp_shiba.mk)
$(call inherit-product, vendor/aios/config/common.mk)

PRODUCT_NAME := aios_shiba
PRODUCT_BRAND := AIOS
PRODUCT_MODEL := Pixel 8 (AIOS)
