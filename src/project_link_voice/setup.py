from setuptools import find_packages, setup

package_name = "project_link_voice"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml", "requirements-orin.txt"]),
        (
            "share/" + package_name + "/launch",
            [
                "launch/voice_direct_drive.launch.py",
                "launch/voice_pure_test.launch.py",
                "launch/voice_demo_motion.launch.py",
                "launch/llm_motion_demo.launch.py",
            ],
        ),
        ("share/" + package_name + "/config", ["config/voice_direct_drive.yaml"]),
        ("share/" + package_name + "/data", ["data/default_waypoints.json"]),
    ],
    install_requires=["setuptools", "numpy"],
    zip_safe=True,
    maintainer="Project LINK",
    maintainer_email="wte@example.com",
    description="Production ROS 2 voice dialog and guarded direct-drive nodes.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "voice_dialog_node = project_link_voice.voice_dialog_node:main",
            "llm_motion_demo_node = project_link_voice.llm_motion_demo_node:main",
            "ab_drive_server = project_link_voice.ab_drive_server:main",
            "voice_tts_node = project_link_voice.tts_node:main",
        ],
    },
)
