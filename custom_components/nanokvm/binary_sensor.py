"""Binary sensor platform for Sipeed NanoKVM."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    ICON_DISK,
    ICON_MDNS,
    ICON_MOUSE_JIGGLER,
    ICON_NETWORK,
    ICON_OLED,
    ICON_POWER,
    ICON_SSH,
    ICON_WIFI,
)
from . import NanoKVMDataUpdateCoordinator, NanoKVMEntity


@dataclass
class NanoKVMBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes NanoKVM binary sensor entity."""

    value_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = None
    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True


BINARY_SENSORS: tuple[NanoKVMBinarySensorEntityDescription, ...] = (
    NanoKVMBinarySensorEntityDescription(
        key="power_led",
        name="Power LED",
        icon=ICON_POWER,
        value_fn=lambda coordinator: coordinator.gpio_info.pwr,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="hdd_led",
        name="HDD LED",
        icon=ICON_DISK,
        value_fn=lambda coordinator: coordinator.gpio_info.hdd,
        # HDD LED is only valid for Alpha hardware
        available_fn=lambda coordinator: coordinator.hardware_info.version.value == "Alpha",
    ),
    NanoKVMBinarySensorEntityDescription(
        key="network_device",
        name="Virtual Network Device",
        icon=ICON_NETWORK,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.virtual_device_info.network,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="disk_device",
        name="Virtual Disk Device",
        icon=ICON_DISK,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.virtual_device_info.disk,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="ssh_enabled",
        name="SSH Enabled",
        icon=ICON_SSH,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.ssh_state.enabled,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="mdns_enabled",
        name="mDNS Enabled",
        icon=ICON_MDNS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.mdns_state.enabled,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="oled_present",
        name="OLED Present",
        icon=ICON_OLED,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.oled_info.exist,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="wifi_supported",
        name="WiFi Supported",
        icon=ICON_WIFI,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.wifi_status.supported,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="wifi_connected",
        name="WiFi Connected",
        icon=ICON_WIFI,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.wifi_status.connected,
        available_fn=lambda coordinator: coordinator.wifi_status.supported,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="cdrom_mode",
        name="CD-ROM Mode",
        icon=ICON_DISK,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.cdrom_status.cdrom == 1,
        available_fn=lambda coordinator: coordinator.mounted_image.file != "",
    ),
    NanoKVMBinarySensorEntityDescription(
        key="mouse_jiggler",
        name="Mouse Jiggler",
        icon=ICON_MOUSE_JIGGLER,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.mouse_jiggler_state.enabled if coordinator.mouse_jiggler_state else False,
        available_fn=lambda coordinator: coordinator.mouse_jiggler_state is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM binary sensor based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        NanoKVMBinarySensor(
            coordinator=coordinator,
            description=description,
        )
        for description in BINARY_SENSORS
        if description.available_fn(coordinator)
    )


class NanoKVMBinarySensor(NanoKVMEntity, BinarySensorEntity):
    """Defines a NanoKVM binary sensor."""

    entity_description: NanoKVMBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: NanoKVMDataUpdateCoordinator,
        description: NanoKVMBinarySensorEntityDescription,
    ) -> None:
        """Initialize NanoKVM binary sensor."""
        super().__init__(
            coordinator=coordinator,
            name=f"{description.name}",
            unique_id_suffix=f"binary_sensor_{description.key}",
        )
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the state of the binary sensor."""
        return self.entity_description.value_fn(self.coordinator)
