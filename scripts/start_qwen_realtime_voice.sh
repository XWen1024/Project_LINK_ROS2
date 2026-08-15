#!/usr/bin/env bash
set -eo pipefail

MODE="${1:-pure-test}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"
source /opt/ros/humble/setup.bash
if [[ ! -f "$ROOT/.venv-qwen-realtime/bin/activate" ]]; then
  echo "[qwen-realtime] Missing $ROOT/.venv-qwen-realtime; install package requirements first." >&2
  exit 2
fi
source "$ROOT/.venv-qwen-realtime/bin/activate"
VENV_SITE_PACKAGES="$(python3 -c 'import site; print(site.getsitepackages()[0])')"
export PYTHONPATH="${VENV_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
source scripts/project_link_env.sh
if [[ -f /home/wte/.config/project_link/qwen_realtime.env ]]; then
  source /home/wte/.config/project_link/qwen_realtime.env
fi
if [[ -f install/setup.bash ]]; then
  source install/setup.bash
fi
set -u

params_args=()
if [[ -f "${PROJECT_LINK_QWEN_PARAMS:-}" ]]; then
  params_args+=(params_file:="$PROJECT_LINK_QWEN_PARAMS")
fi

if ros2 node list 2>/dev/null | grep -Eq '^/(voice_dialog_node|qwen_realtime_voice_node)$'; then
  echo "[qwen-realtime] Refusing to start: another voice node is already running." >&2
  ros2 node list 2>/dev/null | grep -E 'voice_dialog_node|qwen_realtime_voice_node' >&2 || true
  exit 2
fi

case "$MODE" in
  pure-test)
    exec ros2 launch project_link_qwen_realtime_voice qwen_realtime_voice.launch.py \
      "${params_args[@]}" \
      enable_motion:=false enable_visual_grasp:=false enable_demo_motion:=false pure_test_mode:=on
    ;;
  demo)
    exec ros2 launch project_link_qwen_realtime_voice qwen_realtime_demo.launch.py \
      "${params_args[@]}"
    ;;
  nav2-dry)
    exec ros2 launch project_link_qwen_realtime_voice qwen_realtime_nav2.launch.py \
      "${params_args[@]}" \
      enable_motion:=false enable_visual_grasp:=false
    ;;
  nav2)
    exec ros2 launch project_link_qwen_realtime_voice qwen_realtime_nav2.launch.py \
      "${params_args[@]}" \
      enable_motion:=true enable_visual_grasp:=false
    ;;
  fetch)
    exec ros2 launch project_link_qwen_realtime_voice qwen_realtime_nav2.launch.py \
      "${params_args[@]}" \
      enable_motion:=true enable_visual_grasp:=true
    ;;
  *)
    echo "Usage: $0 {pure-test|demo|nav2-dry|nav2|fetch}" >&2
    exit 2
    ;;
esac
