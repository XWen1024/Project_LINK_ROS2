# ADR 0001: Control Console Foundation

Date: 2026-08-15
Status: accepted

## Decision

- Keep `main` as the only active development branch.
- Use Ubuntu 22.04, ROS 2 Humble and PySide6 for the operator console.
- Use an integrated 2D renderer plus a separately launched RViz2 diagnostic view.
- Replace production tmux lifecycle management with `systemd --user` services.
- Keep Orin headless; do not move hardware ownership or heavy rendering onto it.
- Preserve classic and Qwen voice as mutually exclusive production backends.
- Archive Volc S2S as an experiment and do not expose it in the normal console.

## Consequences

Existing scripts remain temporary fallback entrypoints until systemd passes two
complete field-validation cycles. The console requires typed status and event
interfaces instead of parsing terminal output. Runtime secrets remain outside
Git and outside ordinary ROS messages.
