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
systemctl --user start project-link-rf2o-fallback.target
```

Copy `console.env.example` to `~/.config/project_link/console.env` for non-secret
path and tuning overrides. Classic voice secrets remain in `voice_api.env`, Qwen
secrets in `qwen_realtime.env`, and the private UWB tag address in `uwb.env`.
Keep all three module files mode `0600`; never commit them.

The first deployment is not production-accepted until mapping and navigation each
pass two complete supervised field cycles. Until then use the repository scripts
as the known-good fallback.
