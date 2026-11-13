"""Update platform for Sipeed NanoKVM."""

import logging
from typing import Any, override

import aiohttp

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from nanokvm.client import NanoKVMError
from nanokvm.models import Permission

from .coordinator import NanoKVMConfigEntry, NanoKVMDataUpdateCoordinator
from .entity import NanoKVMEntity

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


def _normalize_version(version: str | None) -> str | None:
    """Normalize empty version strings to None."""
    if version is None:
        return None

    normalized = version.strip()
    return normalized or None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanoKVMConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM update based on a config entry."""
    coordinator = entry.runtime_data

    # Reading or installing application versions needs the update grant.
    if coordinator.can(Permission.UPDATE):
        async_add_entities([NanoKVMUpdate(coordinator)])


class NanoKVMUpdate(NanoKVMEntity, UpdateEntity):
    """Defines a NanoKVM update entity."""

    entity_description = UpdateEntityDescription(
        key="application",
        translation_key="application",
        device_class=UpdateDeviceClass.FIRMWARE,
    )
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: NanoKVMDataUpdateCoordinator) -> None:
        """Initialize NanoKVM update."""
        super().__init__(coordinator=coordinator, unique_id_suffix="update_application")

    @property
    @override
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available and self.coordinator.application_version_info is not None
        )

    @property
    @override
    def installed_version(self) -> str | None:
        """Version installed and in use."""
        current_version = None
        if self.coordinator.application_version_info is not None:
            current_version = _normalize_version(
                self.coordinator.application_version_info.current
            )

        if current_version is not None:
            return current_version

        return _normalize_version(self.coordinator.device_info.application)

    @property
    @override
    def latest_version(self) -> str | None:
        """Latest version available for install."""
        latest_version = None
        if self.coordinator.application_version_info is not None:
            latest_version = _normalize_version(
                self.coordinator.application_version_info.latest
            )

        if latest_version is not None:
            return latest_version

        return self.installed_version

    @override
    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Trigger NanoKVM application update."""
        del version, backup, kwargs
        try:
            async with self.coordinator.async_client() as client:
                await client.update_application()
        except aiohttp.ServerDisconnectedError:
            _LOGGER.debug(
                "NanoKVM disconnected while starting the application update; "
                "assuming the update is continuing"
            )
            return
        except (TimeoutError, NanoKVMError, aiohttp.ClientError) as err:
            raise HomeAssistantError(
                f"Failed to start NanoKVM application update: {err}"
            ) from err

        await self.coordinator.async_request_refresh()
