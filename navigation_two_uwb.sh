#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${PROJECT_LINK_WORKSPACE:-/home/wte/wheeltec_robot}"
SOURCE_ID="${PROJECT_LINK_UWB_SOURCE_ID:-tag-1}"
COMMAND="${1:-status}"

cd "$WORKSPACE"
set +u
source scripts/project_link_env.sh
source install/setup.bash
set -u

case "$COMMAND" in
  status)
    ros2 topic echo --once /uwb/status
    ros2 topic echo --once /uwb_navigation/status
    ;;
  summon)
    ros2 action send_goal --feedback \
      /uwb_navigation/person_navigation \
      project_link_uwb_interfaces/action/PersonNavigation \
      "{mode: 1, source_id: '$SOURCE_ID'}"
    ;;
  follow)
    ros2 action send_goal --feedback \
      /uwb_navigation/person_navigation \
      project_link_uwb_interfaces/action/PersonNavigation \
      "{mode: 2, source_id: '$SOURCE_ID'}"
    ;;
  stop)
    ros2 service call /uwb_navigation/stop std_srvs/srv/Trigger '{}'
    ;;
  *)
    echo "Usage: ./navigation_two_uwb.sh status|summon|follow|stop" >&2
    exit 2
    ;;
esac
