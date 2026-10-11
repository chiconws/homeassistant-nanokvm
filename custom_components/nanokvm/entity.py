"""Base NanoKVM entity class."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Sequence
import contextlib
import logging
from functools import cached_property
from typing import TypeVar

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from nanokvm.client import NanoKVMClient, NanoKVMError

from .const import DOMAIN, INTEGRATION_TITLE
from .coordinator import NanoKVMDataUpdateCoordinator
from .utils import api_base_url_to_web_url, device_sw_version

_LOGGER = logging.getLogger(__name__)

_DescriptionT = TypeVar("_DescriptionT")


def has_mounted_image(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether there is a mounted image."""
    return bool(coordinator.mounted_image and coordinator.mounted_image.file != "")


def has_connection_type(
    coordinator: NanoKVMDataUpdateCoordinator, connection_type: str
) -> bool:
    """Return whether an IP connection type is active."""
    return any(
        address.addr and address.type.casefold() == connection_type
        for address in coordinator.device_info.ips
    )


@callback
def async_add_entities_as_supported(
    entry: ConfigEntry,
    coordinator: NanoKVMDataUpdateCoordinator,
    async_add_entities: AddEntitiesCallback,
    descriptions: Sequence[_DescriptionT],
    should_create: Callable[[_DescriptionT], bool],
    create_entity: Callable[[_DescriptionT], Entity],
) -> None:
    """Add each described entity once, as soon as the device supports it."""
    added: set[int] = set()

    @callback
    def add_new() -> None:
        new = [
            index
            for index, description in enumerate(descriptions)
            if index not in added and should_create(description)
        ]
        if new:
            added.update(new)
            async_add_entities(create_entity(descriptions[index]) for index in new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class NanoKVMEntity(CoordinatorEntity[NanoKVMDataUpdateCoordinator]):
    """Base class for NanoKVM entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NanoKVMDataUpdateCoordinator,
        unique_id_suffix: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_info.device_key}_{unique_id_suffix}"
        _LOGGER.debug(
            "Created entity %s with unique_id: %s", unique_id_suffix, self._attr_unique_id
        )

    @contextlib.asynccontextmanager
    async def _async_device_action(self) -> AsyncGenerator[NanoKVMClient]:
        """Yield the shared client, reporting device failures as action errors."""
        try:
            async with self.coordinator.async_client() as client:
                yield client
        except (NanoKVMError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(
                f"NanoKVM did not accept the request: {str(err) or type(err).__name__}"
            ) from err

    @cached_property
    def device_info(self) -> DeviceInfo:
        """Return device information about this NanoKVM device."""
        device_data = self.coordinator.device_info
        hostname = (
            self.coordinator.hostname_info.hostname
            if self.coordinator.hostname_info is not None
            else INTEGRATION_TITLE
        )
        hw_version = (
            self.coordinator.hardware_info.version.value
            if self.coordinator.hardware_info is not None
            else "Unknown"
        )

        sw_version = device_sw_version(device_data.application, device_data.image)

        return DeviceInfo(
            identifiers={(DOMAIN, device_data.device_key)},
            name=hostname,
            manufacturer="Sipeed",
            model=f"{INTEGRATION_TITLE} {hw_version}",
            sw_version=sw_version,
            hw_version=hw_version,
            configuration_url=api_base_url_to_web_url(str(self.coordinator.client.url)),
        )
