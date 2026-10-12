"""Data update coordinator for the Sipeed NanoKVM integration."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
import contextlib
import datetime
import logging
from time import monotonic
from typing import Any, TypeVar

import aiohttp
from awesomeversion import AwesomeVersion, AwesomeVersionException

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from nanokvm.client import (
    NanoKVMApiError,
    NanoKVMAuthenticationFailure,
    NanoKVMClient,
    NanoKVMError,
    NanoKVMNotSupportedError,
    NanoKVMPermissionError,
)
from nanokvm.ssh_client import NanoKVMSSHConnectionError
from nanokvm.models import (
    GetAccountRsp,
    GetCdRomRsp,
    GetGpioRsp,
    GetHardwareRsp,
    GetHdmiCaptureRsp,
    GetHdmiPassthroughRsp,
    GetHdmiStateRsp,
    GetHostnameRsp,
    GetHidModeRsp,
    GetInfoRsp,
    GetKeyboardLedStatusRsp,
    GetLedStripRsp,
    GetLcdTimeFormatRsp,
    GetLowPowerRsp,
    GetMdnsStateRsp,
    GetMountedImageRsp,
    GetMouseJigglerRsp,
    GetOLEDRsp,
    GetSSHStateRsp,
    GetStaticIPRsp,
    GetTimeStatusRsp,
    GetTailscaleStatusRsp,
    GetVersionRsp,
    GetVirtualDeviceRsp,
    GetWifiRsp,
    HidMode,
    HWVersion,
    UserRole,
)

from .const import (
    CONF_LED_BRIGHTNESS,
    CONF_PREFERRED_HOST,
    CONF_SSH_HOST_KEY,
    CONF_SSL_FINGERPRINT,
    CONF_USE_STATIC_HOST,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    INTEGRATION_TITLE,
    PREFERRED_HOST_CHECK_INTERVAL_SECONDS,
    PREFERRED_HOST_MAX_CHECK_INTERVAL_SECONDS,
    PREFERRED_HOST_TIMEOUT_SECONDS,
)
from .led import LedBrightnessRequest, note_reported_led_strip
from .ssh_metrics import SSHMetricsCollector, SSHMetricsSnapshot
from .ssh_host_keys import retarget_known_hosts_line
from .utils import (
    CannotConnect,
    InvalidAuth,
    SSLCertificateChanged,
    api_connection_options,
    async_connect,
    device_sw_version,
    extract_ssh_host,
    is_auth_failure,
    verification_url,
)

_LOGGER = logging.getLogger(__name__)

_UPDATE_MAX_ATTEMPTS = 3
_UPDATE_RETRY_DELAY_SECONDS = 1
_UPDATE_TIMEOUT_SECONDS = 10
_REQUEST_REFRESH_COOLDOWN_SECONDS = 1.5
_SSH_METRICS_TIMEOUT_SECONDS = 15
_APP_VERSION_REQUEST_TIMEOUT_SECONDS = 45
_APP_VERSION_CACHE_SECONDS = 300
_APP_VERSION_FAILURE_CACHE_SECONDS = 60
_WATCHDOG_MIN_VERSION = AwesomeVersion("2.2.2")
_MAX_CONCURRENT_REQUESTS = 4
_GATED_CORE_ATTRIBUTES = (
    "virtual_device_info",
    "keyboard_leds",
    "hdmi_state",
    "swap_size",
    "hdmi_capture",
    "hdmi_passthrough",
    "low_power",
    "led_strip",
    "lcd_time_format",
    "time_status",
    "static_ip",
)
_INVALID_FILE_CONTENT_CODE = -2
_INVALID_FILE_CONTENT_MESSAGE = "invalid file content"
_ResponseT = TypeVar("_ResponseT")


def _is_invalid_file_content_error(error: NanoKVMApiError) -> bool:
    """Return whether the API error is NanoKVM's optional-file missing response."""
    return (
        error.code == _INVALID_FILE_CONTENT_CODE
        and error.msg == _INVALID_FILE_CONTENT_MESSAGE
    )


def _format_timeout_error(action: str) -> str:
    """Return a stable timeout message without relying on an empty exception."""
    return f"Timed out {action} after {_UPDATE_TIMEOUT_SECONDS} seconds"


