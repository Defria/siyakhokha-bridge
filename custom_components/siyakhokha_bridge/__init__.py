"""Siyakhokha Bridge integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import voluptuous as vol

from aiohttp import web
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import SiyakhokhaCoordinator

SERVICE_REFRESH = "refresh"
SERVICE_REFRESH_TARIFFS = "refresh_tariffs"
SERVICE_SUBMIT_BATCH_PAYMENT = "submit_batch_payment"
SERVICE_SUBMIT_SINGLE_DEBIT_ORDER = "submit_single_debit_order"
SERVICE_SUBMIT_SINGLE_DEBIT_ORDER_SIMPLE = "submit_single_debit_order_simple"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    merged_data = dict(entry.data)
    merged_data.update(entry.options)

    coordinator = SiyakhokhaCoordinator(hass, entry.entry_id, merged_data)
    await coordinator.async_login_and_prime()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):

        async def _handle_refresh(call: Any) -> None:
            entry_id = call.data.get("entry_id")
            if entry_id:
                target = hass.data[DOMAIN].get(entry_id)
                if isinstance(target, SiyakhokhaCoordinator):
                    await target.async_request_refresh()
                return

            for item in hass.data[DOMAIN].values():
                if isinstance(item, SiyakhokhaCoordinator):
                    await item.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH,
            _handle_refresh,
            schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_TARIFFS):

        async def _handle_refresh_tariffs(call: Any) -> None:
            entry_id = call.data.get("entry_id")
            if entry_id:
                target = hass.data[DOMAIN].get(entry_id)
                if isinstance(target, SiyakhokhaCoordinator):
                    await target.async_refresh_tariffs(reason="service")
                return

            for item in hass.data[DOMAIN].values():
                if isinstance(item, SiyakhokhaCoordinator):
                    await item.async_refresh_tariffs(reason="service")

        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_TARIFFS,
            _handle_refresh_tariffs,
            schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SUBMIT_BATCH_PAYMENT):

        async def _handle_submit_batch_payment(call: Any) -> None:
            entry_id = call.data.get("entry_id")
            confirm = bool(call.data.get("confirm", False))
            accounts = call.data.get("account_numbers") or []
            amounts = call.data.get("amounts") or []

            if not entry_id:
                raise ValueError("entry_id is required")
            if not confirm:
                raise ValueError(
                    "Confirmation required. Set confirm=true to submit payment."
                )

            coordinator_item = hass.data[DOMAIN].get(entry_id)
            if not isinstance(coordinator_item, SiyakhokhaCoordinator):
                raise ValueError("Integration entry not found")

            if len(accounts) != len(amounts):
                raise ValueError("account_numbers and amounts length mismatch")

            def _submit() -> None:
                if coordinator_item.api is None:
                    raise ValueError("API session not initialized")

                coordinator_item.api.login(
                    coordinator_item._entry_data["username"],
                    coordinator_item._entry_data["password"],
                )
                coordinator_item.api.ensure_account_token()
                context = coordinator_item.api.get_bulk_payment_context()
                result = coordinator_item.api.submit_bulk_payment(
                    account_numbers=[str(a) for a in accounts],
                    amounts=[float(x) for x in amounts],
                    context=context,
                )
                coordinator_item.mark_batch_submit_response(
                    {
                        "status": "submitted",
                        "request": {
                            "account_numbers": [str(a) for a in accounts],
                            "amounts": [float(x) for x in amounts],
                        },
                        "response": result,
                    }
                )

            await hass.async_add_executor_job(_submit)
            await coordinator_item.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_SUBMIT_BATCH_PAYMENT,
            _handle_submit_batch_payment,
            schema=vol.Schema(
                {
                    vol.Required("entry_id"): cv.string,
                    vol.Required("account_numbers"): list,
                    vol.Required("amounts"): list,
                    vol.Required("confirm"): cv.boolean,
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SUBMIT_SINGLE_DEBIT_ORDER):

        async def _handle_submit_single_debit_order(call: Any) -> None:
            entry_id = call.data.get("entry_id")
            confirm = bool(call.data.get("confirm", False))
            dry_run = bool(call.data.get("dry_run", True))

            if not entry_id:
                raise ValueError("entry_id is required")
            if not confirm:
                raise ValueError(
                    "Confirmation required. Set confirm=true to submit debit order."
                )

            coordinator_item = hass.data[DOMAIN].get(entry_id)
            if not isinstance(coordinator_item, SiyakhokhaCoordinator):
                raise ValueError("Integration entry not found")

            bank_account_id = int(call.data.get("bank_account_id"))
            account_id = int(call.data.get("account_id"))
            amount = float(call.data.get("amount"))
            strike_day = int(call.data.get("strike_day"))
            start_date = str(call.data.get("start_date"))
            is_recurring = bool(call.data.get("is_recurring", False))

            def _submit_single() -> None:
                if coordinator_item.api is None:
                    raise ValueError("API session not initialized")

                coordinator_item.api.login(
                    coordinator_item._entry_data["username"],
                    coordinator_item._entry_data["password"],
                )
                coordinator_item.api.ensure_account_token()
                result = coordinator_item.api.submit_single_debit_order(
                    bank_account_id=bank_account_id,
                    account_id=account_id,
                    amount=amount,
                    strike_day=strike_day,
                    start_date=start_date,
                    is_recurring=is_recurring,
                    dry_run=dry_run,
                )
                coordinator_item.mark_batch_submit_response(
                    {
                        "status": "dry_run" if dry_run else "submitted",
                        "request": {
                            "bank_account_id": bank_account_id,
                            "account_id": account_id,
                            "amount": amount,
                            "strike_day": strike_day,
                            "start_date": start_date,
                            "is_recurring": is_recurring,
                            "dry_run": dry_run,
                        },
                        "response": result,
                    }
                )

            await hass.async_add_executor_job(_submit_single)
            await coordinator_item.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_SUBMIT_SINGLE_DEBIT_ORDER,
            _handle_submit_single_debit_order,
            schema=vol.Schema(
                {
                    vol.Required("entry_id"): cv.string,
                    vol.Required("bank_account_id"): vol.All(vol.Coerce(int)),
                    vol.Required("account_id"): vol.All(vol.Coerce(int)),
                    vol.Required("amount"): vol.All(vol.Coerce(float)),
                    vol.Required("strike_day"): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=31)
                    ),
                    vol.Required("start_date"): cv.string,
                    vol.Optional("is_recurring", default=False): cv.boolean,
                    vol.Required("confirm"): cv.boolean,
                    vol.Optional("dry_run", default=True): cv.boolean,
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SUBMIT_SINGLE_DEBIT_ORDER_SIMPLE):

        async def _handle_submit_single_debit_order_simple(call: Any) -> None:
            entry_id = call.data.get("entry_id")
            confirm = bool(call.data.get("confirm", False))
            dry_run = bool(call.data.get("dry_run", True))

            if not entry_id:
                raise ValueError("entry_id is required")
            if not confirm:
                raise ValueError(
                    "Confirmation required. Set confirm=true to submit debit order."
                )

            coordinator_item = hass.data[DOMAIN].get(entry_id)
            if not isinstance(coordinator_item, SiyakhokhaCoordinator):
                raise ValueError("Integration entry not found")

            amount = float(call.data.get("amount"))
            override_bank_account_id = call.data.get("bank_account_id")
            override_account_id = call.data.get("account_id")

            def _submit_simple() -> None:
                if coordinator_item.api is None:
                    raise ValueError("API session not initialized")

                coordinator_item.api.login(
                    coordinator_item._entry_data["username"],
                    coordinator_item._entry_data["password"],
                )
                coordinator_item.api.ensure_account_token()

                context = coordinator_item.api.get_single_debit_order_context()
                resolved = (
                    context.get("resolved", {}) if isinstance(context, dict) else {}
                )

                bank_account_id = (
                    int(override_bank_account_id)
                    if override_bank_account_id is not None
                    else resolved.get("bank_account_id")
                )
                account_id = (
                    int(override_account_id)
                    if override_account_id is not None
                    else resolved.get("account_id")
                )

                if bank_account_id is None:
                    raise ValueError(
                        "No bank account id could be resolved from /DebitOrder context"
                    )
                if account_id is None:
                    raise ValueError(
                        "No municipal account id could be resolved from /DebitOrder context"
                    )

                strike_day = datetime.now().day
                start_date = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
                is_recurring = False

                result = coordinator_item.api.submit_single_debit_order(
                    bank_account_id=int(bank_account_id),
                    account_id=int(account_id),
                    amount=amount,
                    strike_day=strike_day,
                    start_date=start_date,
                    is_recurring=is_recurring,
                    dry_run=dry_run,
                    request_verification_token=str(
                        context.get("__RequestVerificationToken", "")
                    ),
                )
                coordinator_item.mark_batch_submit_response(
                    {
                        "status": "dry_run" if dry_run else "submitted",
                        "request": {
                            "bank_account_id": int(bank_account_id),
                            "account_id": int(account_id),
                            "amount": amount,
                            "strike_day": strike_day,
                            "start_date": start_date,
                            "is_recurring": is_recurring,
                            "dry_run": dry_run,
                            "source": "simple_submit",
                            "overrides": {
                                "bank_account_id": override_bank_account_id,
                                "account_id": override_account_id,
                            },
                        },
                        "response": result,
                    }
                )

            await hass.async_add_executor_job(_submit_simple)
            await coordinator_item.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_SUBMIT_SINGLE_DEBIT_ORDER_SIMPLE,
            _handle_submit_single_debit_order_simple,
            schema=vol.Schema(
                {
                    vol.Required("entry_id"): cv.string,
                    vol.Required("amount"): vol.All(vol.Coerce(float)),
                    vol.Optional("bank_account_id"): vol.All(vol.Coerce(int)),
                    vol.Optional("account_id"): vol.All(vol.Coerce(int)),
                    vol.Required("confirm"): cv.boolean,
                    vol.Optional("dry_run", default=True): cv.boolean,
                }
            ),
        )

    if not hass.data[DOMAIN].get("view_registered"):
        from homeassistant.components.http import HomeAssistantView

        class SiyakhokhaPdfView(HomeAssistantView):
            url = "/api/siyakhokha_bridge/{entry_id}/{identification}.pdf"
            name = "api:siyakhokha_bridge:pdf"
            requires_auth = True

            def __init__(self, _hass: HomeAssistant) -> None:
                self.hass = _hass

            async def get(
                self, request: web.Request, entry_id: str, identification: str
            ) -> web.StreamResponse:
                coordinator_item: SiyakhokhaCoordinator | None = self.hass.data[
                    DOMAIN
                ].get(entry_id)
                if coordinator_item is None:
                    return web.Response(status=404, text="Integration entry not found")

                await coordinator_item.async_request_refresh()
                rows = (
                    coordinator_item.data.get("rows", [])
                    if coordinator_item.data
                    else []
                )
                target = None
                for row in rows:
                    if str(row.get("IdentificationNumber")) == identification:
                        target = row
                        break

                if not target:
                    return web.Response(status=404, text="Bill not found")

                token = str(target.get("DownloadLink", "")).strip()
                if not token or token.upper() == "UNPAYABLE":
                    return web.Response(
                        status=404, text="Bill PDF not available for this record"
                    )

                try:
                    pdf_bytes = await self.hass.async_add_executor_job(
                        coordinator_item.api.download_bill, token
                    )
                except Exception as exc:  # noqa: BLE001
                    return web.Response(status=502, text=f"Failed to fetch PDF: {exc}")

                return web.Response(
                    body=pdf_bytes,
                    content_type="application/pdf",
                    headers={
                        "Content-Disposition": (
                            f'inline; filename="siyakhokha_{identification}.pdf"'
                        )
                    },
                )

        class SiyakhokhaLatestPdfView(HomeAssistantView):
            url = "/api/siyakhokha_bridge/{entry_id}/latest.pdf"
            name = "api:siyakhokha_bridge:latest_pdf"
            requires_auth = True

            def __init__(self, _hass: HomeAssistant) -> None:
                self.hass = _hass

            async def get(
                self, request: web.Request, entry_id: str
            ) -> web.StreamResponse:
                coordinator_item: SiyakhokhaCoordinator | None = self.hass.data[
                    DOMAIN
                ].get(entry_id)
                if coordinator_item is None:
                    return web.Response(status=404, text="Integration entry not found")

                await coordinator_item.async_request_refresh()
                local_url = coordinator_item.data.get("latest_local_pdf_url")
                if not local_url:
                    return web.Response(status=404, text="No local PDF available")

                local_path = self.hass.config.path(local_url.replace("/local/", "www/"))
                if not local_path or not local_path.endswith(".pdf"):
                    return web.Response(status=404, text="Invalid local PDF path")

                try:
                    with open(local_path, "rb") as fh:
                        pdf_bytes = fh.read()
                except FileNotFoundError:
                    return web.Response(status=404, text="Local PDF file not found")

                return web.Response(
                    body=pdf_bytes,
                    content_type="application/pdf",
                    headers={
                        "Content-Disposition": 'inline; filename="latest_bill.pdf"'
                    },
                )

        hass.http.register_view(SiyakhokhaPdfView(hass))
        hass.http.register_view(SiyakhokhaLatestPdfView(hass))
        hass.data[DOMAIN]["view_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        has_entries = any(
            isinstance(v, SiyakhokhaCoordinator) for v in hass.data[DOMAIN].values()
        )
        if not has_entries and hass.services.has_service(DOMAIN, SERVICE_REFRESH):
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
        if not has_entries and hass.services.has_service(
            DOMAIN, SERVICE_REFRESH_TARIFFS
        ):
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH_TARIFFS)
        if not has_entries and hass.services.has_service(
            DOMAIN, SERVICE_SUBMIT_BATCH_PAYMENT
        ):
            hass.services.async_remove(DOMAIN, SERVICE_SUBMIT_BATCH_PAYMENT)
        if not has_entries and hass.services.has_service(
            DOMAIN, SERVICE_SUBMIT_SINGLE_DEBIT_ORDER
        ):
            hass.services.async_remove(DOMAIN, SERVICE_SUBMIT_SINGLE_DEBIT_ORDER)
        if not has_entries and hass.services.has_service(
            DOMAIN, SERVICE_SUBMIT_SINGLE_DEBIT_ORDER_SIMPLE
        ):
            hass.services.async_remove(DOMAIN, SERVICE_SUBMIT_SINGLE_DEBIT_ORDER_SIMPLE)
    return unload_ok
