"""Constants for Siyakhokha Bridge."""

DOMAIN = "siyakhokha_bridge"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCOUNT_NUMBER = "account_number"
CONF_BASE_URL = "base_url"
CONF_SCAN_INTERVAL = "scan_interval_minutes"
CONF_TARIFF_AUTO_REFRESH = "tariff_auto_refresh"
CONF_TARIFF_REFRESH_HOURS = "tariff_refresh_hours"

DEFAULT_BASE_URL = "https://siyakhokha.ekurhuleni.gov.za"
DEFAULT_SCAN_INTERVAL_MINUTES = 180
DEFAULT_TARIFF_AUTO_REFRESH = False
DEFAULT_TARIFF_REFRESH_HOURS = 168

# Allowed range for the polling interval (in minutes).
# Floor: 30 min (avoid hammering the portal). Ceiling: 44640 min (~31 days).
SCAN_INTERVAL_MIN = 30
SCAN_INTERVAL_MAX = 44640

PLATFORMS = ["sensor", "button"]
