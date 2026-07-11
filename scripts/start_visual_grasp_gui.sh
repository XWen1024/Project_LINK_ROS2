#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/project_link_env.sh"
ros2 run project_link_visual_grasp_gui visual_grasp_gui "$@"