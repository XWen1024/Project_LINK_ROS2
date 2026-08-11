#!/usr/bin/env python3
"""Synthesize text with Volcano TTS and save mono S16LE PCM audio."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import shlex
import sys
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VOICE_SOURCE = REPO_ROOT / "src" / "project_link_voice"
if str(VOICE_SOURCE) not in sys.path:
    sys.path.insert(0, str(VOICE_SOURCE))

from project_link_voice.tts_protocols import (  # noqa: E402
    EventType,
    MsgType,
    finish_session,
    receive_message,
    start_connection,
    start_session,
    task_request,
)


WS_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
DEFAULT_TIMEOUT_SEC = 60.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True, help="Text to synthesize.")
    parser.add_argument("--output", required=True, help="Destination .pcm path.")
    parser.add_argument(
        "--sample-rate",
        type=int,
        choices=(16000, 24000),
        default=24000,
        help="PCM sample rate, default: 24000.",
    )
    parser.add_argument(
        "--speaker",
        default=None,
        help="Speaker ID. Defaults to VOLCANO_SPEAKER.",
    )
    parser.add_argument(
        "--resource-id",
        default=None,
        help="Resource ID. Defaults to VOLCANO_RESOURCE_ID or seed-tts-2.0.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file.")
    return parser.parse_args()


def request_payload(speaker: str, sample_rate: int, text: str | None = None) -> bytes:
    request_params: dict[str, object] = {
        "speaker": speaker,
        "audio_params": {"format": "pcm", "sample_rate": sample_rate},
        "additions": json.dumps({"disable_markdown_filter": True}),
    }
    if text is not None:
        request_params["text"] = text
    return json.dumps(
        {
            "user": {"uid": str(uuid.uuid4())},
            "namespace": "BidirectionalTTS",
            "req_params": request_params,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def message_details(message) -> str:
    payload = message.payload.decode("utf-8", errors="replace").strip()
    fields = [f"type={message.type}", f"event={message.event}"]
    if message.error_code:
        fields.append(f"error_code={message.error_code}")
    if payload:
        fields.append(f"payload={payload}")
    return ", ".join(fields)


async def expect_event(websocket, event: EventType) -> None:
    message = await receive_message(websocket)
    if message.type == MsgType.Error:
        raise RuntimeError(f"Volcano TTS returned an error: {message_details(message)}")
    if message.type != MsgType.FullServerResponse or message.event != event:
        raise RuntimeError(f"Unexpected Volcano TTS response: {message_details(message)}")


async def synthesize_pcm(
    websockets_module,
    text: str,
    app_id: str,
    access_token: str,
    resource_id: str,
    speaker: str,
    sample_rate: int,
) -> bytes:
    headers = {
        "X-Api-App-Key": app_id,
        "X-Api-Access-Key": access_token,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }
    connect_parameters = inspect.signature(websockets_module.connect).parameters
    header_argument = "additional_headers" if "additional_headers" in connect_parameters else "extra_headers"
    connect_options = {
        header_argument: headers,
        "max_size": 10 * 1024 * 1024,
        "open_timeout": 10,
        "close_timeout": 5,
    }
    session_id = str(uuid.uuid4())
    async with websockets_module.connect(WS_ENDPOINT, **connect_options) as websocket:
        await start_connection(websocket)
        await expect_event(websocket, EventType.ConnectionStarted)
        await start_session(websocket, request_payload(speaker, sample_rate), session_id)
        await expect_event(websocket, EventType.SessionStarted)
        await task_request(websocket, request_payload(speaker, sample_rate, text), session_id)
        await finish_session(websocket, session_id)

        pcm_audio = bytearray()
        while True:
            message = await receive_message(websocket)
            if message.type == MsgType.Error:
                raise RuntimeError(f"Volcano TTS returned an error: {message_details(message)}")
            if message.type == MsgType.AudioOnlyServer:
                pcm_audio.extend(message.payload)
                continue
            if message.type != MsgType.FullServerResponse:
                continue
            if message.event == EventType.SessionFinished:
                break
            if message.event in (EventType.SessionCanceled, EventType.SessionFailed):
                raise RuntimeError(f"Volcano TTS session did not finish: {message_details(message)}")

    if not pcm_audio:
        raise RuntimeError("Volcano TTS returned no PCM audio. Check speaker permission and input text.")
    if len(pcm_audio) % 2:
        raise RuntimeError(f"Volcano TTS returned an invalid odd PCM byte count: {len(pcm_audio)}")
    return bytes(pcm_audio)


async def synthesize_with_timeout(**kwargs) -> bytes:
    return await asyncio.wait_for(synthesize_pcm(**kwargs), timeout=DEFAULT_TIMEOUT_SEC)


def write_atomic(output: Path, pcm_audio: bytes, force: bool) -> None:
    if output.exists() and not force:
        raise FileExistsError(f"Output already exists: {output}. Add --force to overwrite it.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(pcm_audio)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    text = args.text.strip()
    if not text:
        print("Error: --text cannot be empty.", file=sys.stderr)
        return 2

    app_id = os.environ.get("VOLCANO_APP_ID", "").strip()
    access_token = os.environ.get("VOLCANO_ACCESS_TOKEN", "").strip()
    resource_id = (args.resource_id or os.environ.get("VOLCANO_RESOURCE_ID") or "seed-tts-2.0").strip()
    speaker = (args.speaker or os.environ.get("VOLCANO_SPEAKER") or "").strip()
    missing = [
        name
        for name, value in (
            ("VOLCANO_APP_ID", app_id),
            ("VOLCANO_ACCESS_TOKEN", access_token),
            ("VOLCANO_SPEAKER or --speaker", speaker),
        )
        if not value
    ]
    if missing:
        print(f"Error: missing required configuration: {', '.join(missing)}", file=sys.stderr)
        return 2

    output = Path(args.output).expanduser()
    if output.exists() and not args.force:
        print(f"Error: output already exists: {output}. Add --force to overwrite it.", file=sys.stderr)
        return 2

    try:
        import websockets
    except ImportError:
        print("Error: websockets is not installed. Install project_link_voice requirements first.", file=sys.stderr)
        return 2

    try:
        pcm_audio = asyncio.run(
            synthesize_with_timeout(
                websockets_module=websockets,
                text=text,
                app_id=app_id,
                access_token=access_token,
                resource_id=resource_id,
                speaker=speaker,
                sample_rate=args.sample_rate,
            )
        )
        write_atomic(output, pcm_audio, args.force)
    except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    duration_sec = len(pcm_audio) / (args.sample_rate * 2)
    print(f"Saved PCM: {output}")
    print(f"Audio: {len(pcm_audio)} bytes, {duration_sec:.2f} s, mono S16_LE, {args.sample_rate} Hz")
    print(f"Playback: aplay -f S16_LE -r {args.sample_rate} -c 1 {shlex.quote(str(output))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
