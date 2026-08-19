from setuptools import find_packages, setup

package_name = "project_link_console_agent"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    tests_require=["pytest"],
    zip_safe=True,
    maintainer="Project LINK",
    maintainer_email="wte@example.com",
    description="Headless lifecycle, health and teleoperation agent for Project LINK.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "console_agent = project_link_console_agent.node:main",
            "front_camera = project_link_console_agent.front_camera:main",
        ],
    },
)
