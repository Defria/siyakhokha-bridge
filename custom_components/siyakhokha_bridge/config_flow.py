"""Config flow for Siyakhokha Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .api import SiyakhokhaApi, SiyakhokhaApiError
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TARIFF_AUTO_REFRESH,
    CONF_TARIFF_REFRESH_HOURS,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_TARIFF_AUTO_REFRESH,
    DEFAULT_TARIFF_REFRESH_HOURS,
    DOMAIN,
)


def _scan_interval_schema(default_scan_interval: int) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SCAN_INTERVAL, default=default_scan_interval): vol.All(
                vol.Coerce(int), vol.Range(min=1440, max=44640)
            )
        }
    )


class SiyakhokhaBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return SiyakhokhaBridgeOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_USERNAME]}_{user_input[CONF_ACCOUNT_NUMBER]}"
            )
            self._abort_if_unique_id_configured()

            def _validate() -> None:
                api = SiyakhokhaApi(user_input[CONF_BASE_URL])
                api.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                api.ensure_account_token()
                data = api.get_bills(page_size=10, page_number=1)
                if not isinstance(data, dict) or "rows" not in data:
                    raise SiyakhokhaApiError("Unexpected response from bills endpoint")

            try:
                await self.hass.async_add_executor_job(_validate)
            except SiyakhokhaApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="Siyakhokha Bridge", data=user_input
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ACCOUNT_NUMBER): str,
                vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                **_scan_interval_schema(DEFAULT_SCAN_INTERVAL_MINUTES).schema,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class SiyakhokhaBridgeOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = int(
            self._config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self._config_entry.data.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                ),
            )
        )

        current_tariff_auto_refresh = bool(
            self._config_entry.options.get(
                CONF_TARIFF_AUTO_REFRESH,
                self._config_entry.data.get(
                    CONF_TARIFF_AUTO_REFRESH, DEFAULT_TARIFF_AUTO_REFRESH
                ),
            )
        )
        current_tariff_refresh_hours = int(
            self._config_entry.options.get(
                CONF_TARIFF_REFRESH_HOURS,
                self._config_entry.data.get(
                    CONF_TARIFF_REFRESH_HOURS, DEFAULT_TARIFF_REFRESH_HOURS
                ),
            )
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                        vol.Coerce(int), vol.Range(min=1440, max=44640)
                    ),
                    vol.Required(
                        CONF_TARIFF_AUTO_REFRESH,
                        default=current_tariff_auto_refresh,
                    ): bool,
                    vol.Required(
                        CONF_TARIFF_REFRESH_HOURS,
                        default=current_tariff_refresh_hours,
                    ): vol.All(vol.Coerce(int), vol.Range(min=24, max=8760)),
                }
            ),
        )
