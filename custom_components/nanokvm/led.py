"""NanoKVM Pro LED strip helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from nanokvm.models import GetLedStripRsp

from .const import (
    LED_BEAD_MIN,
    LED_BEAD_TOTAL_LIMIT,
    LED_BRIGHTNESS_MAX,
    LED_BRIGHTNESS_MIN,
)

if TYPE_CHECKING:
    from .coordinator import NanoKVMDataUpdateCoordinator


@dataclass(slots=True)
class LedBrightnessRequest:
    """Brightness last sent to the device and what it reported afterwards.

    NanoKVM Pro can report a lower brightness than the one it stores. Writing
    the reported value back with an unrelated change would lower the setting.
    """

    requested: int
    reported: int | None = None


def max_horizontal_count(vertical_count: int) -> int:
    """Return maximum horizontal bead count for a vertical bead count."""
    return LED_BEAD_TOTAL_LIMIT - (2 * vertical_count)


def max_vertical_count(horizontal_count: int) -> int:
    """Return maximum vertical bead count for a horizontal bead count."""
    return (LED_BEAD_TOTAL_LIMIT - horizontal_count) // 2


def note_reported_led_strip(
    coordinator: NanoKVMDataUpdateCoordinator, reported: GetLedStripRsp | None
) -> bool:
    """Record the brightness reported by the first poll after a write."""
    request = coordinator.led_brightness_request
    if request is None or request.reported is not None or reported is None:
        return False
    request.reported = reported.brightness
    return True


async def async_set_led_strip(
    coordinator: NanoKVMDataUpdateCoordinator,
    *,
    on: bool | None = None,
    brightness: int | None = None,
    horizontal_count: int | None = None,
    vertical_count: int | None = None,
) -> None:
    """Send a partial LED strip update, keeping the other settings as requested."""
    current = coordinator.led_strip
    if current is None:
        raise HomeAssistantError("LED strip state is unavailable")

    changes_settings = any(
        value is not None for value in (brightness, horizontal_count, vertical_count)
    )
    request = coordinator.led_brightness_request
    if (
        brightness is None
        and request is not None
        and request.reported in (None, current.brightness)
    ):
        # Nothing else changed the brightness since the last write.
        brightness = request.requested

    on = current.on if on is None else on
    brightness = current.brightness if brightness is None else brightness
    if horizontal_count is None:
        horizontal_count = current.horizontal_count
    if vertical_count is None:
        vertical_count = current.vertical_count

    if not LED_BRIGHTNESS_MIN <= brightness <= LED_BRIGHTNESS_MAX:
        raise HomeAssistantError(
            f"LED brightness must be between {LED_BRIGHTNESS_MIN} and "
            f"{LED_BRIGHTNESS_MAX}"
        )
    if horizontal_count < LED_BEAD_MIN:
        raise HomeAssistantError(
            f"Horizontal LED beads must be at least {LED_BEAD_MIN}"
        )
    if vertical_count < LED_BEAD_MIN:
        raise HomeAssistantError(f"Vertical LED beads must be at least {LED_BEAD_MIN}")
    if horizontal_count + (2 * vertical_count) > LED_BEAD_TOTAL_LIMIT:
        raise HomeAssistantError(
            "LED bead counts must satisfy horizontal_count + "
            f"(2 * vertical_count) <= {LED_BEAD_TOTAL_LIMIT}"
        )
    if changes_settings and not on:
        # The device accepts the request but ignores settings while off.
        raise HomeAssistantError("Turn the LED strip on before changing its settings")

    await coordinator.client.set_led_strip(
        on=on,
        brightness=brightness,
        horizontal_count=horizontal_count,
        vertical_count=vertical_count,
    )
    coordinator.led_brightness_request = LedBrightnessRequest(brightness)
