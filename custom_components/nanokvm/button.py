"""Button platform for Sipeed NanoKVM."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from nanokvm.models import GpioType

from .const import (
    DOMAIN,
    ICON_HID,
    ICON_KVM,
    ICON_POWER,
    ICON_RESET,
)
from . import NanoKVMDataUpdateCoordinator, NanoKVMEntity


@dataclass
class NanoKVMButtonEntityDescription(ButtonEntityDescription):
    """Describes NanoKVM button entity."""

    press_fn: Callable[[NanoKVMDataUpdateCoordinator], None] = None
    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True


BUTTONS: tuple[NanoKVMButtonEntityDescription, ...] = (
    NanoKVMButtonEntityDescription(
        key="power",
        name="Power Button",
        icon=ICON_POWER,
        press_fn=lambda coordinator: coordinator.client.push_button(GpioType.POWER, 100),
    ),
    NanoKVMButtonEntityDescription(
        key="reset",
        name="Reset Button",
        icon=ICON_RESET,
        press_fn=lambda coordinator: coordinator.client.push_button(GpioType.RESET, 100),
    ),
    NanoKVMButtonEntityDescription(
        key="reboot",
        name="Reboot System",
        icon=ICON_RESET,
        press_fn=lambda coordinator: coordinator.client.reboot_system(),
    ),
    NanoKVMButtonEntityDescription(
        key="reset_hdmi",
        name="Reset HDMI",
        icon=ICON_KVM,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.client.reset_hdmi(),
        available_fn=lambda coordinator: coordinator.hardware_info.version.value == "PCIE",
    ),
    NanoKVMButtonEntityDescription(
        key="reset_hid",
        name="Reset HID",
        icon=ICON_HID,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.client.reset_hid(),
    ),
    NanoKVMButtonEntityDescription(
        key="update_application",
        name="Update Application",
        icon=ICON_KVM,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.client.update_application(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM button based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        NanoKVMButton(
            coordinator=coordinator,
            description=description,
        )
        for description in BUTTONS
        if description.available_fn(coordinator)
    )


class NanoKVMButton(NanoKVMEntity, ButtonEntity):
    """Defines a NanoKVM button."""

    entity_description: NanoKVMButtonEntityDescription

    def __init__(
        self,
        coordinator: NanoKVMDataUpdateCoordinator,
        description: NanoKVMButtonEntityDescription,
    ) -> None:
        """Initialize NanoKVM button."""
        super().__init__(
            coordinator=coordinator,
            name=f"{description.name}",
            unique_id_suffix=f"button_{description.key}",
        )
        self.entity_description = description

    async def async_press(self) -> None:
        """Press the button."""
        async with self.coordinator.client:
            await self.entity_description.press_fn(self.coordinator)
        await self.coordinator.async_request_refresh()
