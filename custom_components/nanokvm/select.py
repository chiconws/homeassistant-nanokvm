"""Select platform for Sipeed NanoKVM."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from nanokvm.models import HidMode, MouseJigglerMode

from .const import (
    DOMAIN,
    ICON_HID,
    ICON_MOUSE_JIGGLER,
    ICON_OLED,
)
from . import NanoKVMDataUpdateCoordinator, NanoKVMEntity

_LOGGER = logging.getLogger(__name__)


@dataclass
class NanoKVMSelectEntityDescription(SelectEntityDescription):
    """Describes NanoKVM select entity."""

    value_fn: Callable[[NanoKVMDataUpdateCoordinator], str] = None
    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True
    select_option_fn: Callable[[NanoKVMDataUpdateCoordinator, str], Any] = None


MOUSE_JIGGLER_OPTIONS = [
    "Disable",
    "Relative Mode",
    "Absolute Mode",
]

HID_MODE_OPTIONS = [
    "Normal",
    "HID Only",
]

OLED_SLEEP_OPTIONS = {
    "Never": 0,
    "15 sec": 15,
    "30 sec": 30,
    "1 min": 60,
    "3 min": 180,
    "5 min": 300,
    "10 min": 600,
    "30 min": 1800,
    "1 hour": 3600,
}
OLED_SLEEP_VALUES = {v: k for k, v in OLED_SLEEP_OPTIONS.items()}


SELECTS: tuple[NanoKVMSelectEntityDescription, ...] = (
    NanoKVMSelectEntityDescription(
        key="hid_mode",
        name="HID Mode (Reboot Required)",
        icon=ICON_HID,
        entity_category=EntityCategory.CONFIG,
        options=HID_MODE_OPTIONS,
        value_fn=lambda coordinator: (
            "HID Only" if coordinator.hid_mode.mode == HidMode.HID_ONLY else "Normal"
        ),
        select_option_fn=lambda coordinator, option: coordinator.client.set_hid_mode(
            HidMode.HID_ONLY if option == "HID Only" else HidMode.NORMAL
        ),
        available_fn=lambda coordinator: coordinator.hid_mode is not None,
    ),
    NanoKVMSelectEntityDescription(
        key="mouse_jiggler_mode",
        name="Mouse Jiggler Mode",
        icon=ICON_MOUSE_JIGGLER,
        entity_category=EntityCategory.CONFIG,
        options=MOUSE_JIGGLER_OPTIONS,
        value_fn=lambda coordinator: (
            "Disable" if not coordinator.mouse_jiggler_state or not coordinator.mouse_jiggler_state.enabled
            else "Relative Mode" if coordinator.mouse_jiggler_state.mode == MouseJigglerMode.RELATIVE
            else "Absolute Mode"
        ),
        select_option_fn=lambda coordinator, option: coordinator.client.set_mouse_jiggler_state(
            option != "Disable",
            MouseJigglerMode.RELATIVE if option == "Relative Mode" else MouseJigglerMode.ABSOLUTE
        ),
        available_fn=lambda coordinator: coordinator.mouse_jiggler_state is not None,
    ),
    NanoKVMSelectEntityDescription(
        key="oled_sleep_timeout",
        name="OLED Sleep Timeout",
        icon=ICON_OLED,
        entity_category=EntityCategory.CONFIG,
        options=list(OLED_SLEEP_OPTIONS.keys()),
        value_fn=lambda coordinator: OLED_SLEEP_VALUES.get(
            coordinator.oled_info.sleep, f"{coordinator.oled_info.sleep} sec"
        ),
        select_option_fn=lambda coordinator, option: coordinator.client.set_oled_sleep(
            OLED_SLEEP_OPTIONS.get(option, 0)
        ),
        available_fn=lambda coordinator: coordinator.oled_info.exist,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM select based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        NanoKVMSelect(
            coordinator=coordinator,
            description=description,
        )
        for description in SELECTS
        if description.available_fn(coordinator)
    )


class NanoKVMSelect(NanoKVMEntity, SelectEntity):
    """Defines a NanoKVM select."""

    entity_description: NanoKVMSelectEntityDescription

    def __init__(
        self,
        coordinator: NanoKVMDataUpdateCoordinator,
        description: NanoKVMSelectEntityDescription,
    ) -> None:
        """Initialize NanoKVM select."""
        super().__init__(
            coordinator=coordinator,
            name=f"{description.name}",
            unique_id_suffix=f"select_{description.key}",
        )
        self.entity_description = description

    @property
    def current_option(self) -> str:
        """Return the current selected option."""
        return self.entity_description.value_fn(self.coordinator)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        async with self.coordinator.client:
            await self.entity_description.select_option_fn(self.coordinator, option)
        await self.coordinator.async_request_refresh()
