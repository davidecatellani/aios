# Prodotti di AIOS su base AOSP (Pixel e GSI). Su base LineageOS si usa invece
# vendor/extra/product.mk (vedi scripts/prepara.sh): resta il prodotto di LineageOS
# per quel telefono, con AIOS aggiunto sopra.
PRODUCT_MAKEFILES := \
    $(LOCAL_DIR)/products/aios_gsi_arm64.mk \
    $(LOCAL_DIR)/products/aios_shiba.mk

COMMON_LUNCH_CHOICES := \
    aios_gsi_arm64-trunk_staging-userdebug \
    aios_shiba-trunk_staging-userdebug
