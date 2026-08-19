"""Plain PySide6 remote GUI for the headless Project LINK visual grasp node."""
from __future__ import annotations

from collections import deque
import json
import sys
import time

import rclpy
from rcl_interfaces.srv import GetParameters, SetParameters
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
from wheeltec_robot_msg.msg import VisualGraspStatus
from wheeltec_robot_msg.srv import SetGripper, SetTarget

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

PARAMETERS = {
    "camera_device": "/dev/project_link_arm_camera",
    "camera_width": 1280,
    "camera_height": 720,
    "camera_fps": 30.0,
    "preview_fps": 30.0,
    "prefer_native_mjpeg": True,
    "camera_reopen_interval_sec": 2.0,
    "jpeg_quality": 75,
    "detector_backend": "external_cuda",
    "detector_stale_timeout_sec": 1.0,
    "model_path": "/home/wte/models/yolov8s-worldv2.pt",
    "yolo_device": "cpu",
    "yolo_conf_threshold": 0.15,
    "yolo_max_lost_frames": 15,
    "yolo_infer_interval_sec": 0.0,
    "yolo_ema_alpha": 0.6,
    "yolo_max_center_jump_ratio": 0.12,
    "yolo_max_area_change_ratio": 1.8,
    "yolo_outlier_hold_frames": 4,
    "yolo_track_iou_weight": 0.5,
    "robot_port": "/dev/project_link_so101",
    "robot_id": "so101_slave",
    "pan_gain": 25.0,
    "tilt_gain": 15.0,
    "pan_direction": 1.0,
    "tilt_direction": -1.0,
    "centering_tilt_motion_enabled": False,
    "auto_lock_vertical_center_on_pregrasp": False,
    "auto_lock_vertical_center_offset_ratio": 0.10,
    "approach_step": 1.5,
    "approach_max_command_lead": 4.0,
    "approach_profile_max_lift_delta": 34.0,
    "approach_profile_elbow_delta": 12.3,
    "approach_profile_wrist_delta": -54.0,
    "approach_profile_wrist_trim": 0.0,
    "visual_servo_max_joint_step": 6.0,
    "visual_handoff_enabled": True,
    "visual_handoff_bbox_height_ratio": 0.85,
    "visual_handoff_area_ratio": 0.18,
    "visual_handoff_tof_m": 0.19,
    "visual_handoff_max_tof_m": 0.21,
    "final_grasp_tof_m": 0.090,
    "final_approach_step": 1.0,
    "final_approach_max_command_lead": 4.0,
    "final_approach_max_lift_delta": 20.0,
    "final_approach_command_interval_sec": 0.10,
    "final_approach_timeout_sec": 6.0,
    "final_approach_endpoint_settle_sec": 0.75,
    "centering_threshold": 0.04,
    "centering_limit_hold_cycles": 3,
    "centering_error_window": 3,
    "centering_min_samples": 2,
    "centering_confirm_cycles": 2,
    "centering_step_limit": 1.5,
    "centering_min_step_limit": 0.25,
    "centering_slow_zone": 0.12,
    "centering_max_command_lead": 4.0,
    "centering_command_interval_sec": 0.08,
    "grasp_area_threshold": 0.45,
    "gripper_open": 70.0,
    "gripper_close": 0.0,
    "move_fps": 15.0,
    "arrive_threshold": 2.0,
    "elbow_arrive_threshold": 5.0,
    "arrive_stable_margin": 0.75,
    "arrive_stable_delta": 0.35,
    "arrive_stable_cycles": 5,
    "move_step_limit": 3.0,
    "move_timeout_sec": 15.0,
    "grasp_timeout_sec": 20.0,
    "joint_command_limit": 95.0,
    "preset_joint_limit": 95.0,
    "standby_joint_limit": 99.5,
    "center_offset_x": 143.0,
    "center_offset_y": 61.0,
    "detection_anchor_x_ratio": 0.5,
    "detection_anchor_y_ratio": 0.5,
    "tof_enabled": False,
    "tof_control_enabled": False,
    "tof_calibrated": False,
    "tof_topic": "/visual_grasp/tof_range",
    "tof_stale_timeout_sec": 0.25,
    "tof_filter_window": 5,
    "tof_min_valid_samples": 3,
    "tof_grasp_distance_m": 0.06,
    "action_default_timeout_sec": 45.0,
}

