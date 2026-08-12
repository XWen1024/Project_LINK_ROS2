"""Pure state-policy helpers for the Volcengine continuous S2S wrapper."""

from __future__ import annotations


def input_event_belongs_to_turn(first_input_ns: int | None, event_ns: int) -> bool:
    """Reject session greeting/status events that predate user PCM upload."""
    return first_input_ns is not None and int(event_ns) >= int(first_input_ns)


def response_audio_belongs_to_turn(
    first_input_ns: int | None,
    response_created_ns: int | None,
    audio_ns: int,
) -> bool:
    """Accept audio only after this turn has both input and response.created."""
    return (
        first_input_ns is not None
        and response_created_ns is not None
        and int(audio_ns) >= int(response_created_ns)
    )


def response_event_belongs_to_turn(
    first_input_ns: int | None,
    server_input_done: bool,
    event_ns: int,
) -> bool:
    """A response must follow the cloud endpoint for this user's audio."""
    return (
        server_input_done
        and first_input_ns is not None
        and int(event_ns) >= int(first_input_ns)
    )


def endpoint_event_belongs_to_turn(
    first_input_ns: int | None,
    speech_started_ns: int | None,
    event_ns: int,
) -> bool:
    """Do not let a session greeting or previous turn stop current capture."""
    return (
        first_input_ns is not None
        and speech_started_ns is not None
        and int(event_ns) >= int(speech_started_ns)
    )


def no_speech_outcome(continuation: bool) -> str:
    return "continuous_silence_timeout" if continuation else "no_speech_timeout"


def can_continue_session(
    enabled: bool,
    turn_index: int,
    max_turns: int,
    bridge_connected: bool,
    session_ready: bool,
) -> tuple[bool, str]:
    if not enabled:
        return False, "continuous_disabled"
    if int(turn_index) >= max(1, int(max_turns)):
        return False, "max_turns"
    if not bridge_connected or not session_ready:
        return False, "connection_lost"
    return True, "continue"
