"""Tests for NanoKVM button descriptions and availability helpers."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

from homeassistant.const import EntityCategory

from custom_components.nanokvm.button import (
    BUTTONS,
    NanoKVMButton,
)


def test_button_descriptions_expose_stable_metadata() -> None:
    """Button descriptions must retain their UI-facing metadata contract."""
    assert [
        (
            description.key,
            description.translation_key,
            description.entity_category,
        )
        for description in BUTTONS
    ] == [
        ("power", "power", None),
        ("reset", "reset", None),
        ("reboot", "reboot", None),
        ("reset_hdmi", "reset_hdmi", EntityCategory.CONFIG),
        ("reset_hid", "reset_hid", EntityCategory.CONFIG),
        ("sync_time", "sync_time", EntityCategory.CONFIG),
    ]
    assert all(description.press_fn is not None for description in BUTTONS)


def test_button_description_availability_matches_each_hardware_family(
    coordinator_state_factory: Callable[..., SimpleNamespace],
) -> None:
    """Hardware-specific descriptions must not hide generic buttons."""
    states = {
        "non_pcie": coordinator_state_factory(
            supports_hdmi_endpoint=False,
            is_pro_hardware=False,
        ),
        "pcie": coordinator_state_factory(
            supports_hdmi_endpoint=True,
            is_pro_hardware=False,
        ),
        "pro": coordinator_state_factory(
            supports_hdmi_endpoint=False,
            is_pro_hardware=True,
        ),
    }

    assert {
        name: {
            description.key
            for description in BUTTONS
            if description.available_fn(state)
        }
        for name, state in states.items()
    } == {
        "non_pcie": {"power", "reset", "reboot", "reset_hid"},
        "pcie": {"power", "reset", "reboot", "reset_hdmi", "reset_hid"},
        "pro": {"power", "reset", "reboot", "reset_hid", "sync_time"},
    }


def test_button_entity_uses_description_key_in_unique_id(
    coordinator_state_factory: Callable[..., SimpleNamespace],
) -> None:
    """Button entities must derive a stable platform-qualified unique ID."""
    coordinator = coordinator_state_factory(
        device_info=SimpleNamespace(device_key="device-key")
    )
    description = next(item for item in BUTTONS if item.key == "power")

    entity = NanoKVMButton(coordinator, description)

    assert entity.entity_description is description
    assert entity.unique_id == "device-key_button_power"
