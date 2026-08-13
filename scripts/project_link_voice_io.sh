#!/usr/bin/env bash

# Source before launching voice nodes. Stable USB identities replace tty/card indexes.

PROJECT_LINK_WAKEUP_BY_ID="/dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0004-if00"
PROJECT_LINK_WAKEUP_ALIAS="/dev/project_link_wakeup"
PROJECT_LINK_CMEDIA_SINK_DEFAULT="alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo"
PROJECT_LINK_IFLYTEK_SOURCE_PATTERN="alsa_input.usb-iflytek_XFM-DP-V0.0.18_"

if [[ -e "$PROJECT_LINK_WAKEUP_ALIAS" ]]; then
  export PROJECT_LINK_WAKEUP_SERIAL="$PROJECT_LINK_WAKEUP_ALIAS"
elif [[ -e "$PROJECT_LINK_WAKEUP_BY_ID" ]]; then
  export PROJECT_LINK_WAKEUP_SERIAL="$PROJECT_LINK_WAKEUP_BY_ID"
else
  export PROJECT_LINK_WAKEUP_SERIAL="${PROJECT_LINK_WAKEUP_SERIAL:-$PROJECT_LINK_WAKEUP_ALIAS}"
  echo "[voice-io] Warning: iFlytek wake serial is missing: $PROJECT_LINK_WAKEUP_BY_ID" >&2
fi

export PROJECT_LINK_AUDIO_INPUT_NAME="${PROJECT_LINK_AUDIO_INPUT_NAME:-XFM-DP-V0.0.18}"
export PROJECT_LINK_AUDIO_OUTPUT_DEVICE="${PROJECT_LINK_AUDIO_OUTPUT_DEVICE:-$PROJECT_LINK_CMEDIA_SINK_DEFAULT}"

if command -v pactl >/dev/null 2>&1; then
  PROJECT_LINK_AUDIO_INPUT_SOURCE="$(
    pactl list short sources 2>/dev/null \
      | awk -v prefix="$PROJECT_LINK_IFLYTEK_SOURCE_PATTERN" 'index($2, prefix) == 1 {print $2; exit}'
  )"
  if [[ -n "$PROJECT_LINK_AUDIO_INPUT_SOURCE" ]]; then
    export PROJECT_LINK_AUDIO_INPUT_SOURCE
    pactl set-default-source "$PROJECT_LINK_AUDIO_INPUT_SOURCE" >/dev/null 2>&1 || true
    pactl set-source-mute "$PROJECT_LINK_AUDIO_INPUT_SOURCE" 0 >/dev/null 2>&1 || true
    echo "[voice-io] iFlytek microphone: $PROJECT_LINK_AUDIO_INPUT_SOURCE"
  else
    echo "[voice-io] Warning: iFlytek Pulse source is missing." >&2
  fi
  if pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fxq "$PROJECT_LINK_AUDIO_OUTPUT_DEVICE"; then
    export PULSE_SINK="$PROJECT_LINK_AUDIO_OUTPUT_DEVICE"
    export SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-pulseaudio}"
    pactl set-default-sink "$PROJECT_LINK_AUDIO_OUTPUT_DEVICE" >/dev/null 2>&1 || true
    echo "[voice-io] USB speaker: $PROJECT_LINK_AUDIO_OUTPUT_DEVICE"
  else
    echo "[voice-io] Warning: C-Media USB speaker sink is missing: $PROJECT_LINK_AUDIO_OUTPUT_DEVICE" >&2
  fi
fi

echo "[voice-io] Wake serial: $PROJECT_LINK_WAKEUP_SERIAL"
echo "[voice-io] Microphone match: $PROJECT_LINK_AUDIO_INPUT_NAME"
