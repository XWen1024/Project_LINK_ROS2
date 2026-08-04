from setuptools import find_packages, setup


package_name = "project_link_uwb_navigation"


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml", "requirements-orin.txt", "requirements-mcp.txt"]),
        ("share/" + package_name + "/launch", ["launch/uwb_navigation.launch.py"]),
        ("share/" + package_name + "/config", ["config/uwb_navigation.yaml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Project LINK",
    maintainer_email="wte@example.com",
    description="Fail-closed UWB person summon and following through Nav2.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "uwb_serial_node = project_link_uwb_navigation.serial_node:main",
            "uwb_nav2_server = project_link_uwb_navigation.nav2_server:main",
            "uwb_mcp_server = project_link_uwb_navigation.mcp_server:main",
        ],
    },
)
