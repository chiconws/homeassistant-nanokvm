"""Camera platform for Sipeed NanoKVM."""
from __future__ import annotations

import io
import logging
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_flow import normalize_host
from .const import DOMAIN, ICON_HDMI
from . import NanoKVMDataUpdateCoordinator, NanoKVMEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True)
class NanoKVMCameraEntityDescription(EntityDescription):
    """Describes NanoKVM camera entity."""

    available_fn: Callable[[NanoKVMDataUpdateCoordinator], bool] = lambda _: True


CAMERAS: tuple[NanoKVMCameraEntityDescription, ...] = (
    NanoKVMCameraEntityDescription(
        key="hdmi",
        name="HDMI Stream",
        translation_key="hdmi",
        icon=ICON_HDMI,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM camera based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        NanoKVMCamera(
            coordinator=coordinator,
            description=description,
        )
        for description in CAMERAS
        if description.available_fn(coordinator)
    )


class NanoKVMCamera(NanoKVMEntity, Camera):
    """Defines a NanoKVM camera."""

    entity_description: NanoKVMCameraEntityDescription

    def __init__(
        self,
        coordinator: NanoKVMDataUpdateCoordinator,
        description: NanoKVMCameraEntityDescription,
    ) -> None:
        """Initialize NanoKVM camera."""
        self.entity_description = description
        super().__init__(
            coordinator=coordinator,
            unique_id_suffix=f"camera_{description.key}",
        )
        self.access_tokens: deque[str] = deque()
        self._webrtc_provider = None
        self._rtsp_provider = None
        self._stream_provider = None
        self._supports_native_async_webrtc = False
        self._supports_native_webrtc = False
        self._supports_native_async_rtsp = False
        self._supports_native_rtsp = False
        self.content_type = "image/jpeg"

        self.access_tokens.append(self.generate_access_token())

    async def stream_source(self) -> str | None:
        """Return the MJPEG stream URL."""
        config_entry = self.coordinator.config_entry
        if config_entry and config_entry.data:
            host = normalize_host(config_entry.data.get("host", ""))
            return f"{host.rstrip('/')}/stream/mjpeg"
        return None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        try:
            async with self.coordinator.client:
                async for image in self.coordinator.client.mjpeg_stream():
                    img_byte_arr = io.BytesIO()
                    image.save(img_byte_arr, format='JPEG')
                    return img_byte_arr.getvalue()
        except Exception as err:
            _LOGGER.error("Error fetching still image: %s", err)
            return None

    def generate_access_token(self) -> str:
        """Generate a random access token for camera security."""
        return secrets.token_hex(32)
