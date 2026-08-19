# DDS Router over SSH transport

The production transport keeps ROS 2 Topic, Service and Action interfaces while
removing direct cross-machine DDS discovery from field Wi-Fi:

```text
Ubuntu GUI domain 142 -> Ubuntu DDS Router -> TCP 127.0.0.1:11666
                     -> SSH LocalForward -> Orin DDS Router -> Orin domain 42
```

DDS Router is built from locked source commits into an isolated user prefix. It
must not overwrite ROS Humble's Fast DDS installation. Orin listens only on
loopback; SSH owns authentication, encryption and reconnects.

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

The GUI must run with `ROS_DOMAIN_ID=142`; all Orin robot services remain on 42.
The allowlist excludes UWB and the high-bandwidth raw lidar/Point-LIO streams.

After both transport units are installed and the Ubuntu environment file is
configured, launch the production console with:

```bash
./deploy/dds-router/bin/project-link-console
```

The wrapper starts only the Ubuntu tunnel/router services, sets domain 142 and
then opens the GUI. The sidebar reports the local tunnel/router state separately
from the Orin ROS heartbeat.
