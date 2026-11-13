"""Tests for SSH login checks and host-key persistence."""

from pathlib import Path
from types import TracebackType
from typing import Self
from unittest.mock import MagicMock

from nanokvm.ssh_client import NanoKVMSSHAuthenticationError
import pytest

import custom_components.nanokvm.ssh_host_keys as host_keys_module
from custom_components.nanokvm.ssh_host_keys import (
    async_check_ssh_login,
    async_write_known_hosts,
    known_hosts_path,
)


def test_known_hosts_path_is_scoped_to_the_entry(tmp_path: Path) -> None:
    """Each config entry receives a separate trust file."""
    assert known_hosts_path(tmp_path, "entry-1") == (
        tmp_path / "nanokvm" / "ssh" / "entry-1.known_hosts"
    )
    assert known_hosts_path(tmp_path, "entry-2") != known_hosts_path(
        tmp_path, "entry-1"
    )


def test_retarget_known_hosts_line_keeps_the_trusted_key() -> None:
    """Updating a verified device host changes only its known-hosts alias."""
    assert (
        host_keys_module.retarget_known_hosts_line(
            "nanokvm.local ssh-ed25519 ZHVtbXk=",
            "192.0.2.20",
        )
        == "192.0.2.20 ssh-ed25519 ZHVtbXk="
    )


@pytest.mark.parametrize(
    ("known_hosts_line", "host"),
    [
        ("", "192.0.2.20"),
        ("nanokvm.local ssh-ed25519", "192.0.2.20"),
        ("nanokvm.local ssh-ed25519 key", "bad host"),
    ],
)
def test_retarget_known_hosts_line_rejects_invalid_entries(
    known_hosts_line: str,
    host: str,
) -> None:
    """Malformed trust material cannot be silently rebound to another host."""
    with pytest.raises(ValueError, match="single entry"):
        host_keys_module.retarget_known_hosts_line(known_hosts_line, host)


def test_write_known_hosts_rejects_multiline_content(tmp_path: Path) -> None:
    """Host-key storage cannot be used to inject extra known-hosts entries."""
    with pytest.raises(ValueError, match="exactly one non-empty line"):
        host_keys_module._write_known_hosts(  # noqa: SLF001
            tmp_path / "known_hosts",
            "host ssh-ed25519 key\nother-host ssh-ed25519 key",
        )


@pytest.mark.asyncio
async def test_write_known_hosts_creates_owner_only_file(tmp_path: Path) -> None:
    """Persisting a public key creates a private filesystem entry safely."""
    hass = MagicMock()
    hass.config.path.return_value = str(tmp_path)
    line = "nanokvm.local ssh-ed25519 ZHVtbXk="

    path = await async_write_known_hosts(hass, "entry-1", line)

    assert path == tmp_path / "nanokvm" / "ssh" / "entry-1.known_hosts"
    assert path.read_text() == line + "\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


class _FakeSSH:
    """Record how the login check uses the library's SSH client."""

    instances: list[_FakeSSH] = []
    error: Exception | None = None

    def __init__(self, host: str, username: str, *, host_key: str) -> None:
        self.host = host
        self.username = username
        self.host_key = host_key
        self.passwords: list[str] = []
        self.closed = False
        _FakeSSH.instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.closed = True

    async def authenticate(self, password: str) -> None:
        self.passwords.append(password)
        if _FakeSSH.error is not None:
            raise _FakeSSH.error


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, NanoKVMSSHAuthenticationError("denied")])
async def test_check_ssh_login_pins_the_saved_key_and_disconnects(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception | None,
) -> None:
    """The check signs in to the line's host with only that key, then closes."""
    _FakeSSH.instances = []
    _FakeSSH.error = error
    monkeypatch.setattr(host_keys_module, "NanoKVMSSH", _FakeSSH)
    line = "nanokvm.local,192.0.2.20 ssh-ed25519 ZHVtbXk="

    if error is None:
        await async_check_ssh_login(line, "root", "secret")
    else:
        with pytest.raises(NanoKVMSSHAuthenticationError):
            await async_check_ssh_login(line, "root", "secret")

    [ssh] = _FakeSSH.instances
    assert (ssh.host, ssh.username, ssh.host_key) == ("nanokvm.local", "root", line)
    assert ssh.passwords == ["secret"]
    assert ssh.closed is True
