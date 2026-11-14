"""Tests for the reconnecting NanoKVM SSH metrics collector."""

from unittest.mock import MagicMock

from nanokvm.ssh_client import NanoKVMSSHAuthenticationError, SSHMetrics
import pytest

import custom_components.nanokvm.ssh_metrics as ssh_metrics_module
from custom_components.nanokvm.ssh_metrics import SSHMetricsCollector


class _FakeSSHClient:
    """Controllable fake for the external NanoKVM SSH boundary."""

    def __init__(self, ssh_client: object | None) -> None:
        self.ssh_client = ssh_client
        self.authenticate_calls: list[str] = []
        self.authenticate_error: BaseException | None = None
        self.disconnect_calls = 0
        self.metrics = SSHMetrics(cpu_temperature=42.0, watchdog_enabled=True)
        self.get_metrics_calls = 0
        self.watchdog_calls: list[bool] = []

    async def authenticate(self, password: str) -> None:
        """Record authentication and raise any configured boundary error."""
        self.authenticate_calls.append(password)
        if self.authenticate_error is not None:
            raise self.authenticate_error

    async def disconnect(self) -> None:
        """Record an SSH disconnect request."""
        self.disconnect_calls += 1

    async def get_metrics(self) -> SSHMetrics:
        """Return the configured metrics."""
        self.get_metrics_calls += 1
        return self.metrics

    async def set_watchdog_enabled(self, enabled: bool) -> None:
        """Record a watchdog change."""
        self.watchdog_calls.append(enabled)


def _collector(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ssh_client: object | None = None,
    known_hosts: str | None = None,
) -> tuple[SSHMetricsCollector, _FakeSSHClient, MagicMock]:
    """Return a collector whose external SSH client boundary is mocked."""
    client = _FakeSSHClient(ssh_client)
    client_factory = MagicMock(return_value=client)
    monkeypatch.setattr(ssh_metrics_module, "NanoKVMSSH", client_factory)

    collector = SSHMetricsCollector(
        host="nanokvm.local",
        password="secret",
        known_hosts=known_hosts,
    )
    return collector, client, client_factory


class _Transport:
    """Minimal transport boundary consumed by the collector."""

    def __init__(self, *, active: bool) -> None:
        self._active = active

    def is_active(self) -> bool:
        """Return the configured transport state."""
        return self._active


class _Connection:
    """Minimal SSH connection boundary consumed by the collector."""

    def __init__(self, transport: _Transport | None) -> None:
        self._transport = transport
        self.get_transport_calls = 0

    def get_transport(self) -> _Transport | None:
        """Return the configured transport and record the observation."""
        self.get_transport_calls += 1
        return self._transport


def _active_connection() -> _Connection:
    """Return an SSH connection with an active transport."""
    return _Connection(_Transport(active=True))


def test_constructor_configures_the_library_ssh_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collector construction forwards only connection identity to the client."""
    collector, client, client_factory = _collector(monkeypatch)

    client_factory.assert_called_once_with(
        host="nanokvm.local",
        username="root",
        known_hosts=None,
        allow_unknown_host_key=False,
    )
    assert collector._client is client  # noqa: SLF001


def test_constructor_forwards_approved_known_hosts_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An approved per-entry trust file reaches the library unchanged."""
    _collector(monkeypatch, known_hosts="/config/nanokvm/ssh/entry.known_hosts")

    ssh_metrics_module.NanoKVMSSH.assert_called_once_with(
        host="nanokvm.local",
        username="root",
        allow_unknown_host_key=False,
        known_hosts="/config/nanokvm/ssh/entry.known_hosts",
    )


@pytest.mark.parametrize("connected", [False, True])
async def test_disconnect_only_closes_an_existing_connection(
    monkeypatch: pytest.MonkeyPatch,
    connected: bool,
) -> None:
    """Disconnect is safe before connection and closes an active client once."""
    connection = _active_connection() if connected else None
    collector, client, _ = _collector(monkeypatch, ssh_client=connection)

    await collector.disconnect()

    if connected:
        assert client.disconnect_calls == 1
    else:
        assert client.disconnect_calls == 0


@pytest.mark.parametrize(
    "connection_state",
    ["missing-client", "missing-transport", "inactive-transport"],
)
async def test_ensure_connected_authenticates_unusable_connections(
    monkeypatch: pytest.MonkeyPatch,
    connection_state: str,
) -> None:
    """Missing or inactive SSH transports trigger password authentication."""
    connection: _Connection | None = None
    if connection_state != "missing-client":
        transport = None
        if connection_state == "inactive-transport":
            transport = _Transport(active=False)
        connection = _Connection(transport)
    collector, client, _ = _collector(monkeypatch, ssh_client=connection)

    await collector._async_ensure_connected()  # noqa: SLF001

    assert client.authenticate_calls == ["secret"]


async def test_ensure_connected_reuses_an_active_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active SSH transport is reusable without authentication."""
    connection = _active_connection()
    collector, client, _ = _collector(monkeypatch, ssh_client=connection)

    await collector._async_ensure_connected()  # noqa: SLF001

    assert connection.get_transport_calls == 1
    assert client.authenticate_calls == []


@pytest.mark.parametrize("connected", [False, True])
async def test_collect_connects_when_needed_and_returns_library_metrics(
    monkeypatch: pytest.MonkeyPatch,
    connected: bool,
) -> None:
    """Collection reconnects a dropped session and returns the library's metrics."""
    connection = _active_connection() if connected else None
    collector, client, _ = _collector(monkeypatch, ssh_client=connection)

    assert await collector.collect() is client.metrics
    assert client.get_metrics_calls == 1
    assert client.authenticate_calls == ([] if connected else ["secret"])


async def test_collect_propagates_authentication_errors_without_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected login reaches the coordinator and reads nothing."""
    collector, client, _ = _collector(monkeypatch)
    client.authenticate_error = NanoKVMSSHAuthenticationError("denied")

    with pytest.raises(NanoKVMSSHAuthenticationError):
        await collector.collect()
    assert client.get_metrics_calls == 0


@pytest.mark.parametrize("enabled", [True, False])
async def test_set_watchdog_enabled_connects_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    """Both watchdog transitions reach the library after connecting."""
    collector, client, _ = _collector(monkeypatch)

    await collector.set_watchdog_enabled(enabled)

    assert client.authenticate_calls == ["secret"]
    assert client.watchdog_calls == [enabled]
