#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
VOICE_ENV="${PROJECT_LINK_VOICE_ENV:-/home/wte/.config/project_link/voice_api.env}"
S2S_ENV="${PROJECT_LINK_VOLC_S2S_ENV:-$WORKSPACE/experiments/volc_s2s_smoke/.env.local}"
SESSION="${PROJECT_LINK_VOLC_S2S_SESSION:-project_link_volc_s2s_voice}"
BRIDGE_BIN="${PROJECT_LINK_VOLC_BRIDGE_BIN:-$WORKSPACE/experiments/volc_s2s_smoke/build/volc_ws_bridge}"
WAKEUP_PORT="${WAKEUP_PORT:-auto}"
AUDIO_INPUT_INDEX="${AUDIO_INPUT_INDEX:-0}"
AUDIO_OUTPUT_INDEX="${AUDIO_OUTPUT_INDEX:--1}"
PULSE_SINK="${PULSE_SINK:-alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo}"
KEYBOARD_WAKEUP=false
RESTART=0
ATTACH=1
SCAN_ONLY=0

usage() {
  cat <<EOF
Usage: ./scripts/start_volc_s2s_voice.sh [options]

Safe pure-voice chain:
  iFlytek wake -> cached ack -> USB mic -> FunVAD hard endpoint
  -> persistent native Volcengine low-load WebSocket S2S -> USB speaker

This launch does not start the base and does not publish /cmd_vel.

Options:
  --restart                  Replace the existing tmux session.
  --scan-only                Only scan IO devices and credential variable presence.
  --keyboard-wakeup          Press Enter instead of using the iFlytek serial wake event.
  --wakeup-port PATH|auto    Default: $WAKEUP_PORT
  --audio-input-index N      Default: $AUDIO_INPUT_INDEX
  --audio-output-index N     PyAudio output index, -1 uses the default/Pulse sink.
  --pulse-sink NAME          Stable PulseAudio sink name.
  --voice-env PATH           Legacy TTS/model env file. Default: $VOICE_ENV
  --s2s-env PATH             Embedded Kit credentials. Default: $S2S_ENV
  --bridge-bin PATH          Native bridge executable. Default: $BRIDGE_BIN
  --no-attach                Start tmux in background.
  -h, --help                 Show this help.

Status topic:
  /voice_s2s/status

Timing log:
  ~/.ros/project_link_voice/voice_timing.jsonl
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) RESTART=1 ;;
    --scan-only) SCAN_ONLY=1 ;;
    --keyboard-wakeup) KEYBOARD_WAKEUP=true ;;
    --wakeup-port) WAKEUP_PORT="$2"; shift ;;
    --audio-input-index) AUDIO_INPUT_INDEX="$2"; shift ;;
    --audio-output-index) AUDIO_OUTPUT_INDEX="$2"; shift ;;
    --pulse-sink) PULSE_SINK="$2"; shift ;;
    --voice-env) VOICE_ENV="$2"; shift ;;
    --s2s-env) S2S_ENV="$2"; shift ;;
    --bridge-bin) BRIDGE_BIN="$2"; shift ;;
    --no-attach) ATTACH=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$WORKSPACE"
set +u
source scripts/project_link_env.sh
set -u
if [[ -f "$WORKSPACE/install/setup.bash" ]]; then
  # shellcheck disable=SC1090
  source "$WORKSPACE/install/setup.bash"
fi

if [[ -f "$VOICE_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$VOICE_ENV"
fi
if [[ -f "$S2S_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$S2S_ENV"
else
  echo "ERROR: Volcengine S2S env file not found: $S2S_ENV" >&2
  exit 2
fi

required_vars=(VOLC_BOT_ID VOLC_INSTANCE_ID VOLC_PRODUCT_KEY VOLC_PRODUCT_SECRET VOLC_DEVICE_NAME)
for name in "${required_vars[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: missing required S2S environment variable: $name" >&2
    exit 2
  fi
done

echo "[scan] Voice S2S IO devices and credential variable presence:"
python3 scripts/scan_voice_demo_io.py || true

if [[ "$SCAN_ONLY" -eq 1 ]]; then
  exit 0
fi

if [[ ! -x "$BRIDGE_BIN" ]]; then
  echo "ERROR: native bridge is missing: $BRIDGE_BIN" >&2
  echo "Build it first: cd $WORKSPACE/experiments/volc_s2s_smoke && ./scripts/build.sh" >&2
  exit 2
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  if [[ "$RESTART" -eq 1 ]]; then
    tmux kill-session -t "$SESSION"
  else
    echo "tmux session '$SESSION' already exists. Use --restart to replace it." >&2
    exit 1
  fi
fi

tmux new-session -d -s "$SESSION" -n voice
voice_cmd="cd '$WORKSPACE' && source scripts/project_link_env.sh"
voice_cmd+=" && source '$WORKSPACE/install/setup.bash'"
if [[ -f "$VOICE_ENV" ]]; then
  voice_cmd+=" && source '$VOICE_ENV'"
fi
voice_cmd+=" && source '$S2S_ENV'"
voice_cmd+=" && export PROJECT_LINK_VOLC_BRIDGE_BIN='$BRIDGE_BIN'"
voice_cmd+=" && export PULSE_SINK='$PULSE_SINK'"
voice_cmd+=" && ros2 launch project_link_voice volc_s2s_voice.launch.py"
voice_cmd+=" keyboard_wakeup:=$KEYBOARD_WAKEUP"
voice_cmd+=" wakeup_serial_port:='$WAKEUP_PORT'"
voice_cmd+=" audio_input_device_index:=$AUDIO_INPUT_INDEX"
voice_cmd+=" audio_output_device_index:=$AUDIO_OUTPUT_INDEX"
voice_cmd+=" native_bridge_executable:='$BRIDGE_BIN'"
voice_cmd+=" pulse_sink:='$PULSE_SINK'"
tmux send-keys -t "$SESSION:voice" "$voice_cmd" C-m

tmux new-window -t "$SESSION" -n timing
timing_cmd="mkdir -p ~/.ros/project_link_voice && touch ~/.ros/project_link_voice/voice_timing.jsonl && tail -F ~/.ros/project_link_voice/voice_timing.jsonl"
tmux send-keys -t "$SESSION:timing" "$timing_cmd" C-m

cat <<EOF
Started safe Volcengine S2S pure-voice session: $SESSION

Attach:
  tmux attach -t $SESSION

The node does not publish /cmd_vel and does not start the chassis.

Timing phases include:
  volc_device_registration
  volc_ws_connect
  wakeup_ack_playback
  local_vad_record
  volc_wakeup_to_first_input_audio
  volc_last_input_to_speech_stopped
  volc_commit_to_server_ack
  volc_vad_stop_to_function_call
  volc_last_input_to_function_call
  volc_last_input_to_first_ai_audio
  volc_audio_callback_to_speaker_write
  volc_last_input_to_speaker_write
  volc_last_input_to_response_done
  speaker_playback_drain
EOF

if [[ "$ATTACH" -eq 1 ]]; then
  tmux attach -t "$SESSION"
fi
