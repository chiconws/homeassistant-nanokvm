"""Tests for NanoKVM diagnostics."""

from unittest.mock import MagicMock

from nanokvm.models import GetInfoRsp, GetWifiRsp, IPInfo
from nanokvm.ssh_client import SSHMetrics

from custom_components.nanokvm.diagnostics import async_get_config_entry_diagnostics
from homeassistant.components.diagnostics import REDACTED
from tests.common import Fake


async def test_diagnostics_redact_credentials_and_addresses() -> None:
    """Diagnostics report device replies without hosts, keys or credentials."""
    coordinator = Fake(
        is_admin=True,
        is_pro_hardware=False,
        password="web-password",
        ssh_metrics=SSHMetrics(),
        device_info=GetInfoRsp(
            ips=[IPInfo(name="eth0", addr="192.0.2.10", version="IPv4", type="Wired")],
            mdns="nanokvm.local",
            image="2026-07-12",
            application="2.3.0",
            deviceKey="device-key",
        ),
        wifi_status=GetWifiRsp(
            supported=True, apMode=False, connected=True, ssid="home"
        ),
        led_strip=None,
    )
    entry = Fake(
        data={
            "host": "192.0.2.10",
            "username": "admin",
            "password": "web-password",
            "ssh_password": "ssh-password",
            "ssl_fingerprint": "AB:CD",
            "use_static_host": False,
        },
        options={},
        runtime_data=coordinator,
    )

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    assert result["entry"] == {
        "host": REDACTED,
        "username": REDACTED,
        "password": REDACTED,
        "ssh_password": REDACTED,
        "ssl_fingerprint": REDACTED,
        "use_static_host": False,
    }
    device = result["device"]
    assert set(device) == {"device_info", "wifi_status"}
    assert device["device_info"]["ips"][0]["addr"] == REDACTED
    assert device["device_info"]["device_key"] == REDACTED
    assert device["device_info"]["application"] == "2.3.0"
    assert device["wifi_status"]["ssid"] == REDACTED
    assert "web-password" not in str(result)
    assert result["is_admin"] is True
    assert result["ssh_metrics"]["boot_time"] is None
