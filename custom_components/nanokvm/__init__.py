"""The Sipeed NanoKVM integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_SSH_HOST_KEY,
    CONF_SSL_FINGERPRINT,
    CONF_USE_STATIC_HOST,
    DOMAIN,
)
from .coordinator import NanoKVMDataUpdateCoordinator
from .services import async_register_services
from .ssh_host_keys import async_write_known_hosts
from .utils import (
    CannotConnect,
    InvalidAuth,
    SSLCertificateChanged,
    api_connection_options,
    async_connect,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services."""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sipeed NanoKVM from a config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    use_static_host = entry.data.get(CONF_USE_STATIC_HOST, False)

    _LOGGER.info(
        "Setting up NanoKVM integration for %s (static_host: %s)",
        host,
        use_static_host,
    )

    ssl_fingerprint = entry.data.get(CONF_SSL_FINGERPRINT)
    options = api_connection_options(host, ssl_fingerprint)
    try:
        client, device_info = await async_connect(
            options, username, password, lambda client: client.get_info()
        )
    except InvalidAuth as err:
        raise ConfigEntryAuthFailed(
            f"Authentication failed for NanoKVM at {host}"
        ) from err
    except SSLCertificateChanged as err:
        raise ConfigEntryAuthFailed(
            f"SSL certificate changed for NanoKVM at {host}"
        ) from err
    except CannotConnect as err:
        raise ConfigEntryNotReady(
            f"Failed to fetch initial device info: {err.__cause__}"
        ) from err.__cause__

    ssh_known_hosts: str | None = None
    if approved_ssh_host_key := entry.data.get(CONF_SSH_HOST_KEY):
        ssh_known_hosts = str(
            await async_write_known_hosts(hass, entry.entry_id, approved_ssh_host_key)
        )

    coordinator = NanoKVMDataUpdateCoordinator(
        hass,
        entry,
        client=client,
        username=username,
        password=password,
        device_info=device_info,
        ssh_known_hosts=ssh_known_hosts,
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
