# SoIA GSI: immagine generica per telefoni Treble arm64 (Motorola, Oppo, Samsung…).
$(call inherit-product, $(SRC_TARGET_DIR)/product/aosp_arm64.mk)
# aosp_arm64 applica gsi_release solo quando TARGET_PRODUCT è proprio aosp_arm64:
# il nostro prodotto deve includerlo esplicitamente, dopo la base.
$(call inherit-product, $(SRC_TARGET_DIR)/product/gsi_release.mk)
$(call inherit-product, vendor/aios/config/common.mk)
PRODUCT_SOONG_NAMESPACES += vendor/gapps/arm64 vendor/gapps/common vendor/gapps/overlay
LOCAL_PATH := vendor/gapps/arm64
$(call inherit-product, vendor/gapps/arm64/arm64-vendor.mk)

PRODUCT_NAME := aios_gsi_arm64
PRODUCT_DEVICE := aios_gsi_arm64
PRODUCT_BRAND := SoIA
PRODUCT_MODEL := SoIA GSI
