"""Behavior tests for NanoKVM WebRTC signaling and session lifecycle."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Coroutine
import json
from types import SimpleNamespace
from typing import Any

from aiohttp import WSMsgType
from homeassistant.components.camera.webrtc import (
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCError,
)
from homeassistant.exceptions import HomeAssistantError
import pytest
import pytest_asyncio
from webrtc_models import RTCIceCandidateInit

from nanokvm.webrtc import WebRTCSignaling

from custom_components.nanokvm.media import signaling as webrtc_module
from custom_components.nanokvm.media.signaling import NanoKVMWebRTCManager


COMBINED_OFFER = "\r\n".join(
    (
        "v=0",
        "o=- 1 1 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE video-main audio-main",
        "m=video 9 UDP/TLS/RTP/SAVPF 96",
        "a=mid:video-main",
        "a=sendrecv",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111",
        "a=mid:audio-main",
        "a=sendrecv",
        "",
    )
)
VIDEO_ONLY_OFFER = "\r\n".join(
    (
        "v=0",
        "o=- 1 1 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE camera",
        "m=video 9 UDP/TLS/RTP/SAVPF 96",
        "a=mid:camera",
        "a=sendrecv",
        "",
    )
)
VIDEO_ANSWER = "\r\n".join(
    (
        "v=0",
        "o=- 2 2 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=ice-ufrag:video-ufrag",
        "a=ice-pwd:video-password",
        "m=video 9 UDP/TLS/RTP/SAVPF 96",
        "a=mid:0",
        "a=recvonly",
        "",
    )
)
AUDIO_ANSWER = "\r\n".join(
    (
        "v=0",
        "o=- 3 3 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=ice-ufrag:audio-ufrag",
        "a=ice-pwd:audio-password",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111",
        "a=mid:0",
        "a=recvonly",
        "",
    )
)
_END = object()


class FakeWebSocket:
    """Small asynchronous WebSocket boundary with observable I/O."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_calls = 0
        self.send_failures: dict[str, Exception] = {}
        self.close_error: Exception | None = None
        self._incoming: asyncio.Queue[object] = asyncio.Queue()
        self._exception: Exception | None = None

    def __aiter__(self) -> FakeWebSocket:
        return self

    async def __anext__(self) -> object:
        item = await self._incoming.get()
        if item is _END:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item

    async def send_json(self, payload: dict[str, object]) -> None:
        event = payload.get("event")
        if isinstance(event, str) and event in self.send_failures:
            raise self.send_failures[event]
        self.sent.append(payload)

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        self._incoming.put_nowait(_END)
        if self.close_error is not None:
            raise self.close_error

    def exception(self) -> Exception | None:
        """Return the transport exception exposed by aiohttp."""
        return self._exception

    def feed_signal(self, event: object, data: object = "") -> None:
        """Feed one JSON signaling message to the reader."""
        self.feed_raw(json.dumps({"event": event, "data": data}))

    def feed_raw(
        self, data: object, *, message_type: WSMsgType = WSMsgType.TEXT
    ) -> None:
        """Feed one aiohttp-shaped message to the reader."""
        self._incoming.put_nowait(SimpleNamespace(type=message_type, data=data))

    def feed_error(self, error: Exception) -> None:
        """Make the reader's next receive raise an exception."""
        self._exception = error
        self._incoming.put_nowait(error)

    def feed_close(self) -> None:
        """Feed the close frame observed by a normal reader shutdown."""
        self._incoming.put_nowait(SimpleNamespace(type=WSMsgType.CLOSE, data=None))


class FakeClient:
    """NanoKVM client boundary, opening real signaling on a fake WebSocket."""

    def __init__(
        self, websocket: FakeWebSocket | None = None, *, pro: bool = False
    ) -> None:
        self.websocket = websocket or FakeWebSocket()
        self.pro = pro
        self.open_calls = 0
        self.open_error: Exception | None = None
        self.open_started = asyncio.Event()
        self.open_release: asyncio.Event | None = None
        self.enter_calls = 0
        self.exit_calls = 0
        self.exit_error: Exception | None = None

    async def open_webrtc_signaling(self) -> WebRTCSignaling:
        """Record the request and return or fail the signaling session."""
        self.open_calls += 1
        self.open_started.set()
        if self.open_release is not None:
            await self.open_release.wait()
        if self.open_error is not None:
            raise self.open_error
        return WebRTCSignaling(self.websocket, pro=self.pro)  # type: ignore[arg-type]

    async def __aenter__(self) -> FakeClient:
        self.enter_calls += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self.exit_calls += 1
        if self.exit_error is not None:
            raise self.exit_error


