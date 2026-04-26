"""Config flow for Siyakhokha Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

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
    SCAN_INTERVAL_MAX,
    SCAN_INTERVAL_MIN,
)


class SiyakhokhaBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, Any] = {}
        self._discovered_accounts: list[dict[str, Any]] = []

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
            def _validate_and_discover() -> list[dict[str, Any]]:
                api = SiyakhokhaApi(user_input[CONF_BASE_URL])
                api.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                accounts = api.get_account_list()
                if not accounts:
                    raise SiyakhokhaApiError(
                        "Login OK but no accounts returned by /Profile/LoadAccounts"
                    )
                return accounts

            try:
                accounts = await self.hass.async_add_executor_job(_validate_and_discover)
            except SiyakhokhaApiError:
                errors["base"] = "cannot_connect"
            else:
                self._credentials = dict(user_input)
                self._discovered_accounts = accounts
                if len(accounts) == 1:
                    return await self._finish_with_account(
                        accounts[0]["account_number"]
                    )
                return await self.async_step_pick_account()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_MINUTES
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=SCAN_INTERVAL_MIN, max=SCAN_INTERVAL_MAX),
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_pick_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return await self._finish_with_account(user_input[CONF_ACCOUNT_NUMBER])

        options = [
            selector.SelectOptionDict(
                value=str(acct["account_number"]),
                label=str(acct.get("description") or acct["account_number"]),
            )
            for acct in self._discovered_accounts
            if acct.get("account_number")
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_ACCOUNT_NUMBER): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="pick_account", data_schema=schema)

    async def _finish_with_account(self, account_number: str) -> FlowResult:
        await self.async_set_unique_id(
            f"{self._credentials[CONF_USERNAME]}_{account_number}"
        )
        self._abort_if_unique_id_configured()
        data = dict(self._credentials)
        data[CONF_ACCOUNT_NUMBER] = str(account_number)
        return self.async_create_entry(title="Siyakhokha Bridge", data=data)


class SiyakhokhaBridgeOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._discovered_accounts: list[dict[str, Any]] = []

    def _current(self, key: str, default: Any) -> Any:
        return self._config_entry.options.get(
            key, self._config_entry.data.get(key, default)
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate credentials + re-discover account list.
            def _validate() -> list[dict[str, Any]]:
                api = SiyakhokhaApi(user_input[CONF_BASE_URL])
                api.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                return api.get_account_list()

            try:
                accounts = await self.hass.async_add_executor_job(_validate)
            except SiyakhokhaApiError:
                errors["base"] = "cannot_connect"
            else:
                target_account = str(user_input[CONF_ACCOUNT_NUMBER]).strip()
                known = {
                    str(a.get("account_number"))
                    for a in accounts
                    if a.get("account_number")
                }
                if target_account not in known:
                    errors[CONF_ACCOUNT_NUMBER] = "unknown_account"
                else:
                    return self.async_create_entry(title="", data=user_input)

        # Build form with current values as defaults
        username_default = self._current(CONF_USERNAME, "")
        password_default = self._current(CONF_PASSWORD, "")
        account_default = self._current(CONF_ACCOUNT_NUMBER, "")
        base_url_default = self._current(CONF_BASE_URL, DEFAULT_BASE_URL)
        scan_interval_default = int(
            self._current(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
        )
        tariff_auto_default = bool(
            self._current(CONF_TARIFF_AUTO_REFRESH, DEFAULT_TARIFF_AUTO_REFRESH)
        )
        tariff_hours_default = int(
            self._current(CONF_TARIFF_REFRESH_HOURS, DEFAULT_TARIFF_REFRESH_HOURS)
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=username_default): str,
                vol.Required(CONF_PASSWORD, default=password_default): str,
                vol.Required(
                    CONF_ACCOUNT_NUMBER, default=str(account_default)
                ): str,
                vol.Required(
                    CONF_BASE_URL, default=base_url_default
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=scan_interval_default
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=SCAN_INTERVAL_MIN, max=SCAN_INTERVAL_MAX),
                ),
                vol.Required(
                    CONF_TARIFF_AUTO_REFRESH, default=tariff_auto_default
                ): bool,
                vol.Required(
                    CONF_TARIFF_REFRESH_HOURS, default=tariff_hours_default
                ): vol.All(vol.Coerce(int), vol.Range(min=24, max=8760)),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
