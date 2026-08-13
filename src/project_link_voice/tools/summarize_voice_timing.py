#!/usr/bin/env python3
"""Summarize Project LINK voice timing JSONL by phase."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


DEFAULT_PHASES = (
    "speech_end_to_vad",
    "vad_to_asr_final",
    "asr_final_to_llm_send",
    "llm_first_delta",
    "llm_first_text",
    "llm_to_tool_call",
    "python_tool",
    "tool_to_tts_send",
    "tts_to_first_audio",
    "first_audio_to_playback",
    "speech_end_to_first_playback",
)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("~/.ros/project_link_voice/voice_timing.jsonl").expanduser(),
    )
    parser.add_argument("--last", type=int, default=0, help="Use only the last N values per phase.")
    parser.add_argument("--all-phases", action="store_true")
    arguments = parser.parse_args()

    values: dict[str, list[float]] = defaultdict(list)
    with arguments.path.expanduser().open(encoding="utf-8-sig") as timing_file:
        for line in timing_file:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") != "timing" or not row.get("phase"):
                continue
            try:
                values[str(row["phase"])].append(float(row["elapsed_ms"]))
            except (TypeError, ValueError):
                continue

    phases = sorted(values) if arguments.all_phases else DEFAULT_PHASES
    print("phase                                    n      mean       p50       p95       min       max")
    for phase in phases:
        samples = values.get(phase, [])
        if arguments.last > 0:
            samples = samples[-arguments.last :]
        if not samples:
            continue
        print(
            f"{phase:40s} {len(samples):4d} "
            f"{statistics.fmean(samples):9.1f} "
            f"{percentile(samples, 0.50):9.1f} "
            f"{percentile(samples, 0.95):9.1f} "
            f"{min(samples):9.1f} "
            f"{max(samples):9.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
