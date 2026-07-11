from glob import glob
from setuptools import find_packages, setup

package_name = "project_link_fall_response"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml", "requirements-orin.txt"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Project LINK",
    maintainer_email="wte@example.com",
    description="Second-camera fall assessment and guarded emergency voice-call response nodes.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "fall_camera_node = project_link_fall_response.fall_camera_node:main",
            "fall_response_node = project_link_fall_response.fall_response_node:main",
        ],
    },
)
