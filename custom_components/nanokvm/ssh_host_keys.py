"""SSH login checks and per-entry host-key trust storage."""
from __future__ import annotations

import asyncio
from pathlib import Path
import re

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file_atomic
from nanokvm.ssh_client import NanoKVMSSH


_SAFE_ENTRY_ID = re.compile(r"[^A-Za-z0-9_.-]")


def retarget_known_hosts_line(known_hosts_line: str, host: str) -> str:
    """Return a trusted key line scoped to a newly verified host."""
    fields = known_hosts_line.split()
    if len(fields) != 3 or not host or any(char.isspace() for char in host):
        raise ValueError("known_hosts_line and host must each be a single entry")

    _, key_type, key_data = fields
    return f"{host} {key_type} {key_data}"


async def async_check_ssh_login(
    known_hosts_line: str, username: str, password: str
) -> None:
    """Sign in to the host the trusted key line names, then disconnect."""
    host = known_hosts_line.split(maxsplit=1)[0].split(",")[0]
    async with NanoKVMSSH(host, username, host_key=known_hosts_line) as ssh:
        await ssh.authenticate(password)


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
