"""Bridge Home Assistant WebRTC sessions to NanoKVM signaling."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from homeassistant.components.camera.webrtc import (
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.exceptions import HomeAssistantError
from nanokvm.client import NanoKVMClient, NanoKVMVideoModeError
from nanokvm import webrtc
from webrtc_models import RTCIceCandidateInit

if TYPE_CHECKING:
    from ..coordinator import NanoKVMDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

_MAX_PENDING_ICE_CANDIDATES = 64


@dataclass(slots=True)
class _NanoKVMWebRTCSession:
    """An active NanoKVM signaling session and the client that owns it."""

    client: NanoKVMClient
    signaling: webrtc.WebRTCSignaling
    reader_task: asyncio.Task[None] | None = None


def _to_nanokvm(candidate: RTCIceCandidateInit) -> webrtc.IceCandidate:
    return webrtc.IceCandidate(
        candidate=candidate.candidate,
        sdp_mid=candidate.sdp_mid,
        sdp_m_line_index=candidate.sdp_m_line_index,
        username_fragment=candidate.user_fragment,
    )


def _to_home_assistant(candidate: webrtc.IceCandidate) -> RTCIceCandidateInit:
    return RTCIceCandidateInit(
        candidate.candidate,
        sdp_mid=candidate.sdp_mid,
        sdp_m_line_index=candidate.sdp_m_line_index,
        user_fragment=candidate.username_fragment,
    )


class NanoKVMWebRTCManager:
    """Manage native Home Assistant WebRTC signaling for NanoKVM cameras."""

    def __init__(
        self,
        coordinator: NanoKVMDataUpdateCoordinator,
        session_state_callback: Callable[[bool], None],
    ) -> None:
        """Initialize WebRTC manager."""
        self._coordinator = coordinator
        self._session_state_callback = session_state_callback
        self._sessions: dict[str, _NanoKVMWebRTCSession] = {}
        # Frontend candidates that arrive before the session's offer is sent.
        self._pending_candidates: dict[str, list[RTCIceCandidateInit]] = {}
        self._session_lock = asyncio.Lock()

    def _notify_session_state(self, streaming: bool) -> None:
        """Notify the camera when the active-session state changes."""
        try:
            self._session_state_callback(streaming)
        except Exception:
            _LOGGER.exception(
                "Error publishing NanoKVM WebRTC session state: streaming=%s",
                streaming,
            )

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Handle Home Assistant WebRTC offer using NanoKVM signaling."""
        client = self._coordinator.clone_client()
        signaling: webrtc.WebRTCSignaling | None = None
        registered = False

        async with self._session_lock:
            self._pending_candidates.setdefault(session_id, [])

        try:
            await client.__aenter__()
            await self._coordinator.async_ensure_authenticated(client)
            signaling = await client.open_webrtc_signaling()
            await signaling.send_offer(offer_sdp)

            session = _NanoKVMWebRTCSession(client=client, signaling=signaling)
            async with self._session_lock:
                first_session = not self._sessions
                self._sessions[session_id] = session
                pending = self._pending_candidates.pop(session_id, [])
                registered = True
            if first_session:
                self._notify_session_state(True)

            session.reader_task = self._coordinator.hass.async_create_task(
                self._async_webrtc_reader(session_id, signaling, send_message)
            )
            for candidate in pending:
                await signaling.send_candidate(_to_nanokvm(candidate))
        except Exception as err:
            if registered:
                await self._async_close_webrtc_session(session_id)
            else:
                async with self._session_lock:
                    self._pending_candidates.pop(session_id, None)
                if signaling is not None:
                    with suppress(Exception):
                        await signaling.close()
                with suppress(Exception):
                    await client.__aexit__(None, None, None)
            raise HomeAssistantError(
                f"Unable to establish NanoKVM WebRTC signaling: {err}"
            ) from err

    async def _async_webrtc_reader(
        self,
        session_id: str,
        signaling: webrtc.WebRTCSignaling,
        send_message: WebRTCSendMessage,
    ) -> None:
        """Forward NanoKVM answers and candidates to the HA frontend."""
        try:
            async for event in signaling:
                if isinstance(event, webrtc.WebRTCAnswer):
                    send_message(WebRTCAnswer(answer=event.sdp))
                else:
                    send_message(
                        WebRTCCandidate(candidate=_to_home_assistant(event.candidate))
                    )
        except NanoKVMVideoModeError as err:
            send_message(
                WebRTCError(code="webrtc_inconsistent_video_mode", message=str(err))
            )
            _LOGGER.warning("%s; closing signaling session", err)
        except Exception as err:
            _LOGGER.error("Error reading NanoKVM WebRTC signaling: %s", err)
            send_message(WebRTCError(code="webrtc_signal_failed", message=str(err)))
        finally:
            await self._async_close_webrtc_session(session_id)

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Forward frontend ICE candidates to NanoKVM signaling websocket."""
        async with self._session_lock:
            session = self._sessions.get(session_id)
            if session is None:
                queue = self._pending_candidates.setdefault(session_id, [])
                if len(queue) < _MAX_PENDING_ICE_CANDIDATES:
                    queue.append(candidate)
                return

        try:
            await session.signaling.send_candidate(_to_nanokvm(candidate))
        except Exception as err:
            raise HomeAssistantError(
                f"Unable to forward WebRTC candidate to NanoKVM: {err}"
            ) from err

    async def _async_close_webrtc_session(self, session_id: str) -> None:
        """Close and cleanup an active NanoKVM WebRTC signaling session."""
        async with self._session_lock:
            session = self._sessions.pop(session_id, None)
            self._pending_candidates.pop(session_id, None)
            last_session = session is not None and not self._sessions

        if session is None:
            return

        if last_session:
            self._notify_session_state(False)

        if (
            session.reader_task is not None
            and session.reader_task is not asyncio.current_task()
        ):
            session.reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await session.reader_task

        with suppress(Exception):
            await session.signaling.close()

        with suppress(Exception):
            await session.client.__aexit__(None, None, None)

    def close_webrtc_session(self, session_id: str) -> None:
        """Close a WebRTC session when frontend unsubscribes."""
        self._coordinator.hass.async_create_task(
            self._async_close_webrtc_session(session_id)
        )

    async def async_shutdown(self) -> None:
        """Close all active WebRTC sessions."""
        for session_id in list(self._sessions):
            await self._async_close_webrtc_session(session_id)