class FakeHass:
    """Home Assistant task-scheduling boundary."""

    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[None]] = []

    def async_create_task(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Schedule and retain an integration task."""
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


class WebRTCHarness:
    """Manager with controllable external boundaries."""

    def __init__(
        self,
        *,
        clients: list[FakeClient] | None = None,
        hass: FakeHass | None = None,
        authenticate_error: Exception | None = None,
        session_state_callback: Callable[[bool], None] = lambda streaming: None,
    ) -> None:
        self.clients = deque(clients or [FakeClient()])
        self.hass = hass or FakeHass()
        self.authenticate_error = authenticate_error
        self.authenticated: list[FakeClient] = []

        async def authenticate(client: FakeClient) -> None:
            self.authenticated.append(client)
            if self.authenticate_error is not None:
                raise self.authenticate_error

        self.manager = NanoKVMWebRTCManager(
            SimpleNamespace(
                hass=self.hass,
                clone_client=lambda: self.clients.popleft(),
                async_ensure_authenticated=authenticate,
            ),
            session_state_callback,
        )


@pytest_asyncio.fixture
async def harness_factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build harnesses and guarantee active signaling tasks are collected."""
    harnesses: list[WebRTCHarness] = []

    def factory(
        *, max_pending_ice_candidates: int | None = None, **kwargs: object
    ) -> WebRTCHarness:
        if max_pending_ice_candidates is not None:
            monkeypatch.setattr(
                webrtc_module,
                "_MAX_PENDING_ICE_CANDIDATES",
                max_pending_ice_candidates,
            )
        harness = WebRTCHarness(**kwargs)
        harnesses.append(harness)
        return harness

    yield factory

    for harness in harnesses:
        await harness.manager.async_shutdown()
        if harness.hass.tasks:
            await asyncio.gather(*harness.hass.tasks, return_exceptions=True)


def _candidate(
    value: str = "candidate:1 1 UDP 1 192.0.2.10 5000 typ host",
    *,
    mid: str | None = "video-main",
    index: int | None = 0,
    user_fragment: str | None = "ufrag",
) -> RTCIceCandidateInit:
    """Return a representative frontend or device ICE candidate."""
    return RTCIceCandidateInit(
        value,
        sdp_mid=mid,
        sdp_m_line_index=index,
        user_fragment=user_fragment,
    )


def _decode_sent_data(message: dict[str, object]) -> dict[str, object]:
    """Decode one outbound NanoKVM signaling data field."""
    data = message["data"]
    assert isinstance(data, str)
    decoded = json.loads(data)
    assert isinstance(decoded, dict)
    return decoded


async def _finish_reader(client: FakeClient, hass: FakeHass) -> None:
    """End a reader through the normal WebSocket close-frame path."""
    client.websocket.feed_close()
    await hass.tasks[0]


async def test_non_pro_session_signals_offer_answer_candidate_and_cleanup(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """A complete non-Pro exchange must preserve SDP, ICE, and transport state."""
    client = FakeClient()
    harness = harness_factory(clients=[client])
    messages: list[object] = []

    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "session", messages.append
    )

    assert client.enter_calls == 1
    assert harness.authenticated == [client]
    assert client.open_calls == 1
    assert client.websocket.sent == [
        {
            "event": "video-offer",
            "data": json.dumps({"type": "offer", "sdp": VIDEO_ONLY_OFFER}),
        }
    ]

    client.websocket.feed_signal(
        "video-answer", json.dumps({"type": "answer", "sdp": VIDEO_ANSWER})
    )
    client.websocket.feed_signal(
        "video-candidate",
        json.dumps(
            {
                "candidate": "candidate:device",
                "sdpMid": "0",
                "sdpMLineIndex": 0,
                "usernameFragment": "device-ufrag",
            }
        ),
    )
    await _finish_reader(client, harness.hass)

    assert messages == [
        WebRTCAnswer(answer=VIDEO_ANSWER),
        WebRTCCandidate(
            candidate=RTCIceCandidateInit(
                "candidate:device",
                sdp_mid="0",
                sdp_m_line_index=0,
                user_fragment="device-ufrag",
            )
        ),
    ]
    assert client.websocket.closed
    assert client.exit_calls == 1


async def test_session_state_callback_tracks_first_open_and_last_close(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """Only the first open and last close transition the public streaming state."""
    clients = [FakeClient(), FakeClient()]
    states: list[bool] = []
    harness = harness_factory(clients=clients, session_state_callback=states.append)

    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "first", lambda _message: None
    )
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "second", lambda _message: None
    )

    assert states == [True]

    await harness.manager._async_close_webrtc_session("first")
    assert states == [True]

    await harness.manager._async_close_webrtc_session("second")
    assert states == [True, False]


