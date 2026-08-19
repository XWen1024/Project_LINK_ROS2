#!/usr/bin/env python3
"""Allowlisted local configuration API used only through the Ubuntu SSH client."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
from typing import Any

import yaml


WORKSPACE = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path.home() / ".config" / "project_link"
VOICE_CLASSIC_PATH = CONFIG_DIR / "voice_classic.yaml"
VOICE_QWEN_PATH = CONFIG_DIR / "voice_qwen.yaml"
VOICE_PROFILE_PATH = CONFIG_DIR / "voice_profile.json"
UWB_CONFIG_PATH = CONFIG_DIR / "uwb_navigation.yaml"

DEFAULT_VOICE_CLASSIC = WORKSPACE / "src/project_link_voice/config/voice_direct_drive.yaml"
DEFAULT_VOICE_QWEN = WORKSPACE / "src/project_link_qwen_realtime_voice/config/qwen_realtime_voice.yaml"
DEFAULT_VOICE_PROFILE = WORKSPACE / "configs/console/voice_profile.json"
DEFAULT_UWB_CONFIG = WORKSPACE / "src/project_link_uwb_navigation/config/uwb_navigation.yaml"


VOICE_PARAMETERS = {
    "classic": {
        "audio_end_silence_ms": (int, 100, 3000),
        "audio_no_speech_timeout_sec": (float, 1.0, 60.0),
        "audio_max_utterance_sec": (float, 2.0, 60.0),
        "continuous_silence_timeout_sec": (float, 2.0, 120.0),
        "audio_pre_roll_ms": (int, 0, 2000),
        "audio_min_speech_sec": (float, 0.05, 3.0),
        "volcano_asr_packet_ms": (int, 20, 1000),
        "volcano_asr_final_timeout_sec": (float, 0.5, 15.0),
        "waiting_prompt_delay_ms": (int, 0, 3000),
        "confirmation_timeout_sec": (float, 5.0, 120.0),
    },
    "qwen": {
        "turn_detection_threshold": (float, 0.0, 1.0),
        "turn_detection_silence_duration_ms": (int, 100, 5000),
        "prefix_padding_ms": (int, 0, 2000),
        "continuous_silence_timeout_sec": (float, 2.0, 180.0),
        "barge_in_enabled": (bool, None, None),
        "audio_input_chunk_ms": (int, 20, 500),
        "audio_output_chunk_ms": (int, 10, 500),
        "first_turn_no_speech_timeout_sec": (float, 2.0, 60.0),
        "confirmation_timeout_sec": (float, 5.0, 120.0),
    },
}

REGISTERED_TOOLS = {
    "get_weather",
    "get_current_location",
    "save_waypoint",
    "list_saved_locations",
    "navigate_to_location",
    "fetch_item_from_location",
    "cancel_current_task",
}

ENV_FILES = {
    "console": (
        CONFIG_DIR / "console.env",
        {
            "PROJECT_LINK_WORKSPACE": False,
            "UNILIDAR_WS": False,
            "CHASSIS_DEVICE": False,
            "UNILIDAR_PORT": False,
            "FRONT_CAMERA_DEVICE": False,
            "FRONT_CAMERA_ROTATION_DEGREES": False,
            "FRONT_CAMERA_PREFER_NATIVE_MJPEG": False,
            "FRONT_CAMERA_MANUAL_EXPOSURE": False,
            "FRONT_CAMERA_EXPOSURE_ABSOLUTE": False,
            "FRONT_CAMERA_GAIN": False,
            "ROS_DOMAIN_ID": False,
            "ROS_LOCALHOST_ONLY": False,
            "PROJECT_LINK_VOICE_ENABLE_MOTION": False,
            "PROJECT_LINK_VOICE_ENABLE_VISUAL_GRASP": False,
            "PROJECT_LINK_QWEN_MODE": False,
        },
    ),
    "voice_api": (
        CONFIG_DIR / "voice_api.env",
        {
            "PROJECT_LINK_ASR_PROVIDER": False,
            "VOLCANO_ASR_ENDPOINT": False,
            "VOLCANO_ASR_RESOURCE_ID": False,
            "VOLCANO_ASR_API_KEY": True,
            "VOLCANO_ASR_APP_ID": True,
            "VOLCANO_ASR_ACCESS_TOKEN": True,
            "DEEPSEEK_API_KEY": True,
            "VOLCANO_APP_ID": True,
            "VOLCANO_ACCESS_TOKEN": True,
            "VOLCANO_RESOURCE_ID": False,
            "VOLCANO_SPEAKER": False,
            "QWEATHER_API_KEY": True,
            "QWEATHER_API_HOST": False,
        },
    ),
    "qwen": (
        CONFIG_DIR / "qwen_realtime.env",
        {
            "DASHSCOPE_API_KEY": True,
            "QWEN_REALTIME_ENDPOINT": False,
            "QWEN_REALTIME_MODEL": False,
            "QWEN_REALTIME_VOICE": False,
            "PROJECT_LINK_AUDIO_INPUT_NAME": False,
            "PROJECT_LINK_AUDIO_OUTPUT_DEVICE": False,
            "QWEATHER_API_KEY": True,
            "QWEATHER_API_HOST": False,
        },
    ),
    "uwb": (
        CONFIG_DIR / "uwb.env",
        {
            "PROJECT_LINK_UWB_DEVICE": False,
            "PROJECT_LINK_UWB_TAG_ADDRESS": True,
        },
    ),
}

ENV_DEFAULTS = {
    "console": {
        "PROJECT_LINK_WORKSPACE": "/home/wte/wheeltec_robot",
        "UNILIDAR_WS": "/home/wte/unilidar_sdk/unitree_lidar_ros2",
        "CHASSIS_DEVICE": "/dev/project_link_chassis",
        "UNILIDAR_PORT": "/dev/project_link_lidar",
        "FRONT_CAMERA_DEVICE": "/dev/project_link_front_camera",
        "FRONT_CAMERA_ROTATION_DEGREES": "0",
        "FRONT_CAMERA_PREFER_NATIVE_MJPEG": "true",
        "FRONT_CAMERA_MANUAL_EXPOSURE": "true",
        "FRONT_CAMERA_EXPOSURE_ABSOLUTE": "300",
        "FRONT_CAMERA_GAIN": "32",
        "ROS_DOMAIN_ID": "42",
        "ROS_LOCALHOST_ONLY": "0",
        "PROJECT_LINK_VOICE_ENABLE_MOTION": "false",
        "PROJECT_LINK_VOICE_ENABLE_VISUAL_GRASP": "false",
        "PROJECT_LINK_QWEN_MODE": "nav2-dry",
    },
    "voice_api": {
        "PROJECT_LINK_ASR_PROVIDER": "volcano",
        "VOLCANO_ASR_ENDPOINT": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        "VOLCANO_ASR_RESOURCE_ID": "volc.seedasr.sauc.duration",
        "VOLCANO_RESOURCE_ID": "seed-tts-2.0",
    },
    "qwen": {
        "QWEN_REALTIME_MODEL": "qwen3.5-omni-flash-realtime",
        "QWEN_REALTIME_VOICE": "Ethan",
        "PROJECT_LINK_AUDIO_INPUT_NAME": "XFM-DP-V0.0.18",
        "PROJECT_LINK_AUDIO_OUTPUT_DEVICE": "alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo",
    },
    "uwb": {"PROJECT_LINK_UWB_DEVICE": "/dev/uwb-bu04"},
}


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_yaml(runtime: Path, default: Path) -> dict[str, Any]:
    selected = runtime if runtime.is_file() else default
    value = yaml.safe_load(selected.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid_yaml:{selected}")
    return value


def write_yaml(path: Path, value: dict[str, Any]) -> None:
    atomic_write(path, yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


def node_parameters(value: dict[str, Any], node_name: str) -> dict[str, Any]:
    node = value.setdefault(node_name, {})
    parameters = node.setdefault("ros__parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError(f"invalid_ros_parameters:{node_name}")
    return parameters


def validate_parameter(name: str, value: Any, spec) -> Any:
    expected, minimum, maximum = spec
    if expected is bool:
        if not isinstance(value, bool):
            raise ValueError(f"invalid_boolean:{name}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_number:{name}")
    converted = expected(value)
    if minimum is not None and converted < minimum:
        raise ValueError(f"below_minimum:{name}")
    if maximum is not None and converted > maximum:
        raise ValueError(f"above_maximum:{name}")
    return converted


def load_json(runtime: Path, default: Path) -> dict[str, Any]:
    selected = runtime if runtime.is_file() else default
    value = json.loads(selected.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid_json:{selected}")
    return value


def validate_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("profile_must_be_object")
    prompts = value.get("prompts", {})
    if not isinstance(prompts, dict):
        raise ValueError("prompts_must_be_object")
    normalized_prompts = {}
    for backend in ("classic", "qwen_realtime"):
        prompt = str(prompts.get(backend, "")).strip()
        if not prompt or len(prompt) > 20000:
            raise ValueError(f"invalid_prompt:{backend}")
        normalized_prompts[backend] = prompt
    tools = value.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("tools_must_be_list")
    normalized_tools = []
    seen = set()
    for item in tools:
        if not isinstance(item, dict):
            raise ValueError("tool_must_be_object")
        name = str(item.get("name", "")).strip()
        if name not in REGISTERED_TOOLS or name in seen:
            raise ValueError(f"tool_not_registered_or_duplicate:{name}")
        seen.add(name)
        description = str(item.get("description", "")).strip()
        parameters = item.get("parameters", {})
        if not description or len(description) > 2000:
            raise ValueError(f"invalid_tool_description:{name}")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError(f"invalid_tool_schema:{name}")
        if len(json.dumps(parameters, ensure_ascii=False)) > 20000:
            raise ValueError(f"tool_schema_too_large:{name}")
        normalized_tools.append(
            {
                "enabled": bool(item.get("enabled", True)),
                "name": name,
                "description": description,
                "parameters": parameters,
            }
        )
    return {"prompts": normalized_prompts, "tools": normalized_tools}


def get_voice() -> dict[str, Any]:
    classic_yaml = load_yaml(VOICE_CLASSIC_PATH, DEFAULT_VOICE_CLASSIC)
    qwen_yaml = load_yaml(VOICE_QWEN_PATH, DEFAULT_VOICE_QWEN)
    classic = node_parameters(classic_yaml, "voice_dialog_node")
    qwen = node_parameters(qwen_yaml, "qwen_realtime_voice_node")
    return {
        "classic": {key: classic.get(key) for key in VOICE_PARAMETERS["classic"]},
        "qwen": {key: qwen.get(key) for key in VOICE_PARAMETERS["qwen"]},
        "profile": load_json(VOICE_PROFILE_PATH, DEFAULT_VOICE_PROFILE),
    }


def set_voice(payload: dict[str, Any]) -> dict[str, Any]:
    classic_yaml = load_yaml(VOICE_CLASSIC_PATH, DEFAULT_VOICE_CLASSIC)
    qwen_yaml = load_yaml(VOICE_QWEN_PATH, DEFAULT_VOICE_QWEN)
    classic = node_parameters(classic_yaml, "voice_dialog_node")
    qwen = node_parameters(qwen_yaml, "qwen_realtime_voice_node")
    for backend, parameters in (("classic", classic), ("qwen", qwen)):
        requested = payload.get(backend, {})
        if not isinstance(requested, dict):
            raise ValueError(f"invalid_backend_payload:{backend}")
        unknown = set(requested) - set(VOICE_PARAMETERS[backend])
        if unknown:
            raise ValueError(f"parameter_not_allowed:{sorted(unknown)}")
        for key, value in requested.items():
            parameters[key] = validate_parameter(key, value, VOICE_PARAMETERS[backend][key])
    profile = validate_profile(payload.get("profile", {}))
    write_yaml(VOICE_CLASSIC_PATH, classic_yaml)
    write_yaml(VOICE_QWEN_PATH, qwen_yaml)
    atomic_write(VOICE_PROFILE_PATH, json.dumps(profile, ensure_ascii=False, indent=2) + "\n")
    return {"success": True, "restart_required": True}


ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGNMENT.match(line)
        if not match:
            continue
        key, raw_value = match.groups()
        try:
            parsed = shlex.split(raw_value, posix=True)
        except ValueError:
            continue
        values[key] = parsed[0] if len(parsed) == 1 else raw_value
    return values


def write_env(path: Path, values: dict[str, str], shell_export: bool) -> None:
    lines = ["# Managed by Project LINK control console."]
    for key in sorted(values):
        prefix = "export " if shell_export else ""
        lines.append(f"{prefix}{key}={shlex.quote(str(values[key]))}")
    atomic_write(path, "\n".join(lines) + "\n")


def get_global() -> dict[str, Any]:
    output = {"files": {}}
    for name, (path, schema) in ENV_FILES.items():
        values = parse_env(path)
        output["files"][name] = {
            key: {
                "secret": secret,
                "configured": bool(values.get(key, "")),
                "value": "" if secret else values.get(key, ENV_DEFAULTS.get(name, {}).get(key, "")),
            }
            for key, secret in schema.items()
        }
    return output


def set_global(payload: dict[str, Any]) -> dict[str, Any]:
    files = payload.get("files", {})
    if not isinstance(files, dict):
        raise ValueError("files_must_be_object")
    for name, updates in files.items():
        if name not in ENV_FILES or not isinstance(updates, dict):
            raise ValueError(f"env_file_not_allowed:{name}")
        path, schema = ENV_FILES[name]
        unknown = set(updates) - set(schema)
        if unknown:
            raise ValueError(f"env_key_not_allowed:{sorted(unknown)}")
        values = parse_env(path)
        for key, value in updates.items():
            text = str(value).strip()
            if len(text) > 4096 or "\n" in text or "\r" in text:
                raise ValueError(f"invalid_env_value:{key}")
            values[key] = text
        write_env(path, values, shell_export=name != "console")
    return {"success": True, "restart_required": True}


CALIBRATION_KEYS = {
    "axis_xx",
    "axis_xy",
    "axis_yx",
    "axis_yy",
    "sensor_yaw_rad",
    "sensor_translation_x_m",
    "sensor_translation_y_m",
}


def get_uwb() -> dict[str, Any]:
    value = load_yaml(UWB_CONFIG_PATH, DEFAULT_UWB_CONFIG)
    parameters = node_parameters(value, "uwb_nav2_server")
    serial = node_parameters(value, "uwb_serial_node")
    return {
        "calibration": {key: parameters.get(key) for key in parameters if key.startswith("axis_") or key.startswith("sensor_") or key.startswith("calibration_")},
        "tuning": {
            "max_range_residual_m": serial.get("max_range_residual_m", 0.50),
            "uwb_ttl_sec": parameters.get("uwb_ttl_sec", 0.50),
            "acquisition_count": parameters.get("acquisition_count", 5),
            "goal_displacement_m": parameters.get("goal_displacement_m", 0.20),
        },
    }


def set_uwb(payload: dict[str, Any]) -> dict[str, Any]:
    calibration = payload.get("calibration", {})
    if not isinstance(calibration, dict):
        raise ValueError("calibration_must_be_object")
    unknown = set(calibration) - CALIBRATION_KEYS - {"calibration_status", "calibration_version"}
    if unknown:
        raise ValueError(f"calibration_key_not_allowed:{sorted(unknown)}")
    value = load_yaml(UWB_CONFIG_PATH, DEFAULT_UWB_CONFIG)
    parameters = node_parameters(value, "uwb_nav2_server")
    serial = node_parameters(value, "uwb_serial_node")
    if calibration:
        parameters["calibration_status"] = "proposed"
        version = re.sub(r"[^A-Za-z0-9_.-]", "-", str(calibration.get("calibration_version", "gui-proposed")))[:80]
        parameters["calibration_version"] = version or "gui-proposed"
        for key in CALIBRATION_KEYS:
            if key in calibration:
                number = float(calibration[key])
                if not math_is_finite(number) or abs(number) > 100.0:
                    raise ValueError(f"invalid_calibration_value:{key}")
                parameters[key] = number
    tuning = payload.get("tuning", {})
    if not isinstance(tuning, dict):
        raise ValueError("tuning_must_be_object")
    tuning_specs = {
        "max_range_residual_m": (float, 0.05, 2.0),
        "uwb_ttl_sec": (float, 0.10, 5.0),
        "acquisition_count": (int, 1, 50),
        "goal_displacement_m": (float, 0.05, 2.0),
    }
    unknown_tuning = set(tuning) - set(tuning_specs)
    if unknown_tuning:
        raise ValueError(f"uwb_tuning_not_allowed:{sorted(unknown_tuning)}")
    for key, requested in tuning.items():
        validated = validate_parameter(key, requested, tuning_specs[key])
        if key == "max_range_residual_m":
            serial[key] = validated
        else:
            parameters[key] = validated
    write_yaml(UWB_CONFIG_PATH, value)
    return {"success": True, "calibration_status": parameters.get("calibration_status", "invalid"), "restart_required": True}


def math_is_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("get", "set"))
    parser.add_argument("section", choices=("voice", "global", "uwb"))
    arguments = parser.parse_args()
    try:
        if arguments.operation == "get":
            result = {"voice": get_voice, "global": get_global, "uwb": get_uwb}[arguments.section]()
        else:
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise ValueError("payload_must_be_object")
            result = {"voice": set_voice, "global": set_global, "uwb": set_uwb}[arguments.section](payload)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
