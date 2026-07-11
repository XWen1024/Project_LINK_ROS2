"""Plain PySide6 remote GUI for the headless Project LINK visual grasp node."""
from __future__ import annotations

import sys
from typing import Callable

import cv2
import numpy as np
import rclpy
from rcl_interfaces.msg import Parameter as ParameterMessage
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.parameter_client import AsyncParameterClient
from sensor_msgs.msg import CompressedImage
from std_srvs.srv import SetBool, Trigger
from wheeltec_robot_msg.msg import VisualGraspStatus
from wheeltec_robot_msg.srv import SetGripper, SetTarget

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

PARAMETERS = {
    "camera_device": "/dev/RgbCam",
    "camera_width": 1280,
    "camera_height": 720,
    "camera_fps": 15.0,
    "preview_fps": 10.0,
    "jpeg_quality": 75,
    "model_path": "/home/wte/models/yolov8s-worldv2.pt",
    "yolo_conf_threshold": 0.15,
    "yolo_max_lost_frames": 15,
    "yolo_infer_interval_sec": 0.0,
    "yolo_ema_alpha": 0.6,
    "robot_port": "/dev/so101",
    "robot_id": "so101_slave",
    "pan_gain": 25.0,
    "tilt_gain": 15.0,
    "approach_step": 1.5,
    "centering_threshold": 0.04,
    "grasp_area_threshold": 0.45,
    "gripper_open": 70.0,
    "gripper_close": 0.0,
    "move_fps": 15.0,
    "arrive_threshold": 2.0,
    "move_step_limit": 3.0,
    "move_timeout_sec": 15.0,
    "center_offset_x": 143.0,
    "center_offset_y": 61.0,
    "action_default_timeout_sec": 45.0,
}


