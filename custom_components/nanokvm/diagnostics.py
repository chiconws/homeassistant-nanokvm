"""Diagnostics: the entry and the device state from the last poll, secrets removed."""

import dataclasses
from typing import Any

from pydantic import BaseModel

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PREFERRED_HOST,
    CONF_SSH_HOST_KEY,
    CONF_SSH_PASSWORD,
    CONF_SSH_USERNAME,
    CONF_SSL_FINGERPRINT,
)
from .coordinator import NanoKVMConfigEntry

TO_REDACT = {
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PREFERRED_HOST,
    CONF_SSH_HOST_KEY,
    CONF_SSH_PASSWORD,
    CONF_SSH_USERNAME,
    CONF_SSL_FINGERPRINT,
    CONF_USERNAME,
    # Device replies
    "account",
    "addr",
    "bssid",
    "device_key",
    "hostname",
    "ip",
    "mac",
    "mdns",
    "name",
    "ssid",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NanoKVMConfigEntry
) -> dict[str, Any]:
    """Return the entry and every device reply the coordinator holds."""
    coordinator = entry.runtime_data
    device = {
        name: value.model_dump(mode="json")
        for name, value in vars(coordinator).items()
        if isinstance(value, BaseModel)
    }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "is_admin": coordinator.is_admin,
        "is_pro_hardware": coordinator.is_pro_hardware,
        "device": async_redact_data(device, TO_REDACT),
        "ssh_metrics": dataclasses.asdict(coordinator.ssh_metrics),
    }
