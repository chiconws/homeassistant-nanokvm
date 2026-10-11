"""Switch platform for Sipeed NanoKVM."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from nanokvm.client import NanoKVMError
from nanokvm.models import GpioType, VirtualDevice

from .coordinator import NanoKVMDataUpdateCoordinator
from .entity import NanoKVMEntity, async_add_entities_as_supported
from .led import async_set_led_strip, led_strip_available

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class NanoKVMSwitchEntityDescription(SwitchEntityDescription):
    """Describes NanoKVM switch entity."""

    value_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = (
        lambda _: False
    )
    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True
    turn_on_fn: Callable[[NanoKVMDataUpdateCoordinator], Awaitable[Any]] | None = None
    turn_off_fn: Callable[[NanoKVMDataUpdateCoordinator], Awaitable[Any]] | None = None
    virtual_device: VirtualDevice | None = None


def _hdmi_value(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return HDMI switch state."""
    return coordinator.hdmi_state.enabled if coordinator.hdmi_state else False


def _hdmi_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether HDMI controls are available."""
    return coordinator.supports_hdmi_endpoint and coordinator.hdmi_state is not None


def _watchdog_value(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return watchdog switch state."""
    return bool(coordinator.ssh_metrics.watchdog_enabled)


def _watchdog_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether the watchdog switch should be available."""
    return (
        coordinator.supports_watchdog
        and coordinator.ssh_metrics.watchdog_enabled is not None
    )


def _pro_virtual_device_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether Pro virtual-device controls are available."""
    return coordinator.is_pro_hardware and coordinator.virtual_device_info is not None


def _pro_virtual_mic_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether Pro virtual microphone control is available."""
    return (
        _pro_virtual_device_available(coordinator)
        and coordinator.virtual_device_info.mic is not None
    )


def _hdmi_capture_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether Pro HDMI capture control is available."""
    return coordinator.is_pro_hardware and coordinator.hdmi_capture is not None


def _hdmi_passthrough_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether Pro HDMI passthrough control is available."""
    return coordinator.is_pro_hardware and coordinator.hdmi_passthrough is not None


def _low_power_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether Pro low-power mode control is available."""
    return coordinator.is_pro_hardware and coordinator.low_power is not None


