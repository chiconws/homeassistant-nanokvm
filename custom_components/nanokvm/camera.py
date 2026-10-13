"""Camera platform for Sipeed NanoKVM."""
from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing

from homeassistant.components.camera import (
    Camera,
    CameraEntityDescription,
    CameraEntityFeature,
)
from homeassistant.components.camera.webrtc import WebRTCSendMessage
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from webrtc_models import RTCIceCandidateInit

from .coordinator import NanoKVMDataUpdateCoordinator
from .entity import NanoKVMEntity
from .media.signaling import NanoKVMWebRTCManager

_LOGGER = logging.getLogger(__name__)

SNAPSHOT_TIMEOUT_SECONDS = 20


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NanoKVM camera based on a config entry."""
    coordinator = entry.runtime_data

    async_add_entities([NanoKVMCamera(coordinator)])


class NanoKVMCamera(NanoKVMEntity, Camera):
    """Defines a NanoKVM camera."""

    entity_description = CameraEntityDescription(
        key="hdmi",
        translation_key="hdmi",
    )

    def __init__(self, coordinator: NanoKVMDataUpdateCoordinator) -> None:
        """Initialize NanoKVM camera."""
        super().__init__(coordinator=coordinator, unique_id_suffix="camera_hdmi")
        Camera.__init__(self)
        self._attr_supported_features = CameraEntityFeature.STREAM
        self._attr_is_streaming = False
        self._webrtc = NanoKVMWebRTCManager(
            coordinator, self._handle_streaming_state
        )

    @callback
    def _handle_streaming_state(self, streaming: bool) -> None:
        """Publish active WebRTC session state through the camera entity."""
        self._attr_is_streaming = streaming
        self.async_write_ha_state()

    async def _async_read_snapshot_frame(self) -> bytes | None:
        """Read one JPEG frame from NanoKVM MJPEG endpoint for snapshots."""
        if self.coordinator.is_pro_hardware:
            # NanoKVM Pro shares one encoder mode; opening MJPEG interrupts WebRTC.
            # TODO: enable once the Pro can serve a frame without switching the
            # stream type: https://github.com/sipeed/NanoKVM-Pro/issues/157
            _LOGGER.debug(
                "Skipping NanoKVM Pro still image to avoid switching video mode"
            )
            return None

        client = self.coordinator.clone_client()
        async with client:
            await self.coordinator.async_ensure_authenticated(client)
            async with (
                asyncio.timeout(SNAPSHOT_TIMEOUT_SECONDS),
                aclosing(client.mjpeg_stream_bytes()) as frames,
            ):
                async for frame in frames:
                    return frame
        return None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        try:
            return await self._async_read_snapshot_frame()
        except Exception as err:
            _LOGGER.warning("Error fetching still image: %s", err)
            return None

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Handle Home Assistant WebRTC offer using NanoKVM signaling."""
        await self._webrtc.async_handle_async_webrtc_offer(
            offer_sdp, session_id, send_message
        )

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Forward frontend ICE candidates to NanoKVM signaling websocket."""
        await self._webrtc.async_on_webrtc_candidate(session_id, candidate)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Close a WebRTC session when frontend unsubscribes."""
        self._webrtc.close_webrtc_session(session_id)

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup camera resources when entity is removed."""
        await super().async_will_remove_from_hass()
        await self._webrtc.async_shutdown()
