"""Number platform for Sipeed NanoKVM."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    LED_BEAD_MIN,
    LED_BEAD_TOTAL_LIMIT,
    LED_BRIGHTNESS_MAX,
    LED_BRIGHTNESS_MIN,
)
from .coordinator import NanoKVMDataUpdateCoordinator
from .entity import NanoKVMEntity
from .led import (
    async_set_led_strip,
    led_strip_available,
    max_horizontal_count,
    max_vertical_count,
)

# The device accepts up to one week.
HDMI_IDLE_TIMEOUT_MAX_MINUTES = 7 * 24 * 60


@dataclass(frozen=True, kw_only=True)
class NanoKVMNumberEntityDescription(NumberEntityDescription):
    """Describes NanoKVM number entity."""

    value_fn: Callable[[NanoKVMDataUpdateCoordinator], float | None] = lambda _: None
    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True
    max_value_fn: Callable[[NanoKVMDataUpdateCoordinator], float]
    set_value_fn: Callable[[NanoKVMDataUpdateCoordinator, float], Awaitable[Any]]


def _led_brightness_value(
    coordinator: NanoKVMDataUpdateCoordinator,
) -> float | None:
    """Return LED strip brightness."""
    return coordinator.led_strip.brightness if coordinator.led_strip else None


def _led_horizontal_value(
    coordinator: NanoKVMDataUpdateCoordinator,
) -> float | None:
    """Return horizontal LED bead count."""
    return coordinator.led_strip.horizontal_count if coordinator.led_strip else None


def _led_vertical_value(
    coordinator: NanoKVMDataUpdateCoordinator,
) -> float | None:
    """Return vertical LED bead count."""
    return coordinator.led_strip.vertical_count if coordinator.led_strip else None


def _led_horizontal_max(coordinator: NanoKVMDataUpdateCoordinator) -> float:
    """Return maximum horizontal beads for the current vertical count."""
    vertical_count = (
        coordinator.led_strip.vertical_count
        if coordinator.led_strip
        else LED_BEAD_MIN
    )
    return max_horizontal_count(vertical_count)


def _led_vertical_max(coordinator: NanoKVMDataUpdateCoordinator) -> float:
    """Return maximum vertical beads for the current horizontal count."""
    horizontal_count = (
        coordinator.led_strip.horizontal_count
        if coordinator.led_strip
        else LED_BEAD_MIN
    )
    return max_vertical_count(horizontal_count)
def _hdmi_idle_timeout_available(coordinator: NanoKVMDataUpdateCoordinator) -> bool:
    """Return whether the HDMI capture idle timeout can be read and changed."""
    return bool(
        coordinator.is_admin
        and coordinator.hdmi_state
        and coordinator.hdmi_state.idle_timeout is not None
    )


NUMBERS: tuple[NanoKVMNumberEntityDescription, ...] = (
    NanoKVMNumberEntityDescription(
        key="led_brightness",
        translation_key="led_brightness",
        entity_category=EntityCategory.CONFIG,
        native_min_value=LED_BRIGHTNESS_MIN,
        native_max_value=LED_BRIGHTNESS_MAX,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=_led_brightness_value,
        available_fn=led_strip_available,
        max_value_fn=lambda _: LED_BRIGHTNESS_MAX,
        set_value_fn=lambda coordinator, value: async_set_led_strip(
            coordinator, brightness=int(value)
        ),
    ),
    NanoKVMNumberEntityDescription(
        key="led_horizontal_beads",
        translation_key="led_horizontal_beads",
        entity_category=EntityCategory.CONFIG,
        native_min_value=LED_BEAD_MIN,
        native_max_value=LED_BEAD_TOTAL_LIMIT - 2,
        native_step=1,
        mode=NumberMode.BOX,
        value_fn=_led_horizontal_value,
        available_fn=led_strip_available,
        max_value_fn=_led_horizontal_max,
        set_value_fn=lambda coordinator, value: async_set_led_strip(
            coordinator, horizontal_count=int(value)
        ),
    ),
    NanoKVMNumberEntityDescription(
        key="led_vertical_beads",
        translation_key="led_vertical_beads",
        entity_category=EntityCategory.CONFIG,
        native_min_value=LED_BEAD_MIN,
        native_max_value=(LED_BEAD_TOTAL_LIMIT - 1) // 2,
        native_step=1,
        mode=NumberMode.BOX,
        value_fn=_led_vertical_value,
        available_fn=led_strip_available,
        max_value_fn=_led_vertical_max,
        set_value_fn=lambda coordinator, value: async_set_led_strip(
            coordinator, vertical_count=int(value)
        ),
    ),
    NanoKVMNumberEntityDescription(
        key="hdmi_idle_timeout",
        translation_key="hdmi_idle_timeout",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.DURATION,
        native_min_value=0,
        native_max_value=HDMI_IDLE_TIMEOUT_MAX_MINUTES,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        value_fn=lambda coordinator: (
            coordinator.hdmi_state.idle_timeout if coordinator.hdmi_state else None
        ),
        available_fn=_hdmi_idle_timeout_available,
        max_value_fn=lambda _: HDMI_IDLE_TIMEOUT_MAX_MINUTES,
        # Zero keeps the capture running with no viewers.
        set_value_fn=lambda coordinator, value: (
            coordinator.client.set_hdmi_idle_timeout(int(value))
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM number based on a config entry."""
    coordinator = entry.runtime_data

    async_add_entities(
        NanoKVMNumber(
            coordinator=coordinator,
            description=description,
        )
        for description in NUMBERS
        if description.available_fn(coordinator)
    )


class NanoKVMNumber(NanoKVMEntity, NumberEntity):
    """Defines a NanoKVM number."""

    entity_description: NanoKVMNumberEntityDescription

    def __init__(
        self,
        coordinator: NanoKVMDataUpdateCoordinator,
        description: NanoKVMNumberEntityDescription,
    ) -> None:
        """Initialize NanoKVM number."""
        self.entity_description = description
        super().__init__(
            coordinator=coordinator,
            unique_id_suffix=f"number_{description.key}",
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.entity_description.available_fn(
            self.coordinator
        )

    @property
    def native_value(self) -> float | None:
        """Return the number value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def native_max_value(self) -> float:
        """Return the dynamic maximum value."""
        return self.entity_description.max_value_fn(self.coordinator)

    async def async_set_native_value(self, value: float) -> None:
        """Set the number value."""
        async with self._async_device_action():
            await self.entity_description.set_value_fn(self.coordinator, value)
        await self.coordinator.async_request_refresh()
