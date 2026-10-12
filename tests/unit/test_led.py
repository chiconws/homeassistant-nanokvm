"""Tests for NanoKVM Pro LED strip configuration helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.exceptions import HomeAssistantError
from nanokvm.models import GetLedStripRsp
import pytest

from custom_components.nanokvm.const import LED_BEAD_TOTAL_LIMIT
from custom_components.nanokvm.led import (
    async_set_led_strip,
    max_horizontal_count,
    max_vertical_count,
    note_reported_led_strip,
)


def _device_state() -> GetLedStripRsp:
    """Return a complete library response matching a valid LED configuration."""
    return GetLedStripRsp(on=True, hor=30, ver=20, brightness=75)


def test_led_axis_limits_share_the_total_bead_constraint() -> None:
    """Axis-specific maximum helpers must enforce the aggregate strip limit."""
    assert max_horizontal_count(20) == 110
    assert max_vertical_count(30) == 60
    assert max_horizontal_count(0) == LED_BEAD_TOTAL_LIMIT


def _led_coordinator(brightness: int = 68) -> SimpleNamespace:
    """Return a coordinator-shaped object with a reported LED strip state."""
    return SimpleNamespace(
        led_strip=GetLedStripRsp(on=False, hor=114, ver=18, brightness=brightness),
        client=SimpleNamespace(set_led_strip=AsyncMock()),
        led_brightness_request=None,
    )


def _sent_brightness(coordinator: SimpleNamespace) -> int:
    """Return the brightness of the most recent device write."""
    return coordinator.client.set_led_strip.await_args.kwargs["brightness"]


@pytest.mark.parametrize("brightness", [0, 100])
async def test_led_write_accepts_brightness_boundaries(brightness: int) -> None:
    """Both documented brightness boundaries must be valid."""
    coordinator = _led_coordinator()

    await async_set_led_strip(coordinator, on=True, brightness=brightness)

    assert _sent_brightness(coordinator) == brightness


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"brightness": -1}, "LED brightness must be between 0 and 100"),
        ({"brightness": 101}, "LED brightness must be between 0 and 100"),
        ({"horizontal_count": 0}, "Horizontal LED beads must be at least 1"),
        ({"vertical_count": 0}, "Vertical LED beads must be at least 1"),
        (
            {"horizontal_count": 101, "vertical_count": 25},
            r"horizontal_count \+ \(2 \* vertical_count\)",
        ),
    ],
)
async def test_led_write_rejects_invalid_merged_values(
    change: dict[str, int], message: str
) -> None:
    """Values outside the hardware limits are rejected before any device write."""
    coordinator = _led_coordinator()

    with pytest.raises(HomeAssistantError, match=message):
        await async_set_led_strip(coordinator, on=True, **change)
    coordinator.client.set_led_strip.assert_not_awaited()


async def test_led_write_requires_current_device_state() -> None:
    """Partial updates cannot be sent without a complete device baseline."""
    coordinator = _led_coordinator()
    coordinator.led_strip = None

    with pytest.raises(HomeAssistantError, match="LED strip state is unavailable"):
        await async_set_led_strip(coordinator, brightness=50)


async def test_led_write_applies_complete_valid_update() -> None:
    """Explicit updates must override all device values."""
    coordinator = _led_coordinator()

    await async_set_led_strip(
        coordinator, on=True, brightness=0, horizontal_count=50, vertical_count=50
    )

    coordinator.client.set_led_strip.assert_awaited_once_with(
        on=True, brightness=0, horizontal_count=50, vertical_count=50
    )


async def test_led_toggle_keeps_requested_brightness_when_device_reports_lower() -> None:
    """A lower reported brightness is not written back by an unrelated change."""
    coordinator = _led_coordinator(brightness=68)

    await async_set_led_strip(coordinator, on=True)
    assert _sent_brightness(coordinator) == 68

    # The device now reports the brightness it applies instead of the stored one.
    coordinator.led_strip = GetLedStripRsp(on=True, hor=114, ver=18, brightness=57)
    note_reported_led_strip(coordinator, coordinator.led_strip)
    await async_set_led_strip(coordinator, on=False)

    assert _sent_brightness(coordinator) == 68
    coordinator.client.set_led_strip.assert_awaited_with(
        on=False, brightness=68, horizontal_count=114, vertical_count=18
    )


async def test_led_write_before_the_next_poll_reuses_the_pending_request() -> None:
    """Two quick writes keep the brightness of the first one."""
    coordinator = _led_coordinator(brightness=68)

    await async_set_led_strip(coordinator, on=True, brightness=40)
    await async_set_led_strip(coordinator, on=True)

    assert _sent_brightness(coordinator) == 40


async def test_led_toggle_follows_brightness_changed_outside_home_assistant() -> None:
    """A brightness changed on the device itself replaces the remembered request."""
    coordinator = _led_coordinator(brightness=68)
    await async_set_led_strip(coordinator, on=True)
    note_reported_led_strip(coordinator, coordinator.led_strip)
    note_reported_led_strip(coordinator, None)

    coordinator.led_strip = GetLedStripRsp(on=True, hor=114, ver=18, brightness=20)
    note_reported_led_strip(coordinator, coordinator.led_strip)
    await async_set_led_strip(coordinator, on=False)

    assert _sent_brightness(coordinator) == 20


async def test_led_write_failure_keeps_the_previous_request() -> None:
    """A rejected or invalid write does not replace the remembered brightness."""
    coordinator = _led_coordinator(brightness=68)
    await async_set_led_strip(coordinator, on=True, brightness=40)
    coordinator.client.set_led_strip.side_effect = RuntimeError("device busy")

    with pytest.raises(RuntimeError):
        await async_set_led_strip(coordinator, on=True, brightness=90)
    with pytest.raises(HomeAssistantError):
        await async_set_led_strip(coordinator, on=True, brightness=500)

    assert coordinator.led_brightness_request.requested == 40


@pytest.mark.parametrize(
    "change", [{"brightness": 40}, {"horizontal_count": 100}, {"vertical_count": 10}]
)
async def test_led_settings_are_rejected_while_the_strip_is_off(
    change: dict[str, int],
) -> None:
    """The device ignores settings sent while off, so they are not sent at all."""
    coordinator = _led_coordinator()

    with pytest.raises(HomeAssistantError, match="Turn the LED strip on"):
        await async_set_led_strip(coordinator, **change)
    coordinator.client.set_led_strip.assert_not_awaited()

    await async_set_led_strip(coordinator, on=True, **change)
    coordinator.client.set_led_strip.assert_awaited_once()
