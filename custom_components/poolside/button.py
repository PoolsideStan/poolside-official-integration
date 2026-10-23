"""Reset buttons for the dose counters.

Each button resets one chemical's "dosed since last reset" counter on the
matching last-dose sensor. Buttons are created disabled by default: an
install only turns one on when it wants count-up tracking for a tank.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from typing import override

from . import PoolsideConfigEntry
from .const import DOMAIN
from .entity import PoolsideGroupEntity

DOSE_KEY_TO_CHEMICAL = {
    "acid_dose": "acid",
    "chlorine_dose": "chlorine",
    "salt_chlorinator_dose": "salt chlorinator",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolsideConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one reset button per chemical with a dose counter pair."""
    data = entry.runtime_data
    async_add_entities(
        PoolsideDoseResetButton(data.client, handler)
        for handler in data.dose_handlers.values()
    )


class PoolsideDoseResetButton(PoolsideGroupEntity, ButtonEntity):
    """Resets one chemical's dosed-since-reset counter to zero.

    Attaches to the same device as its dose sensor - the chemical pump
    device when the site reports one (passed through on the sensor), so
    the reset control lives with the counter it zeroes.
    """

    _attr_translation_key = "dose_reset"
    _attr_entity_registry_enabled_default = False

    def __init__(self, client, dose_sensor) -> None:
        """Set up the reset button for one chemical of a body of water."""
        super().__init__(client, dose_sensor._group)
        if getattr(dose_sensor, "_device", None) is not None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, dose_sensor._device.uuid)}
            )
        self._dose_sensor = dose_sensor
        chemical = DOSE_KEY_TO_CHEMICAL.get(
            dose_sensor.entity_description.key,
            dose_sensor.entity_description.key.replace("_dose", "").replace(
                "_", " "
            ),
        )
        self._attr_translation_placeholders = {"chemical": chemical}
        self._attr_unique_id = (
            f"{client.controller_uuid}_{dose_sensor._body_of_water_uuid}"
            f"_{dose_sensor.entity_description.key}_dose_reset"
        )

    @override
    def _status_keys(self) -> set[str]:
        """Refresh alongside the dose sensor it resets."""
        return {self._dose_sensor._body_of_water_uuid}

    @callback
    @override
    async def async_press(self) -> None:
        """Reset the paired dose counter."""
        self._dose_sensor.reset()