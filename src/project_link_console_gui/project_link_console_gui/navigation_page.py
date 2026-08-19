"""Navigation and mapping page for the Project LINK console."""

from __future__ import annotations

from collections import deque
import math
import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .map_view import MapView
from .models import GridLayer, Pose2D
from .teleop_pad import TeleopPad


FUNCTION_LABELS = {
    "project-link-console-agent.service": "中控通信代理",
    "project-link-base.service": "底盘串口与里程计",
    "project-link-lidar.service": "Unitree L1 三维激光雷达",
    "project-link-front-camera.service": "车头 720P 摄像头",
    "project-link-fall-response.service": "跌倒检测响应",
    "project-link-wechatbot.service": "微信紧急通知",
    "project-link-robot-description.service": "机器人模型与传感器坐标",
    "project-link-scan.service": "二维雷达扫描",
    "project-link-point-lio-map.service": "Point-LIO 定位与实时建图",
    "project-link-nav2.service": "Navigation2 路径规划与导航",
    "project-link-rf2o-fallback.service": "rf2o 备用定位",
    "project-link-visual-grasp.service": "机械臂视觉抓取服务",
    "project-link-visual-grasp-detector.service": "机械臂 CUDA 目标检测",
    "project-link-vl53l0x.service": "夹爪距离传感器",
    "project-link-voice-classic.service": "经典语音链路",
    "project-link-voice-qwen.service": "Qwen Realtime 语音",
    "project-link-uwb-shadow.service": "UWB 影子模式",
    "project-link-platform.target": "底盘与传感器基础平台",
    "project-link-mapping.target": "建图模式总流程",
    "project-link-navigation.target": "Navigation2 模式总流程",
    "project-link-rf2o-fallback.target": "rf2o 备用模式总流程",
    "project-link-emergency.target": "紧急响应总流程",
}

SIMPLE_UNITS = {
    "project-link-console-agent.service",
    "project-link-base.service",
    "project-link-lidar.service",
    "project-link-front-camera.service",
    "project-link-robot-description.service",
    "project-link-scan.service",
    "project-link-point-lio-map.service",
    "project-link-nav2.service",
    "project-link-visual-grasp.service",
    "project-link-voice-classic.service",
    "project-link-voice-qwen.service",
}


class CameraPreview(QLabel):
    """Aspect-ratio preserving preview that resizes from the latest source frame."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self._source = QPixmap()
        self._aspect_ratio = 16.0 / 9.0
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumSize(280, 158)
        self.setMaximumHeight(240)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(135, int(width / max(0.1, self._aspect_ratio)))

    def sizeHint(self) -> QSize:
        return QSize(360, self.heightForWidth(360))

    def set_image(self, image: QImage) -> None:
        self._aspect_ratio = image.width() / max(1, image.height())
        self._source = QPixmap.fromImage(image)
        self._render()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if not self._source.isNull():
            super().setPixmap(
                self._source.scaled(
                    self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )


class StackProgressDialog(QDialog):
    """Non-blocking, operator-readable lifecycle progress and step log."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumWidth(560)
        self._last_line = ""
        layout = QVBoxLayout(self)
        self.description = QLabel("正在向 Orin 提交启动请求…")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(2)
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        self.log.setMinimumHeight(210)
        layout.addWidget(self.log)
        self.close_button = QPushButton("启动完成后可关闭")
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.accept)
        layout.addWidget(self.close_button)
        self.append("准备", "正在向 Orin 提交请求")

    def append(self, step: str, message: str) -> None:
        line = f"{step}：{message}" if step else message
        if line == self._last_line:
            return
        self._last_line = line
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {line}")
        self.description.setText(message)

    def update_progress(self, event: dict) -> None:
        state = str(event.get("state", "running"))
        progress = max(0.0, min(1.0, float(event.get("progress", 0.0))))
        message = str(event.get("message", ""))
        step = str(event.get("step", ""))
        self.progress.setValue(round(progress * 100.0))
        self.append(step, message)
        if state in {"complete", "failed"}:
            self.close_button.setEnabled(True)
            self.close_button.setText("关闭")
            if state == "complete":
                self.progress.setValue(100)
                self.description.setText("启动流程已完成")
            else:
                self.description.setText("启动失败：" + message)

    @property
    def running(self) -> bool:
        return not self.close_button.isEnabled()

    def reject(self) -> None:
        if self.running:
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.running:
            event.ignore()
            return
        super().closeEvent(event)


