cmake_minimum_required(VERSION 3.8)
project(project_link_console_interfaces)

find_package(ament_cmake REQUIRED)
find_package(action_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/ConsoleEvent.msg"
  "msg/SubsystemState.msg"
  "msg/SystemState.msg"
  "msg/TeleopCommand.msg"
  "action/ManageStack.action"
  "action/SwitchVoice.action"
  DEPENDENCIES action_msgs builtin_interfaces diagnostic_msgs std_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