def _led_strip_value(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return LED strip state."""
    return bool(coordinator.led_strip and coordinator.led_strip.on)


SWITCHES: tuple[NanoKVMSwitchEntityDescription, ...] = (
    NanoKVMSwitchEntityDescription(
        key="ssh",
        translation_key="ssh",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: bool(
            coordinator.ssh_state and coordinator.ssh_state.enabled
        ),
        turn_on_fn=lambda coordinator: coordinator.client.enable_ssh(),
        turn_off_fn=lambda coordinator: coordinator.client.disable_ssh(),
    ),
    NanoKVMSwitchEntityDescription(
        key="mdns",
        translation_key="mdns",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: bool(
            coordinator.mdns_state and coordinator.mdns_state.enabled
        ),
        turn_on_fn=lambda coordinator: coordinator.client.enable_mdns(),
        turn_off_fn=lambda coordinator: coordinator.client.disable_mdns(),
    ),
    NanoKVMSwitchEntityDescription(
        key="virtual_network",
        translation_key="virtual_network",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: bool(
            coordinator.virtual_device_info and coordinator.virtual_device_info.network
        ),
        available_fn=lambda coordinator: coordinator.is_non_pro_hardware,
        virtual_device=VirtualDevice.NETWORK,
    ),
    NanoKVMSwitchEntityDescription(
        key="virtual_disk",
        translation_key="virtual_disk",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: bool(
            coordinator.virtual_device_info and coordinator.virtual_device_info.disk
        ),
        available_fn=lambda coordinator: coordinator.is_non_pro_hardware,
        virtual_device=VirtualDevice.DISK,
    ),
    NanoKVMSwitchEntityDescription(
        key="virtual_network",
        translation_key="virtual_network",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: bool(
            coordinator.virtual_device_info and coordinator.virtual_device_info.network
        ),
        available_fn=_pro_virtual_device_available,
        virtual_device=VirtualDevice.NETWORK,
    ),
    NanoKVMSwitchEntityDescription(
        key="virtual_mic",
        translation_key="virtual_mic",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: bool(
            coordinator.virtual_device_info and coordinator.virtual_device_info.mic
        ),
        available_fn=_pro_virtual_mic_available,
        virtual_device=VirtualDevice.MIC,
    ),
    NanoKVMSwitchEntityDescription(
        key="power",
        translation_key="power",
        value_fn=lambda coordinator: bool(
            coordinator.gpio_info and coordinator.gpio_info.pwr
        ),
        turn_on_fn=lambda coordinator: coordinator.client.push_button(GpioType.POWER, 200),
        turn_off_fn=lambda coordinator: coordinator.client.push_button(GpioType.POWER, 200),
    ),
    NanoKVMSwitchEntityDescription(
        key="hdmi",
        translation_key="hdmi",
        entity_category=EntityCategory.CONFIG,
        value_fn=_hdmi_value,
        turn_on_fn=lambda coordinator: coordinator.client.enable_hdmi(),
        turn_off_fn=lambda coordinator: coordinator.client.disable_hdmi(),
        available_fn=_hdmi_available,
    ),
    NanoKVMSwitchEntityDescription(
        key="hdmi_capture",
        translation_key="hdmi_capture",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: bool(
            coordinator.hdmi_capture and coordinator.hdmi_capture.enabled
        ),
        turn_on_fn=lambda coordinator: coordinator.client.set_hdmi_capture(True),
        turn_off_fn=lambda coordinator: coordinator.client.set_hdmi_capture(False),
        available_fn=_hdmi_capture_available,
    ),
    NanoKVMSwitchEntityDescription(
        key="hdmi_passthrough",
        translation_key="hdmi_passthrough",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: bool(
            coordinator.hdmi_passthrough and coordinator.hdmi_passthrough.enabled
        ),
        turn_on_fn=lambda coordinator: coordinator.client.set_hdmi_passthrough(True),
        turn_off_fn=lambda coordinator: coordinator.client.set_hdmi_passthrough(False),
        available_fn=_hdmi_passthrough_available,
    ),
    NanoKVMSwitchEntityDescription(
        key="low_power",
        translation_key="low_power",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda coordinator: bool(
            coordinator.low_power and coordinator.low_power.enabled
        ),
        turn_on_fn=lambda coordinator: coordinator.client.set_low_power(True),
        turn_off_fn=lambda coordinator: coordinator.client.set_low_power(False),
        available_fn=_low_power_available,
    ),
    NanoKVMSwitchEntityDescription(
        key="led_strip",
        translation_key="led_strip",
        entity_category=EntityCategory.CONFIG,
        value_fn=_led_strip_value,
        turn_on_fn=lambda coordinator: async_set_led_strip(coordinator, on=True),
        turn_off_fn=lambda coordinator: async_set_led_strip(coordinator, on=False),
        available_fn=led_strip_available,
    ),
)

SSH_SWITCHES: tuple[NanoKVMSwitchEntityDescription, ...] = (
    NanoKVMSwitchEntityDescription(
        key="watchdog",
        translation_key="watchdog",
        entity_category=EntityCategory.CONFIG,
        value_fn=_watchdog_value,
        available_fn=_watchdog_available,
    ),
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM switch based on a config entry."""
    coordinator = entry.runtime_data

    def switch_class(
        description: NanoKVMSwitchEntityDescription,
    ) -> type[NanoKVMSwitch]:
        if description.virtual_device is not None:
            return NanoKVMVirtualDeviceSwitch
        if description.key == "power":
            return NanoKVMPowerSwitch
        return NanoKVMSwitch

    async_add_entities(
        switch_class(description)(coordinator=coordinator, description=description)
        for description in SWITCHES
        if description.available_fn(coordinator)
    )

    async_add_entities_as_supported(
        entry,
        coordinator,
        async_add_entities,
        SSH_SWITCHES,
        lambda description: description.available_fn(coordinator),
        lambda description: NanoKVMWatchdogSwitch(
            coordinator=coordinator, description=description
        ),
    )


class NanoKVMSwitch(NanoKVMEntity, SwitchEntity):
    """Defines a NanoKVM switch."""

    entity_description: NanoKVMSwitchEntityDescription

    def __init__(
        self,
        coordinator: NanoKVMDataUpdateCoordinator,
        description: NanoKVMSwitchEntityDescription,
    ) -> None:
        """Initialize NanoKVM switch."""
        self.entity_description = description
        super().__init__(
            coordinator=coordinator,
            unique_id_suffix=f"switch_{description.key}",
        )

    @property
    def is_on(self) -> bool:
        """Return the state of the switch."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.entity_description.available_fn(self.coordinator)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        await self._async_set(False)

    async def _async_call_handler(self, enabled: bool) -> None:
        """Run the description's device call for the requested state."""
        description = self.entity_description
        handler = description.turn_on_fn if enabled else description.turn_off_fn
        assert handler is not None
        async with self._async_device_action():
            await handler(self.coordinator)

    async def _async_set(self, enabled: bool) -> None:
        """Switch to the requested state and refresh."""
        await self._async_call_handler(enabled)
        await self.coordinator.async_request_refresh()


class NanoKVMPowerSwitch(NanoKVMSwitch):
    """Defines a NanoKVM power switch with special shutdown behavior."""

    _shutdown_monitor: asyncio.Task[None] | None = None

    async def _async_current_power_state(self) -> bool | None:
        """Refresh and return the current power state when available."""
        await self.coordinator.async_refresh()
        if self.coordinator.gpio_info is None:
            return None
        return bool(self.coordinator.gpio_info.pwr)

    async def _async_set(self, enabled: bool) -> None:
        """Press the power button unless the host is already in that state."""
        if await self._async_current_power_state() is enabled:
            return
        await self._async_call_handler(enabled)

        if enabled:
            await asyncio.sleep(1)
            await self.coordinator.async_request_refresh()
            return

        # A host can take minutes to shut down, so follow it in the background
        # instead of holding the action open.
        if self._shutdown_monitor is not None:
            self._shutdown_monitor.cancel()
        self._shutdown_monitor = self.hass.async_create_background_task(
            self._async_monitor_shutdown(),
            name=f"nanokvm power-off monitor {self.unique_id}",
        )

    async def _async_monitor_shutdown(self) -> None:
        """Refresh until the host reports power off or the wait times out."""
        SHUTDOWN_TIMEOUT = 300
        SHUTDOWN_POLL_INTERVAL = 5

        start_time = self.hass.loop.time()
        while self.hass.loop.time() - start_time < SHUTDOWN_TIMEOUT:
            await self.coordinator.async_request_refresh()
            if self.coordinator.gpio_info and not self.coordinator.gpio_info.pwr:
                await self.coordinator.async_request_refresh()
                return
            await asyncio.sleep(SHUTDOWN_POLL_INTERVAL)

        _LOGGER.warning("Device did not turn off within %s seconds", SHUTDOWN_TIMEOUT)
        await self.coordinator.async_request_refresh()

    async def async_will_remove_from_hass(self) -> None:
        """Stop following a pending shutdown when the entity is removed."""
        await super().async_will_remove_from_hass()
        if self._shutdown_monitor is not None:
            self._shutdown_monitor.cancel()
            self._shutdown_monitor = None


class NanoKVMVirtualDeviceSwitch(NanoKVMSwitch):
    """Defines a virtual device switch backed by a toggle-only API."""

    async def _async_set(self, enabled: bool) -> None:
        """Refresh first, then toggle only when the requested state differs."""
        await self.coordinator.async_refresh()
        if self.is_on == enabled:
            return

        async with self._async_device_action() as client:
            await client.update_virtual_device(self.entity_description.virtual_device)
        await self.coordinator.async_request_refresh()


class NanoKVMWatchdogSwitch(NanoKVMSwitch):
    """Defines a NanoKVM watchdog switch backed by SSH file control."""

    async def _async_set(self, enabled: bool) -> None:
        """Set watchdog state via SSH and refresh coordinator state."""
        collector = self.coordinator.async_ensure_ssh_metrics_collector()
        try:
            await collector.set_watchdog_enabled(enabled)
        except NanoKVMError as err:
            raise HomeAssistantError(
                f"NanoKVM did not accept the request: {err}"
            ) from err
        await self.coordinator.async_request_refresh()
