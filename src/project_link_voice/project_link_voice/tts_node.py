#!/usr/bin/env python3
"""Configurable local TTS command bridge for `/voice/tts_text`."""

from __future__ import annotations

import shlex
import subprocess

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class VoiceTtsNode(Node):
    def __init__(self) -> None:
        super().__init__("voice_tts_node")
        self.declare_parameter("tts_command", "espeak-ng -v zh")
        self.declare_parameter("tts_enabled", True)
        self._command = shlex.split(str(self.get_parameter("tts_command").value))
        self.create_subscription(String, "/voice/tts_text", self._on_tts_text, 10)

    def _on_tts_text(self, message: String) -> None:
        if not bool(self.get_parameter("tts_enabled").value):
            return
        if not self._command:
            self.get_logger().warn(f"TTS command is disabled; text was: {message.data}")
            return
        try:
            subprocess.Popen(
                [*self._command, message.data],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            self.get_logger().error(
                f"TTS executable '{self._command[0]}' is unavailable. Install/configure a local TTS command."
            )


def main() -> None:
    rclpy.init()
    node = VoiceTtsNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()