class NanoKVMDataUpdateCoordinator(DataUpdateCoordinator[None]):
    """Class to manage fetching NanoKVM data."""

    config_entry: ConfigEntry
    device_info: GetInfoRsp
    hostname_info: GetHostnameRsp | None = None
    hardware_info: GetHardwareRsp | None = None
    account: GetAccountRsp | None = None
    gpio_info: GetGpioRsp | None = None
    virtual_device_info: GetVirtualDeviceRsp | None = None
    ssh_state: GetSSHStateRsp | None = None
    mdns_state: GetMdnsStateRsp | None = None
    hid_mode: GetHidModeRsp | None = None
    keyboard_leds: GetKeyboardLedStatusRsp | None = None
    oled_info: GetOLEDRsp | None = None
    wifi_status: GetWifiRsp | None = None
    application_version_info: GetVersionRsp | None = None
    mounted_image: GetMountedImageRsp | None = None
    cdrom_status: GetCdRomRsp | None = None
    mouse_jiggler_state: GetMouseJigglerRsp | None = None
    hdmi_state: GetHdmiStateRsp | None = None
    hdmi_capture: GetHdmiCaptureRsp | None = None
    hdmi_passthrough: GetHdmiPassthroughRsp | None = None
    low_power: GetLowPowerRsp | None = None
    led_strip: GetLedStripRsp | None = None
    lcd_time_format: GetLcdTimeFormatRsp | None = None
    time_status: GetTimeStatusRsp | None = None
    static_ip: GetStaticIPRsp | None = None
    swap_size: int | None = None
    tailscale_status: GetTailscaleStatusRsp | None = None
    ssh_metrics: SSHMetricsSnapshot = SSHMetricsSnapshot()
    ssh_metrics_collector: SSHMetricsCollector | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: NanoKVMClient,
        username: str,
        password: str,
        device_info: GetInfoRsp,
        ssh_known_hosts: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.username = username
        self.password = password
        self.device_info = device_info
        self.ssh_known_hosts = ssh_known_hosts
        # Kept in the entry so a restart does not forget the requested value.
        stored_brightness = config_entry.data.get(CONF_LED_BRIGHTNESS)
        self.led_brightness_request: LedBrightnessRequest | None = (
            LedBrightnessRequest(*stored_brightness)
            if isinstance(stored_brightness, list) and len(stored_brightness) == 2
            else None
        )
        self._ssh_connection_warning_logged = False
        self._app_version_last_fetched: datetime.datetime | None = None
        self._app_version_fetch_task: asyncio.Task[None] | None = None
        self._client_lock = asyncio.Lock()
        self._preferred_host_last_checked: float | None = None
        self._registered_device_details: tuple[str, str] | None = None
        self._preferred_host_check_interval = PREFERRED_HOST_CHECK_INTERVAL_SECONDS

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=datetime.timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            # The default ten second cooldown left entities stale after the
            # second of two actions; polling this device is cheap.
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=_REQUEST_REFRESH_COOLDOWN_SECONDS,
                immediate=True,
            ),
        )

    @contextlib.asynccontextmanager
    async def async_client(self) -> AsyncGenerator[NanoKVMClient]:
        """Yield the shared API client to one caller at a time."""
        async with self._client_lock:
            async with self.client as client:
                yield client

    def clone_client(self, **kwargs: Any) -> NanoKVMClient:
        """Return a separate client on the active transport, sharing its token."""
        return NanoKVMClient(
            str(self.client.url),
            token=self.client.token,
            ssl_fingerprint=(
                self.config_entry.data.get(CONF_SSL_FINGERPRINT)
                if self.client.url.scheme == "https"
                else None
            ),
            **kwargs,
        )

    async def async_ensure_authenticated(self, client: NanoKVMClient) -> None:
        """Log a client in with the entry credentials when it has no token."""
        if not client.token:
            await client.authenticate(self.username, self.password)

    async def _async_update_data(self) -> None:
        """Fetch data from NanoKVM."""
        if await self._async_restore_preferred_host():
            # The entry will reload with the verified preferred endpoint.
            # Avoid polling its superseded client while that reload is pending.
            return
        use_static_host = self.config_entry.data.get(CONF_USE_STATIC_HOST, False)
        current_host = self.config_entry.data[CONF_HOST]

        _LOGGER.debug(
            "Fetching data from NanoKVM at %s (static_host: %s)",
            current_host,
            use_static_host,
        )

        for attempt in range(1, _UPDATE_MAX_ATTEMPTS):
            try:
                return await self._async_fetch_once()
            except UpdateFailed as err:
                _LOGGER.debug(
                    "NanoKVM update attempt %s/%s failed for %s: %s. Retrying in %ss",
                    attempt,
                    _UPDATE_MAX_ATTEMPTS,
                    current_host,
                    err,
                    _UPDATE_RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_UPDATE_RETRY_DELAY_SECONDS)

        return await self._async_fetch_once()

    async def _async_restore_preferred_host(self) -> bool:
        """Restore a verified preferred endpoint without requiring another mDNS event."""
        data = dict(self.config_entry.data)
        preferred_host = data.get(CONF_PREFERRED_HOST)
        if (
            data.get(CONF_USE_STATIC_HOST, False)
            or not isinstance(preferred_host, str)
            or preferred_host == data[CONF_HOST]
        ):
            return False

        now = monotonic()
        if (
            self._preferred_host_last_checked is not None
            and now - self._preferred_host_last_checked
            < self._preferred_host_check_interval
        ):
            return False
        self._preferred_host_last_checked = now

        # A scheme-less preferred host is reached like the working connection,
        # unless a saved pin lets the probe verify the host before logging in.
        probe_url = verification_url(
            preferred_host,
            "https" if data.get(CONF_SSL_FINGERPRINT) else self.client.url.scheme,
        )
        try:
            async with asyncio.timeout(PREFERRED_HOST_TIMEOUT_SECONDS):
                async with NanoKVMClient(
                    probe_url,
                    ssl_fingerprint=(
                        data.get(CONF_SSL_FINGERPRINT)
                        if probe_url.startswith("https://")
                        else None
                    ),
                    request_timeout=PREFERRED_HOST_TIMEOUT_SECONDS,
                ) as client:
                    await client.authenticate(self.username, self.password)
                    info = await client.get_info()
        except (aiohttp.ClientError, NanoKVMError, asyncio.TimeoutError):
            # Back off so a long-gone address is not probed every minute.
            self._preferred_host_check_interval = min(
                self._preferred_host_check_interval * 2,
                PREFERRED_HOST_MAX_CHECK_INTERVAL_SECONDS,
            )
            _LOGGER.debug(
                "Preferred NanoKVM host %s is not available, next check in %ss",
                preferred_host,
                self._preferred_host_check_interval,
            )
            return False
        self._preferred_host_check_interval = PREFERRED_HOST_CHECK_INTERVAL_SECONDS

        if self.config_entry.data != data:
            return False

        if str(info.device_key) != self.config_entry.unique_id:
            # The address now belongs to another NanoKVM. Forget it so stored
            # credentials are not sent there again.
            _LOGGER.debug(
                "Preferred NanoKVM host %s now serves another device, keeping %s",
                preferred_host,
                data[CONF_HOST],
            )
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=data | {CONF_PREFERRED_HOST: data[CONF_HOST]},
            )
            return False

        updated_data = data | {CONF_HOST: preferred_host}
        if trusted_key := data.get(CONF_SSH_HOST_KEY):
            try:
                updated_data[CONF_SSH_HOST_KEY] = retarget_known_hosts_line(
                    trusted_key, extract_ssh_host(preferred_host)
                )
            except ValueError:
                _LOGGER.debug(
                    "Cannot restore preferred NanoKVM host %s with an invalid SSH key",
                    preferred_host,
                )
                return False

        if self.hass.config_entries.async_update_entry(
            self.config_entry, data=updated_data
        ):
            _LOGGER.debug(
                "Restoring preferred NanoKVM host from %s to %s",
                data[CONF_HOST], preferred_host,
            )
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return True
        return False

    async def _async_fetch_once(self, *, recover: bool = True) -> None:
        """Fetch data once, recovering the transport or session a single time."""
        try:
            return await self._async_fetch_with_client()
        except (
            aiohttp.ServerFingerprintMismatch,
            aiohttp.ClientConnectorCertificateError,
        ) as err:
            if (
                recover
                and self.client.url.scheme == "http"
                and await self._async_failover_client(err)
            ):
                return await self._async_fetch_once(recover=False)
            raise ConfigEntryAuthFailed(
                "SSL certificate changed for NanoKVM"
            ) from err
        except aiohttp.ClientConnectionError as err:
            if recover and await self._async_failover_client(err):
                return await self._async_fetch_once(recover=False)
            raise UpdateFailed(f"Error communicating with NanoKVM: {err}") from err
        except (aiohttp.ClientResponseError, NanoKVMAuthenticationFailure) as err:
            if is_auth_failure(err):
                if not recover:
                    raise ConfigEntryAuthFailed(
                        "Stored NanoKVM credentials are no longer valid"
                    ) from err
                await self._async_reauthenticate_client()
                return await self._async_fetch_once(recover=False)

            if isinstance(err, aiohttp.ClientResponseError):
                raise UpdateFailed(f"HTTP error with NanoKVM: {err}") from err
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except asyncio.TimeoutError:
            raise UpdateFailed(
                _format_timeout_error("communicating with NanoKVM")
            ) from None
        except (NanoKVMError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Error communicating with NanoKVM: {err}") from err

    async def _async_fetch_with_client(self) -> None:
        """Fetch data using the current client instance."""
        async with self.async_client() as client:
            async with asyncio.timeout(_UPDATE_TIMEOUT_SECONDS):
                await self.async_ensure_authenticated(client)
                await self._async_fetch_core_data()
                await self._async_fetch_storage_data()

        self._async_sync_device_registry()
        await self._async_refresh_ssh_data()
        self._async_schedule_app_version_refresh()

    def _async_sync_device_registry(self) -> None:
        """Keep the registered device name and firmware current between reloads."""
        details = (
            self.hostname_info.hostname if self.hostname_info else INTEGRATION_TITLE,
            device_sw_version(self.device_info.application, self.device_info.image),
        )
        if self._registered_device_details in (None, details):
            # Entities register the device with these values themselves.
            self._registered_device_details = details
            return

        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, self.device_info.device_key)}
        )
        if device is None:
            return
        registry.async_update_device(
            device.id, name=details[0], sw_version=details[1]
        )
        self._registered_device_details = details

    async def _async_connect(self, *, fallback_only: bool = False) -> NanoKVMClient:
        """Connect with the stored credentials, preferring the active transport."""
        current_url = str(self.client.url)
        options = api_connection_options(
            self.config_entry.data[CONF_HOST],
            self.config_entry.data.get(CONF_SSL_FINGERPRINT),
            preferred_url=current_url,
        )
        if fallback_only:
            options = tuple(
                option for option in options if option.base_url != current_url
            )
        try:
            client, _ = await async_connect(options, self.username, self.password)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(
                "Stored NanoKVM credentials are no longer valid"
            ) from err
        except SSLCertificateChanged as err:
            raise ConfigEntryAuthFailed("SSL certificate changed for NanoKVM") from err
        return client

    async def _async_reauthenticate_client(self) -> None:
        """Reauthenticate and replace the client when token/auth fails."""
        async with self._client_lock:
            try:
                self.client = await self._async_connect()
            except CannotConnect as err:
                cause = err.__cause__
                if isinstance(cause, asyncio.TimeoutError):
                    raise UpdateFailed(
                        _format_timeout_error("reauthenticating with NanoKVM")
                    ) from None
                raise UpdateFailed(f"Reauthentication failed: {cause}") from cause

    async def _async_failover_client(self, original_error: Exception) -> bool:
        """Switch to an alternate API transport after a connection failure."""
        async with self._client_lock:
            try:
                new_client = await self._async_connect(fallback_only=True)
            except CannotConnect as err:
                cause = err.__cause__
                if isinstance(cause, asyncio.TimeoutError):
                    raise UpdateFailed(
                        _format_timeout_error(
                            "checking alternate NanoKVM API transport"
                        )
                    ) from None
                if cause is not None and not isinstance(
                    cause, aiohttp.ClientConnectionError
                ):
                    raise UpdateFailed(
                        f"Error communicating with NanoKVM: {cause}"
                    ) from cause
                _LOGGER.debug(
                    "No alternate NanoKVM API transport succeeded after connection "
                    "failure from %s: %s",
                    self.client.url,
                    original_error,
                )
                return False

            _LOGGER.debug(
                "Switched NanoKVM API transport from %s to %s after connection failure",
                self.client.url,
                new_client.url,
            )
            self.client = new_client
            return True

    async def _fetch_optional(
        self,
        endpoint: str,
        call: Callable[[], Awaitable[_ResponseT]],
    ) -> _ResponseT | None:
        """Run an optional endpoint call; return None when the device lacks it."""
        try:
            return await call()
        except NanoKVMNotSupportedError as err:
            _LOGGER.debug(
                "NanoKVM endpoint %s is not supported on this device: %s",
                endpoint,
                err,
            )
            return None
        except NanoKVMPermissionError:
            _LOGGER.debug(
                "NanoKVM endpoint %s is not allowed for this account", endpoint
            )
            return None
        except NanoKVMApiError as err:
            _LOGGER.debug(
                "NanoKVM optional endpoint %s returned an API error: %s",
                endpoint,
                err,
            )
            return None
        except aiohttp.ClientResponseError as err:
            if err.status != 404:
                raise
            _LOGGER.debug("NanoKVM endpoint %s is not available on this device", endpoint)
            return None

    async def _fetch_oled_info(self) -> GetOLEDRsp | None:
        """Fetch OLED state, treating NanoKVM Pro's missing OLED file as unavailable."""
        try:
            return await self.client.get_oled_info()
        except NanoKVMApiError as err:
            if not _is_invalid_file_content_error(err):
                raise
            _LOGGER.debug(
                "NanoKVM endpoint /vm/oled returned %r; treating OLED as unavailable",
                _INVALID_FILE_CONTENT_MESSAGE,
            )
            return None

    async def _async_fetch_concurrently(
        self, calls: dict[str, Callable[[], Awaitable[Any]]]
    ) -> dict[str, Any]:
        """Run independent endpoint calls a few at a time and return all results."""
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)

        async def run(call: Callable[[], Awaitable[Any]]) -> Any:
            async with semaphore:
                return await call()

        tasks = [asyncio.ensure_future(run(call)) for call in calls.values()]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return dict(zip(calls, results))

    async def _async_fetch_core_data(self) -> None:
        """Fetch required API data used by entities."""
        client = self.client
        # Hardware identity gates the endpoints below and never changes per device.
        self.hardware_info = await client.get_hardware()
        # The account role gates the administrator-only endpoints below.
        previous_account = self.account
        self.account = (
            await self._fetch_optional("/auth/account", client.get_account)
            or previous_account
        )
        if (
            previous_account is not None
            and self.account is not None
            and previous_account.role != self.account.role
        ):
            # Entities are created per role, so build them again.
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

        def optional(
            endpoint: str, call: Callable[[], Awaitable[Any]]
        ) -> Callable[[], Awaitable[Any]]:
            return lambda: self._fetch_optional(endpoint, call)

        calls: dict[str, Callable[[], Awaitable[Any]]] = {
            "device_info": client.get_info,
            "hostname_info": client.get_hostname,
            "gpio_info": client.get_gpio,
            "hid_mode": client.get_hid_mode,
        }
        if self.is_admin:
            calls |= {
                "ssh_state": client.get_ssh_state,
                "mdns_state": client.get_mdns_state,
                "oled_info": self._fetch_oled_info,
                "wifi_status": client.get_wifi_status,
                "mouse_jiggler_state": client.get_mouse_jiggler_state,
                "tailscale_status": client.get_tailscale_status,
            }
            if self.hardware_info is not None:
                calls["virtual_device_info"] = optional(
                    "/vm/device/virtual", client.get_virtual_device_status
                )
        if self.supports_hdmi_endpoint:
            calls["hdmi_state"] = optional("/vm/hdmi", client.get_hdmi_state)
        if self.is_non_pro_hardware:
            calls["keyboard_leds"] = optional(
                "/hid/leds", client.get_keyboard_led_status
            )
            if self.is_admin:
                calls["swap_size"] = optional("/vm/swap", client.get_swap_size)
        if self.is_pro_hardware:
            calls |= {
                "hdmi_capture": optional("/vm/hdmi/capture", client.get_hdmi_capture),
                "hdmi_passthrough": optional(
                    "/vm/hdmi/passthrough", client.get_hdmi_passthrough
                ),
                "low_power": optional("/vm/low-power", client.get_low_power),
                "led_strip": optional("/vm/ledstrip/get", client.get_led_strip),
                "lcd_time_format": optional(
                    "/vm/lcd/time/format", client.get_lcd_time_format
                ),
                "time_status": optional("/vm/time/status", client.get_time_status),
                "static_ip": optional("/network/static-ip", client.get_static_ip),
            }

        results = await self._async_fetch_concurrently(calls)
        # The library gates endpoints on a cached firmware version; refresh it
        # after an update so newly supported endpoints stop being refused.
        if client.application_version not in (
            None,
            results["device_info"].application,
        ):
            await client.detect_versions()

        # Publish everything together so a failed poll leaves the last state intact.
        for attribute in _GATED_CORE_ATTRIBUTES:
            setattr(self, attribute, None)
        for attribute, value in results.items():
            setattr(self, attribute, value)
        request = self.led_brightness_request
        if request is not None and note_reported_led_strip(self, self.led_strip):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=self.config_entry.data
                | {CONF_LED_BRIGHTNESS: [request.requested, request.reported]},
            )

    def _async_schedule_app_version_refresh(self) -> None:
        """Refresh application version info outside the critical poll path."""
        if not self.is_admin:
            return
        if (
            self._app_version_fetch_task is not None
            and not self._app_version_fetch_task.done()
        ):
            return

        now = datetime.datetime.now(datetime.UTC)
        cache_ttl = (
            _APP_VERSION_CACHE_SECONDS
            if self.application_version_info is not None
            else _APP_VERSION_FAILURE_CACHE_SECONDS
        )
        if (
            self._app_version_last_fetched is not None
            and (now - self._app_version_last_fetched).total_seconds() < cache_ttl
        ):
            return

        self._app_version_fetch_task = self.hass.async_create_task(
            self._async_refresh_app_version()
        )

    async def _async_refresh_app_version(self) -> None:
        """Fetch and cache application version info without blocking coordinator refreshes."""
        try:
            self.application_version_info = await self._async_fetch_app_version()
            self._app_version_last_fetched = datetime.datetime.now(datetime.UTC)
            self.async_update_listeners()
        finally:
            self._app_version_fetch_task = None

    async def _async_fetch_app_version(self) -> GetVersionRsp | None:
        """Fetch application version using a dedicated client and timeout."""
        version_client = self.clone_client(
            request_timeout=_APP_VERSION_REQUEST_TIMEOUT_SECONDS
        )
        try:
            async with version_client:
                return await version_client.get_application_version()
        except (NanoKVMError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug(
                "Failed to fetch application version from NanoKVM: %s "
                "(device may have no internet access)",
                err,
            )
            return None

    async def _async_fetch_storage_data(self) -> None:
        """Fetch storage-specific state (mounted image and CD-ROM mode)."""
        if not self.is_admin:
            return
        mounted_image = GetMountedImageRsp(file="", cdrom=False, readOnly=False)
        cdrom_status = GetCdRomRsp(cdrom=0) if self.is_non_pro_hardware else None
        if self.hid_mode and self.hid_mode.mode == HidMode.NORMAL:
            try:
                mounted_image = await self.client.get_mounted_image()
            except NanoKVMApiError as err:
                _LOGGER.debug(
                    "Failed to get mounted image, retrieving default value: %s", err
                )

            if self.is_non_pro_hardware:
                cdrom_status = await self._fetch_optional(
                    "/storage/cdrom", self.client.get_cdrom_status
                )

        self.mounted_image = mounted_image
        self.cdrom_status = cdrom_status

    async def _async_refresh_ssh_data(self) -> None:
        """Fetch or clear SSH metrics depending on SSH state."""
        if self.ssh_state and self.ssh_state.enabled:
            try:
                async with asyncio.timeout(_SSH_METRICS_TIMEOUT_SECONDS):
                    await self._async_update_ssh_data()
            except TimeoutError:
                _LOGGER.debug(
                    "Timed out fetching optional SSH metrics after %s seconds",
                    _SSH_METRICS_TIMEOUT_SECONDS,
                )
                await self._async_clear_ssh_data()
            except asyncio.CancelledError:
                await self._async_clear_ssh_data()
                raise
        else:
            await self._async_clear_ssh_data()

    @property
    def supports_watchdog(self) -> bool:
        """Return whether NanoKVM watchdog support is available."""
        application_version = self.device_info.application.strip()
        if not application_version:
            return False

        try:
            return AwesomeVersion(application_version) >= _WATCHDOG_MIN_VERSION
        except AwesomeVersionException:
            _LOGGER.debug(
                "Unable to determine watchdog support from NanoKVM application version %r",
                application_version,
            )
            return False

    @property
    def is_admin(self) -> bool:
        """Return whether the account may use administrator-only endpoints.

        Devices without user roles report none; their one account is an admin.
        """
        return self.account is None or self.account.role in (None, UserRole.ADMIN)

    @property
    def is_pro_hardware(self) -> bool:
        """Return whether the detected NanoKVM hardware is the Pro model."""
        return bool(
            self.hardware_info and self.hardware_info.version == HWVersion.PRO
        )

    @property
    def is_non_pro_hardware(self) -> bool:
        """Return whether non-Pro endpoints and controls apply to this device."""
        return self.hardware_info is not None and not self.is_pro_hardware

    @property
    def supports_hdmi_endpoint(self) -> bool:
        """Return whether the non-Pro HDMI endpoint should be queried."""
        return bool(
            self.hardware_info and self.hardware_info.version == HWVersion.PCIE
        )

    @callback
    def async_ensure_ssh_metrics_collector(self) -> SSHMetricsCollector:
        """Return the active SSH collector, creating it when needed."""
        if not self.ssh_metrics_collector:
            self.ssh_metrics_collector = SSHMetricsCollector(
                host=extract_ssh_host(self.config_entry.data[CONF_HOST]),
                password=self.password,
                known_hosts=self.ssh_known_hosts,
            )
        return self.ssh_metrics_collector

    async def _async_update_ssh_data(self) -> None:
        """Fetch data via SSH."""
        try:
            collector = self.async_ensure_ssh_metrics_collector()
            metrics = await collector.collect(include_watchdog=self.supports_watchdog)
            self._ssh_connection_warning_logged = False
            self.ssh_metrics = metrics
            _LOGGER.debug("SSH coordinator metrics updated: %s", metrics)
        except NanoKVMSSHConnectionError as err:
            if self._ssh_connection_warning_logged:
                _LOGGER.debug(
                    "SSH metrics remain unavailable for %s: %s",
                    self.config_entry.data[CONF_HOST],
                    err,
                )
            else:
                _LOGGER.warning(
                    "SSH metrics unavailable for %s because host-key verification or "
                    "the SSH connection failed: %s. Reconfigure the NanoKVM entry "
                    "after verifying the device fingerprint if its host key changed.",
                    self.config_entry.data[CONF_HOST],
                    err,
                )
                self._ssh_connection_warning_logged = True
            self.ssh_metrics = SSHMetricsSnapshot()
            if self.ssh_metrics_collector:
                await self.ssh_metrics_collector.disconnect()
        except Exception as err:
            _LOGGER.debug("Failed to fetch data via SSH: %s", err)
            self.ssh_metrics = SSHMetricsSnapshot()
            if self.ssh_metrics_collector:
                await self.ssh_metrics_collector.disconnect()

    async def _async_clear_ssh_data(self) -> None:
        """Clear SSH data and disconnect client."""
        self.ssh_metrics = SSHMetricsSnapshot()
        if self.ssh_metrics_collector:
            await self.ssh_metrics_collector.disconnect()
            self.ssh_metrics_collector = None

    async def async_shutdown(self) -> None:
        """Release any background tasks and live connections owned by the coordinator."""
        try:
            await super().async_shutdown()
        finally:
            try:
                if self._app_version_fetch_task is not None:
                    self._app_version_fetch_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._app_version_fetch_task
                    self._app_version_fetch_task = None
            finally:
                if self.ssh_metrics_collector:
                    await self.ssh_metrics_collector.disconnect()
                    self.ssh_metrics_collector = None
