from glob import glob
import os

from setuptools import find_packages, setup


package_name = "project_link_console_gui"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "config"), glob("config/*")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Project LINK",
    maintainer_email="wte@example.com",
    description="Ubuntu PySide6 operator console for Project LINK.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "project_link_console = project_link_console_gui.app:main",
        ],
    },
)
