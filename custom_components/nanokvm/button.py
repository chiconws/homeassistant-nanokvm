"""Button platform for Sipeed NanoKVM."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from nanokvm.models import GpioType

from .coordinator import NanoKVMDataUpdateCoordinator
from .entity import NanoKVMEntity


@dataclass(frozen=True, kw_only=True)
class NanoKVMButtonEntityDescription(ButtonEntityDescription):
    """Describes NanoKVM button entity."""

    press_fn: Callable[[NanoKVMDataUpdateCoordinator], Awaitable[None]]
    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True


BUTTONS: tuple[NanoKVMButtonEntityDescription, ...] = (
    NanoKVMButtonEntityDescription(
        key="power",
        translation_key="power",
        press_fn=lambda coordinator: coordinator.client.push_button(GpioType.POWER, 100),
    ),
    NanoKVMButtonEntityDescription(
        key="reset",
        translation_key="reset",
        press_fn=lambda coordinator: coordinator.client.push_button(GpioType.RESET, 100),
    ),
    NanoKVMButtonEntityDescription(
        key="reboot",
        translation_key="reboot",
        press_fn=lambda coordinator: coordinator.client.reboot_system(),
        available_fn=lambda coordinator: coordinator.is_admin,
    ),
    NanoKVMButtonEntityDescription(
        key="reset_hdmi",
        translation_key="reset_hdmi",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.client.reset_hdmi(),
        available_fn=lambda coordinator: coordinator.supports_hdmi_endpoint,
    ),
    NanoKVMButtonEntityDescription(
        key="reset_hid",
        translation_key="reset_hid",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.client.reset_hid(),
        available_fn=lambda coordinator: coordinator.is_admin,
    ),
    NanoKVMButtonEntityDescription(
        key="sync_time",
        translation_key="sync_time",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.client.sync_time(),
        available_fn=lambda coordinator: coordinator.is_pro_hardware,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM button based on a config entry."""
    coordinator = entry.runtime_data

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
        self.entity_description = description
        super().__init__(
            coordinator=coordinator,
            unique_id_suffix=f"button_{description.key}",
        )

    async def async_press(self) -> None:
        """Press the button."""
        async with self._async_device_action():
            await self.entity_description.press_fn(self.coordinator)
        await self.coordinator.async_request_refresh()
