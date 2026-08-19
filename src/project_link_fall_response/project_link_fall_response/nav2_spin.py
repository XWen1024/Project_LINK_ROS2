"""Fail-closed Nav2 Spin adapter for the fall-response scan."""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Callable

from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from lifecycle_msgs.msg import State
from lifecycle_msgs.srv import GetState
from nav2_msgs.action import Spin
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.duration import Duration
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener
from wheeltec_robot_msg.msg import VisualGraspStatus


@dataclass(frozen=True)
class Nav2Preflight:
    ready: bool
    action_ready: bool
    lifecycle_ready: bool
    tf_ready: bool
    odom_ready: bool
    costmap_ready: bool
    rotation_clear: bool
    cmd_vel_clear: bool
    arm_safe: bool
    message: str


def _normalize_degrees(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


class Nav2SpinAdapter:
    """Own relative heading changes without ever publishing velocity commands."""

    def __init__(self, node, callback_group: ReentrantCallbackGroup) -> None:
        self._node = node
        self._callbacks = callback_group
        self._action = ActionClient(
            node,
            Spin,
            str(node.get_parameter("spin_action").value),
            callback_group=callback_group,
        )
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(
            self._tf_buffer, node, spin_thread=False
        )
        self._lock = threading.Lock()
        self._odom: Odometry | None = None
        self._odom_received = 0.0
        self._costmap: OccupancyGrid | None = None
        self._costmap_received = 0.0
        self._arm: VisualGraspStatus | None = None
        self._arm_received = 0.0
        self._goal_handle = None
        self._motion_active = False
        self._initial_odom_yaw: float | None = None
        self._current_heading_deg = 0.0
        self._target_heading_deg = 0.0
        self._last_preflight = Nav2Preflight(
            False, False, False, False, False, False, False, False, False,
            "preflight has not run",
        )
        node.create_subscription(
            Odometry,
            str(node.get_parameter("odom_topic").value),
            self._on_odom,
            20,
            callback_group=callback_group,
        )
        node.create_subscription(
            OccupancyGrid,
            str(node.get_parameter("local_costmap_topic").value),
            self._on_costmap,
            5,
            callback_group=callback_group,
        )
        node.create_subscription(
            VisualGraspStatus,
            str(node.get_parameter("arm_status_topic").value),
            self._on_arm,
            10,
            callback_group=callback_group,
        )
        self._lifecycle_clients = [
            node.create_client(
                GetState, service, callback_group=callback_group
            )
            for service in node.get_parameter("nav2_lifecycle_services").value
        ]
        self._competing_cancel_clients = [
            node.create_client(
                CancelGoal, service, callback_group=callback_group
            )
            for service in node.get_parameter("competing_action_cancel_services").value
        ]

    @property
    def motion_active(self) -> bool:
        return self._motion_active

    @property
    def current_heading_deg(self) -> float:
        return self._current_heading_deg

    @property
    def target_heading_deg(self) -> float:
        return self._target_heading_deg

    @property
    def last_preflight(self) -> Nav2Preflight:
        return self._last_preflight

    def action_ready(self) -> bool:
        return bool(self._action.server_is_ready())

    def _on_odom(self, message: Odometry) -> None:
        with self._lock:
            self._odom = message
            self._odom_received = time.monotonic()
            if self._initial_odom_yaw is None:
                self._initial_odom_yaw = self._odom_yaw(message)
            self._current_heading_deg = self._relative_heading_locked(message)

    def _on_costmap(self, message: OccupancyGrid) -> None:
        with self._lock:
            self._costmap = message
            self._costmap_received = time.monotonic()

    def _on_arm(self, message: VisualGraspStatus) -> None:
        with self._lock:
            self._arm = message
            self._arm_received = time.monotonic()

    @staticmethod
    def _wait_future(future, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.02)
        return future.done()

    @staticmethod
    def _odom_yaw(message: Odometry) -> float:
        q = message.pose.pose.orientation
        return math.atan2(
            2.0 * (q.w * q.z + q.x * q.y),
            1.0 - 2.0 * (q.y * q.y + q.z * q.z),
        )

    def _relative_heading_locked(self, message: Odometry) -> float:
        yaw = self._odom_yaw(message)
        initial = yaw if self._initial_odom_yaw is None else self._initial_odom_yaw
        return math.degrees(yaw - initial) % 360.0

    def _lifecycle_ready(self) -> tuple[bool, str]:
        for client in self._lifecycle_clients:
            if not client.wait_for_service(timeout_sec=0.15):
                return False, f"lifecycle service unavailable: {client.srv_name}"
            future = client.call_async(GetState.Request())
            if not self._wait_future(future, 0.75):
                return False, f"lifecycle state timed out: {client.srv_name}"
            response = future.result()
            if (
                response is None
                or response.current_state.id != State.PRIMARY_STATE_ACTIVE
            ):
                label = "unknown" if response is None else response.current_state.label
                return False, f"Nav2 node is not active: {client.srv_name}={label}"
        return True, "Nav2 lifecycle nodes are active"

    def _tf_ready(self) -> tuple[bool, str]:
        try:
            transform = self._tf_buffer.lookup_transform(
                str(self._node.get_parameter("odom_frame").value),
                str(self._node.get_parameter("base_frame").value),
                Time(),
                timeout=Duration(seconds=0.2),
            )
        except TransformException as exc:
            return False, f"TF unavailable: {exc}"
        stamp = Time.from_msg(transform.header.stamp)
        age = (self._node.get_clock().now() - stamp).nanoseconds / 1e9
        ttl = float(self._node.get_parameter("tf_ttl_sec").value)
        if age < 0.0 or age > ttl:
            return False, f"TF is stale ({age:.2f}s)"
        return True, "TF is fresh"

    def _odom_ready(self) -> tuple[bool, str]:
        with self._lock:
            received = self._odom_received
        age = time.monotonic() - received if received else math.inf
        ttl = float(self._node.get_parameter("odom_ttl_sec").value)
        return (
            (True, "odometry is fresh")
            if age <= ttl
            else (False, f"odometry is stale ({age:.2f}s)")
        )

    def _costmap_snapshot(self):
        with self._lock:
            return self._costmap, self._costmap_received

    def _rotation_clear(self) -> tuple[bool, str]:
        grid, received = self._costmap_snapshot()
        age = time.monotonic() - received if received else math.inf
        ttl = float(self._node.get_parameter("costmap_ttl_sec").value)
        if grid is None or age > ttl:
            return False, f"local costmap is stale ({age:.2f}s)"
        frame = grid.header.frame_id or str(
            self._node.get_parameter("odom_frame").value
        )
        try:
            transform = self._tf_buffer.lookup_transform(
                frame,
                str(self._node.get_parameter("base_frame").value),
                Time(),
                timeout=Duration(seconds=0.2),
            )
        except TransformException as exc:
            return False, f"costmap TF unavailable: {exc}"
        bx = float(transform.transform.translation.x)
        by = float(transform.transform.translation.y)
        origin = grid.info.origin
        yaw = math.atan2(
            2.0 * (origin.orientation.w * origin.orientation.z),
            1.0 - 2.0 * origin.orientation.z * origin.orientation.z,
        )
        cosine, sine = math.cos(yaw), math.sin(yaw)
        radius = float(
            self._node.get_parameter("rotation_clearance_radius_m").value
        )
        threshold = int(
            self._node.get_parameter("rotation_obstacle_cost_threshold").value
        )
        resolution = float(grid.info.resolution)
        for row in range(int(grid.info.height)):
            for column in range(int(grid.info.width)):
                cost = int(grid.data[row * int(grid.info.width) + column])
                if cost < threshold:
                    continue
                lx = (column + 0.5) * resolution
                ly = (row + 0.5) * resolution
                x = origin.position.x + cosine * lx - sine * ly
                y = origin.position.y + sine * lx + cosine * ly
                if math.hypot(x - bx, y - by) <= radius:
                    return False, (
                        f"obstacle cost {cost} inside {radius:.2f}m rotation sweep"
                    )
        return True, "rotation sweep is clear"

    def _cmd_vel_clear(self) -> tuple[bool, str]:
        allowed = {
            str(name).strip().lstrip("/")
            for name in self._node.get_parameter(
                "allowed_cmd_vel_publishers"
            ).value
        }
        unexpected = []
        topic = str(self._node.get_parameter("cmd_vel_topic").value)
        for endpoint in self._node.get_publishers_info_by_topic(topic):
            name = endpoint.node_name.strip().lstrip("/")
            if name not in allowed:
                unexpected.append(
                    f"{endpoint.node_namespace.rstrip('/')}/{name}"
                )
        if unexpected:
            return False, "unexpected cmd_vel publishers: " + ", ".join(
                sorted(set(unexpected))
            )
        return True, "cmd_vel ownership is allowlisted"

    def _arm_safe(self) -> tuple[bool, str]:
        with self._lock:
            arm = self._arm
            received = self._arm_received
        publishers = self._node.get_publishers_info_by_topic(
            str(self._node.get_parameter("arm_status_topic").value)
        )
        if not publishers:
            return True, "manipulator service is inactive"
        age = time.monotonic() - received if received else math.inf
        if arm is None or age > 2.0:
            return False, "manipulator status is unavailable"
        if bool(self._node.get_parameter("require_arm_torque_off").value) and arm.torque_enabled:
            return False, "manipulator torque is enabled; stow it and disable torque"
        return True, "manipulator is in the configured safe state"

    def _cancel_competing_actions(self, cancelled: Callable[[], bool]) -> tuple[bool, str]:
        if not bool(
            self._node.get_parameter("cancel_competing_actions").value
        ):
            return True, "competing-action cancellation is disabled"
        for client in self._competing_cancel_clients:
            if cancelled():
                return False, "event cancelled during preflight"
            if not client.wait_for_service(timeout_sec=0.15):
                continue
            future = client.call_async(CancelGoal.Request())
            if not self._wait_future(future, 1.5):
                return False, f"action cancellation timed out: {client.srv_name}"
            response = future.result()
            if response is None:
                return False, f"action cancellation failed: {client.srv_name}"
        return True, "competing navigation goals are cancelled"

    def preflight(
        self,
        cancelled: Callable[[], bool],
        *,
        cancel_competing: bool = True,
    ) -> Nav2Preflight:
        with self._lock:
            if self._odom is not None:
                self._initial_odom_yaw = self._odom_yaw(self._odom)
                self._current_heading_deg = 0.0
                self._target_heading_deg = 0.0
        action_ready = self._action.wait_for_server(timeout_sec=0.2)
        lifecycle_ready, lifecycle_message = self._lifecycle_ready()
        tf_ready, tf_message = self._tf_ready()
        odom_ready, odom_message = self._odom_ready()
        rotation_clear, costmap_message = self._rotation_clear()
        costmap_ready = not costmap_message.startswith("local costmap is stale")
        cmd_vel_clear, cmd_vel_message = self._cmd_vel_clear()
        arm_safe, arm_message = self._arm_safe()
        messages = []
        checks = (
            (action_ready, "Spin Action is unavailable"),
            (lifecycle_ready, lifecycle_message),
            (tf_ready, tf_message),
            (odom_ready, odom_message),
            (costmap_ready, costmap_message),
            (rotation_clear, costmap_message),
            (cmd_vel_clear, cmd_vel_message),
            (arm_safe, arm_message),
        )
        for ready, message in checks:
            if not ready and message not in messages:
                messages.append(message)
        if not messages and cancel_competing:
            stopped, message = self._cancel_competing_actions(cancelled)
            if not stopped:
                messages.append(message)
            elif not self.wait_until_stopped():
                messages.append(
                    "robot did not reach stable zero angular velocity after cancelling navigation"
                )
            if not messages:
                # Re-evaluate ownership and clearance after other Nav2 goals
                # have been cancelled and the base has settled.
                cmd_vel_clear, cmd_vel_message = self._cmd_vel_clear()
                rotation_clear, costmap_message = self._rotation_clear()
                if not cmd_vel_clear:
                    messages.append(cmd_vel_message)
                if not rotation_clear:
                    messages.append(costmap_message)
        ready = not messages and not cancelled()
        snapshot = Nav2Preflight(
            ready=ready,
            action_ready=action_ready,
            lifecycle_ready=lifecycle_ready,
            tf_ready=tf_ready,
            odom_ready=odom_ready,
            costmap_ready=costmap_ready,
            rotation_clear=rotation_clear,
            cmd_vel_clear=cmd_vel_clear,
            arm_safe=arm_safe,
            message="ready" if ready else "; ".join(messages),
        )
        self._last_preflight = snapshot
        return snapshot

    def wait_until_stopped(self) -> bool:
        threshold = float(
            self._node.get_parameter("stop_angular_velocity_rps").value
        )
        stable_sec = float(self._node.get_parameter("stop_stable_sec").value)
        timeout = float(self._node.get_parameter("stop_timeout_sec").value)
        deadline = time.monotonic() + timeout
        stable_since = None
        while time.monotonic() < deadline:
            with self._lock:
                odom = self._odom
                received = self._odom_received
            fresh = received and time.monotonic() - received <= float(
                self._node.get_parameter("odom_ttl_sec").value
            )
            angular = math.inf if odom is None else abs(float(odom.twist.twist.angular.z))
            if fresh and angular <= threshold:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= stable_sec:
                    return True
            else:
                stable_since = None
            time.sleep(0.02)
        return False

    def cancel_current_segment_and_wait(self) -> bool:
        handle = self._goal_handle
        if handle is None:
            return not self._motion_active
        future = handle.cancel_goal_async()
        acknowledged = self._wait_future(
            future, float(self._node.get_parameter("spin_cancel_timeout_sec").value)
        )
        if acknowledged:
            response = future.result()
            acknowledged = response is not None and bool(response.goals_canceling)
        self._goal_handle = None
        self._motion_active = False
        return bool(acknowledged and self.wait_until_stopped())

    def go_to_heading(
        self,
        target_heading_deg: float,
        _stage: str,
        _step: int,
        _total: int,
        should_interrupt: Callable[[], bool],
    ) -> bool:
        target = float(target_heading_deg)
        with self._lock:
            if self._odom is not None:
                self._current_heading_deg = self._relative_heading_locked(self._odom)
            current = self._current_heading_deg
        delta = _normalize_degrees(target - current)
        self._target_heading_deg = target
        if abs(delta) < 0.5:
            return self.wait_until_stopped()
        if not self._action.wait_for_server(timeout_sec=0.2):
            raise RuntimeError("Nav2 Spin Action is unavailable")
        goal = Spin.Goal()
        goal.target_yaw = math.radians(delta)
        goal.time_allowance.sec = int(
            float(self._node.get_parameter("spin_timeout_sec").value)
        )
        response_future = self._action.send_goal_async(goal)
        if not self._wait_future(response_future, 2.0):
            raise RuntimeError("Nav2 Spin goal response timed out")
        handle = response_future.result()
        if handle is None or not handle.accepted:
            raise RuntimeError("Nav2 Spin goal was rejected")
        self._goal_handle = handle
        self._motion_active = True
        result_future = handle.get_result_async()
        while not result_future.done():
            if should_interrupt():
                stopped = self.cancel_current_segment_and_wait()
                if not stopped:
                    raise RuntimeError("Spin cancellation was not acknowledged or robot did not stop")
                return False
            time.sleep(0.02)
        wrapped = result_future.result()
        self._goal_handle = None
        self._motion_active = False
        if wrapped is None or wrapped.status != GoalStatus.STATUS_SUCCEEDED:
            raise RuntimeError(
                "Nav2 Spin failed with status "
                + str(GoalStatus.STATUS_UNKNOWN if wrapped is None else wrapped.status)
            )
        if not self.wait_until_stopped():
            raise RuntimeError("robot angular velocity did not settle after Spin")
        with self._lock:
            if self._odom is not None:
                self._current_heading_deg = self._relative_heading_locked(self._odom)
            else:
                self._current_heading_deg = target % 360.0
        return True

    def return_to_start(self, cancelled: Callable[[], bool]) -> bool:
        return self.go_to_heading(360.0, "return_to_start", 0, 0, cancelled)

    def shutdown(self) -> None:
        self.cancel_current_segment_and_wait()
