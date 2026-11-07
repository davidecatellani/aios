#!/system/bin/sh
# SoIA: valori di partenza per la batteria. Nova poi decide giorno per giorno.
settings put global adaptive_battery_management_enabled 1
settings put global app_standby_enabled 1
settings put global app_auto_restriction_enabled 1
settings put global cached_apps_freezer enabled
