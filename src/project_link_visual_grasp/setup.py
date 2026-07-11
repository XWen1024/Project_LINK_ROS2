from glob import glob
from setuptools import find_packages, setup

package_name = "project_link_visual_grasp"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
    ],
    install_requires=["setuptools", "PyYAML", "numpy"],
    zip_safe=True,
    maintainer="Project LINK",
    maintainer_email="wheeltec@todo.todo",
    description="Headless YOLO-World visual-servo grasping node for Project LINK.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "visual_grasp_node = project_link_visual_grasp.node:main",
        ],
    },
)