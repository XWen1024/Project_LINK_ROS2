# Experimental DDS Router over SSH transport

This source-locked experiment attempted to keep ROS 2 Topic, Service and Action
interfaces while removing direct cross-machine DDS discovery from field Wi-Fi:

```text
Ubuntu GUI domain 142 -> Ubuntu DDS Router -> TCP 127.0.0.1:11666
                     -> SSH LocalForward -> Orin DDS Router -> Orin domain 42
```

DDS Router is built from locked source commits into an isolated user prefix. It
must not overwrite ROS Humble's Fast DDS installation. Orin listens only on
loopback; SSH owns authentication, encryption and reconnects.

This route is not the current production default. On 2026-08-19 both host builds,
loopback listeners, single and reverse SSH forwarding, explicit DDS types and a
forced reader/writer pair were tested, but no ROS endpoint crossed the WAN
participants. Keep the units disabled/inactive unless explicitly diagnosing the
experiment. The MVP console uses native DDS Peer on domain 42 and SSH for
lifecycle/configuration.

Build on each Linux host:

```bash
./deploy/dds-router/build-user-prefix.sh
```

Install units without starting them:

```bash
./deploy/dds-router/install-user-services.sh orin
./deploy/dds-router/install-user-services.sh ubuntu
```

On Ubuntu, copy `dds-transport.env.example` to
`~/.config/project_link/dds-transport.env`, set the verified current Orin SSH
target, and keep it mode `0600`. The SSH key must be the Ubuntu user's own key.

Experimental Router mode runs the GUI with `ROS_DOMAIN_ID=142`; all Orin robot
services remain on 42. The allowlist excludes UWB and the high-bandwidth raw
lidar/Point-LIO streams.

The normal MVP launcher starts no Router service and uses domain 42:

```bash
./deploy/dds-router/bin/project-link-console
```

For diagnostics only, opt into the unaccepted Router path:

```bash
PROJECT_LINK_ENABLE_EXPERIMENTAL_DDS_ROUTER=1 \
  ./deploy/dds-router/bin/project-link-console
```

The experimental wrapper starts only the Ubuntu tunnel/router services and sets
domain 142; it starts no robot hardware.
