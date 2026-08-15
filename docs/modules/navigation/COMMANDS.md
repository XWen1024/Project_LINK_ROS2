```bash
cd /home/wte/wheeltec_robot
chmod +x navigation_two_*.sh

./navigation_two_start.sh --restart
./navigation_two_start_navigation.sh --restart
./navigation_two_start_mapping.sh --restart
./navigation_two_save_map.sh
./navigation_two_save_map.sh --name my_map
./navigation_two_status.sh

export PROJECT_LINK_UWB_TAG_ADDRESS='<private-a16>'
./navigation_two_start_uwb.sh --shadow \
  --device /dev/uwb-bu04 \
  --params ~/.config/project_link/uwb_navigation.yaml \
  --restart
./navigation_two_uwb.sh status
./navigation_two_uwb.sh summon
./navigation_two_uwb.sh follow
# Run stop in another terminal while follow is attached.
./navigation_two_uwb.sh stop

./navigation_two_stop.sh
```
