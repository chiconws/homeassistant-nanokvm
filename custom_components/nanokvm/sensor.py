"""Sensor platform for Sipeed NanoKVM."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    ICON_DISK,
    ICON_HID,
    ICON_IMAGE,
    ICON_KVM,
    ICON_OLED,
    ICON_SSH,
    SIGNAL_NEW_SSH_SENSORS,
)
from . import NanoKVMDataUpdateCoordinator, NanoKVMEntity

_LOGGER = logging.getLogger(__name__)

@dataclass
class NanoKVMSensorEntityDescription(SensorEntityDescription):
    """Describes NanoKVM sensor entity."""

    value_fn: Callable[[NanoKVMDataUpdateCoordinator], Any] = None
    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True
    attributes_fn: Callable[[NanoKVMDataUpdateCoordinator], dict[str, Any]] = lambda _: {}


SENSORS: tuple[NanoKVMSensorEntityDescription, ...] = (
    NanoKVMSensorEntityDescription(
        key="hid_mode",
        name="HID Mode",
        icon=ICON_HID,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.hid_mode.mode.value,
    ),
    NanoKVMSensorEntityDescription(
        key="oled_sleep",
        name="OLED Sleep Timeout",
        icon=ICON_OLED,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: coordinator.oled_info.sleep,
        available_fn=lambda coordinator: coordinator.oled_info.exist,
    ),
    NanoKVMSensorEntityDescription(
        key="hardware_version",
        name="Hardware Version",
        icon=ICON_KVM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.hardware_info.version.value,
    ),
    NanoKVMSensorEntityDescription(
        key="application_version",
        name="Application Version",
        icon=ICON_KVM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.device_info.application,
    ),
    NanoKVMSensorEntityDescription(
        key="mounted_image",
        name="Mounted Image",
        icon=ICON_IMAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.mounted_image.file,
        available_fn=lambda coordinator: coordinator.mounted_image.file != "",
    ),
)

SSH_SENSORS: tuple[NanoKVMSensorEntityDescription, ...] = (
    NanoKVMSensorEntityDescription(
        key="uptime",
        name="Uptime",
        icon=ICON_SSH,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda coordinator: coordinator.uptime,
        available_fn=lambda coordinator: coordinator.uptime is not None,
    ),
    NanoKVMSensorEntityDescription(
        key="memory_used_percent",
        name="Memory Used",
        icon="mdi:memory",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.memory_used_percent,
        available_fn=lambda coordinator: coordinator.memory_used_percent is not None,
        attributes_fn=lambda coordinator: (
            {"total_mb": coordinator.memory_total}
            if coordinator.memory_total is not None
            else {}
        ),
    ),
    NanoKVMSensorEntityDescription(
        key="storage_used_percent",
        name="Storage Used",
        icon=ICON_DISK,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.storage_used_percent,
        available_fn=lambda coordinator: coordinator.storage_used_percent is not None,
        attributes_fn=lambda coordinator: (
            {"total_mb": coordinator.storage_total}
            if coordinator.storage_total is not None
            else {}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM sensor based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        NanoKVMSensor(
            coordinator=coordinator,
            description=description,
        )
        for description in SENSORS
        if description.available_fn(coordinator)
    )

    # Add SSH sensors if SSH is already enabled
    if coordinator.ssh_state and coordinator.ssh_state.enabled:
        _LOGGER.debug("SSH already enabled, creating SSH sensors")
        async_add_entities(
            NanoKVMSensor(
                coordinator=coordinator,
                description=description,
            )
            for description in SSH_SENSORS
        )
        coordinator.ssh_sensors_created = True

    # Listen for signal to add SSH sensors later
    @callback
    def async_add_ssh_sensors() -> None:
        """Add SSH sensors when SSH is enabled."""
        _LOGGER.debug("Received signal to create SSH sensors")
        async_add_entities(
            NanoKVMSensor(
                coordinator=coordinator,
                description=description,
            )
            for description in SSH_SENSORS
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_NEW_SSH_SENSORS.format(entry.entry_id), async_add_ssh_sensors
        )
    )


class NanoKVMSensor(NanoKVMEntity, SensorEntity):
    """Defines a NanoKVM sensor."""

    entity_description: NanoKVMSensorEntityDescription

    def __init__(
        self,
        coordinator: NanoKVMDataUpdateCoordinator,
        description: NanoKVMSensorEntityDescription,
    ) -> None:
        """Initialize NanoKVM sensor."""
        super().__init__(
            coordinator=coordinator,
            name=f"{description.name}",
            unique_id_suffix=f"sensor_{description.key}",
        )
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.entity_description.available_fn(self.coordinator)

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return self.entity_description.attributes_fn(self.coordinator)
