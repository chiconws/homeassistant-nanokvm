"""Sensor platform for Sipeed NanoKVM."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import NanoKVMDataUpdateCoordinator
from .entity import (
    NanoKVMEntity,
    async_add_entities_as_supported,
    has_connection_type,
    has_mounted_image,
)

_TAILSCALE_STATE_MAP: dict[str, str] = {
    "notInstall": "not_install",
    "notRunning": "not_running",
    "notLogin": "not_login",
}


def _normalize_tailscale_state(raw_state: str | None) -> str | None:
    """Map API tailscale states to translation-safe keys."""
    if raw_state is None:
        return None
    return _TAILSCALE_STATE_MAP.get(raw_state, raw_state)


def _ssh_enabled(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether the device reports its SSH service as enabled."""
    return bool(coordinator.ssh_state and coordinator.ssh_state.enabled)


def _mounted_image_value(coordinator: NanoKVMDataUpdateCoordinator) -> str:
    """Return mounted image path."""
    return coordinator.mounted_image.file if coordinator.mounted_image else ""


def _addresses_for_connection_type(
    coordinator: NanoKVMDataUpdateCoordinator, connection_type: str | None
) -> list[Any]:
    """Return NanoKVM addresses for a connection type, or all of them for None."""
    return [
        address
        for address in coordinator.device_info.ips
        if connection_type in (None, address.type.casefold())
    ]


def _connection_ip_value(
    coordinator: NanoKVMDataUpdateCoordinator, connection_type: str | None = None
) -> str | None:
    """Return the preferred IP address, optionally for one connection type."""
    addresses = _addresses_for_connection_type(coordinator, connection_type)
    if not addresses:
        return None

    for address in addresses:
        if address.version == "IPv4":
            return address.addr

    return addresses[0].addr


def _connection_ip_attributes(
    coordinator: NanoKVMDataUpdateCoordinator, connection_type: str | None = None
) -> dict[str, Any]:
    """Return reported addresses, optionally for one connection type."""
    return {
        "addresses": [
            {
                "name": address.name,
                "addr": address.addr,
                "version": address.version,
                "type": address.type,
            }
            for address in _addresses_for_connection_type(
                coordinator, connection_type
            )
        ]
    }


def _tailscale_state_value(coordinator: NanoKVMDataUpdateCoordinator) -> str | None:
    """Return normalized tailscale state value."""
    if coordinator.tailscale_status is None:
        return None
    return _normalize_tailscale_state(coordinator.tailscale_status.state.value)


def _tailscale_attributes(coordinator: NanoKVMDataUpdateCoordinator) -> dict[str, Any]:
    """Return tailscale sensor attributes."""
    if coordinator.tailscale_status is None:
        return {}

    return {
        "name": coordinator.tailscale_status.name,
        "ip": coordinator.tailscale_status.ip,
        "account": coordinator.tailscale_status.account,
    }


def _memory_total_attribute(coordinator: NanoKVMDataUpdateCoordinator) -> dict[str, Any]:
    """Return memory total attribute when available."""
    total = coordinator.ssh_metrics.memory_total
    return {"total_mb": total} if total is not None else {}


def _storage_total_attribute(coordinator: NanoKVMDataUpdateCoordinator) -> dict[str, Any]:
    """Return storage total attribute when available."""
    total = coordinator.ssh_metrics.storage_total
    return {"total_mb": total} if total is not None else {}


@dataclass(frozen=True, kw_only=True)
class NanoKVMSensorEntityDescription(SensorEntityDescription):
    """Describes NanoKVM sensor entity."""

    value_fn: Callable[[NanoKVMDataUpdateCoordinator], Any] = lambda _: None
    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True
    should_create_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True
    attributes_fn: Callable[[NanoKVMDataUpdateCoordinator], dict[str, Any]] = lambda _: {}


MEDIA_SENSORS: tuple[NanoKVMSensorEntityDescription, ...] = (
    NanoKVMSensorEntityDescription(
        key="mounted_image",
        translation_key="mounted_image",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mounted_image_value,
        should_create_fn=has_mounted_image,
        available_fn=has_mounted_image,
    ),
)

