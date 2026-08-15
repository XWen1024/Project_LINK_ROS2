"""UWB shadow observation, plots, and measured four-direction calibration."""

from __future__ import annotations

from collections import deque
import math
import statistics

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


DIRECTIONS = (
    ("front", "前方", (1.0, 0.0)),
    ("back", "后方", (-1.0, 0.0)),
    ("left", "左侧", (0.0, 1.0)),
    ("right", "右侧", (0.0, -1.0)),
)


class RelativeUwbView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 360)
        self._observation: dict | None = None

    def set_observation(self, observation: dict) -> None:
        self._observation = observation
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#111419"))
        center = QPointF(self.width() * 0.5, self.height() * 0.54)
        radius = min(self.width(), self.height()) * 0.38
        painter.setPen(QPen(QColor("#303842"), 1))
        for factor in (0.25, 0.5, 0.75, 1.0):
            painter.drawEllipse(center, radius * factor, radius * factor)
        painter.drawLine(QPointF(center.x() - radius, center.y()), QPointF(center.x() + radius, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - radius), QPointF(center.x(), center.y() + radius))
        painter.setPen(QColor("#7f8995"))
        painter.drawText(int(center.x() - 12), int(center.y() - radius - 8), "前")
        painter.drawText(int(center.x() - 12), int(center.y() + radius + 20), "后")
        painter.drawText(int(center.x() - radius - 25), int(center.y() + 5), "左")
        painter.drawText(int(center.x() + radius + 10), int(center.y() + 5), "右")

        car = QPolygonF(
            [
                QPointF(center.x(), center.y() - 28),
                QPointF(center.x() - 20, center.y() + 22),
                QPointF(center.x() + 20, center.y() + 22),
            ]
        )
        painter.setBrush(QColor("#e6e9ed"))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(car)
        if not self._observation or not self._observation.get("valid", False):
            painter.setPen(QColor("#8f99a6"))
            painter.drawText(self.rect(), Qt.AlignCenter, "等待有效 UWB shadow 观测")
            return
        x = float(self._observation.get("x_m", 0.0))
        y = float(self._observation.get("y_m", 0.0))
        distance = max(1.0, math.hypot(x, y))
        scale = radius / max(3.0, math.ceil(distance))
        target = QPointF(center.x() - y * scale, center.y() - x * scale)
        painter.setPen(QPen(QColor("#ef9a62"), 2))
        painter.drawLine(center, target)
        painter.setBrush(QColor("#ef9a62"))
        painter.drawEllipse(target, 8, 8)
        angle = math.degrees(math.atan2(float(display["y_m"]), float(display["x_m"])))
        painter.setPen(QColor("#f2c29f"))
        painter.drawText(
            QRectF(target.x() + 12, target.y() - 28, 180, 45),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"{distance:.2f} m  {angle:+.1f}°",
        )


