import json
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPOSITORY_ROOT / "scripts" / "project_link_console_config.py"


def _run(tmp_path, operation: str, section: str, payload=None):
    environment = os.environ.copy()
    environment["HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(HELPER), operation, section],
        input="" if payload is None else json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_global_get_masks_secret_values(tmp_path):
    config_dir = tmp_path / ".config" / "project_link"
    config_dir.mkdir(parents=True)
    (config_dir / "voice_api.env").write_text(
        "export DEEPSEEK_API_KEY=secret-value\nPROJECT_LINK_ASR_PROVIDER=volcano\n",
        encoding="utf-8",
    )
    value = _run(tmp_path, "get", "global")
    deepseek = value["files"]["voice_api"]["DEEPSEEK_API_KEY"]
    assert deepseek == {"secret": True, "configured": True, "value": ""}
    assert value["files"]["voice_api"]["PROJECT_LINK_ASR_PROVIDER"]["value"] == "volcano"


def test_voice_and_uwb_runtime_overrides_round_trip(tmp_path):
    voice = _run(tmp_path, "get", "voice")
    voice["classic"]["audio_end_silence_ms"] = 650
    voice["qwen"]["turn_detection_threshold"] = 0.6
    saved = _run(tmp_path, "set", "voice", voice)
    assert saved["restart_required"] is True
    loaded = _run(tmp_path, "get", "voice")
    assert loaded["classic"]["audio_end_silence_ms"] == 650
    assert loaded["qwen"]["turn_detection_threshold"] == 0.6

    result = _run(
        tmp_path,
        "set",
        "uwb",
        {
            "calibration": {
                "calibration_version": "test-proposed",
                "axis_xx": 0.0,
                "axis_xy": 1.0,
                "axis_yx": -1.0,
                "axis_yy": 0.0,
            },
            "tuning": {"uwb_ttl_sec": 0.8, "acquisition_count": 8},
        },
    )
    assert result["calibration_status"] == "proposed"
    uwb = _run(tmp_path, "get", "uwb")
    assert uwb["calibration"]["calibration_status"] == "proposed"
    assert uwb["tuning"]["uwb_ttl_sec"] == 0.8
