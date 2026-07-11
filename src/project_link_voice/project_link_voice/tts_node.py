"""Volcano TTS bridge for `/voice/tts_text`."""

from __future__ import annotations

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .volcano_tts import VolcanoTts


class VoiceTtsNode(Node):
    def __init__(self) -> None:
        super().__init__("voice_tts_node")
        self.declare_parameter("tts_enabled", True)
        self.declare_parameter("tts_sample_rate", 24000)
        self.declare_parameter("volcano_resource_id", "")
        self.declare_parameter("volcano_speaker", "")
        self._tts = VolcanoTts(
            resource_id=str(self.get_parameter("volcano_resource_id").value).strip() or None,
            speaker=str(self.get_parameter("volcano_speaker").value).strip() or None,
            sample_rate=int(self.get_parameter("tts_sample_rate").value),
            enabled=bool(self.get_parameter("tts_enabled").value),
        )
        self.create_subscription(String, "/voice/tts_text", self._on_tts_text, 10)

    def _on_tts_text(self, message: String) -> None:
        text = message.data.strip()
        if text:
            self.get_logger().info(f"TTS: {text}")
            self._tts.speak(text)

    def destroy_node(self):
        self._tts.shutdown()
        return super().destroy_node()


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
