"""Shared utility helpers for the Sipeed NanoKVM integration."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar, overload

import aiohttp
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from nanokvm.client import NanoKVMAuthenticationFailure, NanoKVMClient, NanoKVMError

_ResultT = TypeVar("_ResultT")


class CannotConnect(HomeAssistantError):
    """No API candidate could be reached; the cause is the last failure."""


class InvalidAuth(HomeAssistantError):
    """The device rejected the credentials."""


class SSLCertificateChanged(HomeAssistantError):
    """The device certificate is untrusted or differs from the saved one."""


@dataclass(frozen=True, slots=True)
class NanoKVMAPIConnectionOption:
    """Resolved API base URL plus the applicable SSL fingerprint setting."""

    base_url: str
    scheme: str
    ssl_fingerprint: str | None


def _normalize_api_path(path: str) -> str:
    """Normalize an origin path to the NanoKVM API base path."""
    normalized_path = path.rstrip("/")
    if not normalized_path:
        return "/api/"
    if normalized_path.endswith("/api"):
        return f"{normalized_path}/"
    return f"{normalized_path}/api/"


def _parse_host(host: str) -> tuple[URL, bool]:
    """Parse a configured host and return the origin plus scheme state."""
    raw_host = host.strip()
    has_explicit_scheme = "://" in raw_host
    origin = URL(raw_host if has_explicit_scheme else f"http://{raw_host}")
    return origin.with_query(None).with_fragment(None), has_explicit_scheme


def _api_base_url(origin: URL, scheme: str) -> str:
    """Build an API base URL from a parsed origin and target scheme."""
    return str(origin.with_scheme(scheme).with_path(_normalize_api_path(origin.path)))


def api_connection_options(
    host: str,
    ssl_fingerprint: str | None = None,
    *,
    preferred_url: str | None = None,
) -> tuple[NanoKVMAPIConnectionOption, ...]:
    """Return candidate API connection options for a configured host."""
    origin, has_explicit_scheme = _parse_host(host)
    options = [
        NanoKVMAPIConnectionOption(
            base_url=_api_base_url(origin, scheme),
            scheme=scheme,
            ssl_fingerprint=ssl_fingerprint if scheme == "https" else None,
        )
        for scheme in ((origin.scheme,) if has_explicit_scheme else ("http", "https"))
    ]
    # A stable sort moves the preferred URL first and keeps the rest in order.
    return tuple(sorted(options, key=lambda option: option.base_url != preferred_url))


def https_probe_url(host: str) -> str:
    """Return the HTTPS API base URL for certificate fingerprint probing."""
    return _api_base_url(_parse_host(host)[0], "https")


def verification_url(host: str, default_scheme: str = "https") -> str:
    """Return the API base URL for verifying a host, keeping an explicit scheme."""
    origin, has_explicit_scheme = _parse_host(host)
    return _api_base_url(
        origin, origin.scheme if has_explicit_scheme else default_scheme
    )


def async_get_entry_device(
    hass: HomeAssistant, entry_id: str, identifier: tuple[str, str]
) -> dr.DeviceEntry | None:
    """Return the device with the identifier that the config entry owns."""
    registry = dr.async_get(hass)
    if hasattr(registry, "async_get_device_by_identifier"):
        return registry.async_get_device_by_identifier(identifier, entry_id)
    # TODO: Home Assistant added the per-entry lookup in 2026.8.0 and removes
    # async_get_device in 2027.8.0. Before then, raise "homeassistant" in
    # hacs.json to 2026.8.0 and drop this fallback and the hasattr check.
    return registry.async_get_device(identifiers={identifier})


def api_base_url_to_web_url(base_url: str) -> str:
    """Convert a NanoKVM API base URL into the corresponding web UI URL."""
    parsed_url = URL(base_url).with_query(None).with_fragment(None)
    web_path = parsed_url.path.rstrip("/").removesuffix("/api")
    return str(parsed_url.with_path(f"{web_path}/"))


def device_sw_version(application: str, image: str | None) -> str:
    """Return the software version shown for a NanoKVM device."""
    return f"{application} (Image: {image})" if image else application


def normalize_host(host: str, ssl_fingerprint: str | None = None) -> str:
    """Return the first candidate API base URL for a configured host."""
    return api_connection_options(host, ssl_fingerprint)[0].base_url


def normalize_mdns(mdns: str) -> str:
    """Normalize mDNS hostnames to include a trailing dot."""
    return mdns if mdns.endswith(".") else f"{mdns}."


def extract_ssh_host(host: str) -> str:
    """Extract SSH host value from integration host configuration."""
    origin = _parse_host(host)[0]
    ssh_host = origin.host or origin.raw_host
    if ssh_host is None:
        raise ValueError(f"Invalid NanoKVM host value: {origin}")
    return ssh_host


def host_match_key(host: str) -> tuple[str, int | None, str]:
    """Return a normalized key for matching a host to a config entry."""
    origin = _parse_host(host)[0]
    return (
        extract_ssh_host(host),
        origin.explicit_port,
        _normalize_api_path(origin.path),
    )


def is_auth_failure(error: BaseException) -> bool:
    """Return whether the exception represents invalid credentials."""
    return isinstance(error, NanoKVMAuthenticationFailure) or (
        isinstance(error, aiohttp.ClientResponseError) and error.status == 401
    )


@overload
async def async_connect(
    options: Sequence[NanoKVMAPIConnectionOption], username: str, password: str
) -> tuple[NanoKVMClient, None]: ...


@overload
async def async_connect(
    options: Sequence[NanoKVMAPIConnectionOption],
    username: str,
    password: str,
    then: Callable[[NanoKVMClient], Awaitable[_ResultT]],
) -> tuple[NanoKVMClient, _ResultT]: ...


async def async_connect(
    options: Sequence[NanoKVMAPIConnectionOption],
    username: str,
    password: str,
    then: Callable[[NanoKVMClient], Awaitable[_ResultT]] | None = None,
) -> tuple[NanoKVMClient, _ResultT | None]:
    """Authenticate against the first candidate that answers.

    Returns the authenticated client and what `then` returned for it. An
    unreachable candidate gives way to the next one; any other failure stops.
    """
    last_error: Exception | None = None

    for index, option in enumerate(options):
        client = NanoKVMClient(option.base_url, ssl_fingerprint=option.ssl_fingerprint)
        try:
            async with client:
                await client.authenticate(username, password)
                result = await then(client) if then is not None else None
            return client, result
        except (
            aiohttp.ServerFingerprintMismatch,
            aiohttp.ClientConnectorCertificateError,
        ) as err:
            if option.scheme == "http" and index < len(options) - 1:
                last_error = err
                continue
            raise SSLCertificateChanged from err
        except aiohttp.ClientConnectionError as err:
            last_error = err
        except (aiohttp.ClientError, NanoKVMError, asyncio.TimeoutError) as err:
            if is_auth_failure(err):
                raise InvalidAuth from err
            last_error = err
            break

    raise CannotConnect from last_error