DETECTION_ANCHORS = (
    ("检测框中心", 0.5, 0.5),
    ("检测框上边中点", 0.5, 0.0),
    ("检测框下边中点", 0.5, 1.0),
    ("检测框左边中点", 0.0, 0.5),
    ("检测框右边中点", 1.0, 0.5),
)


def map_display_point_to_frame(
    click_x: float,
    click_y: float,
    display_width: int,
    display_height: int,
    image_width: int,
    image_height: int,
    frame_width: int,
    frame_height: int,
):
    if min(
        display_width,
        display_height,
        image_width,
        image_height,
        frame_width,
        frame_height,
    ) <= 0:
        return None
    offset_x = (display_width - image_width) / 2.0
    offset_y = (display_height - image_height) / 2.0
    local_x = click_x - offset_x
    local_y = click_y - offset_y
    if not 0.0 <= local_x < image_width or not 0.0 <= local_y < image_height:
        return None
    frame_x = round(local_x * frame_width / image_width)
    frame_y = round(local_y * frame_height / image_height)
    return (
        min(frame_width - 1, max(0, frame_x)),
        min(frame_height - 1, max(0, frame_y)),
    )


class ClickableVideoLabel(QLabel):
    frame_point_selected = Signal(int, int)

    def __init__(self, text: str):
        super().__init__(text)
        self._selection_enabled = False
        self._frame_size = (0, 0)

    def set_frame_size(self, width: int, height: int) -> None:
        self._frame_size = (int(width), int(height))

    def set_selection_enabled(self, enabled: bool) -> None:
        self._selection_enabled = bool(enabled)
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        if self._selection_enabled and event.button() == Qt.LeftButton:
            pixmap = self.pixmap()
            if pixmap is not None and not pixmap.isNull():
                point = map_display_point_to_frame(
                    event.position().x(),
                    event.position().y(),
                    self.width(),
                    self.height(),
                    pixmap.width(),
                    pixmap.height(),
                    self._frame_size[0],
                    self._frame_size[1],
                )
                if point is not None:
                    self.frame_point_selected.emit(*point)
                    event.accept()
                    return
        super().mousePressEvent(event)


class ParameterServiceClient:
    """Small parameter client compatible with the ROS 2 Humble rclpy API."""

    def __init__(self, node: Node, remote_node_name: str) -> None:
        root = "/" + remote_node_name.strip("/")
        self._get_client = node.create_client(GetParameters, root + "/get_parameters")
        self._set_client = node.create_client(SetParameters, root + "/set_parameters")

    def get_parameters(self, names: list[str]):
        request = GetParameters.Request()
        request.names = names
        return self._get_client.call_async(request)

    def set_parameters(self, parameters: list[Parameter]):
        request = SetParameters.Request()
        request.parameters = [parameter.to_parameter_msg() for parameter in parameters]
        return self._set_client.call_async(request)