class NavigationPage(QWidget):
    launch_rviz_requested = Signal()
    launch_lidar_calibration_rviz_requested = Signal()

    MODE_OFF = 0
    MODE_MAPPING = 1
    MODE_NAVIGATION = 2
    MODE_RF2O = 3

    def __init__(self, bridge, config_client=None, parent=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._config_client = config_client
        self._mode = self.MODE_OFF
        self._connected = False
        self._pending_goal: Pose2D | None = None
        self._advanced = False
        self._last_state: dict = {}
        self._progress_dialog: StackProgressDialog | None = None
        self._camera_frames: deque[float] = deque(maxlen=120)
        self._lidar_saved_rpy_rad = (
            -math.pi / 2.0,
            -0.0383972435,
            math.pi / 2.0,
        )
        self._lidar_pending_rpy_rad: tuple[float, float, float] | None = None
        self._lidar_restart_pending = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("建图与导航")
        title.setObjectName("pageTitle")
        self.mode_badge = QLabel("系统关闭")
        self.mode_badge.setObjectName("modeBadge")
        title_row.addWidget(title)
        title_row.addWidget(self.mode_badge)
        title_row.addStretch()
        self.rviz_button = QPushButton("打开 RViz2 深度调试")
        self.rviz_button.clicked.connect(self.launch_rviz_requested)
        title_row.addWidget(self.rviz_button)
        root.addLayout(title_row)

        actions = QHBoxLayout()
        self.mapping_button = QPushButton("开始建图")
        self.navigation_button = QPushButton("开始 Navigation2")
        self.mapping_only_button = QPushButton("停止导航，保留建图")
        self.stop_button = QPushButton("全部停止")
        self.emergency_button = QPushButton("紧急停车")
        self.emergency_button.setObjectName("dangerButton")
        self.mapping_button.clicked.connect(
            lambda: self._request_stack(1, "启动建图模式")
        )
        self.navigation_button.clicked.connect(
            lambda: self._request_stack(2, "启动 Navigation2")
        )
        self.mapping_only_button.clicked.connect(
            lambda: self._request_stack(4, "停止导航并保留建图")
        )
        self.stop_button.clicked.connect(
            lambda: self._request_stack(5, "停止建图与导航")
        )
        self.emergency_button.clicked.connect(bridge.emergency_stop)
        for button in (
            self.mapping_button,
            self.navigation_button,
            self.mapping_only_button,
            self.stop_button,
            self.emergency_button,
        ):
            actions.addWidget(button)
        actions.addStretch()
        root.addLayout(actions)

        splitter = QSplitter(Qt.Horizontal)
        self.map_view = MapView()
        splitter.addWidget(self.map_view)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side_scroll.setMinimumWidth(330)
        side_scroll.setMaximumWidth(450)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(4, 0, 4, 0)

        camera_group = QGroupBox("车头摄像头 · 原生 720P/16:9")
        camera_layout = QVBoxLayout(camera_group)
        self.front_camera_preview = CameraPreview("等待 Orin 车头摄像头画面")
        self.front_camera_preview.setStyleSheet(
            "background: #090b0e; border: 1px solid #343b45; color: #7f8995;"
        )
        self.front_camera_preview.setToolTip(
            "Orin 独占 /dev/project_link_front_camera，Ubuntu 仅渲染 1280×720 压缩图像"
        )
        self.front_camera_status = QLabel("未收到 /front_camera/image/compressed")
        self.front_camera_status.setStyleSheet("color: #8e98a5;")
        camera_layout.addWidget(self.front_camera_preview)
        camera_layout.addWidget(self.front_camera_status)
        side_layout.addWidget(camera_group)

        layer_group = QGroupBox("显示图层")
        layer_layout = QGridLayout(layer_group)
        layers = [
            ("occupancy_map", "实时地图", True),
            ("global_costmap", "全局代价地图", False),
            ("local_costmap", "局部代价地图", True),
            ("laser_scan", "雷达扫描", True),
            ("point_cloud", "雷达点云", False),
            ("path", "导航路径", True),
        ]
        self.layer_checks: dict[str, QCheckBox] = {}
        for index, (name, label, checked) in enumerate(layers):
            checkbox = QCheckBox(label)
            checkbox.setChecked(checked)
            checkbox.toggled.connect(
                lambda visible, layer=name: self.map_view.set_layer_visible(layer, visible)
            )
            if name == "point_cloud" and hasattr(bridge, "set_cloud_enabled"):
                checkbox.toggled.connect(bridge.set_cloud_enabled)
            self.layer_checks[name] = checkbox
            layer_layout.addWidget(checkbox, index // 2, index % 2)
        side_layout.addWidget(layer_group)

        goal_group = QGroupBox("点选导航目标")
        goal_layout = QGridLayout(goal_group)
        self.select_goal_button = QPushButton("在地图上选点")
        self.select_goal_button.setCheckable(True)
        self.select_goal_button.toggled.connect(self.map_view.set_goal_selection_enabled)
        self.goal_label = QLabel("尚未选点")
        self.goal_yaw = QDoubleSpinBox()
        self.goal_yaw.setRange(-180.0, 180.0)
        self.goal_yaw.setSuffix(" °")
        self.goal_yaw.setValue(0.0)
        self.send_goal_button = QPushButton("确认并发送目标")
        self.send_goal_button.setEnabled(False)
        self.send_goal_button.clicked.connect(self._send_goal)
        goal_layout.addWidget(self.select_goal_button, 0, 0, 1, 2)
        goal_layout.addWidget(self.goal_label, 1, 0, 1, 2)
        goal_layout.addWidget(QLabel("目标朝向"), 2, 0)
        goal_layout.addWidget(self.goal_yaw, 2, 1)
        goal_layout.addWidget(self.send_goal_button, 3, 0, 1, 2)
        self.map_view.goal_selected.connect(self._goal_selected)
        side_layout.addWidget(goal_group)

        self.teleop = TeleopPad()
        self.teleop.setToolTip("备选 GUI 遥控；日常人工驾驶优先使用 STM32 原生手柄")
        self.teleop.command_requested.connect(bridge.send_teleop)
        self.teleop.setVisible(False)
        side_layout.addWidget(self.teleop)

        self.advanced_group = QGroupBox("高级参数")
        advanced_layout = QGridLayout(self.advanced_group)
        self.linear_speed = QDoubleSpinBox()
        self.linear_speed.setRange(0.02, 0.18)
        self.linear_speed.setSingleStep(0.01)
        self.linear_speed.setValue(0.12)
        self.linear_speed.setSuffix(" m/s")
        self.angular_speed = QDoubleSpinBox()
        self.angular_speed.setRange(0.10, 0.60)
        self.angular_speed.setSingleStep(0.05)
        self.angular_speed.setValue(0.45)
        self.angular_speed.setSuffix(" rad/s")
        self.linear_speed.valueChanged.connect(self._update_teleop_speeds)
        self.angular_speed.valueChanged.connect(self._update_teleop_speeds)
        advanced_layout.addWidget(QLabel("建图遥控线速度"), 0, 0)
        advanced_layout.addWidget(self.linear_speed, 0, 1)
        advanced_layout.addWidget(QLabel("建图遥控角速度"), 1, 0)
        advanced_layout.addWidget(self.angular_speed, 1, 1)
        note = QLabel("GUI 发送 20 Hz 租约；Orin 负责限幅、模式门控和 250 ms 超时停车。")
        note.setWordWrap(True)
        advanced_layout.addWidget(note, 2, 0, 1, 2)
        self.camera_auto_exposure = QCheckBox("自动曝光（弱光时可能降低帧率）")
        self.camera_exposure = QSpinBox()
        self.camera_exposure.setRange(1, 5000)
        self.camera_exposure.setValue(300)
        self.camera_exposure.setSuffix(" 级")
        self.camera_gain = QSpinBox()
        self.camera_gain.setRange(0, 63)
        self.camera_gain.setValue(32)
        self.camera_apply = QPushButton("立即应用车头曝光")
        self.camera_apply.clicked.connect(self._apply_camera_exposure)
        self.camera_auto_exposure.toggled.connect(self._camera_auto_changed)
        self.camera_config_status = QLabel("固定曝光可保持约 30 FPS；长期默认值可在全局设置保存。")
        self.camera_config_status.setWordWrap(True)
        advanced_layout.addWidget(self.camera_auto_exposure, 3, 0, 1, 2)
        advanced_layout.addWidget(QLabel("手动曝光"), 4, 0)
        advanced_layout.addWidget(self.camera_exposure, 4, 1)
        advanced_layout.addWidget(QLabel("画面增益"), 5, 0)
        advanced_layout.addWidget(self.camera_gain, 5, 1)
        advanced_layout.addWidget(self.camera_apply, 6, 0, 1, 2)
        advanced_layout.addWidget(self.camera_config_status, 7, 0, 1, 2)

        lidar_calibration = QGroupBox("雷达方向可视化标定")
        lidar_layout = QGridLayout(lidar_calibration)
        self.lidar_axis_sliders: dict[str, QSlider] = {}
        self.lidar_axis_degrees: dict[str, QDoubleSpinBox] = {}
        for row, (axis, label) in enumerate(
            (("roll", "横滚 Roll"), ("pitch", "俯仰 Pitch"), ("yaw", "航向 Yaw"))
        ):
            slider = QSlider(Qt.Horizontal)
            slider.setRange(-1800, 1800)
            slider.setSingleStep(10)
            degrees = QDoubleSpinBox()
            degrees.setRange(-180.0, 180.0)
            degrees.setDecimals(1)
            degrees.setSingleStep(1.0)
            degrees.setSuffix(" °")
            slider.valueChanged.connect(
                lambda value, name=axis: self._set_lidar_axis(
                    name, value / 10.0, source="slider"
                )
            )
            degrees.valueChanged.connect(
                lambda value, name=axis: self._set_lidar_axis(
                    name, value, source="spin"
                )
            )
            self.lidar_axis_sliders[axis] = slider
            self.lidar_axis_degrees[axis] = degrees
            lidar_layout.addWidget(QLabel(label), row * 2, 0)
            lidar_layout.addWidget(degrees, row * 2, 1)
            lidar_layout.addWidget(slider, row * 2 + 1, 0, 1, 2)
        self.lidar_reset_preview = QPushButton("恢复已保存方向")
        self.lidar_open_rviz = QPushButton("打开 RViz2 三轴实时标定")
        self.lidar_save_apply = QPushButton("保存并应用方向")
        self.lidar_reset_preview.clicked.connect(self._reset_lidar_preview)
        self.lidar_open_rviz.clicked.connect(self._open_lidar_calibration_rviz)
        self.lidar_save_apply.clicked.connect(self._save_lidar_calibration)
        self.lidar_calibration_status = QLabel(
            "拖动三轴参数只改变 Ubuntu 的独立预览 TF，不会修改生产 TF 或控制机器人。"
        )
        self.lidar_calibration_status.setWordWrap(True)
        lidar_layout.addWidget(self.lidar_open_rviz, 6, 0, 1, 2)
        lidar_layout.addWidget(self.lidar_reset_preview, 7, 0)
        lidar_layout.addWidget(self.lidar_save_apply, 7, 1)
        lidar_layout.addWidget(self.lidar_calibration_status, 8, 0, 1, 2)
        advanced_layout.addWidget(lidar_calibration, 8, 0, 1, 2)
        self.advanced_group.setVisible(False)
        side_layout.addWidget(self.advanced_group)
        side_layout.addStretch()
        side_scroll.setWidget(side)
        splitter.addWidget(side_scroll)
        splitter.setStretchFactor(0, 1)
        root.addWidget(splitter, 1)

        status_group = QGroupBox("功能运行状态")
        status_layout = QVBoxLayout(status_group)
        self.status_table = QTableWidget(0, 4)
        self.status_table.setHorizontalHeaderLabels(["功能", "模块名", "状态", "就绪"])
        self.status_table.horizontalHeader().setStretchLastSection(True)
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.status_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.status_table.setMaximumHeight(190)
        self.status_table.setColumnHidden(1, True)
        status_layout.addWidget(self.status_table)
        root.addWidget(status_group)

        bridge.stack_progress.connect(self._update_stack_progress)
        bridge.front_camera_parameters.connect(self._update_camera_parameters)
        bridge.front_camera_configured.connect(self._camera_configured)
        bridge.lifecycle_completed.connect(self._lidar_lifecycle_completed)
        if self._config_client is not None:
            self._config_client.loaded.connect(self._lidar_config_loaded)
            self._config_client.saved.connect(self._lidar_config_saved)
            self._config_client.failed.connect(self._lidar_config_failed)

    def _request_stack(self, operation: int, title: str, restart: bool = False) -> None:
        if self._progress_dialog is not None and self._progress_dialog.running:
            self._progress_dialog.raise_()
            self._progress_dialog.activateWindow()
            return
        self._progress_dialog = StackProgressDialog(title, self)
        self._progress_dialog.show()
        self._bridge.manage_stack(operation, restart)

    def _update_stack_progress(self, event: dict) -> None:
        if self._progress_dialog is None:
            self._progress_dialog = StackProgressDialog("建图与导航流程", self)
            self._progress_dialog.show()
        self._progress_dialog.update_progress(event)

    def set_advanced(self, enabled: bool) -> None:
        self._advanced = bool(enabled)
        self.teleop.setVisible(self._advanced)
        self.advanced_group.setVisible(self._advanced)
        self.status_table.setColumnHidden(1, not self._advanced)
        if self._advanced:
            self._bridge.request_front_camera_parameters()
            if self._config_client is not None:
                self._config_client.load("global")
        self._render_status()

    def _set_lidar_axis(self, axis: str, value_degrees: float, source: str) -> None:
        value = max(-180.0, min(180.0, float(value_degrees)))
        slider = self.lidar_axis_sliders[axis]
        degrees = self.lidar_axis_degrees[axis]
        if source != "slider":
            slider.blockSignals(True)
            slider.setValue(round(value * 10.0))
            slider.blockSignals(False)
        if source != "spin":
            degrees.blockSignals(True)
            degrees.setValue(value)
            degrees.blockSignals(False)
        self._update_lidar_preview()

    def _current_lidar_rpy(self) -> tuple[float, float, float]:
        return tuple(
            math.radians(self.lidar_axis_degrees[axis].value())
            for axis in ("roll", "pitch", "yaw")
        )

    def _update_lidar_preview(self) -> None:
        roll, pitch, yaw = self._current_lidar_rpy()
        self._bridge.set_lidar_preview_rpy(roll, pitch, yaw)
        self.lidar_calibration_status.setText(
            "仅预览 · "
            f"Roll {math.degrees(roll):+.1f}° / "
            f"Pitch {math.degrees(pitch):+.1f}° / "
            f"Yaw {math.degrees(yaw):+.1f}°"
        )

    @staticmethod
    def _normalize_angle(value: float) -> float:
        while value <= -math.pi:
            value += math.tau
        while value > math.pi:
            value -= math.tau
        return value

    def _set_lidar_controls(self, values: tuple[float, float, float]) -> None:
        for axis, radians_value in zip(("roll", "pitch", "yaw"), values):
            degrees_value = math.degrees(radians_value)
            self._set_lidar_axis(axis, degrees_value, source="config")

    def _reset_lidar_preview(self) -> None:
        self._set_lidar_controls(self._lidar_saved_rpy_rad)
        self.lidar_calibration_status.setText("已恢复当前保存的三轴方向")

    def _open_lidar_calibration_rviz(self) -> None:
        self._bridge.set_lidar_calibration_enabled(True)
        self._update_lidar_preview()
        self.launch_lidar_calibration_rviz_requested.emit()
        self.lidar_calibration_status.setText(
            "RViz2 标定已打开：红色为当前生产点云，绿色为三轴预览点云"
        )

    def _lidar_config_loaded(self, section: str, data: dict) -> None:
        if section != "global" or self._lidar_pending_rpy_rad is not None:
            return
        console = data.get("files", {}).get("console", {})
        try:
            self._lidar_saved_rpy_rad = tuple(
                self._normalize_angle(float(console[key].get("value", default)))
                for key, default in (
                    ("LIDAR_MOUNT_ROLL_RAD", "-1.5707963268"),
                    ("LIDAR_MOUNT_PITCH_RAD", "-0.0383972435"),
                    ("LIDAR_MOUNT_YAW_RAD", "1.5707963268"),
                )
            )
        except (TypeError, ValueError):
            self.lidar_calibration_status.setText("Orin 雷达方向配置无效")
            return
        self._set_lidar_controls(self._lidar_saved_rpy_rad)
        self.lidar_calibration_status.setText(
            "已读取 Orin 当前三轴方向；打开 RViz2 后可实时拖动预览"
        )

    def _save_lidar_calibration(self) -> None:
        if self._config_client is None:
            self.lidar_calibration_status.setText("配置通道不可用")
            return
        candidate = tuple(self._normalize_angle(value) for value in self._current_lidar_rpy())
        self._lidar_pending_rpy_rad = candidate
        self.lidar_save_apply.setEnabled(False)
        self.lidar_calibration_status.setText("正在保存到 Orin 白名单配置…")
        self._config_client.save(
            "global",
            {
                "files": {
                    "console": {
                        "LIDAR_MOUNT_ROLL_RAD": f"{candidate[0]:.10f}",
                        "LIDAR_MOUNT_PITCH_RAD": f"{candidate[1]:.10f}",
                        "LIDAR_MOUNT_YAW_RAD": f"{candidate[2]:.10f}",
                    }
                }
            },
        )

    def _lidar_config_saved(self, section: str, _data: dict) -> None:
        if section != "global" or self._lidar_pending_rpy_rad is None:
            return
        if self._mode == self.MODE_NAVIGATION:
            self._lidar_restart_pending = True
            self.lidar_calibration_status.setText("已保存，正在无目标重启建图与 Navigation2…")
            self._request_stack(2, "应用雷达方向并重启 Navigation2", restart=True)
        elif self._mode == self.MODE_MAPPING:
            self._lidar_restart_pending = True
            self.lidar_calibration_status.setText("已保存，正在无运动重启建图…")
            self._request_stack(1, "应用雷达方向并重启建图", restart=True)
        else:
            self._finish_lidar_apply("已保存；下次启动建图或导航时生效")

    def _lidar_config_failed(self, section: str, message: str) -> None:
        if section != "global" or self._lidar_pending_rpy_rad is None:
            return
        self._lidar_pending_rpy_rad = None
        self.lidar_save_apply.setEnabled(True)
        self.lidar_calibration_status.setText(f"保存失败：{message}")

    def _lidar_lifecycle_completed(self, action: str, success: bool) -> None:
        if action != "stack" or not self._lidar_restart_pending:
            return
        self._lidar_restart_pending = False
        if success:
            self._finish_lidar_apply("雷达方向已保存并应用")
        else:
            self.lidar_save_apply.setEnabled(True)
            self.lidar_calibration_status.setText("方向已保存，但节点重启失败；预览修正仍保留")

    def _finish_lidar_apply(self, message: str) -> None:
        if self._lidar_pending_rpy_rad is not None:
            self._lidar_saved_rpy_rad = self._lidar_pending_rpy_rad
        self._lidar_pending_rpy_rad = None
        self._set_lidar_controls(self._lidar_saved_rpy_rad)
        self.lidar_save_apply.setEnabled(True)
        self.lidar_calibration_status.setText(message)

    def _camera_auto_changed(self, automatic: bool) -> None:
        self.camera_exposure.setEnabled(not automatic)
        self.camera_gain.setEnabled(not automatic)

    def _apply_camera_exposure(self) -> None:
        self.camera_apply.setEnabled(False)
        self.camera_config_status.setText("正在应用到 Orin…")
        self._bridge.set_front_camera_exposure(
            self.camera_auto_exposure.isChecked(),
            self.camera_exposure.value(),
            self.camera_gain.value(),
        )

    def _update_camera_parameters(self, values: dict) -> None:
        automatic = bool(values.get("automatic", False))
        self.camera_auto_exposure.blockSignals(True)
        self.camera_auto_exposure.setChecked(automatic)
        self.camera_auto_exposure.blockSignals(False)
        self.camera_exposure.setValue(int(values.get("exposure", 300)))
        self.camera_gain.setValue(int(values.get("gain", 32)))
        self._camera_auto_changed(automatic)
        self.camera_apply.setEnabled(True)
        self.camera_config_status.setText(
            "自动曝光已启用" if automatic else "固定曝光已启用，可稳定保持满帧"
        )

    def _camera_configured(self, success: bool, message: str) -> None:
        self.camera_apply.setEnabled(True)
        self.camera_config_status.setText(message)

    def update_system_state(self, state: dict) -> None:
        self._last_state = state
        self._mode = int(state.get("mode", self.MODE_OFF))
        names = {
            self.MODE_OFF: "系统关闭",
            self.MODE_MAPPING: "建图模式",
            self.MODE_NAVIGATION: "Navigation2",
            self.MODE_RF2O: "rf2o 回退",
            4: "切换中",
            5: "故障",
        }
        self.mode_badge.setText(names.get(self._mode, state.get("mode_name", "未知")))
        self.teleop.set_mapping_mode(self._connected and self._mode == self.MODE_MAPPING)
        self.send_goal_button.setEnabled(
            self._connected
            and self._mode == self.MODE_NAVIGATION
            and self._pending_goal is not None
        )
        self._render_status()

    def _render_status(self) -> None:
        subsystems = list(self._last_state.get("subsystems", []))
        if not self._advanced:
            filtered = [item for item in subsystems if item.get("name") in SIMPLE_UNITS]
            if filtered:
                subsystems = filtered
        self.status_table.setRowCount(len(subsystems))
        for row, subsystem in enumerate(subsystems):
            module = str(subsystem.get("name", ""))
            function = FUNCTION_LABELS.get(
                module, str(subsystem.get("display_name") or module)
            )
            status = self._translate_status(
                str(subsystem.get("active_state", "")),
                str(subsystem.get("sub_state", "")),
            )
            values = [
                function,
                module,
                status,
                "是" if subsystem.get("ready") else "否",
            ]
            for column, value in enumerate(values):
                self.status_table.setItem(row, column, QTableWidgetItem(value))
        self.status_table.resizeColumnsToContents()
        self.status_table.horizontalHeader().setStretchLastSection(True)

    @staticmethod
    def _translate_status(active_state: str, sub_state: str) -> str:
        if active_state == "active":
            return "运行中" if sub_state == "running" else "已启用"
        if active_state == "activating":
            return "正在检查就绪条件" if sub_state == "start-post" else "正在启动"
        if active_state == "deactivating":
            return "正在停止"
        if active_state == "inactive":
            return "已停止"
        if active_state == "failed":
            return "故障"
        if active_state == "unknown":
            return "状态不可用"
        return "/".join(value for value in (active_state, sub_state) if value) or "未知"

    def set_connection_available(self, connected: bool) -> None:
        self._connected = bool(connected)
        self.teleop.set_mapping_mode(self._connected and self._mode == self.MODE_MAPPING)
        for button in (
            self.mapping_button,
            self.navigation_button,
            self.mapping_only_button,
            self.stop_button,
        ):
            button.setEnabled(self._connected)
        self.send_goal_button.setEnabled(
            self._connected
            and self._mode == self.MODE_NAVIGATION
            and self._pending_goal is not None
        )

    def update_grid(self, name: str, grid: GridLayer) -> None:
        self.map_view.set_grid(name, grid)

    def update_scan(self, points: list[tuple[float, float]]) -> None:
        self.map_view.set_scan(points)

    def update_cloud(self, points: list[tuple[float, float]]) -> None:
        self.map_view.set_cloud(points)

    def update_path(self, points: list[tuple[float, float]]) -> None:
        self.map_view.set_path(points)

    def update_robot(self, pose: Pose2D) -> None:
        self.map_view.set_robot(pose)

    def update_front_camera(self, jpeg_data: bytes) -> None:
        # PySide6 6.11 rejects the explicit format overload for Python bytes on
        # the Ubuntu runtime. Qt reliably auto-detects the JPEG signature.
        image = QImage.fromData(jpeg_data)
        if image.isNull():
            self.front_camera_status.setText("车头摄像头图像解码失败")
            return
        self.front_camera_preview.set_image(image)
        now = time.monotonic()
        self._camera_frames.append(now)
        while self._camera_frames and now - self._camera_frames[0] > 2.0:
            self._camera_frames.popleft()
        fps = 0.0
        if len(self._camera_frames) >= 2:
            fps = (len(self._camera_frames) - 1) / max(
                0.001, self._camera_frames[-1] - self._camera_frames[0]
            )
        divisor = math.gcd(image.width(), image.height())
        ratio = f"{image.width() // divisor}:{image.height() // divisor}"
        self.front_camera_status.setText(
            f"已连接 · {image.width()}×{image.height()} · {ratio} · {fps:.1f} FPS"
        )

    def _goal_selected(self, x: float, y: float) -> None:
        self._pending_goal = Pose2D(x, y, math.radians(self.goal_yaw.value()))
        self.map_view.set_goal(self._pending_goal)
        self.goal_label.setText(f"x={x:.2f} m，y={y:.2f} m")
        self.select_goal_button.setChecked(False)
        self.send_goal_button.setEnabled(
            self._connected and self._mode == self.MODE_NAVIGATION
        )

    def _send_goal(self) -> None:
        if not self._connected or self._pending_goal is None or self._mode != self.MODE_NAVIGATION:
            return
        self._pending_goal = Pose2D(
            self._pending_goal.x,
            self._pending_goal.y,
            math.radians(self.goal_yaw.value()),
        )
        self.map_view.set_goal(self._pending_goal)
        answer = QMessageBox.question(
            self,
            "确认导航",
            f"确认让机器人导航到 x={self._pending_goal.x:.2f}, y={self._pending_goal.y:.2f}？",
        )
        if answer == QMessageBox.Yes:
            self._bridge.send_navigation_goal(self._pending_goal)

    def _update_teleop_speeds(self) -> None:
        self.teleop.set_speeds(self.linear_speed.value(), self.angular_speed.value())
