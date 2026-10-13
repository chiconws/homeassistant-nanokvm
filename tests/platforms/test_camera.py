"""Tests for the NanoKVM camera platform."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.camera import CameraEntityFeature, CameraState
from homeassistant.components.camera.const import StreamType
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
import pytest
from webrtc_models import RTCIceCandidateInit
from yarl import URL

import custom_components.nanokvm.camera as camera_module


def _coordinator(**overrides: object) -> SimpleNamespace:
    """Build the coordinator state consumed by the camera entity."""
    values: dict[str, object] = {
        "client": SimpleNamespace(
            url=URL("http://nanokvm.local/api/"),
            token="existing-token",
        ),
        "config_entry": SimpleNamespace(
            data={CONF_USERNAME: "admin", CONF_PASSWORD: "password"}
        ),
        "device_info": SimpleNamespace(device_key="test-device"),
        "is_pro_hardware": False,
        "last_update_success": True,
        "clone_client": MagicMock(return_value=None),
        "async_ensure_authenticated": AsyncMock(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _camera(
    monkeypatch: pytest.MonkeyPatch,
    coordinator: SimpleNamespace | None = None,
) -> tuple[
    camera_module.NanoKVMCamera,
    MagicMock,
    MagicMock,
]:
    """Create a camera with its WebRTC manager isolated at the platform edge."""
    manager = MagicMock()
    manager.async_handle_async_webrtc_offer = AsyncMock()
    manager.async_on_webrtc_candidate = AsyncMock()
    manager.async_shutdown = AsyncMock()
    manager_factory = MagicMock(return_value=manager)
    monkeypatch.setattr(camera_module, "NanoKVMWebRTCManager", manager_factory)

    entity = camera_module.NanoKVMCamera(coordinator or _coordinator())
    return entity, manager, manager_factory


@pytest.mark.asyncio
async def test_async_setup_entry_adds_the_hdmi_camera(
    hass_mock: MagicMock,
    config_entry_mock: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup adds the single HDMI stream camera for the entry's coordinator."""
    coordinator = _coordinator()
    config_entry_mock.runtime_data = coordinator
    entity_factory = MagicMock(return_value="camera")
    monkeypatch.setattr(camera_module, "NanoKVMCamera", entity_factory)
    batches: list[list[object]] = []

    await camera_module.async_setup_entry(
        hass_mock,
        config_entry_mock,
        lambda entities: batches.append(list(entities)),
    )

    assert batches == [["camera"]]
    entity_factory.assert_called_once_with(coordinator)


