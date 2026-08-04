#!/usr/bin/env python3
"""Optional stdio MCP facade for the guarded ROS 2 UWB action server."""

from __future__ import annotations

import json
import threading

import rclpy
from mcp.server.fastmcp import FastMCP
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import Trigger

from project_link_uwb_interfaces.action import PersonNavigation


class RosUwbBridge:
    """Keep MCP at the high-level ROS action boundary."""

    def __init__(self) -> None:
        rclpy.init(args=None)
        self.node = Node("uwb_mcp_bridge")
        self.client = ActionClient(self.node, PersonNavigation, "/uwb_navigation/person_navigation")
        self.stop_client = self.node.create_client(Trigger, "/uwb_navigation/stop")
        self.status = {"state": "unknown", "reason": "no_status_received"}
        self.node.create_subscription(String, "/uwb_navigation/status", self._on_status, 10)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self.executor.spin, name="uwb-mcp-ros", daemon=True)
        self.thread.start()

    def _on_status(self, message: String) -> None:
        try:
            value = json.loads(message.data)
            self.status = value if isinstance(value, dict) else {"state": "invalid_status"}
        except json.JSONDecodeError:
            self.status = {"state": "invalid_status"}

    @staticmethod
    def _wait(future, timeout_sec: float):
        event = threading.Event()
        future.add_done_callback(lambda _done: event.set())
        if not event.wait(timeout_sec):
            raise TimeoutError("ROS 2 request timed out")
        return future.result()

    def start(self, mode: int) -> dict:
        if not self.client.wait_for_server(timeout_sec=1.0):
            return {"accepted": False, "message": "UWB person-navigation action is unavailable."}
        goal = PersonNavigation.Goal()
        goal.mode = mode
        handle = self._wait(self.client.send_goal_async(goal), 3.0)
        if handle is None or not handle.accepted:
            return {
                "accepted": False,
                "message": "The local ROS safety layer rejected the request. MCP cannot arm motion.",
            }
        return {"accepted": True, "message": "ROS accepted the high-level task."}

    def stop(self) -> dict:
        if not self.stop_client.wait_for_service(timeout_sec=1.0):
            return {"success": False, "message": "UWB stop service is unavailable."}
        response = self._wait(self.stop_client.call_async(Trigger.Request()), 3.0)
        return {"success": bool(response.success), "message": response.message}


mcp = FastMCP("project_link_uwb_navigation")
_bridge: RosUwbBridge | None = None


def bridge() -> RosUwbBridge:
    global _bridge
    if _bridge is None:
        _bridge = RosUwbBridge()
    return _bridge


@mcp.tool(name="uwb_get_person_navigation_status", structured_output=True)
def get_person_navigation_status() -> dict:
    """Read the current guarded UWB summon/follow state without moving the robot."""
    return dict(bridge().status)


@mcp.tool(name="uwb_summon_robot", structured_output=True)
def summon_robot() -> dict:
    """Request a Nav2 summon task; the local ROS node must already be motion-enabled and calibrated."""
    return bridge().start(1)


@mcp.tool(name="uwb_start_following", structured_output=True)
def start_following() -> dict:
    """Request continuous UWB following; MCP cannot bypass local calibration or motion gates."""
    return bridge().start(2)


@mcp.tool(name="uwb_stop_person_navigation", structured_output=True)
def stop_person_navigation() -> dict:
    """Cancel the active UWB/Nav2 task through the local fail-closed stop service."""
    return bridge().stop()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
