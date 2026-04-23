"""Button platform for Siyakhokha Bridge."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SiyakhokhaCoordinator

PN_DOMAIN = "persistent_notification"
PN_SERVICE_CREATE = "create"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SiyakhokhaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SiyakhokhaRefreshBillsButton(coordinator, entry),
            SiyakhokhaRefreshTariffsButton(coordinator, entry),
            SiyakhokhaOpenLatestBillButton(coordinator, entry),
        ]
    )


class SiyakhokhaRefreshBillsButton(
    CoordinatorEntity[SiyakhokhaCoordinator], ButtonEntity
):
    _attr_has_entity_name = True
    _attr_name = "Refresh Bills"

    def __init__(self, coordinator: SiyakhokhaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_refresh_bills"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class SiyakhokhaOpenLatestBillButton(
    CoordinatorEntity[SiyakhokhaCoordinator], ButtonEntity
):
    _attr_has_entity_name = True
    _attr_name = "Open Latest Downloadable Bill"

    def __init__(self, coordinator: SiyakhokhaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_open_latest_downloadable_bill"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
        rows = self.coordinator.data.get("rows", []) if self.coordinator.data else []
        url = None
        for row in rows:
            if row.get("DownloadAvailable"):
                url = row.get("HaPdfUrl")
                break

        if url:
            message = f"[Open latest downloadable bill]({url})"
        else:
            message = "No downloadable bill PDF is currently available."

        await self.hass.services.async_call(
            PN_DOMAIN,
            PN_SERVICE_CREATE,
            {
                "title": "Siyakhokha Bill",
                "message": message,
                "notification_id": f"siyakhokha_bill_{self._entry.entry_id}",
            },
            blocking=True,
        )


class SiyakhokhaRefreshTariffsButton(
    CoordinatorEntity[SiyakhokhaCoordinator], ButtonEntity
):
    _attr_has_entity_name = True
    _attr_name = "Refresh Tariffs"

    def __init__(self, coordinator: SiyakhokhaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_refresh_tariffs"

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_tariffs(reason="button")
