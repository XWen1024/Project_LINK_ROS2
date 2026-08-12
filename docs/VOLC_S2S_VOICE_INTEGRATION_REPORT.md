# Volcengine S2S local voice integration report

Date: 2026-08-12

## Isolation

- Branch: `codex/volc-s2s-voice-integration`
- Windows worktree: `C:\Users\XWen1024\Documents\ROS2小车-volc-voice`
- Orin worktree: `/home/wte/wheeltec_robot-volc-voice`
- Validated integration commit: `6c776f465395f47b16a38473d8163fe36dc5091b`
- Official Embedded Kit commit:
  `2c94f96f3aad4094e0e818cbb031149fd4384ead`

The original voice chain was not edited, stopped, migrated, or used as a hidden
fallback. The new launch starts no chassis process and does not publish
`/cmd_vel`.

## Result summary

| Gate | Result | Evidence |
| --- | --- | --- |
| Native ARM64 bridge build | PASS | `volc_ws_bridge` is ELF64 AArch64; RTC disabled/not linked |
| ROS package build | PASS | `project_link_voice_interfaces`, `wheeltec_robot_msg`, and `project_link_voice` built |
| Focused tests | PASS | 15 bridge/timing/FunVAD/wakeup tests |
| Embedded Kit registration | PASS | Three observed runs: 857, 918, and 1203 ms |
| Persistent low-load WSS | PASS | Three observed runs: 465, 421, and 867 ms |
| Session configuration | PASS | `doubao-seed-2-1-turbo-260628`, PCM16 input/output, `server_vad` |
| Local FunVAD dependency | REMOVED | Current S2S node sends raw PCM and uses cloud `server_vad`; Legacy path unchanged |
| Wake acknowledgement | PASS (board-audio test) | Cached file played successfully in 1909.611 and 1898.789 ms |
| Audio streaming entry | PASS (board-input test) | First 16 kHz frame sent; ack end to first input was 315.021 ms |
| Empty-turn safety | PASS | 8-second local no-speech timeout cleared input without commit |
| iFlytek hardware wake | PASS | Stable serial `0004` produced a real wake trace |
| XFM microphone capture | PASS | PyAudio index 25 streamed 16 kHz mono PCM through FunVAD/WSS |
| C-Media speaker playback | PASS | Stable Pulse sink received the AI PCM playback stream |
| Real speech to AI speaker response | PASS | Trace `7eacadf4833d`; last input to speaker write 3199.287 ms |

## Observed startup latency

The three persistent-session starts produced:

| Run | Registration ms | WSS connect ms | Startup trace total ms |
| --- | ---: | ---: | ---: |
| 1 | 857 | 465 | 1329.106 |
| 2 | 918 | 421 | 1376.518 |
| 3 | 1203 | 867 | 2099.580 |

These are startup-only measurements. They are not paid once per spoken turn
because the native bridge keeps the engine and WSS session alive.

## Safe state-machine probe

The keyboard test reads the tmux controlling terminal rather than the ROS launch
stdin pipe. A no-speech probe exercised the same Python/native path without
sending robot commands:

```text
keyboard wake
-> cached acknowledgement
-> open 16 kHz Pulse input
-> stream realtime audio over the existing WSS
-> local FunVAD no-speech timeout
-> clear input and return to ready
```

Latest trace:

```text
wakeup_ack_playback             1898.789 ms
volc_wakeup_to_first_input_audio 2215.047 ms
wakeup_ack_to_first_input_audio   315.021 ms
local_vad_record                8140.431 ms
outcome                         no_speech_timeout
```

No user speech was present, so no commit, cloud response, AI PCM, or speaker
first-write measurement is claimed for this run.

## Deployment fixes found during bring-up

1. Humble/colcon setup scripts may read unset variables. The launcher now
   temporarily disables `nounset` while sourcing them, then restores it.
2. Values sourced from ignored `.env.local` were shell variables only. The
   launcher now auto-exports the file while sourcing it so Python and the native
   bridge receive the five `VOLC_*` variables. Values remain unprinted.
3. ROS launch gives the node a pipe for stdin. Keyboard test wake now reads
   `/dev/tty`; the real serial wake path is unchanged.
4. Timing now includes the direct `wakeup_ack_to_first_input_audio` edge in
   addition to wake-relative measurements.
5. The long-lived tmux server retained the main workspace ROS overlay and could
   resolve `project_link_voice` to the legacy install after a restart. The pure
   S2S tmux command now clears inherited ROS overlay variables, sources Humble,
   then sources only the dedicated worktree `local_setup.bash`.

## Hardware blocker and next command

At the last scan the Orin USB bus contained only the onboard hubs and Bluetooth.
There was no stable wake serial path and no external USB audio card. Connect and
power the iFlytek wake board, XFM microphone, and C-Media speaker, then run:

```bash
cd /home/wte/wheeltec_robot-volc-voice
PROJECT_LINK_WORKSPACE=/home/wte/wheeltec_robot-volc-voice \
  ./scripts/start_volc_s2s_voice.sh --scan-only
```

Only after the scan shows the stable iFlytek serial path, XFM capture device,
and C-Media Pulse sink should the real session be started. Use the PyAudio input
index printed by that scan; USB indices may change after reconnection.

```bash
PROJECT_LINK_WORKSPACE=/home/wte/wheeltec_robot-volc-voice \
  ./scripts/start_volc_s2s_voice.sh \
    --restart \
    --wakeup-port auto \
    --audio-input-index <XFM_INDEX> \
    --audio-output-index -1 \
    --pulse-sink alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo \
    --no-attach

tmux attach -t project_link_volc_s2s_voice
```

Expected real-trace edges include raw PCM capture, last input to cloud speech stop,
last input to first AI audio, callback to speaker write, last input to speaker
write, response completion, and playback drain. The timing file remains:

```text
~/.ros/project_link_voice/voice_timing.jsonl
```

## First complete acoustic turn

After the three USB devices were connected, Orin enumerated the stable iFlytek
serial `0004`, XFM PyAudio input index `25`, and C-Media output index `24`. The
C-Media Pulse profile required one off/on refresh before its stable analog sink
was created.

The first real turn passed wake, acknowledgement, XFM capture, the initial local FunVAD revision,
realtime WSS upload, Turbo S2S response, native PCM callback, and C-Media speaker
write. Observed trace `7eacadf4833d`:

```text
wakeup_ack_playback                 1970.765 ms
wakeup_ack_to_first_input_audio      208.928 ms
local_vad_record                    4644.046 ms
volc_last_input_to_commit             41.404 ms
volc_commit_to_server_ack            119.750 ms
volc_last_input_to_response_created 3190.857 ms
volc_last_input_to_first_ai_audio   3191.512 ms
volc_audio_callback_to_speaker_write   7.775 ms
volc_last_input_to_speaker_write    3199.287 ms
volc_first_audio_to_audio_done      1690.689 ms
```

The official low-load SDK printed raw `response.done` internally but did not
forward that event through `on_volc_message_data` in this run. It did forward
`VOLC_CONV_STATUS_ANSWER_FINISH` through the documented conversation-status
callback. The integration now treats `ANSWER_FINISH` as the primary completion
signal while retaining `response.done` as a compatible alternate, preventing a
false 45-second response timeout after audio has already completed.

## Cloud server-VAD revision

The first hardware revision reused the Legacy FunVAD recorder as a local turn
endpoint. Live logs then proved that this was redundant for S2S: the server
advertised `turn_detection.type=server_vad`, entered `THINKING`, emitted
`input_audio_buffer.committed`, and could begin returning AI audio while the
local FunVAD loop was still recording. A later manual commit could duplicate the
server transition and risk feeding speaker audio back into the microphone.

The current isolated S2S path now uses:

```text
iFlytek wake -> cached acknowledgement -> raw 16 kHz mono PCM (100 ms cadence)
-> cloud server_vad endpoint / automatic commit -> S2S or Function Calling
-> AI PCM -> C-Media speaker
```

Normal turns do not run local VAD, local ASR, or `input_audio_buffer.commit`.
Local code retains only an 8-second no-speech guard and a 30-second maximum
utterance guard so a hardware/cloud failure cannot leave the microphone open
forever. The maximum-duration guard may issue a manual commit only when that
safety limit is actually reached. Legacy voice nodes still use `funvad.py` and
were not changed.

New timing uses `raw_pcm_capture` and treats cloud speech-stop/automatic
committed events as authoritative. `volc_last_input_to_server_commit` measures
the final locally sent PCM to the server's committed event. The older
`local_vad_record` and manual
commit measurements above remain historical baseline data and must not be
reported as measurements of the current server-VAD-only revision.

## Continuous conversation and reconnect race fix

Two real turns on the same cloud session produced `resp_round_1` and
`resp_round_2`, proving the service retains multi-turn context. The previous
Python wrapper nevertheless returned to iFlytek wake after every reply. It also
allowed microphone upload before a reconnect's `session.created` and treated any
AI audio as an input endpoint; a short session greeting could therefore stop
capture and corrupt latency.

The current wrapper uses a bounded half-duplex session:

```text
one wake + one acknowledgement
-> user turn -> cloud reply/tool reply -> speaker drain
-> automatically listen for the next user turn
-> silence/connection loss/timeout/max-turns -> wake-word mode
```

It waits for `session.created` before PCM, accepts endpoint events only after
the first user PCM frame, requires the cloud endpoint before `response.created`,
and drops audio that arrives before the current turn owns a response. Default
limits are 8 seconds of continuation silence and 8 turns. Full duplex is not
enabled because the current microphone/speaker path has no acoustic echo
cancellation.