class HistoryPlot(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(180)
        self._distance: deque[float] = deque(maxlen=180)
        self._residual: deque[float] = deque(maxlen=180)

    def append(self, distance: float, residual: float) -> None:
        self._distance.append(float(distance))
        self._residual.append(abs(float(residual)))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#111419"))
        area = self.rect().adjusted(42, 15, -15, -28)
        painter.setPen(QPen(QColor("#303842"), 1))
        for index in range(5):
            y = area.top() + area.height() * index / 4.0
            painter.drawLine(area.left(), int(y), area.right(), int(y))
        if len(self._distance) < 2:
            painter.setPen(QColor("#8f99a6"))
            painter.drawText(self.rect(), Qt.AlignCenter, "等待距离历史")
            return
        maximum = max(1.0, max(self._distance), max(self._residual) * 4.0)
        self._draw_series(painter, area, list(self._distance), maximum, QColor("#76b7e5"))
        self._draw_series(painter, area, list(self._residual), maximum, QColor("#ef9a62"))
        painter.setPen(QColor("#76b7e5"))
        painter.drawText(area.left(), self.height() - 8, "距离")
        painter.setPen(QColor("#ef9a62"))
        painter.drawText(area.left() + 55, self.height() - 8, "残差")

    @staticmethod
    def _draw_series(painter, area, values: list[float], maximum: float, color: QColor) -> None:
        points = QPolygonF()
        count = len(values)
        for index, value in enumerate(values):
            x = area.left() + area.width() * index / max(1, count - 1)
            y = area.bottom() - area.height() * value / maximum
            points.append(QPointF(x, y))
        painter.setPen(QPen(color, 2))
        painter.drawPolyline(points)


class UwbPage(QWidget):
    def __init__(self, bridge, config_client, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._client = config_client
        self._latest: dict | None = None
        self._calibration: dict = {}
        self._captures: dict[str, list[dict]] = {name: [] for name, _label, _expected in DIRECTIONS}
        self._advanced = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        header = QHBoxLayout()
        title = QLabel("远程召唤 · UWB Shadow")
        title.setObjectName("pageTitle")
        self.mode_badge = QLabel("未运行")
        self.mode_badge.setObjectName("modeBadge")
        self.start_button = QPushButton("启动 Shadow")
        self.stop_button = QPushButton("停止 Shadow")
        self.load_button = QPushButton("读取当前标定")
        self.stop_button.setObjectName("dangerButton")
        self.start_button.clicked.connect(self._bridge.start_uwb_shadow)
        self.stop_button.clicked.connect(self._bridge.stop_uwb_shadow)
        self.load_button.clicked.connect(lambda: self._client.load("uwb"))
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.mode_badge)
        header.addWidget(self.load_button)
        header.addWidget(self.start_button)
        header.addWidget(self.stop_button)
        root.addLayout(header)

        note = QLabel("本页面固定为 shadow：只显示和标定 UWB，不调用 Nav2、不发布速度。")
        note.setStyleSheet("color: #d2ad72;")
        root.addWidget(note)

        upper = QHBoxLayout()
        self.relative_view = RelativeUwbView()
        upper.addWidget(self.relative_view, 3)
        status_box = QGroupBox("实时数据")
        status = QGridLayout(status_box)
        self.value_labels = {}
        fields = [
            ("distance", "坐标距离"),
            ("range", "模块距离 D"),
            ("angle", "相对角度"),
            ("x", "前后分量 X"),
            ("y", "左右分量 Y"),
            ("residual", "距离残差"),
            ("valid", "数据有效性"),
            ("reason", "拒绝原因"),
            ("goal", "Shadow 建议目标"),
        ]
        for row, (key, label) in enumerate(fields):
            status.addWidget(QLabel(label), row, 0)
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            status.addWidget(value, row, 1)
            self.value_labels[key] = value
        self.raw_status = QLabel("尚未收到 /uwb/status")
        self.raw_status.setWordWrap(True)
        status.addWidget(self.raw_status, len(fields), 0, 1, 2)
        upper.addWidget(status_box, 2)
        root.addLayout(upper, 2)

        history_box = QGroupBox("距离与残差趋势")
        history_layout = QVBoxLayout(history_box)
        self.history = HistoryPlot()
        history_layout.addWidget(self.history)
        root.addWidget(history_box)

        tuning_box = QGroupBox("Shadow 常用参数")
        tuning_form = QFormLayout(tuning_box)
        self.max_residual = QDoubleSpinBox()
        self.max_residual.setRange(0.05, 2.0)
        self.max_residual.setValue(0.50)
        self.max_residual.setSuffix(" m")
        self.max_residual.setToolTip("D 与 hypot(X,Y) 的最大允许残差")
        self.uwb_ttl = QDoubleSpinBox()
        self.uwb_ttl.setRange(0.10, 5.0)
        self.uwb_ttl.setValue(0.50)
        self.uwb_ttl.setSuffix(" s")
        self.acquisition_count = QSpinBox()
        self.acquisition_count.setRange(1, 50)
        self.acquisition_count.setValue(5)
        self.goal_displacement = QDoubleSpinBox()
        self.goal_displacement.setRange(0.05, 2.0)
        self.goal_displacement.setValue(0.20)
        self.goal_displacement.setSuffix(" m")
        tuning_form.addRow("最大距离残差", self.max_residual)
        tuning_form.addRow("观测有效期", self.uwb_ttl)
        tuning_form.addRow("启动采样数", self.acquisition_count)
        tuning_form.addRow("Shadow 目标更新位移", self.goal_displacement)
        save_tuning = QPushButton("保存 Shadow 参数")
        save_tuning.clicked.connect(self._save_tuning)
        tuning_form.addRow(save_tuning)
        root.addWidget(tuning_box)

        self.calibration_box = QGroupBox("四方向标定采集")
        calibration = QVBoxLayout(self.calibration_box)
        row = QHBoxLayout()
        row.addWidget(QLabel("人员站位距离"))
        self.expected_distance = QDoubleSpinBox()
        self.expected_distance.setRange(0.5, 10.0)
        self.expected_distance.setValue(2.0)
        self.expected_distance.setSuffix(" m")
        row.addWidget(self.expected_distance)
        row.addStretch()
        calibration.addLayout(row)
        self.capture_table = QTableWidget(4, 5)
        self.capture_table.setHorizontalHeaderLabels(["方向", "样本数", "X 中位数", "Y 中位数", "距离中位数"])
        self.capture_table.verticalHeader().setVisible(False)
        self.capture_table.horizontalHeader().setStretchLastSection(True)
        buttons = QHBoxLayout()
        for row_index, (name, label, _expected) in enumerate(DIRECTIONS):
            self.capture_table.setItem(row_index, 0, QTableWidgetItem(label))
            for column in range(1, 5):
                self.capture_table.setItem(row_index, column, QTableWidgetItem("-"))
            button = QPushButton(f"采集{label}")
            button.clicked.connect(lambda _checked=False, direction=name: self._capture(direction))
            buttons.addWidget(button)
        clear_button = QPushButton("清空样本")
        clear_button.clicked.connect(self._clear_captures)
        buttons.addWidget(clear_button)
        calibration.addWidget(self.capture_table)
        calibration.addLayout(buttons)
        result_row = QHBoxLayout()
        self.calibration_result = QLabel("四个方向都有样本后才能生成建议值。")
        self.calibration_result.setWordWrap(True)
        generate_button = QPushButton("生成 proposed 标定")
        save_button = QPushButton("保存到 Orin（仍为 proposed）")
        generate_button.clicked.connect(self._generate_calibration)
        save_button.clicked.connect(self._save_calibration)
        result_row.addWidget(self.calibration_result, 1)
        result_row.addWidget(generate_button)
        result_row.addWidget(save_button)
        calibration.addLayout(result_row)
        self._proposed: dict | None = None
        root.addWidget(self.calibration_box)
        self._client.saved.connect(self._on_config_saved)
        self._client.loaded.connect(self._on_config_loaded)
        self._client.failed.connect(self._on_config_failed)

    def set_connection_available(self, connected: bool) -> None:
        self.start_button.setEnabled(bool(connected))
        self.stop_button.setEnabled(bool(connected))

    def update_system_state(self, state: dict) -> None:
        item = next(
            (entry for entry in state.get("subsystems", []) if entry.get("name") == "project-link-uwb-shadow.service"),
            None,
        )
        active = bool(item and item.get("ready"))
        self.mode_badge.setText("Shadow 运行中" if active else "未运行")

    def update_status(self, text: str) -> None:
        self.raw_status.setText(text or "-")

    def update_goal(self, goal: dict) -> None:
        self.value_labels["goal"].setText(
            f"{goal.get('frame_id', 'map')} ({float(goal.get('x', 0.0)):+.2f}, "
            f"{float(goal.get('y', 0.0)):+.2f})"
        )

    def update_observation(self, observation: dict) -> None:
        self._latest = observation
        x = float(observation.get("x_m", 0.0))
        y = float(observation.get("y_m", 0.0))
        display = dict(observation)
        display["x_m"] = (
            float(self._calibration.get("axis_xx", 1.0)) * x
            + float(self._calibration.get("axis_xy", 0.0)) * y
            + float(self._calibration.get("sensor_translation_x_m", 0.0))
        )
        display["y_m"] = (
            float(self._calibration.get("axis_yx", 0.0)) * x
            + float(self._calibration.get("axis_yy", 1.0)) * y
            + float(self._calibration.get("sensor_translation_y_m", 0.0))
        )
        self.relative_view.set_observation(display)
        distance = float(observation.get("coordinate_range_m", math.hypot(x, y)))
        module_range = float(observation.get("range_m", 0.0))
        residual = float(observation.get("range_residual_m", module_range - distance))
        angle = math.degrees(math.atan2(y, x))
        valid = bool(observation.get("valid", False))
        values = {
            "distance": f"{distance:.3f} m",
            "range": f"{module_range:.3f} m",
            "angle": f"{angle:+.2f}°",
            "x": f"{x:+.3f} m",
            "y": f"{y:+.3f} m",
            "residual": f"{residual:+.3f} m",
            "valid": "有效" if valid else "无效",
            "reason": str(observation.get("rejection_reason", "")) or "-",
        }
        for key, value in values.items():
            self.value_labels[key].setText(value)
        if valid:
            self.history.append(distance, residual)

    def _capture(self, direction: str) -> None:
        if self._latest is None or not self._latest.get("valid", False):
            self.calibration_result.setText("当前没有有效观测，无法采集。")
            return
        self._captures[direction].append(dict(self._latest))
        self._refresh_capture_table()

    def _clear_captures(self) -> None:
        for values in self._captures.values():
            values.clear()
        self._proposed = None
        self.calibration_result.setText("样本已清空。")
        self._refresh_capture_table()

    def _refresh_capture_table(self) -> None:
        for row, (name, _label, _expected) in enumerate(DIRECTIONS):
            values = self._captures[name]
            self.capture_table.item(row, 1).setText(str(len(values)))
            if not values:
                for column in range(2, 5):
                    self.capture_table.item(row, column).setText("-")
                continue
            x = statistics.median(float(item["x_m"]) for item in values)
            y = statistics.median(float(item["y_m"]) for item in values)
            distance = statistics.median(float(item["coordinate_range_m"]) for item in values)
            self.capture_table.item(row, 2).setText(f"{x:+.3f}")
            self.capture_table.item(row, 3).setText(f"{y:+.3f}")
            self.capture_table.item(row, 4).setText(f"{distance:.3f}")

    def _median_point(self, direction: str) -> tuple[float, float]:
        values = self._captures[direction]
        return (
            statistics.median(float(item["x_m"]) for item in values),
            statistics.median(float(item["y_m"]) for item in values),
        )

    def _generate_calibration(self) -> None:
        if any(not values for values in self._captures.values()):
            self.calibration_result.setText("前、后、左、右四个方向都至少需要一个有效样本。")
            return
        front = self._median_point("front")
        back = self._median_point("back")
        left = self._median_point("left")
        right = self._median_point("right")

        def unit(vector):
            length = math.hypot(vector[0], vector[1])
            if length < 1e-6:
                raise ValueError("opposite_direction_samples_overlap")
            return vector[0] / length, vector[1] / length

        try:
            forward = unit((front[0] - back[0], front[1] - back[1]))
            lateral = unit((left[0] - right[0], left[1] - right[1]))
        except ValueError:
            self.calibration_result.setText("相反方向样本过于接近，无法计算坐标轴。")
            return
        expected_distance = self.expected_distance.value()
        expected = {
            "front": (expected_distance, 0.0),
            "back": (-expected_distance, 0.0),
            "left": (0.0, expected_distance),
            "right": (0.0, -expected_distance),
        }
        offsets = []
        for name, _label, _direction in DIRECTIONS:
            raw_x, raw_y = self._median_point(name)
            projected = (
                forward[0] * raw_x + forward[1] * raw_y,
                lateral[0] * raw_x + lateral[1] * raw_y,
            )
            offsets.append((expected[name][0] - projected[0], expected[name][1] - projected[1]))
        translation = (
            statistics.mean(value[0] for value in offsets),
            statistics.mean(value[1] for value in offsets),
        )
        self._proposed = {
            "calibration_status": "proposed",
            "calibration_version": "gui-four-direction-proposed",
            "axis_xx": forward[0],
            "axis_xy": forward[1],
            "axis_yx": lateral[0],
            "axis_yy": lateral[1],
            "sensor_yaw_rad": 0.0,
            "sensor_translation_x_m": translation[0],
            "sensor_translation_y_m": translation[1],
        }
        self.calibration_result.setText(
            "建议矩阵 "
            f"[[{forward[0]:+.3f}, {forward[1]:+.3f}], "
            f"[{lateral[0]:+.3f}, {lateral[1]:+.3f}]]；"
            f"平移 ({translation[0]:+.3f}, {translation[1]:+.3f}) m。"
            "保存后状态仍为 proposed，不能开启运动。"
        )

    def _save_calibration(self) -> None:
        if self._proposed is None:
            self._generate_calibration()
        if self._proposed is not None:
            self._client.save("uwb", {"calibration": self._proposed})

    def _save_tuning(self) -> None:
        self._client.save(
            "uwb",
            {
                "tuning": {
                    "max_range_residual_m": self.max_residual.value(),
                    "uwb_ttl_sec": self.uwb_ttl.value(),
                    "acquisition_count": self.acquisition_count.value(),
                    "goal_displacement_m": self.goal_displacement.value(),
                }
            },
        )

    def _on_config_saved(self, section: str, _data: dict) -> None:
        if section == "uwb":
            self.calibration_result.setText(
                self.calibration_result.text() + " 已保存到 Orin；重启 shadow 后生效。"
            )

    def _on_config_loaded(self, section: str, data: dict) -> None:
        if section != "uwb":
            return
        calibration = data.get("calibration", {})
        self._calibration = dict(calibration)
        tuning = data.get("tuning", {})
        if tuning:
            self.max_residual.setValue(float(tuning.get("max_range_residual_m", 0.50)))
            self.uwb_ttl.setValue(float(tuning.get("uwb_ttl_sec", 0.50)))
            self.acquisition_count.setValue(int(tuning.get("acquisition_count", 5)))
            self.goal_displacement.setValue(float(tuning.get("goal_displacement_m", 0.20)))
        self.calibration_result.setText(
            "当前标定状态："
            f"{calibration.get('calibration_status', 'unknown')}，版本 "
            f"{calibration.get('calibration_version', 'unknown')}；"
            f"平移 ({float(calibration.get('sensor_translation_x_m', 0.0) or 0.0):+.3f}, "
            f"{float(calibration.get('sensor_translation_y_m', 0.0) or 0.0):+.3f}) m。"
        )

    def _on_config_failed(self, section: str, message: str) -> None:
        if section == "uwb":
            self.calibration_result.setText(f"保存失败：{message}")

    def set_advanced(self, enabled: bool) -> None:
        self._advanced = bool(enabled)
        self.calibration_box.setVisible(True)
