# Robot Description

This folder is for the robot model:

```text
urdf/
meshes/
launch/
```

Expected TF backbone:

```text
map
└── odom
    └── base_footprint
        └── base_link
```

Sensor frames such as `unilidar_lidar` should be connected by explicit static transforms or URDF joints.

