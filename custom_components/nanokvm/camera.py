"""Camera platform for Sipeed NanoKVM."""

import logging
from typing import override

from webrtc_models import RTCIceCandidateInit

from homeassistant.components.camera import (
    Camera,
    CameraEntityDescription,
    CameraEntityFeature,
)
from homeassistant.components.camera.webrtc import WebRTCSendMessage
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import NanoKVMConfigEntry, NanoKVMDataUpdateCoordinator
from .entity import NanoKVMEntity
from .media.signaling import NanoKVMWebRTCManager

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

SNAPSHOT_TIMEOUT_SECONDS = 20


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NanoKVMConfigEntry,
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
        self._webrtc = NanoKVMWebRTCManager(coordinator, self._handle_streaming_state)

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
            return await client.get_snapshot(SNAPSHOT_TIMEOUT_SECONDS)

    @override
    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image response from the camera."""
        try:
            return await self._async_read_snapshot_frame()
        except Exception as err:  # noqa: BLE001 - a failed snapshot must not break the camera
            _LOGGER.warning("Error fetching still image: %s", err)
            return None

    @override
    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Handle Home Assistant WebRTC offer using NanoKVM signaling."""
        await self._webrtc.async_handle_async_webrtc_offer(
            offer_sdp, session_id, send_message
        )

    @override
    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Forward frontend ICE candidates to NanoKVM signaling websocket."""
        await self._webrtc.async_on_webrtc_candidate(session_id, candidate)

    @callback
    @override
    def close_webrtc_session(self, session_id: str) -> None:
        """Close a WebRTC session when frontend unsubscribes."""
        self._webrtc.close_webrtc_session(session_id)

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Cleanup camera resources when entity is removed."""
        await super().async_will_remove_from_hass()
        await self._webrtc.async_shutdown()