SENSORS: tuple[NanoKVMSensorEntityDescription, ...] = (
    NanoKVMSensorEntityDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_connection_ip_value,
        available_fn=lambda coordinator: _connection_ip_value(coordinator) is not None,
        attributes_fn=_connection_ip_attributes,
    ),
    NanoKVMSensorEntityDescription(
        key="tailscale_state",
        translation_key="tailscale_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_tailscale_state_value,
        attributes_fn=_tailscale_attributes,
        should_create_fn=lambda coordinator: coordinator.is_admin,
    ),
    NanoKVMSensorEntityDescription(
        key="account_role",
        translation_key="account_role",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: (
            coordinator.account.role if coordinator.account else None
        ),
        # Only firmware with user accounts reports a role.
        should_create_fn=lambda coordinator: bool(
            coordinator.account and coordinator.account.role
        ),
    ),
)

NETWORK_SENSORS: tuple[NanoKVMSensorEntityDescription, ...] = (
    NanoKVMSensorEntityDescription(
        key="wired_ip_address",
        translation_key="wired_ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: _connection_ip_value(coordinator, "wired"),
        available_fn=lambda coordinator: has_connection_type(coordinator, "wired"),
        should_create_fn=lambda coordinator: has_connection_type(
            coordinator, "wired"
        ),
        attributes_fn=lambda coordinator: _connection_ip_attributes(
            coordinator, "wired"
        ),
    ),
    NanoKVMSensorEntityDescription(
        key="wireless_ip_address",
        translation_key="wireless_ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: _connection_ip_value(coordinator, "wireless"),
        available_fn=lambda coordinator: has_connection_type(coordinator, "wireless"),
        should_create_fn=lambda coordinator: has_connection_type(
            coordinator, "wireless"
        ),
        attributes_fn=lambda coordinator: _connection_ip_attributes(
            coordinator, "wireless"
        ),
    ),
)

SSH_SENSORS: tuple[NanoKVMSensorEntityDescription, ...] = (
    NanoKVMSensorEntityDescription(
        key="uptime",
        translation_key="uptime",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
        should_create_fn=_ssh_enabled,
        value_fn=lambda coordinator: coordinator.ssh_metrics.uptime,
        available_fn=lambda coordinator: (
            coordinator.ssh_metrics.uptime is not None
        ),
    ),
    NanoKVMSensorEntityDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        should_create_fn=_ssh_enabled,
        value_fn=lambda coordinator: coordinator.ssh_metrics.cpu_temperature,
        available_fn=lambda coordinator: (
            coordinator.ssh_metrics.cpu_temperature is not None
        ),
    ),
    NanoKVMSensorEntityDescription(
        key="memory_used_percent",
        translation_key="memory_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        should_create_fn=_ssh_enabled,
        value_fn=lambda coordinator: coordinator.ssh_metrics.memory_used_percent,
        available_fn=lambda coordinator: (
            coordinator.ssh_metrics.memory_used_percent is not None
        ),
        attributes_fn=_memory_total_attribute,
    ),
    NanoKVMSensorEntityDescription(
        key="storage_used_percent",
        translation_key="storage_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        should_create_fn=_ssh_enabled,
        value_fn=lambda coordinator: coordinator.ssh_metrics.storage_used_percent,
        available_fn=lambda coordinator: (
            coordinator.ssh_metrics.storage_used_percent is not None
        ),
        attributes_fn=_storage_total_attribute,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM sensor based on a config entry."""
    coordinator = entry.runtime_data

    async_add_entities_as_supported(
        entry,
        coordinator,
        async_add_entities,
        (*SENSORS, *NETWORK_SENSORS, *MEDIA_SENSORS, *SSH_SENSORS),
        lambda description: description.should_create_fn(coordinator),
        lambda description: NanoKVMSensor(
            coordinator=coordinator, description=description
        ),
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
        self.entity_description = description
        super().__init__(
            coordinator=coordinator,
            unique_id_suffix=f"sensor_{description.key}",
        )

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