class RemoteClient(Node):
    def __init__(self) -> None:
        super().__init__("visual_grasp_gui")
        self.namespace = "/visual_grasp"
        self.devices: dict[str, VisualGraspStatus] = {}
        self.status: VisualGraspStatus | None = None
        self.image: CompressedImage | None = None
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
            1,
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
        self.parameter_client = AsyncParameterClient(self, root.lstrip("/"))
        self.triggers = {
            name: self.create_client(Trigger, root + "/" + name)
            for name in (
                "connect_arm", "disconnect_arm", "start_approach", "stop",
                "record_standby", "record_pregrasp", "record_placement",
                "go_standby", "go_pregrasp", "go_placement",
                "start_demo_recording", "stop_demo_recording",
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

    def _on_discovery(self, message: VisualGraspStatus) -> None:
        self.devices[message.robot_namespace] = message


class VisualGraspWindow(QMainWindow):
    def __init__(self, client: RemoteClient):
        super().__init__()
        self.client = client
        self.parameter_widgets: dict[str, QWidget] = {}
        self._last_image_stamp = None
        self.setWindowTitle("Project LINK YOLO World ????")
        self.resize(1380, 860)
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._spin_and_refresh)
        self._timer.start(30)
        QTimer.singleShot(500, self._load_parameters)

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self._device_box())

        body = QHBoxLayout()
        self.video = QLabel("?? Orin ???")
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setMinimumSize(760, 520)
        self.video.setFrameShape(QLabel.Box)
        body.addWidget(self.video, 3)

        controls = QVBoxLayout()
        controls.addWidget(self._status_box())
        controls.addWidget(self._tracking_box())
        controls.addWidget(self._arm_box())
        controls.addWidget(self._positions_box())
        controls.addWidget(self._demo_box())
        body.addLayout(controls, 2)
        layout.addLayout(body)

        parameter_scroll = QScrollArea()
        parameter_scroll.setWidgetResizable(True)
        parameter_scroll.setWidget(self._parameter_box())
        parameter_scroll.setMaximumHeight(250)
        layout.addWidget(parameter_scroll)
        self.setCentralWidget(root)

    def _device_box(self) -> QGroupBox:
        box = QGroupBox("????")
        layout = QHBoxLayout(box)
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._select_discovered_device)
        self.namespace_edit = QLineEdit("/visual_grasp")
        apply = QPushButton("??????")
        apply.clicked.connect(self._apply_namespace)
        refresh = QPushButton("????")
        refresh.clicked.connect(self._load_parameters)
        layout.addWidget(QLabel("????"))
        layout.addWidget(self.device_combo, 2)
        layout.addWidget(QLabel("??????"))
        layout.addWidget(self.namespace_edit, 1)
        layout.addWidget(apply)
        layout.addWidget(refresh)
        return box

    def _status_box(self) -> QGroupBox:
        box = QGroupBox("??")
        layout = QFormLayout(box)
        self.state_label = QLabel("???")
        self.hardware_label = QLabel("????")
        self.target_label = QLabel("-")
        self.message_label = QLabel("-")
        self.message_label.setWordWrap(True)
        layout.addRow("????", self.state_label)
        layout.addRow("??", self.hardware_label)
        layout.addRow("??", self.target_label)
        layout.addRow("??", self.message_label)
        return box

    def _tracking_box(self) -> QGroupBox:
        box = QGroupBox("YOLO World ?????")
        layout = QGridLayout(box)
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("???red cup")
        layout.addWidget(QLabel("????"), 0, 0)
        layout.addWidget(self.target_edit, 0, 1, 1, 3)
        controls = [
            ("????", self._set_target),
            ("????", lambda: self._trigger("start_approach")),
            ("????", lambda: self._trigger("stop")),
        ]
        for column, (text, callback) in enumerate(controls):
            button = QPushButton(text)
            button.clicked.connect(callback)
            layout.addWidget(button, 1, column)
        return box

    def _arm_box(self) -> QGroupBox:
        box = QGroupBox("SO-101")
        layout = QGridLayout(box)
        for column, (text, command) in enumerate((("??", "connect_arm"), ("??", "disconnect_arm"))):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, name=command: self._trigger(name))
            layout.addWidget(button, 0, column)
        self.torque = QCheckBox("????")
        self.torque.toggled.connect(self._set_torque)
        layout.addWidget(self.torque, 0, 2)
        self.gripper = QDoubleSpinBox()
        self.gripper.setRange(-100.0, 100.0)
        self.gripper.setValue(70.0)
        set_gripper = QPushButton("????")
        set_gripper.clicked.connect(self._set_gripper)
        layout.addWidget(QLabel("??"), 1, 0)
        layout.addWidget(self.gripper, 1, 1)
        layout.addWidget(set_gripper, 1, 2)
        return box

    def _positions_box(self) -> QGroupBox:
        box = QGroupBox("????")
        layout = QGridLayout(box)
        labels = (("???", "standby"), ("????", "pregrasp"), ("???", "placement"))
        for row, (label, name) in enumerate(labels):
            layout.addWidget(QLabel(label), row, 0)
            record = QPushButton("??")
            record.clicked.connect(lambda _checked=False, service="record_" + name: self._trigger(service))
            go = QPushButton("??")
            go.clicked.connect(lambda _checked=False, service="go_" + name: self._trigger(service))
            layout.addWidget(record, row, 1)
            layout.addWidget(go, row, 2)
        return box

    def _demo_box(self) -> QGroupBox:
        box = QGroupBox("????")
        layout = QHBoxLayout(box)
        start = QPushButton("??????")
        start.clicked.connect(lambda: self._trigger("start_demo_recording"))
        stop = QPushButton("?????")
        stop.clicked.connect(lambda: self._trigger("stop_demo_recording"))
        layout.addWidget(start)
        layout.addWidget(stop)
        return box

    def _parameter_box(self) -> QGroupBox:
        box = QGroupBox("Orin ????????????????")
        layout = QFormLayout(box)
        for name, default in PARAMETERS.items():
            widget: QWidget
            if isinstance(default, int):
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
        apply = QPushButton("?????? Orin")
        apply.clicked.connect(self._apply_parameters)
        layout.addRow(apply)
        return box

    def _spin_and_refresh(self) -> None:
        rclpy.spin_once(self.client, timeout_sec=0.0)
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
        hardware = "??:{0} ??:{1} ???:{2} ??:{3}".format(
            "??" if status.model_ready else "???/??",
            "??" if status.camera_ready else "???",
            "???" if status.arm_connected else "???",
            "??" if status.torque_enabled else "??",
        )
        self.hardware_label.setText(hardware)
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
        frame = cv2.imdecode(np.frombuffer(message.data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(image.copy())
        self.video.setPixmap(pixmap.scaled(self.video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

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
            self._show_message("??? YOLO World ????")
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
            self._show_message("Orin ???????? ROS_DOMAIN_ID??????????")
            return
        future = client.call_async(request)
        future.add_done_callback(self._service_done)

    def _service_done(self, future) -> None:
        try:
            response = future.result()
            self._show_message(response.message)
        except Exception as exc:
            self._show_message(f"??????: {exc}")

    def _load_parameters(self) -> None:
        future = self.client.parameter_client.get_parameters(list(PARAMETERS))
        future.add_done_callback(self._parameters_loaded)

    def _parameters_loaded(self, future) -> None:
        try:
            values = future.result().values
        except Exception as exc:
            self._show_message(f"?? Orin ????: {exc}")
            return
        for name, value in zip(PARAMETERS, values):
            widget = self.parameter_widgets[name]
            if isinstance(widget, QSpinBox):
                widget.setValue(value.integer_value)
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(value.double_value)
            else:
                widget.setText(value.string_value)

    def _apply_parameters(self) -> None:
        parameters = []
        for name, widget in self.parameter_widgets.items():
            if isinstance(widget, QSpinBox):
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
            results = future.result()
            failures = [result.reason for result in results if not result.successful]
            self._show_message("?????? Orin" if not failures else "; ".join(failures))
        except Exception as exc:
            self._show_message(f"?? Orin ????: {exc}")

    def _show_message(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)

    def closeEvent(self, event) -> None:
        self._timer.stop()
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