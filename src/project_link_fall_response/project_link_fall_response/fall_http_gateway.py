#!/usr/bin/env python3
"""Authenticated aiohttp gateway for the Android fall-guard MVP."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
import threading
from typing import Any

from aiohttp import web
import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool

from project_link_emergency_interfaces.action import RespondToFall
from project_link_emergency_interfaces.srv import CaptureStill, SendFallNotification

from .event_store import BusyEventError, EventStore, TERMINAL_STATUSES
from .gateway_contract import ContractError, public_event, validate_event


MAX_BODY_BYTES = 16 * 1024


class GatewayRosBridge(Node):
    def __init__(self, store: EventStore) -> None:
        super().__init__("fall_http_gateway")
        self._callbacks = ReentrantCallbackGroup()
        self._store = store
        self._action = ActionClient(
            self,
            RespondToFall,
            "/fall_detection/respond_to_fall",
            callback_group=self._callbacks,
        )
        self._capture = self.create_client(
            CaptureStill, "/front_camera/capture_still", callback_group=self._callbacks
        )
        self._notify = self.create_client(
            SendFallNotification,
            "/fall_detection/send_notification",
            callback_group=self._callbacks,
        )
        self._lock = threading.Lock()
        self._goals: dict[str, Any] = {}
        self._notification_ready = False
        self.create_subscription(Bool, "/fall_detection/notification_ready", self._on_notification_ready, 10)

    def _on_notification_ready(self, message: Bool) -> None:
        self._notification_ready = bool(message.data)

    def readiness(self) -> dict[str, bool]:
        return {
            "coordinator_ready": self._action.wait_for_server(timeout_sec=0.0),
            "camera_ready": self._capture.service_is_ready(),
            "notification_ready": self._notify.service_is_ready() and self._notification_ready,
        }

    def dispatch(self, event: dict[str, Any], attempt: int = 0) -> bool:
        if not self._action.wait_for_server(timeout_sec=0.0):
            return False
        goal = RespondToFall.Goal()
        goal.event_id = event["event_id"]
        goal.mode = event["mode"]
        goal.occurred_at_ms = int(event["occurred_at_ms"])
        imu = json.loads(event["imu_json"]) if event.get("imu_json") else None
        goal.has_imu = imu is not None
        if imu:
            goal.peak_accel_g = float(imu["peak_accel_g"])
            goal.orientation_change_deg = float(imu["orientation_change_deg"])
            goal.inactivity_ms = int(imu["inactivity_ms"])
        future = self._action.send_goal_async(goal)
        future.add_done_callback(
            lambda completed, current=event, current_attempt=attempt: self._goal_response(
                current, current_attempt, completed
            )
        )
        return True

    def _goal_response(self, event: dict[str, Any], attempt: int, future) -> None:
        event_id = event["event_id"]
        try:
            handle = future.result()
        except Exception as exc:
            self._store.update(event_id, status="failed", stage="dispatch_failed", message=f"ROS goal failed: {exc}")
            return
        if handle is None or not handle.accepted:
            current = self._store.get(event_id)
            if current and current["status"] == "accepted" and attempt < 9:
                threading.Timer(0.5, lambda: self.dispatch(current, attempt + 1)).start()
                return
            self._store.update(
                event_id,
                status="failed",
                stage="dispatch_rejected",
                message="fall coordinator rejected event after bounded retries",
            )
            return
        goal_id = bytes(handle.goal_id.uuid).hex()
        self._store.update(event_id, ros_goal_id=goal_id)
        with self._lock:
            self._goals[event_id] = handle
        result_future = handle.get_result_async()
        result_future.add_done_callback(lambda completed, current_id=event_id: self._goal_result(current_id, completed))

    def _goal_result(self, event_id: str, future) -> None:
        with self._lock:
            self._goals.pop(event_id, None)
        event = self._store.get(event_id)
        if event is None or event["status"] in TERMINAL_STATUSES:
            return
        try:
            wrapped = future.result()
            result = wrapped.result
            final_status = result.final_status if result.final_status in TERMINAL_STATUSES else "failed"
            self._store.update(event_id, status=final_status, stage="completed", message=result.message)
        except Exception as exc:
            self._store.update(event_id, status="failed", stage="action_failed", message=f"fall action failed: {exc}")

    def cancel(self, event_id: str) -> None:
        with self._lock:
            handle = self._goals.get(event_id)
        if handle is not None:
            handle.cancel_goal_async()


class FallGatewayApp:
    def __init__(
        self,
        store: EventStore,
        bridge: GatewayRosBridge,
        token: str,
        model_path: str,
        world_model_path: str = "/home/wte/models/yolov8s-worldv2.pt",
    ) -> None:
        if not token:
            raise RuntimeError("FALL_GUARD_TOKEN is not configured")
        self.store = store
        self.bridge = bridge
        self.token = token
        self.model_path = Path(model_path).expanduser()
        self.world_model_path = Path(world_model_path).expanduser()

    @web.middleware
    async def authenticate(self, request: web.Request, handler):
        supplied = request.headers.get("X-Fall-Guard-Token", "")
        if not hmac.compare_digest(supplied, self.token):
            return web.json_response({"error": "unauthorized"}, status=401)
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            return web.json_response({"error": "request body too large"}, status=413)
        try:
            return await handler(request)
        except ContractError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)
        except web.HTTPException:
            raise
        except Exception as exc:
            return web.json_response({"error": f"internal error: {type(exc).__name__}"}, status=500)

    async def health(self, _request: web.Request) -> web.Response:
        readiness = self.bridge.readiness()
        active = self.store.active_event()
        return web.json_response(
            {
                "status": "ok",
                "service": "project-link-fall-gateway",
                "camera_ready": readiness["camera_ready"],
                "model_ready": self.model_path.is_file() and self.world_model_path.is_file(),
                "specialized_model_ready": self.model_path.is_file(),
                "world_model_ready": self.world_model_path.is_file(),
                "vision_ready": (
                    readiness["camera_ready"]
                    and self.model_path.is_file()
                    and self.world_model_path.is_file()
                    and bool(os.environ.get("OPENAI_API_KEY"))
                ),
                "notification_ready": readiness["notification_ready"],
                "coordinator_ready": readiness["coordinator_ready"],
                "active_event": active["event_id"] if active else None,
            }
        )

    async def submit(self, request: web.Request) -> web.Response:
        raw = await request.read()
        if len(raw) > MAX_BODY_BYTES:
            return web.json_response({"error": "request body too large"}, status=413)
        payload = validate_event(json.loads(raw.decode("utf-8")))
        existing = self.store.get(payload["event_id"])
        if existing is not None:
            return web.json_response(public_event(existing), status=200)
        if not self.bridge.readiness()["coordinator_ready"]:
            return web.json_response(
                {"error": "fall coordinator is not ready"},
                status=503,
                headers={"Retry-After": "2"},
            )
        try:
            result = self.store.create_event(payload)
        except BusyEventError:
            return web.json_response({"error": "another fall event is active"}, status=503, headers={"Retry-After": "2"})
        if result.preempted_event_id:
            self.bridge.cancel(result.preempted_event_id)
        if result.created and not self.bridge.dispatch(result.event):
            event = self.store.update(
                result.event["event_id"], status="failed", stage="dispatch_failed", message="coordinator became unavailable"
            )
            return web.json_response(public_event(event), status=503, headers={"Retry-After": "2"})
        return web.json_response(public_event(result.event), status=202 if result.created else 200)

    async def status(self, request: web.Request) -> web.Response:
        event = self.store.get(request.match_info["event_id"])
        if event is None:
            return web.json_response({"error": "unknown event_id"}, status=404)
        return web.json_response(public_event(event))

    async def cancel(self, request: web.Request) -> web.Response:
        raw = await request.read()
        if raw.strip() not in {b"", b"{}"}:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                raise
            if payload != {}:
                raise ContractError("cancel body must be an empty object")
        event, cancelled = self.store.cancel(request.match_info["event_id"])
        if event is None:
            return web.json_response({"error": "unknown event_id"}, status=404)
        if cancelled:
            self.bridge.cancel(event["event_id"])
        return web.json_response(public_event(event))

    def build(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY_BYTES, middlewares=[self.authenticate])
        app.router.add_get("/health", self.health)
        app.router.add_post("/api/fall", self.submit)
        app.router.add_get("/api/fall/{event_id}", self.status)
        app.router.add_post("/api/fall/{event_id}/cancel", self.cancel)
        return app


async def serve(node: GatewayRosBridge, executor: MultiThreadedExecutor) -> None:
    store = node._store
    recovered = store.recover_incomplete()
    if recovered:
        node.get_logger().warn(f"Marked {recovered} interrupted fall events failed")
    application = FallGatewayApp(
        store,
        node,
        os.environ.get("FALL_GUARD_TOKEN", ""),
        os.environ.get(
            "FALL_SPECIALIZED_MODEL",
            "/home/wte/models/project_link/human-fall-detection-yolo11.pt",
        ),
        os.environ.get("FALL_WORLD_MODEL", "/home/wte/models/yolov8s-worldv2.pt"),
    ).build()
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(
        runner,
        os.environ.get("FALL_GATEWAY_HOST", "0.0.0.0"),
        int(os.environ.get("FALL_GATEWAY_PORT", "8765")),
    )
    await site.start()
    node.get_logger().info("Fall HTTP gateway listening without starting robot motion")
    try:
        while rclpy.ok():
            await asyncio.sleep(0.5)
    finally:
        await runner.cleanup()
        executor.shutdown()


def main() -> None:
    rclpy.init()
    store = EventStore(
        os.environ.get("FALL_EVENT_DB", os.path.expanduser("~/.local/state/project-link/fall-response/events.sqlite3"))
    )
    node = GatewayRosBridge(store)
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()
    try:
        asyncio.run(serve(node, executor))
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        spin_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