class RemoteClient(Node):
    def __init__(self) -> None:
        super().__init__("visual_grasp_gui")
        self.namespace = "/visual_grasp"
        self.devices: dict[str, VisualGraspStatus] = {}
        self.status: VisualGraspStatus | None = None
        self.image: CompressedImage | None = None
        self.camera_status: dict = {}
        self._status_sub = self.create_subscription(
            VisualGraspStatus,
            "/visual_grasp/status",
            self._on_status,
            10,
        )
        self._image_sub = self.create_subscription(
            CompressedImage,
            "/visual_grasp/image/compressed",
            self._on_image,
            qos_profile_sensor_data,
        )
        self._camera_status_sub = self.create_subscription(
            String,
            "/visual_grasp/camera_status",
            self._on_camera_status,
            10,
        )
        self._discovery_sub = self.create_subscription(
            VisualGraspStatus,
            "/project_link_visual_grasp/discovery",
            self._on_discovery,
            10,
        )
        self._create_clients()

    def _create_clients(self) -> None:
        root = self.namespace.rstrip("/")
        self.set_target = self.create_client(SetTarget, root + "/set_target")
        self.set_gripper = self.create_client(SetGripper, root + "/set_gripper")
        self.parameter_client = ParameterServiceClient(self, root)
        self.triggers = {
            name: self.create_client(Trigger, root + "/" + name)
            for name in (
                "connect_arm", "disconnect_arm", "start_approach", "stop",
                "start_visual_servo",
                "record_standby", "record_pregrasp", "record_placement",
                "go_standby", "go_pregrasp", "go_placement",
                "start_demo_recording", "stop_demo_recording",
                "calibration_start", "calibration_set_middle",
                "calibration_finish", "calibration_cancel",
            )
        }
        self.set_torque = self.create_client(SetBool, root + "/set_torque")

    def set_namespace(self, namespace: str) -> None:
        namespace = namespace.strip() or "/visual_grasp"
        if not namespace.startswith("/"):
            namespace = "/" + namespace
        if namespace == self.namespace:
            return
        self.namespace = namespace.rstrip("/")
        self._create_clients()

    def _on_status(self, message: VisualGraspStatus) -> None:
        if message.robot_namespace == self.namespace:
            self.status = message

    def _on_image(self, message: CompressedImage) -> None:
        self.image = message

    def _on_camera_status(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except (TypeError, ValueError):
            return
        if isinstance(payload, dict):
            self.camera_status = payload

    def _on_discovery(self, message: VisualGraspStatus) -> None:
        self.devices[message.robot_namespace] = message


class VisualGraspPanel(QWidget):
    message = Signal(str)

    def __init__(self, client: RemoteClient, show_advanced_parameters: bool = True):
        super().__init__()
        self.client = client
        self.parameter_widgets: dict[str, QWidget] = {}
        self._last_image_stamp = None
        self._image_times: deque[float] = deque(maxlen=120)
        self._frame_size = (0, 0)
        self._show_advanced_parameters = bool(show_advanced_parameters)
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._spin_and_refresh)
        self._timer.start(30)
        QTimer.singleShot(500, self._load_parameters)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._device_box())

        body = QHBoxLayout()
        video_panel = QWidget()
        video_layout = QVBoxLayout(video_panel)
        video_layout.setContentsMargins(0, 0, 0, 0)
        self.video = ClickableVideoLabel("等待 Orin 视频流")
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setMinimumSize(480, 320)
        self.video.setFrameShape(QLabel.Box)
        self.video.frame_point_selected.connect(self._set_servo_point)
        video_layout.addWidget(self.video, 1)

        self.video_status = QLabel("等待 /visual_grasp/image/compressed")
        self.video_status.setStyleSheet("color: #8e98a5;")
        video_layout.addWidget(self.video_status)
        video_layout.addWidget(self._alignment_box())
        body.addWidget(video_panel, 3)

        control_widget = QWidget()
        controls = QVBoxLayout(control_widget)
        controls.addWidget(self._status_box())
        controls.addWidget(self._tracking_box())
        controls.addWidget(self._arm_box())
        controls.addWidget(self._calibration_box())
        controls.addWidget(self._positions_box())
        controls.addWidget(self._demo_box())
        controls.addStretch(1)
        control_scroll = QScrollArea()
        control_scroll.setWidgetResizable(True)
        control_scroll.setWidget(control_widget)
        control_scroll.setMinimumWidth(340)
        body.addWidget(control_scroll, 2)
        layout.addLayout(body)

        self.parameter_scroll = QScrollArea()
        self.parameter_scroll.setWidgetResizable(True)
        self.parameter_scroll.setWidget(self._parameter_box())
        self.parameter_scroll.setMaximumHeight(250)
        self.parameter_scroll.setVisible(self._show_advanced_parameters)
        layout.addWidget(self.parameter_scroll)

    def _device_box(self) -> QGroupBox:
        box = QGroupBox("设备连接")
        layout = QGridLayout(box)
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._select_discovered_device)
        self.namespace_edit = QLineEdit("/visual_grasp")
        apply = QPushButton("使用命名空间")
        apply.clicked.connect(self._apply_namespace)
        refresh = QPushButton("刷新参数")
        refresh.clicked.connect(self._load_parameters)
        layout.addWidget(QLabel("自动发现"), 0, 0)
        layout.addWidget(self.device_combo, 0, 1)
        layout.addWidget(refresh, 0, 2)
        layout.addWidget(QLabel("手动命名空间"), 1, 0)
        layout.addWidget(self.namespace_edit, 1, 1)
        layout.addWidget(apply, 1, 2)
        layout.setColumnStretch(1, 1)
        return box

    def _alignment_box(self) -> QGroupBox:
        box = QGroupBox("视觉伺服对齐")
        layout = QGridLayout(box)
        self.pick_servo_point = QPushButton("点击画面选择黄色对齐点")
        self.pick_servo_point.setCheckable(True)
        self.pick_servo_point.toggled.connect(self._toggle_servo_point_selection)
        self.use_detection_anchor = QPushButton("黄色点设为当前绿色点")
        self.use_detection_anchor.clicked.connect(self._use_current_detection_anchor)
        self.reset_servo_point = QPushButton("恢复画面中心")
        self.reset_servo_point.clicked.connect(self._reset_servo_point)
        self.detection_anchor_combo = QComboBox()
        for label, ratio_x, ratio_y in DETECTION_ANCHORS:
            self.detection_anchor_combo.addItem(label, (ratio_x, ratio_y))
        self.detection_anchor_combo.currentIndexChanged.connect(
            self._detection_anchor_changed
        )
        self.enable_vertical_servo = QCheckBox("允许上下对齐（现场确认后启用）")
        self.enable_vertical_servo.toggled.connect(self._vertical_servo_changed)
        self.servo_point_status = QLabel(
            "黄色十字是机械臂对齐点；绿色圆点是检测框锚点。"
        )
        self.servo_point_status.setWordWrap(True)
        layout.addWidget(self.pick_servo_point, 0, 0, 1, 2)
        layout.addWidget(self.use_detection_anchor, 0, 2)
        layout.addWidget(self.reset_servo_point, 0, 3)
        layout.addWidget(QLabel("绿色锚点"), 1, 0)
        layout.addWidget(self.detection_anchor_combo, 1, 1, 1, 3)
        layout.addWidget(self.enable_vertical_servo, 2, 0, 1, 4)
        layout.addWidget(self.servo_point_status, 3, 0, 1, 4)
        return box

    def _status_box(self) -> QGroupBox:
        box = QGroupBox("状态")
        layout = QFormLayout(box)
        self.state_label = QLabel("未连接")
        self.hardware_label = QLabel("等待状态")
        self.tof_label = QLabel("未启用")
        self.target_label = QLabel("-")
        self.message_label = QLabel("-")
        self.message_label.setWordWrap(True)
        layout.addRow("执行状态", self.state_label)
        layout.addRow("硬件", self.hardware_label)
        layout.addRow("末端 ToF", self.tof_label)
        layout.addRow("目标", self.target_label)
        layout.addRow("消息", self.message_label)
        return box

    def _tracking_box(self) -> QGroupBox:
        box = QGroupBox("YOLO World 跟踪和抓取")
        layout = QGridLayout(box)
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("例如：red cup")
        layout.addWidget(QLabel("目标文本"), 0, 0)
        layout.addWidget(self.target_edit, 0, 1, 1, 3)
        controls = [
            ("开始跟踪", self._set_target),
            ("开始视觉伺服", lambda: self._trigger("start_visual_servo")),
            ("开始抓取", lambda: self._trigger("start_approach")),
            ("停止运动", lambda: self._trigger("stop")),
        ]
        for column, (text, callback) in enumerate(controls):
            button = QPushButton(text)
            button.clicked.connect(callback)
            layout.addWidget(button, 1, column)
        return box

    def _arm_box(self) -> QGroupBox:
        box = QGroupBox("SO-101")
        layout = QGridLayout(box)
        for column, (text, command) in enumerate((("连接", "connect_arm"), ("断开", "disconnect_arm"))):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=command: self._trigger(name))
            layout.addWidget(button, 0, column)
        self.torque = QCheckBox("启用扭矩")
        self.torque.toggled.connect(self._set_torque)
        layout.addWidget(self.torque, 1, 0, 1, 2)
        self.gripper = QDoubleSpinBox()
        self.gripper.setRange(-100.0, 100.0)
        self.gripper.setValue(70.0)
        set_gripper = QPushButton("设置夹爪")
        set_gripper.clicked.connect(self._set_gripper)
        layout.addWidget(QLabel("夹爪"), 2, 0)
        layout.addWidget(self.gripper, 2, 1)
        layout.addWidget(set_gripper, 3, 0, 1, 2)
        return box

    def _calibration_box(self) -> QGroupBox:
        box = QGroupBox("LeRobot SO-101 校准")
        layout = QGridLayout(box)
        self.calibration_label = QLabel("未开始")
        self.calibration_label.setWordWrap(True)
        layout.addWidget(self.calibration_label, 0, 0, 1, 2)

        buttons = (
            ("1. 开始校准并卸力", "calibration_start"),
            ("2. 记录中位", "calibration_set_middle"),
            ("3. 完成全行程记录", "calibration_finish"),
            ("取消校准", "calibration_cancel"),
        )
        for row, (text, command) in enumerate(buttons, start=1):
            button = QPushButton(text)
            button.clicked.connect(
                lambda _checked=False, name=command: self._trigger(name)
            )
            layout.addWidget(button, row, 0, 1, 2)
        return box

    def _positions_box(self) -> QGroupBox:
        box = QGroupBox("预设姿态")
        layout = QGridLayout(box)
        labels = (("待机位", "standby"), ("待抓取位", "pregrasp"), ("放置位", "placement"))
        for row, (label, name) in enumerate(labels):
            layout.addWidget(QLabel(label), row, 0)
            record = QPushButton("录制")
            record.clicked.connect(lambda _checked=False, service="record_" + name: self._trigger(service))
            go = QPushButton("前往")
            go.clicked.connect(lambda _checked=False, service="go_" + name: self._trigger(service))
            layout.addWidget(record, row, 1)
            layout.addWidget(go, row, 2)
        return box

    def _demo_box(self) -> QGroupBox:
        box = QGroupBox("示教录制")
        layout = QHBoxLayout(box)
        start = QPushButton("开始示教录制")
        start.clicked.connect(lambda: self._trigger("start_demo_recording"))
        stop = QPushButton("停止并保存")
        stop.clicked.connect(lambda: self._trigger("stop_demo_recording"))
        layout.addWidget(start)
        layout.addWidget(stop)
        return box

    def _parameter_box(self) -> QGroupBox:
        box = QGroupBox("Orin 参数（修改后立即生效并持久保存）")
        layout = QFormLayout(box)
        for name, default in PARAMETERS.items():
            widget: QWidget
            if isinstance(default, bool):
                checkbox = QCheckBox()
                checkbox.setChecked(default)
                widget = checkbox
            elif isinstance(default, int):
                spin = QSpinBox()
                spin.setRange(-100000, 100000)
                spin.setValue(default)
                widget = spin
            elif isinstance(default, float):
                spin = QDoubleSpinBox()
                spin.setDecimals(4)
                spin.setRange(-100000.0, 100000.0)
                spin.setValue(default)
                widget = spin
            else:
                widget = QLineEdit(str(default))
            self.parameter_widgets[name] = widget
            layout.addRow(name, widget)
        apply = QPushButton("应用并保存到 Orin")
        apply.clicked.connect(self._apply_parameters)
        layout.addRow(apply)
        return box

    def _spin_and_refresh(self) -> None:
        if not rclpy.ok():
            self._timer.stop()
            return
        try:
            rclpy.spin_once(self.client, timeout_sec=0.0)
        except Exception as exc:
            if not rclpy.ok() or "context is not valid" in str(exc):
                self._timer.stop()
                return
            raise
        self._refresh_devices()
        self._refresh_status()
        self._refresh_image()

    def _refresh_devices(self) -> None:
        known = [self.device_combo.itemData(index) for index in range(self.device_combo.count())]
        for namespace, status in self.client.devices.items():
            if namespace in known:
                continue
            text = f"{status.hostname or 'Orin'} {status.ipv4 or ''} ({namespace})"
            self.device_combo.addItem(text, namespace)

    def _refresh_status(self) -> None:
        status = self.client.status
        if status is None:
            return
        self.state_label.setText(status.state)
        hardware = "模型:{0} 相机:{1} 机械臂:{2} 校准:{3} 扭矩:{4}".format(
            (
                f"就绪({status.detector_device}, {status.detector_inference_ms:.0f} ms)"
                if status.model_ready
                else "加载中/错误"
            ),
            "就绪" if status.camera_ready else "不可用",
            "已连接" if status.arm_connected else "未连接",
            "有效" if status.arm_calibrated else "未完成",
            "开启" if status.torque_enabled else "关闭",
        )
        self.hardware_label.setText(hardware)
        self.calibration_label.setText(
            f"{status.calibration_state}: {status.calibration_message}"
        )
        if not status.tof_enabled:
            tof_text = "未启用"
        elif status.tof_ready:
            mode = "控制" if status.tof_control_enabled else "影子"
            tof_text = (
                f"{status.tof_range_m:.3f} m，{mode}，"
                f"{status.tof_decision}，年龄 {status.tof_age_sec:.2f} s"
            )
        else:
            tof_text = f"无效：{status.tof_state}，决策 {status.tof_decision}"
        self.tof_label.setText(tof_text)
        self.target_label.setText(status.target or "-")
        self.message_label.setText(status.message)
        self.torque.blockSignals(True)
        self.torque.setChecked(status.torque_enabled)
        self.torque.blockSignals(False)

    def _refresh_image(self) -> None:
        message = self.client.image
        if message is None or message.header.stamp == self._last_image_stamp:
            return
        self._last_image_stamp = message.header.stamp
        image = QImage.fromData(bytes(message.data))
        if image.isNull():
            self.video_status.setText("机械臂相机图像解码失败")
            return
        first_frame = self._frame_size == (0, 0)
        self._frame_size = (image.width(), image.height())
        self.video.set_frame_size(image.width(), image.height())
        if first_frame:
            self._sync_alignment_controls()
        image = image.convertToFormat(QImage.Format_RGB888)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        target_x = image.width() / 2.0 + self._parameter_float(
            "center_offset_x",
            0.0,
        )
        target_y = image.height() / 2.0 + self._parameter_float(
            "center_offset_y",
            0.0,
        )
        painter.setPen(QPen(QColor(255, 196, 0), 3))
        painter.drawLine(int(target_x - 12), int(target_y), int(target_x + 12), int(target_y))
        painter.drawLine(int(target_x), int(target_y - 12), int(target_x), int(target_y + 12))
        status = self.client.status
        if (
            status is not None
            and status.target
            and status.bbox_width > 0
            and status.bbox_height > 0
        ):
            painter.setPen(QPen(QColor(0, 220, 90), 3))
            painter.drawRect(
                status.bbox_x,
                status.bbox_y,
                status.bbox_width,
                status.bbox_height,
            )
            painter.drawText(
                status.bbox_x,
                max(22, status.bbox_y - 8),
                f"{status.target} {status.confidence:.2f}",
            )
            anchor_x = status.bbox_x + status.bbox_width * self._parameter_float(
                "detection_anchor_x_ratio",
                0.5,
            )
            anchor_y = status.bbox_y + status.bbox_height * self._parameter_float(
                "detection_anchor_y_ratio",
                0.5,
            )
            painter.setBrush(QColor(0, 220, 90))
            painter.drawEllipse(int(anchor_x - 6), int(anchor_y - 6), 12, 12)
        painter.end()
        pixmap = QPixmap.fromImage(image)
        self.video.setPixmap(
            pixmap.scaled(
                self.video.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )
        now = time.monotonic()
        self._image_times.append(now)
        while self._image_times and now - self._image_times[0] > 2.0:
            self._image_times.popleft()
        fps = 0.0
        if len(self._image_times) >= 2:
            fps = (len(self._image_times) - 1) / max(
                0.001,
                self._image_times[-1] - self._image_times[0],
            )
        camera_status = self.client.camera_status
        capture_mode = (
            "原生 MJPEG 直传"
            if camera_status.get("capture_mode") == "native_mjpeg"
            else "MJPEG 兼容模式"
        )
        source_fps = float(camera_status.get("capture_fps", 0.0))
        self.video_status.setText(
            f"已连接 · {image.width()}×{image.height()} · 显示 {fps:.1f} FPS · "
            f"采集 {source_fps:.1f} FPS · {capture_mode}"
        )

    def _parameter_float(self, name: str, default: float) -> float:
        widget = self.parameter_widgets.get(name)
        if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
            return float(widget.value())
        return float(default)

    def _toggle_servo_point_selection(self, enabled: bool) -> None:
        if enabled and self._frame_size == (0, 0):
            self.pick_servo_point.blockSignals(True)
            self.pick_servo_point.setChecked(False)
            self.pick_servo_point.blockSignals(False)
            self._show_message("请先等待机械臂相机画面")
            return
        self.video.set_selection_enabled(enabled)
        self.pick_servo_point.setText(
            "请点击黄色十字最终应在的位置"
            if enabled
            else "点击画面选择黄色对齐点"
        )

    def _set_servo_point(self, frame_x: int, frame_y: int) -> None:
        width, height = self._frame_size
        if width <= 0 or height <= 0:
            return
        offset_x = float(frame_x - width / 2.0)
        offset_y = float(frame_y - height / 2.0)
        self._set_alignment_parameters(
            {
                "center_offset_x": offset_x,
                "center_offset_y": offset_y,
                "auto_lock_vertical_center_on_pregrasp": False,
            },
            f"黄色对齐点已保存为 ({frame_x}, {frame_y})",
        )
        self.pick_servo_point.setChecked(False)

    def _use_current_detection_anchor(self) -> None:
        status = self.client.status
        if status is None or status.bbox_width <= 0 or status.bbox_height <= 0:
            self._show_message("当前没有稳定的检测框")
            return
        ratio_x, ratio_y = self._selected_detection_anchor()
        self._set_servo_point(
            round(status.bbox_x + status.bbox_width * ratio_x),
            round(status.bbox_y + status.bbox_height * ratio_y),
        )

    def _reset_servo_point(self) -> None:
        self._set_alignment_parameters(
            {
                "center_offset_x": 0.0,
                "center_offset_y": 0.0,
                "auto_lock_vertical_center_on_pregrasp": False,
            },
            "黄色对齐点已恢复为画面中心",
        )

    def _selected_detection_anchor(self) -> tuple[float, float]:
        value = self.detection_anchor_combo.currentData()
        if isinstance(value, tuple) and len(value) == 2:
            return float(value[0]), float(value[1])
        return 0.5, 0.5

    def _detection_anchor_changed(self, _index: int) -> None:
        ratio_x, ratio_y = self._selected_detection_anchor()
        self._set_alignment_parameters(
            {
                "detection_anchor_x_ratio": ratio_x,
                "detection_anchor_y_ratio": ratio_y,
            },
            f"绿色检测锚点已切换为：{self.detection_anchor_combo.currentText()}",
        )

    def _vertical_servo_changed(self, enabled: bool) -> None:
        self._set_alignment_parameters(
            {"centering_tilt_motion_enabled": bool(enabled)},
            "视觉伺服将同时进行左右和上下对齐"
            if enabled
            else "视觉伺服仅进行左右对齐",
        )

    def _set_alignment_parameters(self, values: dict, success_message: str) -> None:
        parameters = [Parameter(name, value=value) for name, value in values.items()]
        future = self.client.parameter_client.set_parameters(parameters)
        future.add_done_callback(
            lambda completed: self._alignment_parameters_applied(
                completed,
                values,
                success_message,
            )
        )

    def _alignment_parameters_applied(
        self,
        future,
        values: dict,
        success_message: str,
    ) -> None:
        try:
            results = future.result().results
            failures = [result.reason for result in results if not result.successful]
        except Exception as exc:
            self._show_message(f"保存视觉伺服对齐点失败：{exc}")
            return
        if failures:
            self._show_message("；".join(failures))
            return
        for name, value in values.items():
            widget = self.parameter_widgets.get(name)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                widget.setValue(value)
        self.servo_point_status.setText(success_message)
        self._show_message(success_message)

    def _select_discovered_device(self, index: int) -> None:
        namespace = self.device_combo.itemData(index)
        if namespace:
            self.namespace_edit.setText(namespace)
            self._apply_namespace()

    def _apply_namespace(self) -> None:
        self.client.set_namespace(self.namespace_edit.text())
        self._load_parameters()

    def _set_target(self) -> None:
        target = self.target_edit.text().strip()
        if not target:
            self._show_message("请输入 YOLO World 目标文本")
            return
        request = SetTarget.Request()
        request.target = target
        self._call(self.client.set_target, request)

    def _set_gripper(self) -> None:
        request = SetGripper.Request()
        request.position = float(self.gripper.value())
        self._call(self.client.set_gripper, request)

    def _set_torque(self, enabled: bool) -> None:
        request = SetBool.Request()
        request.data = enabled
        self._call(self.client.set_torque, request)

    def _trigger(self, name: str) -> None:
        self._call(self.client.triggers[name], Trigger.Request())

    def _call(self, client, request) -> None:
        if not client.wait_for_service(timeout_sec=0.2):
            self._show_message("Orin 服务不可用；检查 ROS_DOMAIN_ID、命名空间和节点状态")
            return
        future = client.call_async(request)
        future.add_done_callback(self._service_done)

    def _service_done(self, future) -> None:
        try:
            response = future.result()
            self._show_message(response.message)
        except Exception as exc:
            self._show_message(f"远程调用失败: {exc}")

    def _load_parameters(self) -> None:
        future = self.client.parameter_client.get_parameters(list(PARAMETERS))
        future.add_done_callback(self._parameters_loaded)

    def refresh_remote(self) -> None:
        """Reload parameters after the Orin service becomes available."""
        self._load_parameters()

    def _parameters_loaded(self, future) -> None:
        try:
            values = future.result().values
        except Exception as exc:
            self._show_message(f"读取 Orin 参数失败: {exc}")
            return
        for name, value in zip(PARAMETERS, values):
            widget = self.parameter_widgets[name]
            if isinstance(widget, QCheckBox):
                widget.setChecked(value.bool_value)
            elif isinstance(widget, QSpinBox):
                widget.setValue(value.integer_value)
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(value.double_value)
            else:
                widget.setText(value.string_value)
        self._sync_alignment_controls()

    def _sync_alignment_controls(self) -> None:
        ratio_x = self._parameter_float("detection_anchor_x_ratio", 0.5)
        ratio_y = self._parameter_float("detection_anchor_y_ratio", 0.5)
        best_index = min(
            range(self.detection_anchor_combo.count()),
            key=lambda index: abs(
                float(self.detection_anchor_combo.itemData(index)[0]) - ratio_x
            )
            + abs(float(self.detection_anchor_combo.itemData(index)[1]) - ratio_y),
        )
        self.detection_anchor_combo.blockSignals(True)
        self.detection_anchor_combo.setCurrentIndex(best_index)
        self.detection_anchor_combo.blockSignals(False)
        vertical_widget = self.parameter_widgets.get("centering_tilt_motion_enabled")
        vertical_enabled = (
            vertical_widget.isChecked()
            if isinstance(vertical_widget, QCheckBox)
            else False
        )
        self.enable_vertical_servo.blockSignals(True)
        self.enable_vertical_servo.setChecked(vertical_enabled)
        self.enable_vertical_servo.blockSignals(False)
        width, height = self._frame_size
        if width > 0 and height > 0:
            point_x = width / 2.0 + self._parameter_float("center_offset_x", 0.0)
            point_y = height / 2.0 + self._parameter_float("center_offset_y", 0.0)
            self.servo_point_status.setText(
                f"黄色对齐点 ({point_x:.0f}, {point_y:.0f})；"
                f"绿色点使用{self.detection_anchor_combo.currentText()}。"
            )

    def _apply_parameters(self) -> None:
        parameters = []
        for name, widget in self.parameter_widgets.items():
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                value = widget.value()
            else:
                value = widget.text().strip()
            parameters.append(Parameter(name, value=value))
        future = self.client.parameter_client.set_parameters(parameters)
        future.add_done_callback(self._parameters_applied)

    def _parameters_applied(self, future) -> None:
        try:
            results = future.result().results
            failures = [result.reason for result in results if not result.successful]
            self._show_message("参数已保存到 Orin" if not failures else "; ".join(failures))
        except Exception as exc:
            self._show_message(f"保存 Orin 参数失败: {exc}")

    def _show_message(self, message: str) -> None:
        self.message.emit(message)

    def set_advanced(self, enabled: bool) -> None:
        self.parameter_scroll.setVisible(bool(enabled))

    def shutdown(self) -> None:
        self._timer.stop()


class VisualGraspWindow(QMainWindow):
    def __init__(self, client: RemoteClient):
        super().__init__()
        self.setWindowTitle("Project LINK YOLO World 远程抓取")
        self.resize(1380, 860)
        self.panel = VisualGraspPanel(client, show_advanced_parameters=True)
        self.panel.message.connect(lambda message: self.statusBar().showMessage(message, 6000))
        self.setCentralWidget(self.panel)

    def closeEvent(self, event) -> None:
        self.panel.shutdown()
        event.accept()


def main(args=None) -> None:
    rclpy.init(args=args)
    app = QApplication(sys.argv)
    client = RemoteClient()
    window = VisualGraspWindow(client)
    window.show()
    try:
        app.exec()
    finally:
        client.destroy_node()
        rclpy.shutdown()
