# Project LINK systemd user units

These units replace production tmux lifecycle management on the Orin while the
existing scripts remain available as a field fallback.

Install without starting hardware:

```bash
cd /home/wte/wheeltec_robot
./deploy/systemd/install-user-units.sh
```

The installer copies and verifies the units, reloads the user manager and enables
only `project-link-console-agent.service` for future logins. Add `--start-agent`
to start that headless, no-motion agent immediately. It never starts a robot stack.

Operator lifecycle examples:

```bash
systemctl --user start project-link-mapping.target
systemctl --user start project-link-navigation.target
systemctl --user stop project-link-navigation.target
systemctl --user stop project-link-mapping.target
systemctl --user stop project-link-platform.target
systemctl --user start project-link-rf2o-fallback.target
```

Copy `console.env.example` to `~/.config/project_link/console.env` for non-secret
path and tuning overrides. Classic voice secrets remain in `voice_api.env`, Qwen
secrets in `qwen_realtime.env`, and the private UWB tag address in `uwb.env`.
Keep all three module files mode `0600`; never commit them.

The Android fall backend uses a fourth private file, copied from
`fall_response.env.example`, and is deliberately not enabled during installation:

```bash
install -m 0600 deploy/systemd/fall_response.env.example \
  ~/.config/project_link/fall_response.env
systemctl --user start project-link-emergency.target
```

Generate the Android shared token with
`python3 scripts/generate_fall_guard_token.py`. The VLM uses the provider-neutral
`OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL` settings. On JetPack 6,
`deploy/systemd/bin/project-link-setup-fall-cuda` creates an isolated CUDA Torch
environment for fall inference; it does not replace LeRobot's user-level Torch.

Starting the emergency target starts only the front camera, HTTP/visual
coordinator and WeChat notifier. It does not start the base, lidar or Nav2.

`project-link-platform.target` requires the shared base, lidar, robot-description
and scan services, and optionally starts the front-camera preview. Camera failure
therefore remains visible but cannot block mapping or Nav2. Mapping and rf2o targets reuse that platform without sharing
`PartOf` relationships across mutually exclusive modes. The console agent stops
the platform last during an explicit stop-all operation.

The first deployment is not production-accepted until mapping and navigation each
pass two complete supervised field cycles. Until then use the repository scripts
as the known-good fallback.
