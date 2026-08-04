#!/usr/bin/env python3
"""UWB summon/follow action server using Nav2 as the only motion owner."""

from __future__ import annotations

import json
import math
import threading
import time

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.time import Time
from std_msgs.msg import String
from std_srvs.srv import Trigger
from tf2_ros import Buffer, TransformException, TransformListener

from project_link_uwb_interfaces.action import PersonNavigation
from project_link_uwb_interfaces.msg import UwbObservation

from .geometry import Calibration, base_to_map, sensor_to_base, yaw_from_quaternion
from .policy import GoalThrottler, PersonMode, PolicyConfig, propose_goal, target_speed_mps


STATUS_SUCCEEDED = 0
STATUS_CANCELED = 1
STATUS_REJECTED = 2
STATUS_TARGET_LOST = 3
STATUS_NAVIGATION_FAILED = 4


class UwbNav2Server(Node):
    def __init__(self) -> None:
        super().__init__("uwb_nav2_server")
        self._declare_parameters()
        self._callbacks = ReentrantCallbackGroup()
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self, spin_thread=False)
        self._nav_client = ActionClient(
            self,
            NavigateToPose,
            str(self.get_parameter("nav2_action_name").value),
            callback_group=self._callbacks,
        )
        self._observation_lock = threading.Lock()
        self._latest_observation: UwbObservation | None = None
        self._active_lock = threading.Lock()
        self._reserved = False
        self._active = False
        self._stop_requested = threading.Event()
        self._nav_goal_handle = None
        self._nav_result_future = None
        self._state = "disabled"
        self._last_reason = "startup"
        self._last_person_distance = math.nan
        self._last_proposed_goal = PoseStamped()

        self.create_subscription(
            UwbObservation,
            str(self.get_parameter("observation_topic").value),
            self._on_observation,
            20,
            callback_group=self._callbacks,
        )
        self._goal_pub = self.create_publisher(
            PoseStamped,
            str(self.get_parameter("proposed_goal_topic").value),
            10,
        )
        self._status_pub = self.create_publisher(
            String,
            str(self.get_parameter("status_topic").value),
            10,
        )
        self.create_service(
            Trigger,
            str(self.get_parameter("stop_service_name").value),
            self._stop_service,
            callback_group=self._callbacks,
        )
        self._server = ActionServer(
            self,
            PersonNavigation,
            str(self.get_parameter("person_action_name").value),
            execute_callback=self._execute,
            goal_callback=self._goal_callback,
            cancel_callback=self._cancel_callback,
            callback_group=self._callbacks,
        )
        self.create_timer(1.0, self._publish_status, callback_group=self._callbacks)
        mode = "LIVE NAV2" if bool(self.get_parameter("enable_motion").value) else "SHADOW"
        self.get_logger().warn(
            f"UWB person navigation starts in {mode} mode. It never publishes /cmd_vel; Nav2 remains the sole motion path."
        )

    def _declare_parameters(self) -> None:
        self.declare_parameter("enable_motion", False)
        self.declare_parameter("observation_topic", "/uwb/person_observation")
        self.declare_parameter("proposed_goal_topic", "/uwb_navigation/proposed_goal")
        self.declare_parameter("status_topic", "/uwb_navigation/status")
        self.declare_parameter("person_action_name", "/uwb_navigation/person_navigation")
        self.declare_parameter("stop_service_name", "/uwb_navigation/stop")
        self.declare_parameter("nav2_action_name", "/navigate_to_pose")
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("source_id", "tag-1")
        self.declare_parameter("update_rate_hz", 5.0)
        self.declare_parameter("acquisition_count", 5)
        self.declare_parameter("acquisition_timeout_sec", 8.0)
        self.declare_parameter("uwb_ttl_sec", 0.50)
        self.declare_parameter("slam_ttl_sec", 0.30)
        self.declare_parameter("cancel_timeout_sec", 2.0)
        self.declare_parameter("goal_response_timeout_sec", 2.0)
        self.declare_parameter("goal_displacement_m", 0.20)
        self.declare_parameter("goal_refresh_sec", 0.75)
        self.declare_parameter("max_target_speed_mps", 3.0)
        self.declare_parameter("summon_distance_m", 1.0)
        self.declare_parameter("summon_min_distance_m", 0.75)
        self.declare_parameter("summon_arrival_distance_m", 1.15)
        self.declare_parameter("follow_distance_m", 1.5)
        self.declare_parameter("follow_min_distance_m", 1.3)
        self.declare_parameter("follow_hold_distance_m", 1.7)
        self.declare_parameter("calibration_status", "invalid")
        self.declare_parameter("calibration_version", "unapproved")
        self.declare_parameter("axis_xx", 1.0)
        self.declare_parameter("axis_xy", 0.0)
        self.declare_parameter("axis_yx", 0.0)
        self.declare_parameter("axis_yy", 1.0)
        self.declare_parameter("sensor_yaw_rad", 0.0)
        self.declare_parameter("sensor_translation_x_m", 0.0)
        self.declare_parameter("sensor_translation_y_m", 0.0)
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("allowed_cmd_vel_publishers", ["velocity_smoother", "behavior_server"])

    def _policy_config(self) -> PolicyConfig:
        return PolicyConfig(
            summon_distance_m=float(self.get_parameter("summon_distance_m").value),
            summon_min_distance_m=float(self.get_parameter("summon_min_distance_m").value),
            summon_arrival_distance_m=float(self.get_parameter("summon_arrival_distance_m").value),
            follow_distance_m=float(self.get_parameter("follow_distance_m").value),
            follow_min_distance_m=float(self.get_parameter("follow_min_distance_m").value),
            follow_hold_distance_m=float(self.get_parameter("follow_hold_distance_m").value),
        )

    def _calibration(self) -> Calibration:
        return Calibration(
            status=str(self.get_parameter("calibration_status").value),
            version=str(self.get_parameter("calibration_version").value),
            axis_xx=float(self.get_parameter("axis_xx").value),
            axis_xy=float(self.get_parameter("axis_xy").value),
            axis_yx=float(self.get_parameter("axis_yx").value),
            axis_yy=float(self.get_parameter("axis_yy").value),
            yaw_rad=float(self.get_parameter("sensor_yaw_rad").value),
            translation_x_m=float(self.get_parameter("sensor_translation_x_m").value),
            translation_y_m=float(self.get_parameter("sensor_translation_y_m").value),
        )

    def _goal_callback(self, request: PersonNavigation.Goal) -> GoalResponse:
        if request.mode not in (PersonMode.SUMMON, PersonMode.FOLLOW):
            self.get_logger().error("Rejected unsupported UWB person-navigation mode.")
            return GoalResponse.REJECT
        configured_source = str(self.get_parameter("source_id").value)
        if request.source_id and request.source_id != configured_source:
            self.get_logger().error("Rejected a person-navigation goal for an unconfigured source ID.")
            return GoalResponse.REJECT
        try:
            self._policy_config().validate()
            self._calibration().validate(require_approved=bool(self.get_parameter("enable_motion").value))
        except ValueError as exc:
            self.get_logger().error(f"Rejected UWB goal: {exc}")
            return GoalResponse.REJECT
        if bool(self.get_parameter("enable_motion").value):
            if not self._nav_client.wait_for_server(timeout_sec=0.0):
                self.get_logger().error("Rejected UWB goal because Nav2 NavigateToPose is unavailable.")
                return GoalResponse.REJECT
            unexpected = self._unexpected_cmd_vel_publishers()
            if unexpected:
                self.get_logger().error(f"Rejected UWB goal due to competing /cmd_vel publishers: {unexpected}")
                return GoalResponse.REJECT
        with self._active_lock:
            if self._active or self._reserved:
                self.get_logger().error("Rejected UWB goal because another person-navigation task is active.")
                return GoalResponse.REJECT
            self._reserved = True
        return GoalResponse.ACCEPT

    def _cancel_callback(self, _goal_handle) -> CancelResponse:
        self._stop_requested.set()
        return CancelResponse.ACCEPT

    def _stop_service(self, _request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        self._stop_requested.set()
        response.success = True
        response.message = "Stop requested; active Nav2 goal will be canceled fail-closed."
        return response

    def _on_observation(self, message: UwbObservation) -> None:
        if message.source_id != str(self.get_parameter("source_id").value):
            return
        with self._observation_lock:
            self._latest_observation = message

    def _observation_snapshot(self) -> UwbObservation | None:
        with self._observation_lock:
            return self._latest_observation

    def _robot_pose(self) -> tuple[float, float, float, float] | None:
        map_frame = str(self.get_parameter("map_frame").value).lstrip("/")
        base_frame = str(self.get_parameter("base_frame").value).lstrip("/")
        try:
            transform = self._tf_buffer.lookup_transform(map_frame, base_frame, Time())
        except TransformException as exc:
            self._last_reason = f"tf_unavailable:{exc}"
            return None
        stamp_ns = Time.from_msg(transform.header.stamp).nanoseconds
        now_ns = self.get_clock().now().nanoseconds
        age_sec = max(0.0, (now_ns - stamp_ns) / 1e9) if stamp_ns else math.inf
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        return (
            translation.x,
            translation.y,
            yaw_from_quaternion(rotation.x, rotation.y, rotation.z, rotation.w),
            age_sec,
        )

    def _target_in_map(self, observation: UwbObservation, robot_pose) -> tuple[float, float]:
        base_x, base_y = sensor_to_base(
            observation.x_m,
            observation.y_m,
            self._calibration(),
            require_approved=bool(self.get_parameter("enable_motion").value),
        )
        return base_to_map(base_x, base_y, robot_pose[0], robot_pose[1], robot_pose[2])

    def _execute(self, goal_handle):
        with self._active_lock:
            self._reserved = False
            self._active = True
        self._stop_requested.clear()
        self._state = "acquiring"
        self._last_reason = "waiting_for_fresh_uwb_and_tf"
        acquired = 0
        last_tag_time: int | None = None
        start = time.monotonic()
        live = bool(self.get_parameter("enable_motion").value)
        mode = PersonMode(goal_handle.request.mode)
        throttler = GoalThrottler(
            float(self.get_parameter("goal_displacement_m").value),
            float(self.get_parameter("goal_refresh_sec").value),
        )
        previous_target: tuple[float, float] | None = None
        previous_target_time_ns: int | None = None
        previous_target_tag: int | None = None
        period = 1.0 / max(1.0, float(self.get_parameter("update_rate_hz").value))
        try:
            while rclpy.ok():
                if goal_handle.is_cancel_requested or self._stop_requested.is_set():
                    self._state = "stopping"
                    self._cancel_nav_goal()
                    goal_handle.canceled()
                    return self._result(STATUS_CANCELED, "Person-navigation task canceled; Nav2 goal canceled.")

                observation = self._observation_snapshot()
                robot_pose = self._robot_pose()
                now_ros_ns = self.get_clock().now().nanoseconds
                observation_age = math.inf
                if observation is not None:
                    observation_stamp = Time.from_msg(observation.header.stamp).nanoseconds
                    observation_age = max(0.0, (now_ros_ns - observation_stamp) / 1e9)
                fresh = (
                    observation is not None
                    and observation.valid
                    and observation_age <= float(self.get_parameter("uwb_ttl_sec").value)
                    and robot_pose is not None
                    and robot_pose[3] <= float(self.get_parameter("slam_ttl_sec").value)
                )

                if not fresh:
                    acquired = 0
                    if self._state != "acquiring":
                        self._state = "stopping"
                        self._cancel_nav_goal()
                        goal_handle.abort()
                        return self._result(STATUS_TARGET_LOST, "Fresh UWB or map-to-base pose was lost; Nav2 goal canceled.")
                    if time.monotonic() - start > float(self.get_parameter("acquisition_timeout_sec").value):
                        goal_handle.abort()
                        return self._result(STATUS_TARGET_LOST, "Timed out acquiring fresh UWB and SLAM observations.")
                    self._feedback(goal_handle, "acquiring", math.nan, observation_age, None)
                    time.sleep(period)
                    continue

                if observation.tag_time_raw != last_tag_time:
                    last_tag_time = observation.tag_time_raw
                    acquired += 1
                if acquired < int(self.get_parameter("acquisition_count").value):
                    self._feedback(goal_handle, "acquiring", observation.coordinate_range_m, observation_age, None)
                    time.sleep(period)
                    continue

                self._state = "following" if mode == PersonMode.FOLLOW else "summoning"
                person_x, person_y = self._target_in_map(observation, robot_pose)
                if observation.tag_time_raw != previous_target_tag:
                    observation_stamp_ns = Time.from_msg(observation.header.stamp).nanoseconds
                    if previous_target is not None and previous_target_time_ns is not None:
                        speed = target_speed_mps(
                            previous_target[0],
                            previous_target[1],
                            previous_target_time_ns,
                            person_x,
                            person_y,
                            observation_stamp_ns,
                        )
                        if speed > float(self.get_parameter("max_target_speed_mps").value):
                            self._cancel_nav_goal()
                            goal_handle.abort()
                            return self._result(
                                STATUS_TARGET_LOST,
                                f"Rejected physically implausible UWB target speed: {speed:.2f} m/s.",
                            )
                    previous_target = (person_x, person_y)
                    previous_target_time_ns = observation_stamp_ns
                    previous_target_tag = observation.tag_time_raw
                decision = propose_goal(mode, robot_pose[0], robot_pose[1], person_x, person_y, self._policy_config())
                self._last_person_distance = decision.person_distance_m
                self._last_reason = decision.reason

                if decision.action in ("hold", "arrived"):
                    self._state = "holding" if decision.action == "hold" else "arrived"
                    self._cancel_nav_goal()
                    self._feedback(goal_handle, self._state, decision.person_distance_m, observation_age, None)
                    if mode == PersonMode.SUMMON:
                        goal_handle.succeed()
                        return self._result(STATUS_SUCCEEDED, "Robot is within the configured summon distance and is stopped.")
                    time.sleep(period)
                    continue
                if decision.action != "navigate" or decision.goal_x_m is None or decision.goal_y_m is None:
                    self._cancel_nav_goal()
                    goal_handle.abort()
                    return self._result(STATUS_REJECTED, f"UWB goal policy rejected the target: {decision.reason}")

                proposed = self._pose(decision.goal_x_m, decision.goal_y_m, decision.goal_yaw_rad or 0.0)
                self._last_proposed_goal = proposed
                self._goal_pub.publish(proposed)
                self._feedback(goal_handle, self._state, decision.person_distance_m, observation_age, proposed)

                if not live:
                    self._last_reason = "shadow_goal_only"
                    if mode == PersonMode.SUMMON:
                        goal_handle.succeed()
                        return self._result(STATUS_SUCCEEDED, "Shadow mode produced a summon goal; no Nav2 goal was sent.")
                    time.sleep(period)
                    continue

                unexpected = self._unexpected_cmd_vel_publishers()
                if unexpected:
                    self._cancel_nav_goal()
                    goal_handle.abort()
                    return self._result(STATUS_REJECTED, f"Competing /cmd_vel publisher detected: {unexpected}")

                if throttler.should_replace(person_x, person_y, now_ros_ns):
                    if not self._cancel_nav_goal():
                        goal_handle.abort()
                        return self._result(STATUS_NAVIGATION_FAILED, "Nav2 goal cancellation was not acknowledged.")
                    if not self._send_nav_goal(proposed):
                        goal_handle.abort()
                        return self._result(STATUS_NAVIGATION_FAILED, "Nav2 rejected or failed to accept the UWB rolling goal.")
                    throttler.mark_submitted(person_x, person_y, now_ros_ns)

                nav_failure = self._completed_nav_failure()
                if nav_failure:
                    goal_handle.abort()
                    return self._result(STATUS_NAVIGATION_FAILED, nav_failure)
                time.sleep(period)
        except Exception as exc:
            self.get_logger().error(f"UWB person navigation failed closed: {exc}")
            self._cancel_nav_goal()
            goal_handle.abort()
            return self._result(STATUS_NAVIGATION_FAILED, f"Fail-closed exception: {exc}")
        finally:
            self._cancel_nav_goal()
            self._state = "disabled"
            with self._active_lock:
                self._active = False
                self._reserved = False

    def _pose(self, x_m: float, y_m: float, yaw_rad: float) -> PoseStamped:
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = str(self.get_parameter("map_frame").value).lstrip("/")
        pose.pose.position.x = x_m
        pose.pose.position.y = y_m
        pose.pose.orientation.z = math.sin(yaw_rad / 2.0)
        pose.pose.orientation.w = math.cos(yaw_rad / 2.0)
        return pose

    def _send_nav_goal(self, pose: PoseStamped) -> bool:
        request = NavigateToPose.Goal()
        request.pose = pose
        future = self._nav_client.send_goal_async(request)
        if not self._wait_future(future, float(self.get_parameter("goal_response_timeout_sec").value)):
            return False
        handle = future.result()
        if handle is None or not handle.accepted:
            return False
        self._nav_goal_handle = handle
        self._nav_result_future = handle.get_result_async()
        return True

    def _cancel_nav_goal(self) -> bool:
        if self._nav_goal_handle is None:
            self._nav_result_future = None
            return True
        if self._nav_result_future is not None and self._nav_result_future.done():
            self._nav_goal_handle = None
            self._nav_result_future = None
            return True
        future = self._nav_goal_handle.cancel_goal_async()
        acknowledged = self._wait_future(future, float(self.get_parameter("cancel_timeout_sec").value))
        if acknowledged:
            response = future.result()
            acknowledged = response is not None and bool(response.goals_canceling)
        self._nav_goal_handle = None
        self._nav_result_future = None
        return acknowledged

    def _completed_nav_failure(self) -> str | None:
        if self._nav_result_future is None or not self._nav_result_future.done():
            return None
        wrapped = self._nav_result_future.result()
        self._nav_goal_handle = None
        self._nav_result_future = None
        if wrapped is None:
            return "Nav2 returned no result."
        if wrapped.status == GoalStatus.STATUS_SUCCEEDED:
            return None
        return f"Nav2 rolling goal ended with status {wrapped.status}."

    @staticmethod
    def _wait_future(future, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while rclpy.ok() and not future.done() and time.monotonic() < deadline:
            time.sleep(0.02)
        return future.done()

    def _unexpected_cmd_vel_publishers(self) -> list[str]:
        allowed = {str(name).strip().lstrip("/") for name in self.get_parameter("allowed_cmd_vel_publishers").value}
        unexpected: list[str] = []
        for endpoint in self.get_publishers_info_by_topic(str(self.get_parameter("cmd_vel_topic").value)):
            name = endpoint.node_name.strip().lstrip("/")
            if name not in allowed:
                unexpected.append(f"{endpoint.node_namespace.rstrip('/')}/{name}")
        return sorted(set(unexpected))

    def _feedback(self, goal_handle, state: str, distance: float, age: float, pose: PoseStamped | None) -> None:
        feedback = PersonNavigation.Feedback()
        feedback.state = state
        feedback.person_distance_m = float(distance)
        feedback.observation_age_sec = float(age)
        if pose is not None:
            feedback.proposed_goal = pose
        goal_handle.publish_feedback(feedback)

    @staticmethod
    def _result(status: int, message: str) -> PersonNavigation.Result:
        result = PersonNavigation.Result()
        result.status = status
        result.message = message
        return result

    def _publish_status(self) -> None:
        message = String()
        message.data = json.dumps(
            {
                "state": self._state,
                "reason": self._last_reason,
                "enable_motion": bool(self.get_parameter("enable_motion").value),
                "person_distance_m": (
                    self._last_person_distance if math.isfinite(self._last_person_distance) else None
                ),
                "calibration_version": str(self.get_parameter("calibration_version").value),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._status_pub.publish(message)

    def destroy_node(self):
        self._stop_requested.set()
        self._cancel_nav_goal()
        self._server.destroy()
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = UwbNav2Server()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
