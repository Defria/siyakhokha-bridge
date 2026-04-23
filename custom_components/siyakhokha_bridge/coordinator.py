"""Data update coordinator for Siyakhokha Bridge."""

from __future__ import annotations

import logging
import os
import re
import json
from datetime import datetime
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SiyakhokhaApi, SiyakhokhaApiError
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TARIFF_AUTO_REFRESH,
    CONF_TARIFF_REFRESH_HOURS,
    CONF_USERNAME,
    DEFAULT_TARIFF_AUTO_REFRESH,
    DEFAULT_TARIFF_REFRESH_HOURS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SiyakhokhaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self, hass: HomeAssistant, entry_id: str, entry_data: dict[str, Any]
    ) -> None:
        self.entry_id = entry_id
        self._entry_data = entry_data
        self.api: SiyakhokhaApi | None = None
        self._pdf_dir = hass.config.path("www", "siyakhokha_bridge", entry_id)
        self._storage_dir = hass.config.path(".storage")
        self._tariff_cache_file = os.path.join(
            self._storage_dir, f"{DOMAIN}_{entry_id}_tariffs.json"
        )
        self._latest_local_pdf_url: str | None = None
        self._last_batch_submit_response: dict[str, Any] | None = None
        self._tariff_data: dict[str, Any] = {
            "status": "never_refreshed",
            "last_refresh": None,
            "rows": [],
            "source": {},
            "last_error": None,
        }

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=entry_data[CONF_SCAN_INTERVAL]),
        )

    @property
    def tariff_data(self) -> dict[str, Any]:
        return self._tariff_data

    def _load_tariff_cache(self) -> dict[str, Any]:
        try:
            if not os.path.exists(self._tariff_cache_file):
                return {
                    "status": "never_refreshed",
                    "last_refresh": None,
                    "rows": [],
                    "source": {},
                    "last_error": None,
                }
            with open(self._tariff_cache_file, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ValueError("invalid tariff cache shape")
            raw.setdefault("status", "ok")
            raw.setdefault("last_refresh", None)
            raw.setdefault("rows", [])
            raw.setdefault("source", {})
            raw.setdefault("last_error", None)
            return raw
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed loading tariff cache: %s", exc)
            return {
                "status": "cache_error",
                "last_refresh": None,
                "rows": [],
                "source": {},
                "last_error": str(exc),
            }

    def _save_tariff_cache(self) -> None:
        os.makedirs(self._storage_dir, exist_ok=True)
        with open(self._tariff_cache_file, "w", encoding="utf-8") as handle:
            json.dump(self._tariff_data, handle, ensure_ascii=True, indent=2)

    def _is_tariff_refresh_due(self) -> bool:
        auto_refresh = bool(
            self._entry_data.get(CONF_TARIFF_AUTO_REFRESH, DEFAULT_TARIFF_AUTO_REFRESH)
        )
        if not auto_refresh:
            return False

        refresh_hours = int(
            self._entry_data.get(
                CONF_TARIFF_REFRESH_HOURS, DEFAULT_TARIFF_REFRESH_HOURS
            )
        )
        last_refresh = self._tariff_data.get("last_refresh")
        if not last_refresh:
            return True
        try:
            last_dt = datetime.fromisoformat(str(last_refresh))
        except ValueError:
            return True
        return datetime.now() - last_dt >= timedelta(hours=refresh_hours)

    def _refresh_tariffs_sync(self, reason: str = "manual") -> None:
        if self.api is None:
            self.api = SiyakhokhaApi(self._entry_data[CONF_BASE_URL])

        try:
            data = self.api.fetch_latest_public_tariff_data()
            data["status"] = "ok"
            data["last_error"] = None
            data["refresh_reason"] = reason
            data["row_count"] = len(data.get("rows", []))
            self._tariff_data = data
            self._save_tariff_cache()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Tariff refresh failed: %s", exc)
            self._tariff_data["status"] = "error"
            self._tariff_data["last_error"] = str(exc)
            self._tariff_data["last_attempt"] = datetime.now().isoformat()
            self._tariff_data["refresh_reason"] = reason
            self._save_tariff_cache()

    async def async_refresh_tariffs(self, reason: str = "manual") -> None:
        await self.hass.async_add_executor_job(self._refresh_tariffs_sync, reason)
        await self.async_request_refresh()

    async def async_login_and_prime(self) -> None:
        """Create API client and warm up auth in executor."""

        def _sync_init() -> None:
            self.api = SiyakhokhaApi(self._entry_data[CONF_BASE_URL])
            self._tariff_data = self._load_tariff_cache()
            self.api.login(
                self._entry_data[CONF_USERNAME], self._entry_data[CONF_PASSWORD]
            )
            self.api.ensure_account_token()

        await self.hass.async_add_executor_job(_sync_init)

    async def _async_update_data(self) -> dict[str, Any]:
        def _sync_load() -> dict[str, Any]:
            if self.api is None:
                self.api = SiyakhokhaApi(self._entry_data[CONF_BASE_URL])

            os.makedirs(self._pdf_dir, exist_ok=True)
            self._latest_local_pdf_url = None

            self.api.login(
                self._entry_data[CONF_USERNAME], self._entry_data[CONF_PASSWORD]
            )
            self.api.ensure_account_token()

            page_size = 50
            max_pages = 40
            page_number = 1

            bills = self.api.get_bills(page_size=page_size, page_number=page_number)
            rows = bills.get("rows", []) if isinstance(bills, dict) else []
            all_rows: list[dict[str, Any]] = list(rows)

            total_rows = 0
            if isinstance(bills, dict):
                try:
                    total_rows = int(bills.get("total", 0) or 0)
                except (TypeError, ValueError):
                    total_rows = 0

            while (
                total_rows > 0
                and len(all_rows) < total_rows
                and page_number < max_pages
            ):
                page_number += 1
                page_data = self.api.get_bills(
                    page_size=page_size, page_number=page_number
                )
                page_rows = (
                    page_data.get("rows", []) if isinstance(page_data, dict) else []
                )
                if not page_rows:
                    break
                all_rows.extend(page_rows)

            try:
                payment_history = self.api.get_payment_history()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Failed loading payment history: %s", exc)
                payment_history = {}

            try:
                debit_orders = self.api.get_debit_orders()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Failed loading debit orders: %s", exc)
                debit_orders = {}

            try:
                batch_orders = self.api.get_batch_orders()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Failed loading batch orders: %s", exc)
                batch_orders = {}

            try:
                single_debit_context = self.api.get_single_debit_order_context()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Failed loading single debit context: %s", exc)
                single_debit_context = {}

            if self._is_tariff_refresh_due():
                self._refresh_tariffs_sync(reason="auto")

            filtered: list[dict[str, Any]] = []
            for r in all_rows:
                if str(r.get("AccountNumber", "")) != str(
                    self._entry_data[CONF_ACCOUNT_NUMBER]
                ):
                    continue
                item = dict(r)
                token = str(item.get("DownloadLink", "")).strip()
                if token and token.upper() != "UNPAYABLE":
                    item["DownloadAvailable"] = True
                    item["PortalPdfUrl"] = (
                        f"{self._entry_data[CONF_BASE_URL]}/Report/GenerateBill?q={token}"
                    )
                    ident = item.get("IdentificationNumber")
                    if ident:
                        item["HaPdfUrl"] = (
                            f"/api/siyakhokha_bridge/{self.entry_id}/{ident}.pdf"
                        )
                    else:
                        item["HaPdfUrl"] = None

                    bill_date = str(item.get("BillDate", "")).strip()
                    safe_date = (
                        re.sub(r"[^0-9-]", "", bill_date) if bill_date else "unknown"
                    )
                    file_name = f"{safe_date}.pdf"
                    file_path = os.path.join(self._pdf_dir, file_name)
                    local_url = f"/local/siyakhokha_bridge/{self.entry_id}/{file_name}"

                    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                        pdf_bytes = self.api.download_bill(token)
                        with open(file_path, "wb") as pdf_file:
                            pdf_file.write(pdf_bytes)

                    item["LocalPdfPath"] = file_path
                    item["LocalPdfUrl"] = local_url
                    if self._latest_local_pdf_url is None:
                        self._latest_local_pdf_url = local_url
                else:
                    item["DownloadAvailable"] = False
                    item["PortalPdfUrl"] = None
                    item["HaPdfUrl"] = None
                    item["LocalPdfPath"] = None
                    item["LocalPdfUrl"] = None
                filtered.append(item)
            return {
                "total": total_rows if total_rows > 0 else len(filtered),
                "rows": filtered,
                "latest_local_pdf_url": self._latest_local_pdf_url,
                "payment_history": payment_history,
                "debit_orders": debit_orders,
                "batch_orders": batch_orders,
                "single_debit_context": single_debit_context,
                "last_batch_submit_response": self._last_batch_submit_response,
                "tariffs": self._tariff_data.get("rows", []),
                "tariff_status": self._tariff_data.get("status", "never_refreshed"),
                "tariff_last_refresh": self._tariff_data.get("last_refresh"),
                "tariff_source": self._tariff_data.get("source", {}),
                "tariff_last_error": self._tariff_data.get("last_error"),
            }

        try:
            return await self.hass.async_add_executor_job(_sync_load)
        except SiyakhokhaApiError as exc:
            raise UpdateFailed(str(exc)) from exc

    def mark_batch_submit_response(self, payload: dict[str, Any]) -> None:
        self._last_batch_submit_response = {
            "status": payload.get("status", "unknown"),
            "submitted_at": datetime.now().isoformat(),
            "request": payload.get("request", {}),
            "response": payload.get("response", {}),
        }
