from setuptools import find_packages, setup


package_name = "project_link_qwen_realtime_voice"


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml", "README.md", "requirements-orin.txt"]),
        (
            "share/" + package_name + "/launch",
            [
                "launch/qwen_realtime_voice.launch.py",
                "launch/qwen_realtime_nav2.launch.py",
                "launch/qwen_realtime_demo.launch.py",
            ],
        ),
        (
            "share/" + package_name + "/config",
            ["config/qwen_realtime_voice.yaml", "config/qwen_realtime.env.example"],
        ),
        ("share/" + package_name + "/data", ["data/default_waypoints.json"]),
    ],
    install_requires=["setuptools", "numpy"],
    zip_safe=True,
    maintainer="Project LINK",
    maintainer_email="wte@example.com",
    description="Independent Qwen3.5 Omni realtime ROS 2 voice service.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "qwen_realtime_voice_node = project_link_qwen_realtime_voice.node:main",
        ],
    },
)
