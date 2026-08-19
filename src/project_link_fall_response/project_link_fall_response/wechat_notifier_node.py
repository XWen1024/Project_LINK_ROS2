#!/usr/bin/env python3
"""ROS service facade for the persistent single-contact WeChat notifier."""

from __future__ import annotations

import asyncio
import os
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from project_link_emergency_interfaces.srv import SendFallNotification

from .wechat import PersistentWeChatBot


class WeChatNotifierNode(Node):
    def __init__(self) -> None:
        super().__init__("wechat_notifier_node")
        self.declare_parameter("notification_service", "/fall_detection/send_notification")
        self.declare_parameter(
            "credentials_path", os.path.expanduser("~/.config/project_link/wechatbot/credentials.json")
        )
        self.declare_parameter(
            "binding_path", os.path.expanduser("~/.local/state/project-link/clawbot/binding.json")
        )
        self.declare_parameter(
            "ledger_path", os.path.expanduser("~/.local/state/project-link/clawbot/notifications.sqlite3")
        )
        self._bot = PersistentWeChatBot(
            str(self.get_parameter("credentials_path").value),
            str(self.get_parameter("binding_path").value),
            str(self.get_parameter("ledger_path").value),
        )
        self._poll_failed = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._startup = asyncio.run_coroutine_threadsafe(self._start_bot(), self._loop)
        self._ready_pub = self.create_publisher(Bool, "/fall_detection/notification_ready", 10)
        self.create_timer(1.0, self._publish_ready)
        self.create_service(
            SendFallNotification,
            str(self.get_parameter("notification_service").value),
            self._send,
        )

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _start_bot(self) -> None:
        try:
            await self._bot.start()
            poll_task = asyncio.create_task(self._bot.poll())
            poll_task.add_done_callback(self._poll_finished)
            self.get_logger().info("WeChat notifier logged in and polling")
        except Exception as exc:
            self.get_logger().error(f"WeChat notifier is unavailable: {exc}")

    def _poll_finished(self, task) -> None:
        self._bot._started = False
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.get_logger().error(f"WeChat polling stopped: {error}")
            # A dead long-poll loop cannot recover in place.  End the ROS spin;
            # main() returns a non-zero code so Restart=on-failure can recover.
            self._poll_failed = True
            if rclpy.ok():
                rclpy.shutdown()

    def _publish_ready(self) -> None:
        message = Bool()
        message.data = self._bot.ready
        self._ready_pub.publish(message)

    def _send(self, request, response):
        future = asyncio.run_coroutine_threadsafe(
            self._bot.send_alert(
                event_id=request.event_id,
                degraded=bool(request.degraded),
                confidence=float(request.confidence),
                reason=request.reason,
                occurred_at_ms=int(request.occurred_at_ms),
                jpeg_data=bytes(request.jpeg_data),
            ),
            self._loop,
        )
        try:
            result = future.result(timeout=90.0)
            response.attempted = result.attempted
            response.text_success = result.text_success
            response.image_success = result.image_success
            response.receipt = result.receipt
            response.message = result.message
        except Exception as exc:
            response.attempted = False
            response.text_success = False
            response.image_success = False
            response.message = f"WeChat notification failed: {exc}"
        return response

    def destroy_node(self):
        self._bot.stop()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2.0)
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = WeChatNotifierNode()
    exit_code = 0
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        exit_code = 1 if node._poll_failed else 0
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
