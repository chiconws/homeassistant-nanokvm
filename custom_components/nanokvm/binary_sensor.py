"""Binary sensor platform for Sipeed NanoKVM."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from nanokvm.models import HWVersion

from .coordinator import NanoKVMDataUpdateCoordinator
from .entity import (
    NanoKVMEntity,
    async_add_entities_as_supported,
    has_connection_type,
    has_mounted_image,
)


@dataclass(frozen=True, kw_only=True)
class NanoKVMBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes NanoKVM binary sensor entity."""

    value_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = (
        lambda _: False
    )
    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = (
        lambda _: True
    )
    should_create_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = (
        lambda _: True
    )


def _is_alpha_hardware(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether the device is Alpha hardware."""
    return bool(
        coordinator.hardware_info
        and coordinator.hardware_info.version == HWVersion.ALPHA
    )


def _wifi_supported(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether Wi-Fi is supported."""
    return bool(coordinator.wifi_status and coordinator.wifi_status.supported)


def _wired_active(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether a wired network connection is active."""
    return has_connection_type(coordinator, "wired")


def _static_ip_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether Pro static IP state is available."""
    return coordinator.is_pro_hardware and coordinator.static_ip is not None


def _time_status_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether Pro time synchronization state is available."""
    return coordinator.is_pro_hardware and coordinator.time_status is not None


def _cdrom_mode_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether an image is mounted where /storage/cdrom exists (non-Pro)."""
    return has_mounted_image(coordinator) and coordinator.is_non_pro_hardware


MEDIA_BINARY_SENSORS: tuple[NanoKVMBinarySensorEntityDescription, ...] = (
    NanoKVMBinarySensorEntityDescription(
        key="cdrom_mode",
        translation_key="cdrom_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: bool(
            coordinator.cdrom_status and coordinator.cdrom_status.cdrom == 1
        ),
        available_fn=_cdrom_mode_available,
        should_create_fn=_cdrom_mode_available,
    ),
)


BINARY_SENSORS: tuple[NanoKVMBinarySensorEntityDescription, ...] = (
    NanoKVMBinarySensorEntityDescription(
        key="power_led",
        translation_key="power_led",
        value_fn=lambda coordinator: bool(
            coordinator.gpio_info and coordinator.gpio_info.pwr
        ),
    ),
    NanoKVMBinarySensorEntityDescription(
        key="hdd_led",
        translation_key="hdd_led",
        value_fn=lambda coordinator: bool(
            coordinator.gpio_info and coordinator.gpio_info.hdd
        ),
        # HDD LED is only valid for Alpha hardware
        available_fn=_is_alpha_hardware,
        should_create_fn=_is_alpha_hardware,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="wifi_connected",
        translation_key="wifi_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: bool(
            coordinator.wifi_status and coordinator.wifi_status.connected
        ),
        available_fn=_wifi_supported,
        should_create_fn=_wifi_supported,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="wired_connected",
        translation_key="wired_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_wired_active,
        should_create_fn=_wired_active,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="static_ip_enabled",
        translation_key="static_ip_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: bool(
            coordinator.static_ip and coordinator.static_ip.enabled
        ),
        available_fn=_static_ip_available,
        should_create_fn=_static_ip_available,
    ),
    NanoKVMBinarySensorEntityDescription(
        key="time_synchronized",
        translation_key="time_synchronized",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: bool(
            coordinator.time_status and coordinator.time_status.is_synchronized
        ),
        available_fn=_time_status_available,
        should_create_fn=_time_status_available,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM binary sensor based on a config entry."""
    coordinator = entry.runtime_data

    async_add_entities_as_supported(
        entry,
        coordinator,
        async_add_entities,
        (*BINARY_SENSORS, *MEDIA_BINARY_SENSORS),
        lambda description: description.should_create_fn(coordinator),
        lambda description: NanoKVMBinarySensor(
            coordinator=coordinator, description=description
        ),
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
        self.entity_description = description
        super().__init__(
            coordinator=coordinator,
            unique_id_suffix=f"binary_sensor_{description.key}",
        )

    @property
    def is_on(self) -> bool:
        """Return the state of the binary sensor."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.entity_description.available_fn(self.coordinator)