async def test_reader_ignores_malformed_and_irrelevant_messages(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """Noise on the signaling socket must not surface false HA messages."""
    client = FakeClient()
    harness = harness_factory(clients=[client])
    messages: list[object] = []
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "session", messages.append
    )

    client.websocket.feed_raw("not json")
    client.websocket.feed_signal("video-answer", {})
    client.websocket.feed_signal("video-candidate", json.dumps({"sdpMid": "0"}))
    client.websocket.feed_raw(b"ignored", message_type=WSMsgType.BINARY)
    await _finish_reader(client, harness.hass)

    assert messages == []
    assert client.exit_calls == 1


async def test_pro_session_presents_one_combined_peer_connection(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """Pro split peers must appear to HA as one combined peer connection."""
    client = FakeClient(pro=True)
    harness = harness_factory(clients=[client])
    messages: list[object] = []

    await harness.manager.async_handle_async_webrtc_offer(
        COMBINED_OFFER, "pro-session", messages.append
    )
    await harness.manager.async_on_webrtc_candidate(
        "pro-session", _candidate(mid="audio-main", index=1)
    )

    assert [message["event"] for message in client.websocket.sent] == [
        "video-offer",
        "audio-offer",
        "audio-candidate",
    ]
    client.websocket.feed_signal("video-answer", json.dumps({"sdp": VIDEO_ANSWER}))
    client.websocket.feed_signal("audio-answer", json.dumps({"sdp": AUDIO_ANSWER}))
    client.websocket.feed_signal(
        "audio-candidate",
        json.dumps({"candidate": "candidate:audio", "sdpMid": "0"}),
    )
    await _finish_reader(client, harness.hass)

    assert isinstance(messages[0], WebRTCAnswer)
    assert "a=mid:video-main\r\n" in messages[0].answer
    assert "a=mid:audio-main\r\n" in messages[0].answer
    assert messages[1:] == [
        WebRTCCandidate(
            candidate=RTCIceCandidateInit(
                "candidate:audio", sdp_mid="audio-main", sdp_m_line_index=1
            )
        ),
    ]


async def test_pro_inconsistent_video_status_reports_error_and_closes(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """A conflicting firmware video mode must not leave a frozen HA session."""
    client = FakeClient(pro=True)
    harness = harness_factory(clients=[client])
    messages: list[object] = []
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "pro-session", messages.append
    )

    client.websocket.feed_signal("video-status", json.dumps({"status": "-4"}))
    await harness.hass.tasks[0]

    assert messages == [
        WebRTCError(
            code="webrtc_inconsistent_video_mode",
            message=(
                "NanoKVM Pro stopped WebRTC video because another video mode "
                "is active"
            ),
        )
    ]
    assert client.websocket.closed
    assert client.exit_calls == 1


async def test_candidate_concurrent_with_connection_is_queued_capped_and_flushed(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """ICE arriving during setup must flush after the offer without exceeding the cap."""
    client = FakeClient()
    client.open_release = asyncio.Event()
    harness = harness_factory(clients=[client], max_pending_ice_candidates=2)
    offer_task = asyncio.create_task(
        harness.manager.async_handle_async_webrtc_offer(
            VIDEO_ONLY_OFFER, "session", lambda message: None
        )
    )
    try:
        await client.open_started.wait()

        candidates = [_candidate(f"candidate:{index}") for index in range(3)]
        await asyncio.gather(
            *(
                harness.manager.async_on_webrtc_candidate("session", item)
                for item in candidates
            )
        )
        client.open_release.set()
        await offer_task
    finally:
        client.open_release.set()
        if not offer_task.done():
            offer_task.cancel()
            await asyncio.gather(offer_task, return_exceptions=True)

    assert [message["event"] for message in client.websocket.sent] == [
        "video-offer",
        "video-candidate",
        "video-candidate",
    ]
    assert [
        _decode_sent_data(message)["candidate"] for message in client.websocket.sent[1:]
    ] == ["candidate:0", "candidate:1"]


async def test_frontend_candidate_keeps_its_fields(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """Frontend ICE must reach the device with every field it carries."""
    client = FakeClient()
    harness = harness_factory(clients=[client])
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "session", lambda message: None
    )
    await harness.manager.async_on_webrtc_candidate("session", _candidate())

    assert _decode_sent_data(client.websocket.sent[-1]) == {
        "candidate": "candidate:1 1 UDP 1 192.0.2.10 5000 typ host",
        "sdpMid": "video-main",
        "sdpMLineIndex": 0,
        "usernameFragment": "ufrag",
    }


async def test_candidate_send_failure_is_home_assistant_error(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """A live signaling write failure must use the integration's public error type."""
    client = FakeClient()
    harness = harness_factory(clients=[client])
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "session", lambda message: None
    )
    client.websocket.send_failures["video-candidate"] = RuntimeError("socket lost")

    with pytest.raises(
        HomeAssistantError,
        match="Unable to forward WebRTC candidate to NanoKVM: socket lost",
    ):
        await harness.manager.async_on_webrtc_candidate("session", _candidate())


@pytest.mark.parametrize(
    ("configure", "expected", "closes_websocket"),
    [
        (
            lambda harness, client: setattr(
                harness, "authenticate_error", RuntimeError("bad credentials")
            ),
            "bad credentials",
            False,
        ),
        (
            lambda harness, client: setattr(
                client, "open_error", TimeoutError("connect timed out")
            ),
            "connect timed out",
            False,
        ),
        (
            lambda harness, client: client.websocket.send_failures.__setitem__(
                "video-offer", RuntimeError("write failed")
            ),
            "write failed",
            True,
        ),
    ],
)
async def test_offer_setup_failures_release_the_client(
    harness_factory: Callable[..., WebRTCHarness],
    configure: Callable[[WebRTCHarness, FakeClient], None],
    expected: str,
    closes_websocket: bool,
) -> None:
    """Failures before the session starts must remove queues and exit the client."""
    client = FakeClient()
    states: list[bool] = []
    harness = harness_factory(clients=[client], session_state_callback=states.append)
    configure(harness, client)

    with pytest.raises(
        HomeAssistantError,
        match=f"Unable to establish NanoKVM WebRTC signaling: {expected}",
    ):
        await harness.manager.async_handle_async_webrtc_offer(
            VIDEO_ONLY_OFFER, "session", lambda value: None
        )

    assert client.exit_calls == 1
    assert client.websocket.closed is closes_websocket
    assert "session" not in harness.manager._pending_candidates
    assert states == []
    assert harness.hass.tasks == []


async def test_pro_offer_without_supported_media_is_rejected(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """A Pro offer with no audio/video section must fail and release the client."""
    client = FakeClient(pro=True)
    harness = harness_factory(clients=[client])

    with pytest.raises(
        HomeAssistantError, match="offer does not contain audio or video media"
    ):
        await harness.manager.async_handle_async_webrtc_offer(
            "v=0\r\nm=application 9 UDP/DTLS/SCTP 5000\r\n",
            "session",
            lambda value: None,
        )

    assert client.websocket.closed
    assert client.exit_calls == 1


async def test_reader_timeout_reports_error_and_releases_session(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """An aiohttp receive timeout must be visible to HA and clean up the session."""
    client = FakeClient()
    harness = harness_factory(clients=[client])
    messages: list[object] = []
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "session", messages.append
    )

    client.websocket.feed_error(TimeoutError("signal timed out"))
    await harness.hass.tasks[0]

    assert messages == [
        WebRTCError(code="webrtc_signal_failed", message="signal timed out")
    ]
    assert client.websocket.closed
    assert client.exit_calls == 1


async def test_close_callback_schedules_cleanup(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """Frontend unsubscribe must schedule the session cleanup."""
    client = FakeClient()
    harness = harness_factory(clients=[client])
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "session", lambda value: None
    )

    harness.manager.close_webrtc_session("session")
    await harness.hass.tasks[-1]
    assert client.exit_calls == 1
    assert harness.hass.tasks[0].cancelled()


async def test_shutdown_closes_all_sessions_and_tolerates_close_errors(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """Manager shutdown must attempt every session even when resource closes fail."""
    first = FakeClient(pro=True)
    first.websocket.close_error = RuntimeError("close failed")
    first.exit_error = RuntimeError("exit failed")
    second = FakeClient()
    harness = harness_factory(clients=[first, second])
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "first", lambda value: None
    )
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "second", lambda value: None
    )

    await harness.manager.async_shutdown()

    assert first.websocket.close_calls >= 1
    assert second.websocket.closed
    assert first.exit_calls == second.exit_calls == 1


async def test_new_session_keeps_other_active_session_alive(
    harness_factory: Callable[..., WebRTCHarness],
) -> None:
    """NanoKVM Pro can broadcast one encoder stream to multiple clients."""
    first = FakeClient(pro=True)
    second = FakeClient(pro=True)
    harness = harness_factory(clients=[first, second])
    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "first", lambda value: None
    )

    await harness.manager.async_handle_async_webrtc_offer(
        VIDEO_ONLY_OFFER, "second", lambda value: None
    )

    assert not first.websocket.closed
    assert first.exit_calls == 0
    assert not second.websocket.closed
    assert second.exit_calls == 0


def test_real_client_exposes_the_stream_methods() -> None:
    """The stream paths call these library methods; fail if they move."""
    from nanokvm.client import NanoKVMClient

    assert callable(NanoKVMClient.open_webrtc_signaling)
    assert callable(NanoKVMClient.mjpeg_stream_bytes)
