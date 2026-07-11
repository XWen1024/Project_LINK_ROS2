from setuptools import find_packages, setup

package_name = "project_link_visual_grasp_gui"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Project LINK",
    maintainer_email="wheeltec@todo.todo",
    description="Ubuntu PySide6 remote client for Project LINK visual grasping.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "visual_grasp_gui = project_link_visual_grasp_gui.app:main",
        ],
    },
)