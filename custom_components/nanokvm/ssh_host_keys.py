"""SSH host-key probing and per-entry trust storage."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
import socket

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file_atomic
from nanokvm.ssh_client import (
    NanoKVMSSHAuthenticationError,
    NanoKVMSSHConnectionError,
)
import paramiko
from paramiko.hostkeys import HostKeyEntry


_SSH_PORT = 22
_SSH_PROBE_TIMEOUT_SECONDS = 10
_SAFE_ENTRY_ID = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass(frozen=True, slots=True)
class SSHHostKey:
    """Public host-key material safe to show and persist."""

    host: str
    known_hosts_line: str
    fingerprint: str


def retarget_known_hosts_line(known_hosts_line: str, host: str) -> str:
    """Return a trusted key line scoped to a newly verified host."""
    fields = known_hosts_line.split()
    if len(fields) != 3 or not host or any(char.isspace() for char in host):
        raise ValueError("known_hosts_line and host must each be a single entry")

    _, key_type, key_data = fields
    return f"{host} {key_type} {key_data}"


def _probe_host_key(host: str) -> SSHHostKey:
    """Perform an SSH handshake without authenticating a user."""
    sock = socket.create_connection(
        (host, _SSH_PORT),
        timeout=_SSH_PROBE_TIMEOUT_SECONDS,
    )
    transport: paramiko.Transport | None = None
    try:
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=_SSH_PROBE_TIMEOUT_SECONDS)
        key = transport.get_remote_server_key()
        return SSHHostKey(
            host=host,
            known_hosts_line=f"{host} {key.get_name()} {key.get_base64()}",
            fingerprint=key.fingerprint,
        )
    finally:
        if transport is not None:
            transport.close()
        else:
            sock.close()


async def async_probe_host_key(host: str) -> SSHHostKey:
    """Probe a server key off the Home Assistant event loop."""
    return await asyncio.to_thread(_probe_host_key, host)


def _check_login(known_hosts_line: str, username: str, password: str) -> None:
    """Sign in to the one host the trusted key line names, then disconnect."""
    entry = HostKeyEntry.from_line(known_hosts_line)
    if entry is None:
        raise paramiko.SSHException("known_hosts_line is not a host-key entry")
    client = paramiko.SSHClient()
    try:
        for hostname in entry.hostnames:
            client.get_host_keys().add(hostname, entry.key.get_name(), entry.key)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            entry.hostnames[0],
            port=_SSH_PORT,
            username=username,
            password=password,
            timeout=_SSH_PROBE_TIMEOUT_SECONDS,
            banner_timeout=_SSH_PROBE_TIMEOUT_SECONDS,
            auth_timeout=_SSH_PROBE_TIMEOUT_SECONDS,
            allow_agent=False,
            look_for_keys=False,
        )
    finally:
        client.close()


async def async_check_ssh_login(
    known_hosts_line: str, username: str, password: str
) -> None:
    """Check SSH credentials against a host that must present the trusted key."""
    try:
        await asyncio.to_thread(_check_login, known_hosts_line, username, password)
    except paramiko.AuthenticationException as err:
        raise NanoKVMSSHAuthenticationError(f"SSH authentication failed: {err}") from err
    except (paramiko.SSHException, OSError) as err:
        raise NanoKVMSSHConnectionError(f"SSH connection failed: {err}") from err


def known_hosts_path(config_dir: str | Path, entry_id: str) -> Path:
    """Return the per-entry known-hosts path."""
    safe_entry_id = _SAFE_ENTRY_ID.sub("_", entry_id)
    return Path(config_dir) / "nanokvm" / "ssh" / f"{safe_entry_id}.known_hosts"


def _write_known_hosts(path: Path, known_hosts_line: str) -> None:
    """Atomically write a single owner-readable known-hosts line."""
    if not known_hosts_line or "\n" in known_hosts_line or "\r" in known_hosts_line:
        raise ValueError("known_hosts_line must contain exactly one non-empty line")

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    write_utf8_file_atomic(str(path), f"{known_hosts_line}\n", private=True)


async def async_write_known_hosts(
    hass: HomeAssistant,
    entry_id: str,
    known_hosts_line: str,
) -> Path:
    """Persist one approved host key for a config entry."""
    config_dir = Path(hass.config.path())
    path = known_hosts_path(config_dir, entry_id)
    await asyncio.to_thread(_write_known_hosts, path, known_hosts_line)
    return path
