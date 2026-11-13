"""Regression tests for NanoKVM transport failover."""

import asyncio
from types import SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from yarl import URL

import custom_components.nanokvm as nanokvm_module
import custom_components.nanokvm.config_flow as config_flow_module
from custom_components.nanokvm.coordinator import NanoKVMDataUpdateCoordinator
import custom_components.nanokvm.utils as utils_module
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME


def _coordinator() -> NanoKVMDataUpdateCoordinator:
    """Create a coordinator with mocked Home Assistant dependencies."""
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {CONF_HOST: "nanokvm.local"}
    client = MagicMock()
    client.url = URL("http://nanokvm.local/api/")
    return NanoKVMDataUpdateCoordinator(
        MagicMock(),
        entry,
        client=client,
        username="admin",
        password="password",
        device_info=SimpleNamespace(device_key="test-device", application="1.0.0"),
    )


def test_runtime_disconnect_tries_alternate_transport() -> None:
    """A server disconnect must trigger the configured transport fallback."""

    async def run_test() -> None:
        coordinator = _coordinator()
        disconnect = aiohttp.ServerDisconnectedError("HTTP probe disconnected")
        coordinator._async_fetch_with_client = AsyncMock(  # noqa: SLF001
            side_effect=[disconnect, {"transport": "https"}]
        )
        coordinator._async_failover_client = AsyncMock(return_value=True)  # noqa: SLF001

        assert await coordinator._async_fetch_once() == {"transport": "https"}  # noqa: SLF001
        coordinator._async_failover_client.assert_awaited_once_with(disconnect)  # noqa: SLF001
        assert coordinator._async_fetch_with_client.await_count == 2  # noqa: SLF001

    asyncio.run(run_test())


def test_config_validation_disconnect_tries_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config validation must try HTTPS when the HTTP probe disconnects."""

    async def run_test() -> None:
        attempted_urls: list[str] = []

        class FakeClient:
            """Client that disconnects on HTTP and succeeds on HTTPS."""

            def __init__(self, url: str, **_: object) -> None:
                self.url = URL(url)
                attempted_urls.append(url)

            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def authenticate(self, _username: str, _password: str) -> None:
                if self.url.scheme == "http":
                    raise aiohttp.ServerDisconnectedError("HTTP probe disconnected")

            async def get_info(self) -> SimpleNamespace:
                return SimpleNamespace(device_key="https-device")

        monkeypatch.setattr(config_flow_module, "NanoKVMClient", FakeClient)
        monkeypatch.setattr(utils_module, "NanoKVMClient", FakeClient)

        assert (
            await config_flow_module.validate_input(
                {
                    CONF_HOST: "nanokvm.local",
                    CONF_USERNAME: "admin",
                    CONF_PASSWORD: "password",
                }
            )
            == "https-device"
        )
        assert [URL(url).scheme for url in attempted_urls] == ["http", "https"]

    asyncio.run(run_test())


def test_entry_setup_disconnect_tries_https(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config-entry setup must try HTTPS when the HTTP probe disconnects."""

    async def run_test() -> None:
        attempted_urls: list[str] = []

        class FakeClient:
            """Client that disconnects on HTTP and succeeds on HTTPS."""

            def __init__(self, url: str, **_: object) -> None:
                self.url = URL(url)
                attempted_urls.append(url)

            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def authenticate(self, _username: str, _password: str) -> None:
                if self.url.scheme == "http":
                    raise aiohttp.ServerDisconnectedError("HTTP probe disconnected")

            async def get_info(self) -> SimpleNamespace:
                return SimpleNamespace(
                    device_key="https-device",
                    application="1.0.0",
                )

        coordinator = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(),
            is_pro_hardware=False,
        )
        coordinator_factory = MagicMock(return_value=coordinator)
        monkeypatch.setattr(utils_module, "NanoKVMClient", FakeClient)
        monkeypatch.setattr(
            nanokvm_module,
            "NanoKVMDataUpdateCoordinator",
            coordinator_factory,
        )
        hass = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        entry = MagicMock(spec=ConfigEntry)
        entry.entry_id = "test-entry"
        entry.data = {
            CONF_HOST: "nanokvm.local",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password",
        }

        assert await nanokvm_module.async_setup_entry(hass, entry) is True
        assert [URL(url).scheme for url in attempted_urls] == ["http", "https"]
        assert coordinator_factory.call_args.kwargs["client"].url.scheme == "https"
        coordinator.async_config_entry_first_refresh.assert_awaited_once_with()
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    asyncio.run(run_test())


def test_reauthentication_disconnect_tries_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reauthentication must try HTTPS when the HTTP candidate disconnects."""

    async def run_test() -> None:
        attempted_urls: list[str] = []

        class FakeClient:
            """Client that disconnects on HTTP and authenticates on HTTPS."""

            def __init__(self, url: str, **_: object) -> None:
                self.url = URL(url)
                attempted_urls.append(url)

            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def authenticate(self, _username: str, _password: str) -> None:
                if self.url.scheme == "http":
                    raise aiohttp.ServerDisconnectedError("HTTP probe disconnected")

        monkeypatch.setattr(utils_module, "NanoKVMClient", FakeClient)
        coordinator = _coordinator()

        await coordinator._async_reauthenticate_client()  # noqa: SLF001

        assert [URL(url).scheme for url in attempted_urls] == ["http", "https"]
        assert coordinator.client.url.scheme == "https"

    asyncio.run(run_test())
