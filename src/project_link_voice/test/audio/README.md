# FunVAD Hardware Fixture Contract

Capture these files on the actual Orin USB microphone at 16 kHz, mono, signed
16-bit PCM WAV. Do not commit personal conversations or uncurated recordings.
Use short, consented command clips with known start/end timestamps:

- `quiet_command.wav`: quiet room, one named waypoint command.
- `fan_command.wav`: constant fan or HVAC noise, one command then silence.
- `base_noise_command.wav`: stationary chassis/electrical noise, one command.
- `distant_speech.wav`: distant competing speech plus one local command.
- `long_pause_command.wav`: command with a natural mid-sentence pause.

Run each capture through:

```bash
PYTHONPATH=src/project_link_voice python3 src/project_link_voice/tools/evaluate_vad.py test/audio/<file>.wav
```

Acceptance: the command has retained audio, quiet/no-speech clips finish by the
no-speech timeout, and continuous noise never exceeds `audio_max_utterance_sec`.