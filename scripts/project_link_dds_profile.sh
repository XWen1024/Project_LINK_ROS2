#!/usr/bin/env bash

# Bind Fast DDS to exactly one IPv4 address. The kernel route decides which
# interface is production-active, so DHCP changes do not require hard-coded IPs.
project_link_configure_fastdds() {
  local runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/project-link"
  local profile="${PROJECT_LINK_FASTDDS_PROFILE:-$runtime_dir/fastdds.xml}"
  local peer_ip="${PROJECT_LINK_DDS_PEER_IP:-}"
  local route_target route interface address temporary
  route_target="${peer_ip:-1.1.1.1}"

  if [[ -n "${PROJECT_LINK_DDS_INTERFACE:-}" ]]; then
    interface="$PROJECT_LINK_DDS_INTERFACE"
    address="${PROJECT_LINK_DDS_ADDRESS:-$(ip -o -4 addr show dev "$interface" scope global | awk 'NR == 1 {split($4, a, "/"); print a[1]}')}"
  else
    route="$(ip -4 route get "$route_target" 2>/dev/null | head -n 1)"
    interface="$(awk '{for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}' <<<"$route")"
    address="$(awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}' <<<"$route")"
  fi

  if [[ -z "$interface" || -z "$address" ]]; then
    echo "Project LINK: unable to select a DDS IPv4 interface for $route_target" >&2
    return 1
  fi

  mkdir -p "$runtime_dir"
  temporary="$profile.tmp.$$"
  cat >"$temporary" <<EOF
<?xml version="1.0" encoding="UTF-8" ?>
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <transport_descriptors>
    <transport_descriptor>
      <transport_id>project_link_udp</transport_id>
      <type>UDPv4</type>
      <interfaceWhiteList><address>$address</address></interfaceWhiteList>
    </transport_descriptor>
    <transport_descriptor>
      <transport_id>project_link_shm</transport_id>
      <type>SHM</type>
    </transport_descriptor>
  </transport_descriptors>
  <participant profile_name="project_link_single_interface" is_default_profile="true">
    <rtps>
      <userTransports>
        <transport_id>project_link_udp</transport_id>
        <transport_id>project_link_shm</transport_id>
      </userTransports>
      <useBuiltinTransports>false</useBuiltinTransports>
    </rtps>
  </participant>
</profiles>
EOF
  mv "$temporary" "$profile"
  export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"
  export FASTRTPS_DEFAULT_PROFILES_FILE="$profile"
  export PROJECT_LINK_DDS_SELECTED_INTERFACE="$interface"
  export PROJECT_LINK_DDS_SELECTED_ADDRESS="$address"
}

project_link_configure_fastdds
