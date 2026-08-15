"""Load operator-editable prompts and safe overrides for registered tool schemas."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_PROFILE_PATH = "~/.config/project_link/voice_profile.json"


def load_profile(path: str | None = None) -> dict[str, Any]:
    selected = path or os.environ.get("PROJECT_LINK_VOICE_PROFILE", DEFAULT_PROFILE_PATH)
    profile_path = Path(selected).expanduser()
    try:
        value = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def prompt_for(backend: str, fallback: str, path: str | None = None) -> str:
    prompt = load_profile(path).get("prompts", {}).get(backend, "")
    return str(prompt).strip() or fallback


def configured_tool_schemas(
    schemas: list[dict[str, Any]],
    path: str | None = None,
) -> list[dict[str, Any]]:
    """Apply edits only to known executors; unknown tool names are ignored."""
    profile = load_profile(path)
    if "tools" not in profile:
        return copy.deepcopy(schemas)
    profile_tools = profile.get("tools", [])
    if not isinstance(profile_tools, list):
        return copy.deepcopy(schemas)
    configured = {
        str(item.get("name", "")): item
        for item in profile_tools
        if isinstance(item, dict) and item.get("name")
    }
    output = []
    for schema in schemas:
        value = copy.deepcopy(schema)
        function = value.get("function", {})
        name = str(function.get("name", ""))
        override = configured.get(name)
        if override is None:
            if name == "demo_motion":
                output.append(value)
            continue
        if not bool(override.get("enabled", True)):
            continue
        description = str(override.get("description", "")).strip()
        parameters = override.get("parameters")
        if description:
            function["description"] = description
        if isinstance(parameters, dict) and parameters.get("type") == "object":
            function["parameters"] = parameters
        output.append(value)
    return output
