"""Sensor platform for Siyakhokha Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT_NUMBER, DOMAIN
from .coordinator import SiyakhokhaCoordinator


@dataclass
class BillField:
    key: str
    name: str


LATEST_BILL_AMOUNT = BillField("latest_bill_amount", "Latest Bill Amount")
LATEST_BILL_DATE = BillField("latest_bill_date", "Latest Bill Date")
LATEST_BILL_PDF_URL = BillField("latest_bill_pdf_url", "Latest Bill PDF URL")
LAST_BATCH_SUBMIT_STATUS = BillField(
    "last_batch_submit_status", "Last Batch Submit Status"
)
TARIFF_STATUS = BillField("tariff_status", "Tariff Status")
TARIFF_LAST_REFRESH = BillField("tariff_last_refresh", "Tariff Last Refresh")
TARIFF_SOURCE_DOCUMENT = BillField("tariff_source_document", "Tariff Source Document")
TARIFF_A2_BLOCK_1_EXCL = BillField(
    "tariff_a2_block_0_50_excl", "Tariff A2 Block 0-50 (Excl VAT)"
)
TARIFF_A2_BLOCK_1_INCL = BillField(
    "tariff_a2_block_0_50_incl", "Tariff A2 Block 0-50 (Incl VAT)"
)
CURRENT_BALANCE = BillField("current_balance", "Current Balance")
BALANCE_DUE_DATE = BillField("balance_due_date", "Balance Due Date")
NEXT_RUN_DATE = BillField("next_run_date", "Next Debit Run Date")
ACCOUNT_DESCRIPTION = BillField("account_description", "Account Description")
ACCOUNT_HOLDER = BillField("account_holder", "Account Holder")
CUSTOMER_NAME = BillField("customer_name", "Customer Name")
CUSTOMER_EMAIL = BillField("customer_email", "Customer Email")
CUSTOMER_PHONE = BillField("customer_phone", "Customer Phone")
CUSTOMER_ADDRESS = BillField("customer_address", "Customer Address")


def _join_address(parts: list[Any] | None) -> str | None:
    if not parts:
        return None
    bits = [str(p).strip() for p in parts if p is not None and str(p).strip()]
    return ", ".join(bits) if bits else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SiyakhokhaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SiyakhokhaBillSensor(coordinator, entry, LATEST_BILL_AMOUNT),
            SiyakhokhaBillSensor(coordinator, entry, LATEST_BILL_DATE),
            SiyakhokhaBillSensor(coordinator, entry, LATEST_BILL_PDF_URL),
            SiyakhokhaBillSensor(coordinator, entry, LAST_BATCH_SUBMIT_STATUS),
            SiyakhokhaBillSensor(coordinator, entry, TARIFF_STATUS),
            SiyakhokhaBillSensor(coordinator, entry, TARIFF_LAST_REFRESH),
            SiyakhokhaBillSensor(coordinator, entry, TARIFF_SOURCE_DOCUMENT),
            SiyakhokhaBillSensor(coordinator, entry, TARIFF_A2_BLOCK_1_EXCL),
            SiyakhokhaBillSensor(coordinator, entry, TARIFF_A2_BLOCK_1_INCL),
            SiyakhokhaBillSensor(coordinator, entry, CURRENT_BALANCE),
            SiyakhokhaBillSensor(coordinator, entry, BALANCE_DUE_DATE),
            SiyakhokhaBillSensor(coordinator, entry, NEXT_RUN_DATE),
            SiyakhokhaBillSensor(coordinator, entry, ACCOUNT_DESCRIPTION),
            SiyakhokhaBillSensor(coordinator, entry, ACCOUNT_HOLDER),
            SiyakhokhaBillSensor(coordinator, entry, CUSTOMER_NAME),
            SiyakhokhaBillSensor(coordinator, entry, CUSTOMER_EMAIL),
            SiyakhokhaBillSensor(coordinator, entry, CUSTOMER_PHONE),
            SiyakhokhaBillSensor(coordinator, entry, CUSTOMER_ADDRESS),
        ]
    )


_DIAGNOSTIC_KEYS = {
    "account_description",
    "account_holder",
    "customer_name",
    "customer_email",
    "customer_phone",
    "customer_address",
    "tariff_status",
    "tariff_last_refresh",
    "tariff_source_document",
    "last_batch_submit_status",
}


class SiyakhokhaBillSensor(CoordinatorEntity[SiyakhokhaCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SiyakhokhaCoordinator,
        entry: ConfigEntry,
        field: BillField,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._field = field
        account = entry.data[CONF_ACCOUNT_NUMBER]
        self._attr_unique_id = f"{entry.entry_id}_{field.key}_{account}"
        self._attr_name = field.name
        if field.key in {"latest_bill_amount", "current_balance"}:
            self._attr_native_unit_of_measurement = "ZAR"
            self._attr_device_class = SensorDeviceClass.MONETARY
        if field.key in {"tariff_a2_block_0_50_excl", "tariff_a2_block_0_50_incl"}:
            self._attr_native_unit_of_measurement = "ZAR/kWh"
        # NOTE: deliberately NOT setting SensorDeviceClass.DATE on the date
        # sensors below — that device class requires native_value to be a
        # datetime.date object, but the upstream API returns ISO strings
        # ("2026-05-19") which we surface as-is for dashboard display.
        # Forcing DATE device class makes HA reject the string and mark the
        # entity 'unavailable'.
        if field.key in _DIAGNOSTIC_KEYS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _find_tariff_row(self, charge_or_block: str) -> dict[str, Any] | None:
        tariffs = (self.coordinator.data or {}).get("tariffs", [])
        for row in tariffs:
            if not isinstance(row, dict):
                continue
            if str(row.get("charge_or_block", "")).strip() == charge_or_block:
                return row
        return None

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        rows = data.get("rows", [])
        if self._field.key == "latest_bill_amount":
            if not rows:
                return None
            latest = rows[0]
            return latest.get("BillAmount")
        if self._field.key == "latest_bill_date":
            if not rows:
                return None
            latest = rows[0]
            return latest.get("BillDate")
        if self._field.key == "latest_bill_pdf_url":
            if not rows:
                return None
            for row in rows:
                if row.get("DownloadAvailable"):
                    return row.get("LocalPdfUrl") or row.get("PortalPdfUrl")
            return None
        if self._field.key == "last_batch_submit_status":
            info = data.get("last_batch_submit_response") or {}
            return info.get("status", "never_submitted")
        if self._field.key == "tariff_status":
            return data.get("tariff_status", "never_refreshed")
        if self._field.key == "tariff_last_refresh":
            return data.get("tariff_last_refresh")
        if self._field.key == "tariff_source_document":
            source = data.get("tariff_source") or {}
            schedule = source.get("schedule") if isinstance(source, dict) else {}
            if isinstance(schedule, dict):
                return schedule.get("title")
            return None
        if self._field.key == "tariff_a2_block_0_50_excl":
            row = self._find_tariff_row("A.1.2 Block")
            return row.get("excl_vat") if row else None
        if self._field.key == "tariff_a2_block_0_50_incl":
            row = self._find_tariff_row("A.1.2 Block")
            return row.get("incl_vat") if row else None
        if self._field.key == "current_balance":
            balance = data.get("balance") or {}
            return balance.get("payable")
        if self._field.key == "balance_due_date":
            balance = data.get("balance") or {}
            return balance.get("due_date")
        if self._field.key == "next_run_date":
            balance = data.get("balance") or {}
            return balance.get("next_run_date")
        if self._field.key == "account_description":
            return (data.get("account_info") or {}).get("description")
        if self._field.key == "account_holder":
            return (data.get("account_info") or {}).get("account_holder")
        if self._field.key == "customer_name":
            cust = (data.get("account_info") or {}).get("customer") or {}
            first = cust.get("first_name") or ""
            last = cust.get("last_name") or ""
            full = f"{first} {last}".strip()
            return full or None
        if self._field.key == "customer_email":
            cust = (data.get("account_info") or {}).get("customer") or {}
            return cust.get("email")
        if self._field.key == "customer_phone":
            cust = (data.get("account_info") or {}).get("customer") or {}
            return cust.get("cell_phone")
        if self._field.key == "customer_address":
            cust = (data.get("account_info") or {}).get("customer") or {}
            return _join_address(cust.get("physical_address"))
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        rows = data.get("rows", [])
        latest_download_url = None
        latest_download_portal_url = None
        latest_download_local_url = None
        for row in rows:
            if row.get("DownloadAvailable"):
                latest_download_url = row.get("HaPdfUrl")
                latest_download_portal_url = row.get("PortalPdfUrl")
                latest_download_local_url = row.get("LocalPdfUrl")
                break
        tariff_source = data.get("tariff_source") or {}

        if self._field.key == "latest_bill_amount":
            return {
                "account_number": self._entry.data[CONF_ACCOUNT_NUMBER],
                "bill_count_loaded": len(rows),
                "latest_downloadable_pdf_url": latest_download_url,
                "latest_downloadable_portal_pdf_url": latest_download_portal_url,
                "latest_downloadable_local_pdf_url": latest_download_local_url,
                "payment_history": data.get("payment_history", {}),
                "debit_orders": data.get("debit_orders", {}),
                "batch_orders": data.get("batch_orders", {}),
                "single_debit_context": data.get("single_debit_context", {}),
                "last_batch_submit_response": data.get(
                    "last_batch_submit_response", {}
                ),
                "tariffs": data.get("tariffs", []),
                "tariff_status": data.get("tariff_status", "never_refreshed"),
                "tariff_last_refresh": data.get("tariff_last_refresh"),
                "tariff_source": tariff_source,
                "tariff_last_error": data.get("tariff_last_error"),
                "bills": rows,
                "current_balance": (data.get("balance") or {}).get("payable"),
                "balance_due_date": (data.get("balance") or {}).get("due_date"),
                "balance_next_run_date": (data.get("balance") or {}).get(
                    "next_run_date"
                ),
                "account_info": data.get("account_info", {}),
                "accounts": data.get("accounts", []),
            }

        if self._field.key.startswith("tariff_"):
            schedule = (
                tariff_source.get("schedule", {})
                if isinstance(tariff_source, dict)
                else {}
            )
            return {
                "tariff_status": data.get("tariff_status", "never_refreshed"),
                "tariff_last_refresh": data.get("tariff_last_refresh"),
                "tariff_row_count": len(data.get("tariffs", [])),
                "tariff_source_title": schedule.get("title")
                if isinstance(schedule, dict)
                else None,
                "tariff_source_link": schedule.get("link")
                if isinstance(schedule, dict)
                else None,
                "tariff_last_error": data.get("tariff_last_error"),
            }

        if self._field.key == "last_batch_submit_status":
            return {
                "last_batch_submit_response": data.get(
                    "last_batch_submit_response", {}
                ),
            }

        if self._field.key in {"current_balance", "balance_due_date", "next_run_date"}:
            balance = data.get("balance") or {}
            return {
                "account_number": self._entry.data[CONF_ACCOUNT_NUMBER],
                "payable": balance.get("payable"),
                "due_date": balance.get("due_date"),
                "next_run_date": balance.get("next_run_date"),
                "balance_rows": data.get("balance_rows", []),
            }

        if self._field.key in {
            "account_description",
            "account_holder",
            "customer_name",
            "customer_email",
            "customer_phone",
            "customer_address",
        }:
            account_info = data.get("account_info") or {}
            customer = account_info.get("customer") or {}
            return {
                "account_number": self._entry.data[CONF_ACCOUNT_NUMBER],
                "account_id": account_info.get("account_id"),
                "account_type": account_info.get("account_type"),
                "is_active": account_info.get("is_active"),
                "is_blacklisted": account_info.get("is_blacklisted"),
                "physical_address": _join_address(customer.get("physical_address")),
                "postal_address": _join_address(customer.get("postal_address")),
                "all_accounts": data.get("accounts", []),
            }

        return {
            "account_number": self._entry.data[CONF_ACCOUNT_NUMBER],
            "bill_count_loaded": len(rows),
            "latest_downloadable_pdf_url": latest_download_url,
            "latest_downloadable_portal_pdf_url": latest_download_portal_url,
            "latest_downloadable_local_pdf_url": latest_download_local_url,
        }