@pytest.mark.asyncio
async def test_camera_initializes_native_webrtc_stream(
    hass_mock: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entity metadata and manager providers describe a native WebRTC camera."""
    coordinator = _coordinator(is_pro_hardware=True)
    entity, _manager, manager_factory = _camera(monkeypatch, coordinator)
    entity.hass = hass_mock

    assert entity.unique_id == "test-device_camera_hdmi"
    assert entity.supported_features == CameraEntityFeature.STREAM
    assert entity.is_streaming is False
    assert entity.state == CameraState.IDLE
    assert entity.available is True
    coordinator.last_update_success = False
    assert entity.available is False
    assert await entity.stream_source() is None
    assert entity.camera_capabilities.frontend_stream_types == {StreamType.WEB_RTC}

    manager_factory.assert_called_once_with(
        coordinator, entity._handle_streaming_state
    )

def test_camera_streaming_state_tracks_native_webrtc_sessions(
    hass_mock: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The camera is streaming only while its WebRTC manager has a session."""
    entity, _manager, manager_factory = _camera(monkeypatch)
    entity.hass = hass_mock
    write_state = MagicMock()
    monkeypatch.setattr(entity, "async_write_ha_state", write_state)
    state_callback = manager_factory.call_args.args[1]

    state_callback(True)

    assert entity.is_streaming is True
    assert entity.state == CameraState.STREAMING

    state_callback(False)

    assert entity.is_streaming is False
    assert entity.state == CameraState.IDLE
    assert write_state.call_count == 2


@pytest.mark.asyncio
async def test_snapshot_is_suppressed_on_pro_hardware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Pro snapshot never opens MJPEG because it would interrupt WebRTC."""
    entity, _manager, _manager_factory = _camera(
        monkeypatch, _coordinator(is_pro_hardware=True)
    )
    assert await entity._async_read_snapshot_frame() is None
    entity.coordinator.clone_client.assert_not_called()


class _StreamClient(AbstractAsyncContextManager["_StreamClient"]):
    """Authenticated-client test double yielding prescribed MJPEG frames."""

    def __init__(self, frames: list[bytes]) -> None:
        self.frames = frames
        self.stream_closed = False
        self.exited = False

    async def __aenter__(self) -> _StreamClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.exited = True

    async def mjpeg_stream_bytes(self) -> AsyncGenerator[bytes, None]:
        try:
            for frame in self.frames:
                yield frame
        finally:
            self.stream_closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frames", "expected"),
    [
        ([], None),
        ([b"jpeg", b"later"], b"jpeg"),
    ],
)
async def test_snapshot_returns_first_frame_and_closes_stream(
    monkeypatch: pytest.MonkeyPatch,
    frames: list[bytes],
    expected: bytes | None,
) -> None:
    """The first raw frame is returned, and the stream and client are released."""
    entity, _manager, _manager_factory = _camera(monkeypatch)
    client = _StreamClient(frames)
    authenticate = entity.coordinator.async_ensure_authenticated
    entity.coordinator.clone_client.return_value = client

    assert await entity._async_read_snapshot_frame() == expected
    assert client.stream_closed is True
    assert client.exited is True
    authenticate.assert_awaited_once_with(client)


@pytest.mark.asyncio
async def test_async_camera_image_returns_frame_and_handles_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Still-image requests return bytes, but ordinary fetch errors become no image."""
    entity, _manager, _manager_factory = _camera(monkeypatch)
    read_frame = AsyncMock(return_value=b"jpeg")
    monkeypatch.setattr(entity, "_async_read_snapshot_frame", read_frame)

    assert await entity.async_camera_image(width=640, height=480) == b"jpeg"

    read_frame.side_effect = TimeoutError("snapshot timed out")
    with caplog.at_level(logging.WARNING, logger=camera_module.__name__):
        assert await entity.async_camera_image() is None

    assert "Error fetching still image: snapshot timed out" in caplog.text


@pytest.mark.asyncio
async def test_async_camera_image_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task cancellation is not converted into an ordinary missing snapshot."""
    entity, _manager, _manager_factory = _camera(monkeypatch)
    monkeypatch.setattr(
        entity,
        "_async_read_snapshot_frame",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await entity.async_camera_image()


@pytest.mark.asyncio
async def test_webrtc_calls_delegate_to_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offers, ICE candidates, and close notifications retain their arguments."""
    entity, manager, _manager_factory = _camera(monkeypatch)
    send_message: Callable[[Any], None] = MagicMock()
    candidate = RTCIceCandidateInit(
        candidate="candidate:1",
        sdp_mid="0",
        sdp_m_line_index=0,
        user_fragment=None,
    )

    await entity.async_handle_async_webrtc_offer(
        "offer-sdp", "offer-session", send_message
    )
    await entity.async_on_webrtc_candidate("candidate-session", candidate)
    entity.close_webrtc_session("closed-session")

    manager.async_handle_async_webrtc_offer.assert_awaited_once_with(
        "offer-sdp", "offer-session", send_message
    )
    manager.async_on_webrtc_candidate.assert_awaited_once_with(
        "candidate-session", candidate
    )
    manager.close_webrtc_session.assert_called_once_with("closed-session")


@pytest.mark.asyncio
async def test_camera_removal_stops_webrtc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entity removal shuts down the camera's WebRTC manager."""
    calls: list[str] = []

    async def super_cleanup(_entity: object) -> None:
        calls.append("entity")

    entity, manager, _manager_factory = _camera(monkeypatch)
    manager.async_shutdown.side_effect = lambda: calls.append("webrtc")
    monkeypatch.setattr(
        camera_module.NanoKVMEntity,
        "async_will_remove_from_hass",
        super_cleanup,
    )

    await entity.async_will_remove_from_hass()

    assert calls == ["entity", "webrtc"]
    manager.async_shutdown.assert_awaited_once_with()
