"""Regression tests for coordinator reauthentication error handling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nanokvm.client import NanoKVMAuthenticationFailure
import pytest

from custom_components.nanokvm.coordinator import (
    NanoKVMDataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.config_entries import ConfigEntry


def _coordinator() -> NanoKVMDataUpdateCoordinator:
    """Create a coordinator with mocked Home Assistant dependencies."""
    return NanoKVMDataUpdateCoordinator(
        MagicMock(),
        MagicMock(spec=ConfigEntry, data={}),
        client=MagicMock(),
        username="admin",
        password="password",
        device_info=SimpleNamespace(device_key="test-device", application="1.0.0"),
    )


def test_post_reauthentication_timeout_uses_update_failed_contract() -> None:
    """A timed-out fetch after reauthentication must remain retryable."""

    async def run_test() -> None:
        coordinator = _coordinator()
        coordinator._async_fetch_with_client = AsyncMock(  # noqa: SLF001
            side_effect=[
                NanoKVMAuthenticationFailure("expired token"),
                TimeoutError(),
            ]
        )
        coordinator._async_reauthenticate_client = AsyncMock()  # noqa: SLF001

        with pytest.raises(
            UpdateFailed,
            match="Timed out communicating with NanoKVM after 10 seconds",
        ):
            await coordinator._async_fetch_once()  # noqa: SLF001

        coordinator._async_reauthenticate_client.assert_awaited_once()  # noqa: SLF001
        assert coordinator._async_fetch_with_client.await_count == 2  # noqa: SLF001

    asyncio.run(run_test())
