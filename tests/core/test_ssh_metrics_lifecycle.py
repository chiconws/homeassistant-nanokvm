"""Regression tests for optional SSH metrics lifecycle handling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nanokvm.ssh_client import SSHMetrics
import pytest

import custom_components.nanokvm.coordinator as coordinator_module
from custom_components.nanokvm.coordinator import NanoKVMDataUpdateCoordinator
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST


def _coordinator() -> NanoKVMDataUpdateCoordinator:
    """Create a coordinator configured with SSH enabled."""
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {CONF_HOST: "http://nanokvm.local"}
    coordinator = NanoKVMDataUpdateCoordinator(
        MagicMock(),
        entry,
        client=MagicMock(),
        username="admin",
        password="password",
        device_info=SimpleNamespace(device_key="test-device", application="1.0.0"),
    )
    coordinator.ssh_state = SimpleNamespace(enabled=True)
    return coordinator


def test_stalled_ssh_metrics_do_not_block_coordinator_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled optional SSH collector must time out without failing the poll."""

    async def run_test() -> None:
        coordinator = _coordinator()
        stalled = asyncio.Event()
        collect_started = asyncio.Event()

        async def collect() -> None:
            collect_started.set()
            await stalled.wait()

        collector = SimpleNamespace(
            collect=collect,
            disconnect=AsyncMock(),
        )
        coordinator.ssh_metrics_collector = collector
        coordinator.ssh_metrics = SSHMetrics(cpu_temperature=55.0)
        monkeypatch.setattr(
            coordinator_module,
            "_SSH_METRICS_TIMEOUT_SECONDS",
            0.01,
        )

        await asyncio.wait_for(coordinator._async_refresh_ssh_data(), timeout=0.2)  # noqa: SLF001

        assert collect_started.is_set()
        assert coordinator.ssh_metrics == SSHMetrics()
        collector.disconnect.assert_awaited_once_with()

    asyncio.run(run_test())


def test_cancelled_ssh_metrics_disconnect_before_propagating() -> None:
    """Coordinator cancellation must disconnect an in-flight SSH collector."""

    async def run_test() -> None:
        coordinator = _coordinator()
        collect_started = asyncio.Event()

        async def collect() -> None:
            collect_started.set()
            await asyncio.Event().wait()

        collector = SimpleNamespace(
            collect=collect,
            disconnect=AsyncMock(),
        )
        coordinator.ssh_metrics_collector = collector
        coordinator.ssh_metrics = SSHMetrics(cpu_temperature=55.0)
        task = asyncio.create_task(coordinator._async_refresh_ssh_data())  # noqa: SLF001
        await collect_started.wait()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert coordinator.ssh_metrics == SSHMetrics()
        collector.disconnect.assert_awaited_once_with()

    asyncio.run(run_test())
