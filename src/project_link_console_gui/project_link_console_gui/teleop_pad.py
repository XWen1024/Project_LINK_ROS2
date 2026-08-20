"""Focus-scoped mapping teleoperation widget with a hold-to-run dead-man."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFocusEvent, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .models import TeleopKeyState


class TeleopPad(QWidget):
    command_requested = Signal(bool, bool, float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(150)
        self.setToolTip("先点击此区域，再按住空格和 W/A/S/D。失焦会立即停止。")
        self._keys = TeleopKeyState()
        self._mapping_mode = False
        self._linear_speed = 0.12
        self._angular_speed = 0.45
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._timer_tick)
        self._timer.start()

    def set_mapping_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._mapping_mode:
            return
        self._mapping_mode = enabled
        if not enabled:
            self._keys.clear()
            self._emit_command()
        self.update()

    def set_speeds(self, linear_speed: float, angular_speed: float) -> None:
        self._linear_speed = max(0.0, float(linear_speed))
        self._angular_speed = max(0.0, float(angular_speed))

    @staticmethod
    def _key_name(event: QKeyEvent) -> str | None:
        mapping = {
            Qt.Key_W: "w",
            Qt.Key_A: "a",
            Qt.Key_S: "s",
            Qt.Key_D: "d",
            Qt.Key_Up: "up",
            Qt.Key_Left: "left",
            Qt.Key_Down: "down",
            Qt.Key_Right: "right",
            Qt.Key_Space: "space",
        }
        return mapping.get(event.key())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        name = self._key_name(event)
        if name and not event.isAutoRepeat():
            self._keys.set_key(name, True)
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        name = self._key_name(event)
        if name and not event.isAutoRepeat():
            self._keys.set_key(name, False)
            self._emit_command()
            self.update()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        self._keys.focused = True
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._keys.clear()
        self._emit_command()
        self.update()
        super().focusOutEvent(event)

    def _emit_command(self) -> None:
        self.command_requested.emit(
            *self._keys.command(
                mapping_mode=self._mapping_mode,
                linear_speed=self._linear_speed,
                angular_speed=self._angular_speed,
            )
        )

    def _timer_tick(self) -> None:
        if self._mapping_mode:
            self._emit_command()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        active = self._mapping_mode and self._keys.focused
        painter.setBrush(QColor("#20252c" if active else "#191d22"))
        painter.setPen(QPen(QColor("#4f5966" if active else "#343a43"), 1.5))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
        painter.setPen(QColor("#e4e8ed" if self._mapping_mode else "#777f89"))
        status = "已获得键盘焦点" if self._keys.focused else "点击此区域启用键盘"
        if not self._mapping_mode:
            status = "仅建图模式可用"
        painter.drawText(18, 30, status)
        painter.setPen(QColor("#aeb6c2"))
        painter.drawText(18, 58, "按住 空格 + W/A/S/D（或方向键）")
        painter.drawText(18, 84, "松开空格、切换窗口或 ROS 心跳中断都会停车")
        enabled, deadman, linear, angular = self._keys.command(
            mapping_mode=self._mapping_mode,
            linear_speed=self._linear_speed,
            angular_speed=self._angular_speed,
        )
        painter.setPen(QColor("#66bb6a" if deadman else "#8c949f"))
        painter.drawText(18, 118, f"线速度 {linear:+.2f} m/s    角速度 {angular:+.2f} rad/s")
