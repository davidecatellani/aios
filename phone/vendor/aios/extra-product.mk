# Collegato come vendor/extra/product.mk sulle basi LineageOS (scripts/prepara.sh):
# LineageOS lo include da solo, così SoIA si aggiunge al prodotto del telefono.
$(call inherit-product, vendor/aios/config/common.mk)
