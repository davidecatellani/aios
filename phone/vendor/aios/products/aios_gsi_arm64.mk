# AIOS GSI: immagine generica per telefoni Treble arm64 (Motorola, Oppo, Samsung…).
$(call inherit-product, $(SRC_TARGET_DIR)/product/gsi_release.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/aosp_arm64.mk)
$(call inherit-product, vendor/aios/config/common.mk)

PRODUCT_NAME := aios_gsi_arm64
PRODUCT_DEVICE := generic_arm64
PRODUCT_BRAND := AIOS
PRODUCT_MODEL := AIOS GSI
