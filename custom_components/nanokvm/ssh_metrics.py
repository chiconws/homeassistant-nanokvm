"""SSH metrics collection for NanoKVM: a reconnecting wrapper around the library."""
from __future__ import annotations

from nanokvm.ssh_client import DEFAULT_SSH_USERNAME, NanoKVMSSH, SSHMetrics


class SSHMetricsCollector:
    """Keep one SSH session to a NanoKVM and reconnect it when it drops."""

    def __init__(
        self,
        host: str,
        password: str,
        known_hosts: str | None = None,
        username: str = DEFAULT_SSH_USERNAME,
    ) -> None:
        """Initialize the SSH collector."""
        self._password = password
        self._client = NanoKVMSSH(
            host=host,
            username=username,
            known_hosts=known_hosts,
            allow_unknown_host_key=False,
        )

    async def disconnect(self) -> None:
        """Disconnect the underlying SSH client if connected."""
        if self._client.ssh_client:
            await self._client.disconnect()

    async def _async_ensure_connected(self) -> None:
        """Connect the SSH client when needed."""
        ssh_client = self._client.ssh_client
        transport = ssh_client.get_transport() if ssh_client is not None else None

        if ssh_client is None or transport is None or not transport.is_active():
            await self._client.authenticate(self._password)

    async def collect(self) -> SSHMetrics:
        """Collect boot time, CPU temperature, memory, storage and watchdog state."""
        await self._async_ensure_connected()
        return await self._client.get_metrics()

    async def set_watchdog_enabled(self, enabled: bool) -> None:
        """Enable or disable the NanoKVM watchdog."""
        await self._async_ensure_connected()
        await self._client.set_watchdog_enabled(enabled)
