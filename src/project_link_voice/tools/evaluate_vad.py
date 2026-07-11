#!/usr/bin/env python3
"""Evaluate FunASR VAD endpointing against a 16 kHz mono PCM WAV capture."""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

from project_link_voice.funvad import FunVadRecorder, VadEndpointState, VadSettings, extract_vad_events


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--model", default="fsmn-vad")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--chunk-ms", type=int, default=200)
    parser.add_argument("--max-utterance-sec", type=float, default=12.0)
    arguments = parser.parse_args(argv)
    with wave.open(str(arguments.wav), "rb") as wav:
        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            parser.error("WAV must be 16 kHz, mono, signed 16-bit PCM")
        pcm = wav.readframes(wav.getnframes())

    settings = VadSettings(chunk_ms=arguments.chunk_ms, max_utterance_sec=arguments.max_utterance_sec)
    recorder = FunVadRecorder(settings, arguments.model, arguments.device)
    model = recorder._model_instance()
    state = VadEndpointState(settings)
    cache = {}
    for offset in range(0, len(pcm), settings.chunk_bytes):
        chunk = pcm[offset : offset + settings.chunk_bytes]
        if len(chunk) < settings.chunk_bytes:
            break
        result = model.generate(input=chunk, cache=cache, is_final=False)
        reason = state.feed(chunk, extract_vad_events(result))
        if reason:
            print(f"reason={reason} elapsed_ms={state.elapsed_ms} retained_bytes={len(state.audio)}")
            return 0
    print(f"reason=input_exhausted elapsed_ms={state.elapsed_ms} retained_bytes={len(state.audio)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))