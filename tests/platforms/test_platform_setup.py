"""Tests for Home Assistant platform setup and dynamic entity registration."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST
import pytest
from nanokvm.models import HidMode, HWVersion
from yarl import URL

import custom_components.nanokvm.binary_sensor as binary_sensor_module
import custom_components.nanokvm.button as button_module
import custom_components.nanokvm.number as number_module
import custom_components.nanokvm.select as select_module
import custom_components.nanokvm.sensor as sensor_module
import custom_components.nanokvm.switch as switch_module
import custom_components.nanokvm.update as update_module


def _coordinator(**overrides: object) -> SimpleNamespace:
    """Build a coordinator-shaped state object accepted by every platform."""
    values: dict[str, object] = {
        "application_version_info": SimpleNamespace(current="1.0.0", latest="1.1.0"),
        "client": SimpleNamespace(url=URL("http://nanokvm.local/api/")),
        "device_info": SimpleNamespace(
            application="1.0.0",
            device_key="test-device",
            image="2026-01-01",
            ips=[],
        ),
        "account": None,
        "gpio_info": SimpleNamespace(pwr=False, hdd=False),
        "hardware_info": None,
        "is_admin": True,
        "keyboard_leds": None,
        "hdmi_capture": None,
        "hdmi_passthrough": None,
        "hdmi_state": None,
        "hid_mode": None,
        "hostname_info": None,
        "is_pro_hardware": False,
        "last_update_success": True,
        "lcd_time_format": None,
        "led_strip": None,
        "low_power": None,
        "mdns_state": SimpleNamespace(enabled=False),
        "mounted_image": None,
        "mouse_jiggler_state": None,
        "oled_info": None,
        "ssh_state": SimpleNamespace(enabled=False),
        "static_ip": None,
        "is_non_pro_hardware": False,
        "supports_hdmi_endpoint": False,
        "supports_watchdog": False,
        "swap_size": None,
        "tailscale_status": None,
        "time_status": None,
        "virtual_device_info": None,
        "ssh_metrics": SimpleNamespace(watchdog_enabled=None),
        "wifi_status": None,
    }
    values.update(overrides)
    coordinator = SimpleNamespace(**values, listeners=[])
    coordinator.async_add_listener = lambda listener: (
        coordinator.listeners.append(listener) or MagicMock()
    )
    return coordinator


def _notify(coordinator: SimpleNamespace) -> None:
    """Deliver one coordinator update to every registered listener."""
    for listener in coordinator.listeners:
        listener()


def _configure_hass(
    hass_mock: MagicMock, config_entry_mock: MagicMock, coordinator: object
) -> None:
    """Store a coordinator under the entry like integration setup does."""
    config_entry_mock.data[CONF_HOST] = "nanokvm.local"
    config_entry_mock.runtime_data = coordinator


def _entity_collector() -> tuple[Callable[[object], None], list[list[object]]]:
    """Return an add-entities callback which eagerly materializes generators."""
    batches: list[list[object]] = []

    def add_entities(entities: object) -> None:
        batches.append(list(entities))

    return add_entities, batches


@pytest.mark.asyncio
async def test_binary_sensor_setup_adds_dynamic_media_and_network_entities(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
) -> None:
    """Binary sensor setup must add each dynamic entity once it is supported."""
    coordinator = _coordinator()
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await binary_sensor_module.async_setup_entry(
        hass_mock, config_entry_mock, add_entities
    )

    assert [
        [entity.entity_description.key for entity in batch] for batch in batches
    ] == [["power_led"]]
    assert config_entry_mock.async_on_unload.call_count == 1

    _notify(coordinator)
    assert len(batches) == 1

    coordinator.mounted_image = SimpleNamespace(file="/data/image.iso")
    coordinator.is_non_pro_hardware = True
    _notify(coordinator)
    assert [entity.entity_description.key for entity in batches[1]] == ["cdrom_mode"]

    coordinator.device_info.ips = [
        SimpleNamespace(addr="192.0.2.10", type="wired", version="IPv4", name="eth0")
    ]
    _notify(coordinator)
    _notify(coordinator)
    assert [entity.entity_description.key for entity in batches[2]] == [
        "wired_connected"
    ]
    assert len(batches) == 3


@pytest.mark.asyncio
async def test_binary_sensor_setup_handles_media_already_mounted(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
) -> None:
    """Mounted media present during setup must create its entity immediately."""
    coordinator = _coordinator(
        mounted_image=SimpleNamespace(file="/data/image.iso"),
        is_non_pro_hardware=True,
    )
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await binary_sensor_module.async_setup_entry(
        hass_mock, config_entry_mock, add_entities
    )

    assert [entity.entity_description.key for entity in batches[0]] == [
        "power_led",
        "cdrom_mode",
    ]


@pytest.mark.asyncio
async def test_sensor_setup_adds_dynamic_network_media_and_ssh_entities(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
) -> None:
    """Sensor setup must add each dynamic entity group once it is supported."""
    coordinator = _coordinator()
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await sensor_module.async_setup_entry(hass_mock, config_entry_mock, add_entities)

    assert [entity.entity_description.key for entity in batches[0]] == [
        "ip_address",
        "tailscale_state",
    ]
    assert config_entry_mock.async_on_unload.call_count == 1

    _notify(coordinator)
    assert len(batches) == 1

    coordinator.device_info.ips = [
        SimpleNamespace(addr="192.0.2.10", type="wired", version="IPv4", name="eth0")
    ]
    _notify(coordinator)
    _notify(coordinator)
    assert [entity.entity_description.key for entity in batches[1]] == [
        "wired_ip_address"
    ]

    coordinator.mounted_image = SimpleNamespace(file="/data/image.iso")
    _notify(coordinator)
    assert [entity.entity_description.key for entity in batches[2]] == ["mounted_image"]

    coordinator.ssh_state = SimpleNamespace(enabled=True)
    _notify(coordinator)
    assert [entity.entity_description.key for entity in batches[3]] == [
        "uptime",
        "cpu_temperature",
        "memory_used_percent",
        "storage_used_percent",
    ]
    _notify(coordinator)
    assert len(batches) == 4


@pytest.mark.asyncio
async def test_sensor_setup_handles_initial_media_and_ssh_state(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
) -> None:
    """Initial mounted media and enabled SSH must create dynamic entities at setup."""
    coordinator = _coordinator(
        mounted_image=SimpleNamespace(file="/data/image.iso"),
        ssh_state=SimpleNamespace(enabled=True),
    )
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await sensor_module.async_setup_entry(hass_mock, config_entry_mock, add_entities)

    assert [entity.entity_description.key for entity in batches[0]] == [
        "ip_address",
        "tailscale_state",
        "mounted_image",
        "uptime",
        "cpu_temperature",
        "memory_used_percent",
        "storage_used_percent",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supports_hdmi_endpoint", "is_pro_hardware", "expected_keys"),
    [
        (False, False, ["power", "reset", "reboot", "reset_hid"]),
        (
            True,
            True,
            ["power", "reset", "reboot", "reset_hdmi", "reset_hid", "sync_time"],
        ),
    ],
)
async def test_button_setup_filters_entities_by_hardware(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
    supports_hdmi_endpoint: bool,
    is_pro_hardware: bool,
    expected_keys: list[str],
) -> None:
    """Button setup must expose only actions supported by detected hardware."""
    coordinator = _coordinator(
        supports_hdmi_endpoint=supports_hdmi_endpoint,
        is_pro_hardware=is_pro_hardware,
    )
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await button_module.async_setup_entry(hass_mock, config_entry_mock, add_entities)

    assert [entity.entity_description.key for entity in batches[0]] == expected_keys


@pytest.mark.asyncio
@pytest.mark.parametrize("has_led", [False, True])
async def test_number_setup_filters_on_led_capability(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
    has_led: bool,
) -> None:
    """LED numbers must be created only when Pro LED state is available."""
    led_state = (
        SimpleNamespace(on=True, brightness=50, horizontal_count=30, vertical_count=20)
        if has_led
        else None
    )
    coordinator = _coordinator(is_pro_hardware=has_led, led_strip=led_state)
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await number_module.async_setup_entry(hass_mock, config_entry_mock, add_entities)

    assert [entity.entity_description.key for entity in batches[0]] == (
        ["led_brightness", "led_horizontal_beads", "led_vertical_beads"]
        if has_led
        else []
    )


@pytest.mark.asyncio
async def test_select_setup_filters_on_available_state(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
) -> None:
    """Select setup must expose each control whose backing state is available."""
    coordinator = _coordinator(
        hid_mode=SimpleNamespace(mode=object()),
        is_pro_hardware=True,
        lcd_time_format=SimpleNamespace(format=object()),
        mouse_jiggler_state=SimpleNamespace(enabled=False),
        oled_info=SimpleNamespace(exist=True, sleep=0),
        swap_size=0,
        virtual_device_info=SimpleNamespace(
            is_emmc_exist=True,
            is_sd_card_exist=True,
            mounted_disk="emmc",
        ),
    )
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await select_module.async_setup_entry(hass_mock, config_entry_mock, add_entities)

    assert [entity.entity_description.key for entity in batches[0]] == [
        "hid_mode",
        "mouse_jiggler_mode",
        "oled_sleep_timeout",
        "swap_size",
        "lcd_time_format",
        "virtual_disk_type",
    ]


@pytest.mark.asyncio
async def test_switch_setup_uses_specialized_entities_and_dynamic_watchdog(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
) -> None:
    """Switch setup must select specialized entity classes and add watchdog once."""
    coordinator = _coordinator(
        is_non_pro_hardware=True,
        virtual_device_info=SimpleNamespace(network=False, disk=False, mic=None),
    )
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await switch_module.async_setup_entry(hass_mock, config_entry_mock, add_entities)

    assert [entity.entity_description.key for entity in batches[0]] == [
        "ssh",
        "mdns",
        "virtual_network",
        "virtual_disk",
        "power",
    ]
    assert isinstance(batches[0][2], switch_module.NanoKVMVirtualDeviceSwitch)
    assert isinstance(batches[0][3], switch_module.NanoKVMVirtualDeviceSwitch)
    assert isinstance(batches[0][4], switch_module.NanoKVMPowerSwitch)

    _notify(coordinator)
    assert len(batches) == 1

    coordinator.supports_watchdog = True
    coordinator.ssh_metrics = SimpleNamespace(watchdog_enabled=False)
    _notify(coordinator)
    _notify(coordinator)
    assert [entity.entity_description.key for entity in batches[1]] == ["watchdog"]
    assert isinstance(batches[1][0], switch_module.NanoKVMWatchdogSwitch)
    assert len(batches) == 2


@pytest.mark.asyncio
async def test_switch_setup_handles_initial_watchdog_state(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
) -> None:
    """An already available watchdog must be registered during setup."""
    coordinator = _coordinator(
        supports_watchdog=True, ssh_metrics=SimpleNamespace(watchdog_enabled=True)
    )
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await switch_module.async_setup_entry(hass_mock, config_entry_mock, add_entities)

    assert [entity.entity_description.key for entity in batches[1]] == ["watchdog"]


@pytest.mark.asyncio
async def test_update_setup_adds_application_update(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
) -> None:
    """Update setup must expose the application firmware updater."""
    coordinator = _coordinator()
    _configure_hass(hass_mock, config_entry_mock, coordinator)
    add_entities, batches = _entity_collector()

    await update_module.async_setup_entry(hass_mock, config_entry_mock, add_entities)

    assert [entity.entity_description.key for entity in batches[0]] == ["application"]


@pytest.mark.asyncio
async def test_non_admin_account_gets_only_the_entities_it_may_use(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
) -> None:
    """A role=user account on a PCIe gets no administrator-only entity.

    The state mirrors what firmware 2.5.2 answers such an account: the
    administrator-only endpoints are refused, so their state stays empty.
    """
    coordinator = _coordinator(
        account=SimpleNamespace(username="guest", role="user"),
        is_admin=False,
        hardware_info=SimpleNamespace(version=HWVersion.PCIE),
        is_non_pro_hardware=True,
        supports_hdmi_endpoint=True,
        hdmi_state=SimpleNamespace(enabled=True, signal=True, idle_timeout=0),
        hid_mode=SimpleNamespace(mode=HidMode.NORMAL),
        keyboard_leds=SimpleNamespace(
            num_lock=True, caps_lock=False, scroll_lock=True, known=True
        ),
        application_version_info=None,
        mdns_state=None,
        ssh_state=None,
    )
    _configure_hass(hass_mock, config_entry_mock, coordinator)

    created: dict[str, list[str]] = {}
    for name, module in (
        ("binary_sensor", binary_sensor_module),
        ("button", button_module),
        ("number", number_module),
        ("select", select_module),
        ("sensor", sensor_module),
        ("switch", switch_module),
        ("update", update_module),
    ):
        add_entities, batches = _entity_collector()
        await module.async_setup_entry(hass_mock, config_entry_mock, add_entities)
        created[name] = [
            entity.entity_description.key for batch in batches for entity in batch
        ]

    assert created == {
        "binary_sensor": [
            "power_led",
            "hdmi_signal",
            "num_lock",
            "caps_lock",
            "scroll_lock",
        ],
        "button": ["power", "reset", "reset_hdmi"],
        "number": [],
        "select": [],
        "sensor": ["ip_address", "account_role"],
        "switch": ["power"],
        "update": [],
    }
