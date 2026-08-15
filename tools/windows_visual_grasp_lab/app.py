"""Standalone Windows console for exercising the complete visual grasp stack."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np
from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
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
    QSplitter,
    QTabWidget,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src" / "project_link_visual_grasp"))
sys.path.insert(0, str(REPO_ROOT / "src" / "project_link_vl53l0x"))

from project_link_visual_grasp.core import (  # noqa: E402
    ALL_JOINTS,
    ARM_JOINTS,
    DEMO_CSV_FIELDS,
    SO101Arm,
    ServoState,
    VisualServoController,
    YoloWorldTracker,
)

from hardware import (  # noqa: E402
    CameraCapture,
    DetailedDebugLogger,
    LabStore,
    TofSerialReader,
    map_display_point_to_frame,
)


def _default_model_path() -> str:
    candidates = [
        Path.home() / "Desktop" / "机器人项目" / "VisualTracker" / "yolov8s-worldv2.pt",
        REPO_ROOT / "models" / "yolov8s-worldv2.pt",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


CONTROL_PROFILE_VERSION = 6
CONTROL_PROFILE_KEYS = (
    "pan_direction",
    "tilt_direction",
    "centering_tilt_motion_enabled",
    "auto_lock_vertical_center_on_pregrasp",
    "auto_lock_vertical_center_offset_ratio",
    "approach_max_command_lead",
    "approach_profile_max_lift_delta",
    "approach_profile_elbow_delta",
    "approach_profile_wrist_delta",
    "approach_profile_wrist_trim",
    "visual_servo_max_joint_step",
    "visual_handoff_enabled",
    "visual_handoff_bbox_height_ratio",
    "visual_handoff_area_ratio",
    "visual_handoff_tof_m",
    "visual_handoff_max_tof_m",
    "final_grasp_tof_m",
    "final_approach_step",
    "final_approach_max_command_lead",
    "final_approach_max_lift_delta",
    "final_approach_command_interval_sec",
    "final_approach_timeout_sec",
    "final_approach_endpoint_settle_sec",
    "centering_limit_hold_cycles",
    "centering_error_window",
    "centering_min_samples",
    "centering_confirm_cycles",
    "centering_step_limit",
    "centering_min_step_limit",
    "centering_slow_zone",
    "centering_max_command_lead",
    "centering_command_interval_sec",
)

PARAMETER_LABELS = {
    "yolo_conf_threshold": "YOLO 置信度阈值",
    "yolo_max_lost_frames": "目标丢失容忍帧数",
    "yolo_infer_interval_sec": "YOLO 最小推理间隔（秒）",
    "yolo_ema_alpha": "检测框响应速度",
    "yolo_max_center_jump_ratio": "检测框最大中心跳变",
    "yolo_max_area_change_ratio": "检测框最大面积变化",
    "yolo_outlier_hold_frames": "异常框暂停帧数",
    "yolo_track_iou_weight": "上一目标关联权重",
    "pan_gain": "水平居中增益",
    "tilt_gain": "垂直居中增益",
    "pan_direction": "水平控制方向",
    "tilt_direction": "垂直控制方向",
    "centering_tilt_motion_enabled": "居中阶段允许肩关节伸缩",
    "auto_lock_vertical_center_on_pregrasp": "预抓取后自动锁定纵向中心",
    "auto_lock_vertical_center_offset_ratio": "绿色框目标点向下比例",
    "approach_step": "单次逼近步长",
    "approach_max_command_lead": "逼近目标最大领先量",
    "approach_profile_max_lift_delta": "试教水平逼近肩关节总行程",
    "approach_profile_elbow_delta": "试教水平逼近肘关节总变化",
    "approach_profile_wrist_delta": "试教水平逼近腕关节总变化",
    "approach_profile_wrist_trim": "抓取高度微调（腕终点）",
    "visual_servo_max_joint_step": "视觉伺服单次关节最大变化",
    "visual_handoff_enabled": "启用近距离视觉交接",
    "visual_handoff_bbox_height_ratio": "交接框高度比例",
    "visual_handoff_area_ratio": "交接框面积比例",
    "visual_handoff_tof_m": "交接 ToF 距离（米）",
    "visual_handoff_max_tof_m": "视觉近场交接最大 ToF（米）",
    "final_grasp_tof_m": "最终闭合 ToF 距离（米）",
    "final_approach_step": "盲区单次水平逼近步长",
    "final_approach_max_command_lead": "盲区命令最大领先量",
    "final_approach_max_lift_delta": "盲区最大肩关节行程",
    "final_approach_command_interval_sec": "盲区命令间隔（秒）",
    "final_approach_timeout_sec": "盲区逼近超时（秒）",
    "final_approach_endpoint_settle_sec": "终点反馈稳定等待（秒）",
    "centering_threshold": "居中允许误差",
    "centering_limit_hold_cycles": "限位前确认次数",
    "centering_error_window": "居中滤波窗口",
    "centering_min_samples": "动作前最少新检测数",
    "centering_confirm_cycles": "居中确认次数",
    "centering_step_limit": "远距离最大修正步长",
    "centering_min_step_limit": "近中心最小限速",
    "centering_slow_zone": "近中心减速区域",
    "centering_max_command_lead": "命令相对反馈最大领先量",
    "centering_command_interval_sec": "居中命令最小间隔（秒）",
    "grasp_area_threshold": "画面面积抓取阈值",
    "gripper_open": "夹爪打开位置",
    "gripper_close": "夹爪闭合位置",
    "arrive_threshold": "预设位到达误差",
    "elbow_arrive_threshold": "肘关节到达误差",
    "arrive_stable_margin": "稳定到达附加余量",
    "arrive_stable_delta": "稳定到达变化阈值",
    "arrive_stable_cycles": "稳定到达确认次数",
    "move_step_limit": "预设移动步长",
    "move_timeout_sec": "预设移动超时（秒）",
    "grasp_timeout_sec": "自动抓取总超时（秒）",
    "joint_command_limit": "视觉伺服关节软限位",
    "preset_joint_limit": "普通预设位关节限位",
    "standby_joint_limit": "待机位关节限位",
    "center_offset_x": "目标框水平对齐偏移（像素）",
    "center_offset_y": "目标框垂直对齐偏移（像素）",
    "tof_stale_timeout_sec": "ToF 数据过期时间（秒）",
    "tof_filter_window": "ToF 滤波窗口",
    "tof_min_valid_samples": "ToF 最少有效样本",
    "tof_grasp_distance_m": "ToF 抓取距离（米）",
}

PARAMETER_HELP = {
    "yolo_infer_interval_sec": "建议保持 0。控制器现在只响应新的推理结果，不需要靠降频防抖。",
    "yolo_ema_alpha": "越大越灵敏，越小越平滑；建议先保持 0.6。",
    "centering_step_limit": "大偏差时的最大步长；当前推荐 1.5。",
    "centering_min_step_limit": "接近中心时限制小步修正，减少来回摆动。",
    "centering_slow_zone": "误差进入该范围后逐步减速。",
    "centering_max_command_lead": "允许小步目标累积以克服电机死区，但最多领先实际反馈 4。",
    "approach_max_command_lead": "逼近目标可累计，但最多领先肩关节实际反馈 4。",
    "centering_tilt_motion_enabled": "建议保持 0，防止居中阶段通过伸臂修正纵向误差。",
    "auto_lock_vertical_center_on_pregrasp": "默认保持 0，尊重视频页手动选择的对齐位置；只有明确需要自动跟随首帧绿色框时才开启。",
    "auto_lock_vertical_center_offset_ratio": "绿色框几何中心向下移动的框高比例；当前根据现场反馈默认 0.10。",
    "approach_profile_max_lift_delta": "来自最近一次视觉卸力示教：肩关节从预抓取到闭合前约前伸 34。",
    "approach_profile_elbow_delta": "来自最近一次视觉卸力示教：肘关节同步变化约 +12.3。",
    "approach_profile_wrist_delta": "来自最近一次视觉卸力示教：腕关节同步变化约 -54，保持末端近似水平。",
    "approach_profile_wrist_trim": "只微调最终 wrist_flex 数值，软件强制限制在 -10..+10。偏低时先试 +2；若方向相反立即恢复 0 再试 -2。",
    "visual_servo_max_joint_step": "任何一次视觉命令超过该关节变化都会在发送前拒绝；不要随意增大。",
    "visual_handoff_enabled": "建议保持 1。近距离目标占满画面后不再强制依赖 YOLO。",
    "visual_handoff_max_tof_m": "即使框很大，ToF 超过该距离也不允许进入盲区。",
    "final_grasp_tof_m": "按当前现场要求设为 0.090 m；不会因此放宽盲走或关节安全上限。",
    "final_approach_max_lift_delta": "YOLO 丢失后允许的最大盲走行程，不要为了成功率无限增大。",
    "final_approach_endpoint_settle_sec": "终点命令下发后等待关节和 ToF 更新，避免目标刚到终点就提前报错。",
    "pan_direction": "当前安装应为 1；只有水平越调越远时才改成 -1。",
    "tilt_direction": "当前安装应为 -1；日志已确认原来的 1 会纵向越调越远。",
    "center_offset_x": "优先在视频页点击设置，不建议在这里盲调。",
    "center_offset_y": "优先在视频页点击设置，不建议在这里盲调。",
    "joint_command_limit": "安全参数，不要为了消除报错而提高。",
}


DEFAULT_CONFIG = {
    "control_profile_version": CONTROL_PROFILE_VERSION,
    "camera_index": 0,
    "camera_width": 1280,
    "camera_height": 720,
    "camera_fps": 15.0,
    "model_path": _default_model_path(),
    "robot_port": "COM24",
    "robot_id": "so101_slave",
    "tof_port": "",
    "yolo_conf_threshold": 0.15,
    "yolo_max_lost_frames": 15,
    "yolo_infer_interval_sec": 0.0,
    "yolo_ema_alpha": 0.6,
    "yolo_max_center_jump_ratio": 0.12,
    "yolo_max_area_change_ratio": 1.8,
    "yolo_outlier_hold_frames": 4,
    "yolo_track_iou_weight": 0.5,
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
    "tof_enabled": False,
    "tof_control_enabled": False,
    "tof_calibrated": False,
    "tof_stale_timeout_sec": 0.25,
    "tof_filter_window": 5,
    "tof_min_valid_samples": 3,
    "tof_grasp_distance_m": 0.06,
    "debug_log_enabled": True,
}


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class ArmConnectWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, arm: SO101Arm, port: str, robot_id: str):
        super().__init__()
        self.arm = arm
        self.port = port
        self.robot_id = robot_id

    def run(self) -> None:
        success, message = self.arm.connect(self.port, self.robot_id)
        self.completed.emit(success, message)


class CalibrationStartWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, arm: SO101Arm, port: str, robot_id: str):
        super().__init__()
        self.arm = arm
        self.port = port
        self.robot_id = robot_id

    def run(self) -> None:
        success, message = self.arm.start_calibration(self.port, self.robot_id)
        self.completed.emit(success, message)


class ClickableVideoLabel(QLabel):
    frame_point_selected = Signal(int, int)

    def __init__(self, text: str):
        super().__init__(text)
        self._selection_enabled = False
        self._frame_size = (0, 0)

    def set_frame_size(self, width: int, height: int) -> None:
        self._frame_size = (int(width), int(height))

    def set_selection_enabled(self, enabled: bool) -> None:
        self._selection_enabled = enabled
        if enabled:
            self.setCursor(Qt.CrossCursor)
            self.setStyleSheet("QLabel { border: 2px solid #d9a300; }")
        else:
            self.unsetCursor()
            self.setStyleSheet("")

    def mousePressEvent(self, event) -> None:
        if self._selection_enabled and event.button() == Qt.LeftButton:
            pixmap = self.pixmap()
            if pixmap is not None and not pixmap.isNull():
                frame_point = map_display_point_to_frame(
                    event.position().x(),
                    event.position().y(),
                    self.width(),
                    self.height(),
                    pixmap.width(),
                    pixmap.height(),
                    self._frame_size[0],
                    self._frame_size[1],
                )
                if frame_point is not None:
                    self.frame_point_selected.emit(*frame_point)
                    event.accept()
                    return
        super().mousePressEvent(event)


class VisualGraspLab(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = LabStore()
        self.config = self.store.load_config(DEFAULT_CONFIG)
        self._upgrade_control_profile()
        self.debug_log = DetailedDebugLogger(
            self.store.debug_log_directory,
            bool(self.config.get("debug_log_enabled", True)),
        )
        self.arm = SO101Arm()
        self.camera = CameraCapture()
        self.tof = TofSerialReader()
        self.tracker = YoloWorldTracker(self.config["model_path"], self.config)
        self.controller = VisualServoController(
            self.arm,
            self.config,
            self.store.load_positions(),
        )
        self.controller.set_debug_callback(self._controller_debug_event)
        self.last_logged_controller_state = self.controller.state.value
        self.last_logged_controller_message = self.controller.message
        self.last_detection = None
        self.last_joint_read = 0.0
        self.connect_worker = None
        self.calibration_worker = None
        self.parameter_widgets: dict[str, QWidget] = {}
        self.joint_current_labels: dict[str, QLabel] = {}
        self.joint_target_spins: dict[str, QDoubleSpinBox] = {}
        self.arm_motion_widgets: list[QWidget] = []
        self.arm_record_widgets: list[QWidget] = []
        self.arm_read_widgets: list[QWidget] = []

        self.setWindowTitle("Project LINK Windows 机械臂一体化测试台")
        self.resize(1480, 900)
        self.setMinimumSize(1180, 720)
        self._build_ui()
        self._refresh_ports()
        self._load_config_into_ui()
        self._update_calibration_ui()
        self._update_debug_log_ui()

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self._tick)
        self.tick_timer.start(66)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(500)
        self.calibration_timer = QTimer(self)
        self.calibration_timer.timeout.connect(self._sample_calibration)
        self.calibration_timer.start(50)
        self._log("上位机已启动。默认未连接机械臂、未启用扭矩、未启用 ToF 控制。")
        self.debug_log.write(
            "session_started",
            {
                "python": sys.version,
                "executable": sys.executable,
                "repo_root": str(REPO_ROOT),
                "config": dict(self.config),
                "positions": dict(self.controller.positions),
            },
        )

    def _upgrade_control_profile(self) -> None:
        saved_version = 0
        if self.store.config_path.exists():
            try:
                saved = json.loads(self.store.config_path.read_text(encoding="utf-8"))
                saved_version = int(saved.get("control_profile_version", 0))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                saved_version = 0
        if saved_version >= CONTROL_PROFILE_VERSION:
            return
        for name in CONTROL_PROFILE_KEYS:
            self.config[name] = DEFAULT_CONFIG[name]
        self.config["control_profile_version"] = CONTROL_PROFILE_VERSION
        self.store.save_config(self.config)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)

        safety = QHBoxLayout()
        self.summary_label = QLabel("等待硬件连接")
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        emergency = QPushButton("紧急停止并关闭扭矩")
        emergency.setMinimumHeight(38)
        emergency.setStyleSheet("QPushButton { background:#b42318; color:white; font-weight:bold; }")
        emergency.clicked.connect(self._emergency_stop)
        tutorial = QPushButton("打开 Windows 完整教程")
        tutorial.setMinimumHeight(38)
        tutorial.clicked.connect(self._open_tutorial)
        safety.addWidget(self.summary_label, 1)
        safety.addWidget(tutorial)
        safety.addWidget(emergency)
        root_layout.addLayout(safety)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_run_tab(), "运行与自动抓取")
        self.tabs.addTab(self._build_joint_tab(), "关节、姿态与示教")
        self.tabs.addTab(self._build_parameter_tab(), "参数与日志")
        root_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

    def _build_run_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(self._build_hardware_group())

        body = QHBoxLayout()
        video_panel = QWidget()
        video_layout = QVBoxLayout(video_panel)
        video_layout.setContentsMargins(0, 0, 0, 0)
        self.video_label = ClickableVideoLabel("请先连接摄像头")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(760, 520)
        self.video_label.setFrameShape(QLabel.Box)
        self.video_label.frame_point_selected.connect(self._set_servo_center_from_frame)
        video_layout.addWidget(self.video_label, 1)
        center_controls = QHBoxLayout()
        self.pick_center_button = QPushButton("选择目标框对齐位置（点击视频）")
        self.pick_center_button.setCheckable(True)
        self.pick_center_button.toggled.connect(self._toggle_center_selection)
        use_detection_center = QPushButton("使用当前绿色圆点")
        use_detection_center.clicked.connect(self._use_current_detection_center)
        reset_center = QPushButton("恢复画面中心")
        reset_center.clicked.connect(self._reset_servo_center)
        self.center_point_label = QLabel("视觉抓取中心：等待画面")
        self.center_point_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        center_controls.addWidget(self.pick_center_button)
        center_controls.addWidget(use_detection_center)
        center_controls.addWidget(reset_center)
        center_controls.addWidget(self.center_point_label, 1)
        video_layout.addLayout(center_controls)
        center_help = QLabel(
            "这里设置的是绿色检测框中心最终要对齐的位置，不是瓶身接触点；首次测试优先使用画面中心。"
        )
        center_help.setWordWrap(True)
        video_layout.addWidget(center_help)
        body.addWidget(video_panel, 3)

        side = QVBoxLayout()
        state_box = QGroupBox("运行状态")
        state_form = QFormLayout(state_box)
        self.controller_state_label = QLabel("IDLE")
        self.model_state_label = QLabel("模型加载中")
        self.tof_distance_label = QLabel("---")
        self.tof_stats_label = QLabel("未连接")
        self.controller_message_label = QLabel("-")
        self.controller_message_label.setWordWrap(True)
        state_form.addRow("控制器", self.controller_state_label)
        state_form.addRow("YOLO", self.model_state_label)
        state_form.addRow("ToF 距离", self.tof_distance_label)
        state_form.addRow("ToF 状态", self.tof_stats_label)
        state_form.addRow("消息", self.controller_message_label)
        side.addWidget(state_box)

        target_box = QGroupBox("目标跟踪与抓取")
        target_layout = QGridLayout(target_box)
        self.target_edit = QLineEdit("medicine bottle")
        self.target_edit.setPlaceholderText("例如：medicine bottle / red cup")
        target_layout.addWidget(QLabel("YOLO 目标"), 0, 0)
        target_layout.addWidget(self.target_edit, 0, 1, 1, 2)
        start_tracking = QPushButton("开始目标跟踪（机械臂不动）")
        start_tracking.clicked.connect(self._start_tracking)
        stop_tracking = QPushButton("停止跟踪")
        stop_tracking.clicked.connect(self._stop_tracking)
        self.prepare_grasp_button = QPushButton("准备抓取：前往待抓取位")
        self.prepare_grasp_button.clicked.connect(lambda: self._go_position("pregrasp"))
        self.start_grasp_button = QPushButton("开始自动抓取（先到待抓取位）")
        self.start_grasp_button.clicked.connect(self._start_grasp)
        stop_motion = QPushButton("停止当前运动")
        stop_motion.clicked.connect(self._stop_motion)
        target_layout.addWidget(start_tracking, 1, 0)
        target_layout.addWidget(stop_tracking, 1, 1)
        target_layout.addWidget(self.prepare_grasp_button, 2, 0, 1, 3)
        target_layout.addWidget(self.start_grasp_button, 3, 0, 1, 2)
        target_layout.addWidget(stop_motion, 3, 2)
        flow_note = QLabel(
            "跟踪只识别、不移动；自动抓取会打开夹爪并依次执行：待抓取位 → 居中 → 逼近 → 夹取。"
        )
        flow_note.setWordWrap(True)
        target_layout.addWidget(flow_note, 4, 0, 1, 3)
        self.arm_motion_widgets.extend((self.prepare_grasp_button, self.start_grasp_button))
        side.addWidget(target_box)

        tof_box = QGroupBox("末端 ToF 模式")
        tof_layout = QVBoxLayout(tof_box)
        self.tof_enabled_check = QCheckBox("启用 ToF 数据")
        self.tof_control_check = QCheckBox("允许 ToF 控制逼近和闭合")
        self.tof_calibrated_check = QCheckBox("抓取距离已现场标定")
        for checkbox in (
            self.tof_enabled_check,
            self.tof_control_check,
            self.tof_calibrated_check,
        ):
            checkbox.toggled.connect(self._apply_runtime_controls)
            tof_layout.addWidget(checkbox)
        side.addWidget(tof_box)
        side.addStretch(1)
        body.addLayout(side, 2)
        layout.addLayout(body, 1)
        return tab

    def _build_hardware_group(self) -> QGroupBox:
        box = QGroupBox("硬件连接")
        layout = QGridLayout(box)

        self.camera_index_spin = QSpinBox()
        self.camera_index_spin.setRange(0, 15)
        camera_connect = QPushButton("连接摄像头")
        camera_connect.clicked.connect(self._connect_camera)
        camera_disconnect = QPushButton("断开摄像头")
        camera_disconnect.clicked.connect(self._disconnect_camera)
        layout.addWidget(QLabel("摄像头索引"), 0, 0)
        layout.addWidget(self.camera_index_spin, 0, 1)
        layout.addWidget(camera_connect, 0, 2)
        layout.addWidget(camera_disconnect, 0, 3)

        self.arm_port_combo = QComboBox()
        self.arm_port_combo.setEditable(True)
        self.arm_connect_button = QPushButton("连接机械臂")
        self.arm_connect_button.clicked.connect(self._connect_arm)
        self.arm_disconnect_button = QPushButton("断开机械臂")
        self.arm_disconnect_button.clicked.connect(self._disconnect_arm)
        layout.addWidget(QLabel("SO-101 串口"), 1, 0)
        layout.addWidget(self.arm_port_combo, 1, 1)
        layout.addWidget(self.arm_connect_button, 1, 2)
        layout.addWidget(self.arm_disconnect_button, 1, 3)

        self.tof_port_combo = QComboBox()
        self.tof_port_combo.setEditable(True)
        tof_connect = QPushButton("连接 ToF")
        tof_connect.clicked.connect(self._connect_tof)
        tof_disconnect = QPushButton("断开 ToF")
        tof_disconnect.clicked.connect(self._disconnect_tof)
        self.refresh_ports_button = QPushButton("刷新串口")
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        layout.addWidget(QLabel("ESP32 ToF 串口"), 2, 0)
        layout.addWidget(self.tof_port_combo, 2, 1)
        layout.addWidget(tof_connect, 2, 2)
        layout.addWidget(tof_disconnect, 2, 3)

        self.torque_check = QCheckBox("启用机械臂扭矩")
        self.torque_check.toggled.connect(self._set_torque)
        self.hardware_actions_widget = QWidget()
        hardware_actions = QVBoxLayout(self.hardware_actions_widget)
        hardware_actions.setContentsMargins(0, 0, 0, 0)
        hardware_actions.addWidget(self.refresh_ports_button)
        hardware_actions.addWidget(self.torque_check)
        hardware_actions.addStretch(1)
        layout.addWidget(self.hardware_actions_widget, 0, 4, 3, 1)
        return box

    def _build_joint_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)

        calibration_box = QGroupBox("LeRobot SO-101 校准")
        calibration_layout = QVBoxLayout(calibration_box)
        calibration_note = QLabel(
            "校准会关闭扭矩。全程用手托住机械臂，先摆到各关节中位，再让五个关节和夹爪走遍完整安全行程。"
        )
        calibration_note.setWordWrap(True)
        calibration_layout.addWidget(calibration_note)
        self.calibration_state_label = QLabel("未开始校准")
        self.calibration_state_label.setWordWrap(True)
        self.calibration_state_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        calibration_layout.addWidget(self.calibration_state_label)
        calibration_buttons = QGridLayout()
        self.calibration_start_button = QPushButton("1. 开始校准并卸力")
        self.calibration_start_button.clicked.connect(self._start_calibration)
        self.calibration_middle_button = QPushButton("2. 记录中位并开始采样")
        self.calibration_middle_button.clicked.connect(self._set_calibration_middle)
        self.calibration_finish_button = QPushButton("3. 完成并保存校准")
        self.calibration_finish_button.clicked.connect(self._finish_calibration)
        self.calibration_cancel_button = QPushButton("取消校准")
        self.calibration_cancel_button.clicked.connect(self._cancel_calibration)
        calibration_buttons.addWidget(self.calibration_start_button, 0, 0)
        calibration_buttons.addWidget(self.calibration_middle_button, 0, 1)
        calibration_buttons.addWidget(self.calibration_finish_button, 1, 0)
        calibration_buttons.addWidget(self.calibration_cancel_button, 1, 1)
        calibration_layout.addLayout(calibration_buttons)
        layout.addWidget(calibration_box)

        joint_box = QGroupBox("六关节读取与手动命令")
        grid = QGridLayout(joint_box)
        grid.addWidget(QLabel("关节"), 0, 0)
        grid.addWidget(QLabel("当前位置"), 0, 1)
        grid.addWidget(QLabel("目标位置"), 0, 2)
        for row, name in enumerate(ALL_JOINTS, start=1):
            label = QLabel("---")
            spin = QDoubleSpinBox()
            spin.setRange(-100.0, 100.0)
            spin.setDecimals(2)
            spin.setSingleStep(1.0)
            self.joint_current_labels[name] = label
            self.joint_target_spins[name] = spin
            grid.addWidget(QLabel(name.removesuffix(".pos")), row, 0)
            grid.addWidget(label, row, 1)
            grid.addWidget(spin, row, 2)
        read = QPushButton("读取当前位置到目标栏")
        read.clicked.connect(self._read_joints_to_targets)
        send_arm = QPushButton("发送五个机械臂关节")
        send_arm.clicked.connect(self._send_arm_targets)
        send_gripper = QPushButton("发送夹爪位置")
        send_gripper.clicked.connect(self._send_gripper_target)
        grid.addWidget(read, 7, 0)
        grid.addWidget(send_arm, 7, 1)
        grid.addWidget(send_gripper, 7, 2)
        self.arm_read_widgets.append(read)
        self.arm_motion_widgets.extend((send_arm, send_gripper))
        layout.addWidget(joint_box)

        preset_box = QGroupBox("预设姿态")
        preset_grid = QGridLayout(preset_box)
        for row, (label, name) in enumerate(
            (("待机位", "standby"), ("待抓取位", "pregrasp"), ("放置位", "placement"))
        ):
            record = QPushButton("录制")
            record.clicked.connect(
                lambda _checked=False, key=name: self._record_position(key)
            )
            go = QPushButton("前往")
            go.clicked.connect(lambda _checked=False, key=name: self._go_position(key))
            preset_grid.addWidget(QLabel(label), row, 0)
            preset_grid.addWidget(record, row, 1)
            preset_grid.addWidget(go, row, 2)
            self.arm_record_widgets.append(record)
            self.arm_motion_widgets.append(go)
        open_gripper = QPushButton("打开夹爪")
        open_gripper.clicked.connect(
            lambda: self._set_gripper(float(self.config["gripper_open"]))
        )
        close_gripper = QPushButton("闭合夹爪")
        close_gripper.clicked.connect(
            lambda: self._set_gripper(float(self.config["gripper_close"]))
        )
        preset_grid.addWidget(open_gripper, 3, 1)
        preset_grid.addWidget(close_gripper, 3, 2)
        self.arm_motion_widgets.extend((open_gripper, close_gripper))
        layout.addWidget(preset_box)

        demo_box = QGroupBox("卸力示教")
        demo_layout = QHBoxLayout(demo_box)
        self.demo_start_button = QPushButton("开始 YOLO 视觉卸力示教")
        self.demo_start_button.clicked.connect(self._start_demo)
        self.demo_stop_button = QPushButton("停止并保存视觉示教")
        self.demo_stop_button.clicked.connect(self._stop_demo)
        self.demo_status_label = QLabel("未录制；请先让绿色 YOLO 框稳定")
        demo_layout.addWidget(self.demo_start_button)
        demo_layout.addWidget(self.demo_stop_button)
        demo_layout.addWidget(self.demo_status_label, 1)
        layout.addWidget(demo_box)
        layout.addStretch(1)
        scroll.setWidget(content)
        tab_layout.addWidget(scroll)
        return tab

    def _build_parameter_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)

        splitter = QSplitter(Qt.Horizontal)
        parameter_panel = QWidget()
        parameter_layout = QVBoxLayout(parameter_panel)
        parameter_actions = QHBoxLayout()
        restore_saved = QPushButton("撤销修改（恢复已保存）")
        restore_saved.clicked.connect(self._restore_saved_parameters)
        restore_defaults = QPushButton("一键恢复推荐参数")
        restore_defaults.clicked.connect(self._restore_recommended_parameters)
        save = QPushButton("应用并保存参数")
        save.clicked.connect(self._save_parameters)
        parameter_actions.addWidget(restore_saved)
        parameter_actions.addWidget(restore_defaults)
        parameter_actions.addWidget(save)
        parameter_layout.addLayout(parameter_actions)
        parameter_hint = QLabel(
            "鼠标滚轮只用于上下滚动页面，不会再修改数值。悬停参数可查看说明。"
        )
        parameter_hint.setWordWrap(True)
        parameter_layout.addWidget(parameter_hint)

        parameter_scroll = QScrollArea()
        parameter_scroll.setWidgetResizable(True)
        parameter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        parameters = QGroupBox("测试参数")
        form = QFormLayout(parameters)
        parameter_names = (
            "yolo_conf_threshold",
            "yolo_max_lost_frames",
            "yolo_infer_interval_sec",
            "yolo_ema_alpha",
            "yolo_max_center_jump_ratio",
            "yolo_max_area_change_ratio",
            "yolo_outlier_hold_frames",
            "yolo_track_iou_weight",
            "pan_gain",
            "tilt_gain",
            "pan_direction",
            "tilt_direction",
            "centering_tilt_motion_enabled",
            "auto_lock_vertical_center_on_pregrasp",
            "auto_lock_vertical_center_offset_ratio",
            "approach_step",
            "approach_max_command_lead",
            "approach_profile_max_lift_delta",
            "approach_profile_elbow_delta",
            "approach_profile_wrist_delta",
            "approach_profile_wrist_trim",
            "visual_servo_max_joint_step",
            "visual_handoff_enabled",
            "visual_handoff_bbox_height_ratio",
            "visual_handoff_area_ratio",
            "visual_handoff_tof_m",
            "visual_handoff_max_tof_m",
            "final_grasp_tof_m",
            "final_approach_step",
            "final_approach_max_command_lead",
            "final_approach_max_lift_delta",
            "final_approach_command_interval_sec",
            "final_approach_timeout_sec",
            "final_approach_endpoint_settle_sec",
            "centering_threshold",
            "centering_limit_hold_cycles",
            "centering_error_window",
            "centering_min_samples",
            "centering_confirm_cycles",
            "centering_step_limit",
            "centering_min_step_limit",
            "centering_slow_zone",
            "centering_max_command_lead",
            "centering_command_interval_sec",
            "grasp_area_threshold",
            "gripper_open",
            "gripper_close",
            "arrive_threshold",
            "elbow_arrive_threshold",
            "arrive_stable_margin",
            "arrive_stable_delta",
            "arrive_stable_cycles",
            "move_step_limit",
            "move_timeout_sec",
            "grasp_timeout_sec",
            "joint_command_limit",
            "preset_joint_limit",
            "standby_joint_limit",
            "center_offset_x",
            "center_offset_y",
            "tof_stale_timeout_sec",
            "tof_filter_window",
            "tof_min_valid_samples",
            "tof_grasp_distance_m",
        )
        for name in parameter_names:
            default = DEFAULT_CONFIG[name]
            if isinstance(default, int):
                widget = NoWheelSpinBox()
                widget.setRange(-100000, 100000)
            else:
                widget = NoWheelDoubleSpinBox()
                widget.setDecimals(4)
                widget.setRange(-100000.0, 100000.0)
            widget.setMinimumWidth(150)
            help_text = PARAMETER_HELP.get(name, "")
            tooltip = f"参数名：{name}"
            if help_text:
                tooltip += f"\n{help_text}"
            widget.setToolTip(tooltip)
            label = QLabel(PARAMETER_LABELS.get(name, name))
            label.setToolTip(tooltip)
            self.parameter_widgets[name] = widget
            form.addRow(label, widget)

        self.model_path_edit = QLineEdit()
        browse = QPushButton("选择模型")
        browse.clicked.connect(self._browse_model)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_path_edit, 1)
        model_row.addWidget(browse)
        form.addRow("model_path", model_row)
        parameter_scroll.setWidget(parameters)
        parameter_layout.addWidget(parameter_scroll, 1)
        parameter_panel.setMinimumWidth(520)
        splitter.addWidget(parameter_panel)

        log_box = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_box)
        self.debug_log_check = QCheckBox("启用详细 JSONL 调试日志")
        self.debug_log_path_label = QLabel("详细日志未启用")
        self.debug_log_path_label.setWordWrap(True)
        self.debug_log_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        debug_buttons = QHBoxLayout()
        open_debug_directory = QPushButton("打开详细日志目录")
        open_debug_directory.clicked.connect(self._open_debug_log_directory)
        capture_snapshot = QPushButton("写入当前机械臂快照")
        capture_snapshot.clicked.connect(
            lambda: self._capture_arm_debug_snapshot("manual_snapshot")
        )
        debug_buttons.addWidget(open_debug_directory)
        debug_buttons.addWidget(capture_snapshot)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        clear = QPushButton("清空日志")
        clear.clicked.connect(self.log_view.clear)
        log_layout.addWidget(self.debug_log_check)
        log_layout.addWidget(self.debug_log_path_label)
        log_layout.addLayout(debug_buttons)
        log_layout.addWidget(self.log_view, 1)
        log_layout.addWidget(clear)
        splitter.addWidget(log_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes((600, 600))
        layout.addWidget(splitter)
        return tab

    def _restore_saved_parameters(self) -> None:
        for name, widget in self.parameter_widgets.items():
            widget.setValue(self.config[name])
        self.model_path_edit.setText(str(self.config["model_path"]))
        self._log("已撤销参数页未保存的修改，恢复当前已保存参数")

    def _restore_recommended_parameters(self) -> None:
        answer = QMessageBox.question(
            self,
            "恢复推荐参数",
            "将恢复检测、滤波、运动和 ToF 的推荐参数并立即保存。\n"
            "摄像头、串口、模型路径和画面对齐位置不会改变。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        preserved = {"center_offset_x", "center_offset_y"}
        for name, widget in self.parameter_widgets.items():
            widget.setValue(
                self.config[name] if name in preserved else DEFAULT_CONFIG[name]
            )
        self.model_path_edit.setText(str(self.config["model_path"]))
        self.camera_index_spin.setValue(int(self.config["camera_index"]))
        self._select_port(self.arm_port_combo, str(self.config["robot_port"]))
        self._select_port(self.tof_port_combo, str(self.config.get("tof_port", "")))
        self._save_parameters()
        self._log("已恢复并保存推荐参数；画面对齐位置保持不变")

    def _open_tutorial(self) -> None:
        path = REPO_ROOT / "docs" / "modules" / "manipulation" / "WINDOWS_LAB.md"
        if not path.exists():
            QMessageBox.warning(self, "完整教程", f"教程文件不存在：{path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "完整教程", f"无法打开教程，请手动打开：{path}")

    def _open_debug_log_directory(self) -> None:
        self.store.debug_log_directory.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.store.debug_log_directory))
        ):
            QMessageBox.warning(
                self,
                "详细日志",
                f"无法打开目录，请手动打开：{self.store.debug_log_directory}",
            )

    def _toggle_center_selection(self, enabled: bool) -> None:
        if enabled and self.camera.frame() is None:
            self.pick_center_button.blockSignals(True)
            self.pick_center_button.setChecked(False)
            self.pick_center_button.blockSignals(False)
            QMessageBox.warning(self, "视觉抓取中心", "请先连接摄像头并确认有画面")
            return
        self.video_label.set_selection_enabled(enabled)
        self.pick_center_button.setText(
            "请点击绿色圆点最终应停留的位置"
            if enabled
            else "选择目标框对齐位置（点击视频）"
        )
        if enabled:
            self._log(
                "目标框对齐位置选择已开启：请点击绿色检测框中心最终应该停留的位置，"
                "不要点击瓶身接触点"
            )

    def _set_servo_center_from_frame(self, frame_x: int, frame_y: int) -> None:
        frame = self.camera.frame()
        if frame is None:
            return
        offset_x = float(frame_x - frame.shape[1] / 2.0)
        offset_y = float(frame_y - frame.shape[0] / 2.0)
        self.config["center_offset_x"] = offset_x
        self.config["center_offset_y"] = offset_y
        self.config["auto_lock_vertical_center_on_pregrasp"] = False
        self.parameter_widgets["center_offset_x"].setValue(offset_x)
        self.parameter_widgets["center_offset_y"].setValue(offset_y)
        self.parameter_widgets["auto_lock_vertical_center_on_pregrasp"].setValue(0)
        self.controller.update_config(self.config)
        self.controller.use_configured_visual_center()
        self.store.save_config(self.config)
        self.pick_center_button.setChecked(False)
        self._update_center_point_label(frame.shape[1], frame.shape[0])
        message = (
            f"目标框对齐位置已设置为画面坐标 ({frame_x}, {frame_y})，"
            f"偏移 ({offset_x:+.0f}, {offset_y:+.0f})；自动纵向锁定已关闭，参数已保存"
        )
        self._log(message)
        self.debug_log.write(
            "servo_center_selected",
            {
                "frame_point": [frame_x, frame_y],
                "frame_size": [frame.shape[1], frame.shape[0]],
                "offset_x": offset_x,
                "offset_y": offset_y,
            },
        )

    def _use_current_detection_center(self) -> None:
        if self.last_detection is None:
            QMessageBox.warning(
                self,
                "目标框对齐位置",
                "当前没有稳定的目标检测框，请先开始目标跟踪并等待绿色框出现",
            )
            return
        x, y, width, height = self.last_detection.bbox
        self._set_servo_center_from_frame(
            int(x + width / 2),
            int(y + height / 2),
        )

    def _reset_servo_center(self) -> None:
        self.config["center_offset_x"] = 0.0
        self.config["center_offset_y"] = 0.0
        self.config["auto_lock_vertical_center_on_pregrasp"] = False
        self.parameter_widgets["center_offset_x"].setValue(0.0)
        self.parameter_widgets["center_offset_y"].setValue(0.0)
        self.parameter_widgets["auto_lock_vertical_center_on_pregrasp"].setValue(0)
        self.controller.update_config(self.config)
        self.controller.use_configured_visual_center()
        self.store.save_config(self.config)
        frame = self.camera.frame()
        if frame is not None:
            self._update_center_point_label(frame.shape[1], frame.shape[0])
        self._log("目标框对齐位置已恢复为画面几何中心，参数已保存")

    def _update_center_point_label(self, frame_width: int, frame_height: int) -> None:
        center_x, center_y = self.controller.visual_target_center(
            (frame_width, frame_height)
        )
        configured_center_y = frame_height / 2.0 + float(
            self.config["center_offset_y"]
        )
        lock_text = (
            "，纵向已按预抓取绿色框自动锁定"
            if abs(center_y - configured_center_y) > 0.5
            else ""
        )
        self.center_point_label.setText(
            "视觉抓取中心：({0:.0f}, {1:.0f})，配置偏移 ({2:+.0f}, {3:+.0f}){4}".format(
                center_x,
                center_y,
                float(self.config["center_offset_x"]),
                float(self.config["center_offset_y"]),
                lock_text,
            )
        )

    def _refresh_ports(self) -> None:
        ports = TofSerialReader.ports()
        current_arm = self.arm_port_combo.currentText() if hasattr(self, "arm_port_combo") else ""
        current_tof = self.tof_port_combo.currentText() if hasattr(self, "tof_port_combo") else ""
        for combo in (self.arm_port_combo, self.tof_port_combo):
            combo.clear()
            for device, label in ports:
                combo.addItem(label, device)
        self._select_port(self.arm_port_combo, current_arm or str(self.config["robot_port"]))
        preferred_tof = current_tof or str(self.config.get("tof_port", "")) or self.tof.preferred_port()
        self._select_port(self.tof_port_combo, preferred_tof)

    @staticmethod
    def _select_port(combo: QComboBox, port: str) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == port:
                combo.setCurrentIndex(index)
                return
        if port:
            combo.setEditText(port)

    @staticmethod
    def _combo_port(combo: QComboBox) -> str:
        return str(combo.currentData() or combo.currentText().split(" - ", 1)[0]).strip()

    def _load_config_into_ui(self) -> None:
        self.camera_index_spin.setValue(int(self.config["camera_index"]))
        self.model_path_edit.setText(str(self.config["model_path"]))
        self.tof_enabled_check.setChecked(bool(self.config["tof_enabled"]))
        self.tof_control_check.setChecked(bool(self.config["tof_control_enabled"]))
        self.tof_calibrated_check.setChecked(bool(self.config["tof_calibrated"]))
        self.debug_log_check.setChecked(
            bool(self.config.get("debug_log_enabled", True))
        )
        for name, widget in self.parameter_widgets.items():
            value = self.config[name]
            widget.setValue(value)

    def _connect_camera(self) -> None:
        self.config["camera_index"] = self.camera_index_spin.value()
        success, message = self.camera.start(
            int(self.config["camera_index"]),
            int(self.config["camera_width"]),
            int(self.config["camera_height"]),
            float(self.config["camera_fps"]),
        )
        self._log(message)
        if not success:
            QMessageBox.warning(self, "摄像头", message)

    def _disconnect_camera(self) -> None:
        self.camera.stop()
        self.video_label.clear()
        self.video_label.setText("摄像头已断开")
        self._log("摄像头已断开")

    def _connect_arm(self) -> None:
        if self.arm.calibration_active or self.calibration_worker is not None:
            QMessageBox.warning(self, "机械臂", "校准期间不能执行普通连接")
            return
        if self.arm.connected:
            self._log("机械臂已经连接")
            return
        port = self._combo_port(self.arm_port_combo)
        if not port:
            QMessageBox.warning(self, "机械臂", "请选择 SO-101 串口")
            return
        self.config["robot_port"] = port
        self.arm_connect_button.setEnabled(False)
        self.arm_connect_button.setText("连接中")
        self.connect_worker = ArmConnectWorker(self.arm, port, str(self.config["robot_id"]))
        self.connect_worker.completed.connect(self._arm_connected)
        self.connect_worker.finished.connect(self._arm_connect_worker_finished)
        self.connect_worker.start()

    def _arm_connected(self, success: bool, message: str) -> None:
        self.arm_connect_button.setEnabled(True)
        self.arm_connect_button.setText("连接机械臂")
        self._log(message)
        if success:
            self._read_joints_to_targets()
            self._capture_arm_debug_snapshot("arm_connected")
        elif self.arm.connected and self.arm.calibration_state == "REQUIRED":
            QMessageBox.information(
                self,
                "机械臂需要校准",
                "SO-101 串口已连接且扭矩已关闭，但当前电机没有匹配的校准文件。"
                "软件已经尝试自动恢复已保存文件。\n"
                f"{message}\n"
                "只有恢复后仍无法验证时，才需要重新执行三步校准。",
            )
        elif self.arm.connected:
            QMessageBox.critical(
                self,
                "机械臂硬件异常",
                f"串口已打开，但机械臂未进入安全可控状态。\n{message}\n"
                "请物理断电并等待电机冷却，不要启用扭矩或开始校准。",
            )
        else:
            QMessageBox.warning(self, "机械臂连接失败", message)
        self._update_calibration_ui()

    def _arm_connect_worker_finished(self) -> None:
        self.connect_worker = None
        self._update_calibration_ui()

    def _disconnect_arm(self) -> None:
        if self.arm.calibration_active:
            QMessageBox.warning(self, "机械臂", "请先完成或取消校准")
            return
        self.controller.stop()
        self.torque_check.blockSignals(True)
        self.torque_check.setChecked(False)
        self.torque_check.blockSignals(False)
        success, message = self.arm.disconnect()
        self._log(message)
        if not success:
            QMessageBox.critical(
                self,
                "机械臂断开异常",
                f"{message}\n请立即物理关闭机械臂电源，再拔 USB。",
            )
        elif self.arm.torque_fault_message:
            QMessageBox.warning(
                self,
                "电机硬件告警",
                f"串口已断开。{self.arm.torque_fault_message}",
            )
        self._update_calibration_ui()

    def _set_torque(self, enabled: bool) -> None:
        if self.arm.calibration_active:
            self.torque_check.blockSignals(True)
            self.torque_check.setChecked(False)
            self.torque_check.blockSignals(False)
            QMessageBox.warning(self, "扭矩", "校准期间禁止启用扭矩")
            return
        if not enabled:
            self.controller.stop()
        operation = self.arm.enable_torque if enabled else self.arm.disable_torque
        success, message = operation()
        self._log(message)
        if not success:
            self.torque_check.blockSignals(True)
            self.torque_check.setChecked(self.arm.torque_enabled)
            self.torque_check.blockSignals(False)
            QMessageBox.critical(
                self,
                "断扭矩未确认" if not enabled else "禁止启用扭矩",
                f"{message}\n请立即物理关闭机械臂电源并等待电机冷却。",
            )
        elif self.arm.torque_fault_message:
            QMessageBox.warning(
                self,
                "扭矩已关闭，但电机存在硬件告警",
                f"{message}\n不要重新启用扭矩或开始校准，请物理断电并等待冷却。",
            )
        self._update_calibration_ui()

    def _start_calibration(self) -> None:
        if self.calibration_worker is not None:
            return
        port = self._combo_port(self.arm_port_combo)
        if not port:
            QMessageBox.warning(self, "SO-101 校准", "请选择 SO-101 串口")
            return
        reply = QMessageBox.question(
            self,
            "SO-101 校准安全确认",
            "校准会断开普通控制并关闭扭矩，机械臂可能下坠。\n"
            "请用手托住机械臂、清空周围空间并准备物理断电。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.controller.stop()
        self.controller.demo_recording = False
        self.torque_check.blockSignals(True)
        self.torque_check.setChecked(False)
        self.torque_check.blockSignals(False)
        self.config["robot_port"] = port
        self.calibration_worker = CalibrationStartWorker(
            self.arm,
            port,
            str(self.config["robot_id"]),
        )
        self.calibration_worker.completed.connect(self._calibration_started)
        self.calibration_worker.finished.connect(self._calibration_worker_finished)
        self.calibration_worker.start()
        self._log("正在打开 SO-101 串口并关闭扭矩，请持续托住机械臂")
        self._update_calibration_ui()

    def _calibration_started(self, success: bool, message: str) -> None:
        self._log(message)
        if success:
            QMessageBox.information(
                self,
                "SO-101 校准",
                "机械臂已卸力。请把五个关节和夹爪摆到各自可用行程的中间位置，"
                "然后点击“2. 记录中位并开始采样”。",
            )
        else:
            QMessageBox.warning(self, "SO-101 校准启动失败", message)
        self._update_calibration_ui()

    def _calibration_worker_finished(self) -> None:
        self.calibration_worker = None
        self._update_calibration_ui()

    def _set_calibration_middle(self) -> None:
        success, message = self.arm.calibration_set_middle()
        self._log(message)
        if success:
            QMessageBox.information(
                self,
                "全行程采样已开始",
                "现在缓慢移动肩部、肘部、腕部、旋转关节和夹爪，"
                "让每个关节走遍完整安全行程并至少往返一次。完成后点击“3. 完成并保存校准”。",
            )
        else:
            QMessageBox.warning(self, "记录中位失败", message)
        self._update_calibration_ui()

    def _sample_calibration(self) -> None:
        if self.arm.calibration_state != "RECORDING_RANGE":
            return
        previous_state = self.arm.calibration_state
        self.arm.calibration_sample()
        if previous_state != self.arm.calibration_state:
            self._log(self.arm.calibration_message)
        self._update_calibration_ui()

    def _finish_calibration(self) -> None:
        reply = QMessageBox.question(
            self,
            "完成 SO-101 校准",
            "确认五个关节和夹爪都已经走遍完整安全行程，并且没有撞到机械限位？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.arm.calibration_sample()
        success, message = self.arm.finish_calibration()
        self._log(message)
        if success:
            self.controller.positions.clear()
            self.store.save_positions(self.controller.positions)
            self._capture_arm_debug_snapshot("calibration_completed")
            QMessageBox.information(
                self,
                "SO-101 校准完成",
                f"校准文件已保存。机械臂保持连接且扭矩关闭。\n{message}\n"
                "旧预设姿态已清除，请重新录制待机位、待抓取位和放置位。",
            )
            self._read_joints_to_targets()
        else:
            QMessageBox.warning(self, "SO-101 校准失败", message)
        self._update_calibration_ui()

    def _cancel_calibration(self) -> None:
        success, message = self.arm.cancel_calibration()
        self.torque_check.blockSignals(True)
        self.torque_check.setChecked(False)
        self.torque_check.blockSignals(False)
        self._log(message)
        if not success:
            QMessageBox.warning(self, "取消校准", message)
        self._update_calibration_ui()

    def _update_calibration_ui(self) -> None:
        if not hasattr(self, "calibration_state_label"):
            return
        state = self.arm.calibration_state
        busy = self.calibration_worker is not None
        connect_busy = self.connect_worker is not None and self.connect_worker.isRunning()
        active = self.arm.calibration_active
        state_names = {
            "IDLE": "未校准会话",
            "REQUIRED": "需要校准",
            "WAIT_MIDDLE": "等待记录中位",
            "RECORDING_RANGE": "正在采样全行程",
            "READY": "校准可用",
            "ERROR": "校准错误",
        }
        instructions = {
            "IDLE": "如首次使用或更换电机，请从第 1 步开始。",
            "REQUIRED": "当前电机没有匹配的 LeRobot 校准文件，请执行完整三步校准。",
            "WAIT_MIDDLE": "托住机械臂，将全部关节和夹爪摆到可用行程中间，然后执行第 2 步。",
            "RECORDING_RANGE": "缓慢让全部关节和夹爪走遍安全行程；软件每 50 ms 自动采样。",
            "READY": "校准文件已加载或保存，可以在确认姿态安全后启用扭矩。",
            "ERROR": "保持扭矩关闭，检查串口、电源和电机 ID 后取消并重新开始。",
        }
        if busy:
            title = "正在启动校准"
            detail = "正在连接电机并关闭扭矩，请持续托住机械臂。"
        else:
            title = state_names.get(state, state)
            detail = instructions.get(state, self.arm.calibration_message)
        if self.arm.calibration_message and state in {"READY", "ERROR"}:
            detail = f"{detail}\n{self.arm.calibration_message}"
        self.calibration_state_label.setText(f"状态：{title}\n{detail}")
        self.calibration_start_button.setEnabled(not busy and not active and not connect_busy)
        self.calibration_middle_button.setEnabled(not busy and state == "WAIT_MIDDLE")
        self.calibration_finish_button.setEnabled(not busy and state == "RECORDING_RANGE")
        self.calibration_cancel_button.setEnabled(
            not busy and state in {"WAIT_MIDDLE", "RECORDING_RANGE", "ERROR", "REQUIRED"}
        )
        base_ready = (
            self.arm.connected
            and self.arm.calibrated
            and state == "READY"
            and not self.arm.torque_fault_message
            and not active
            and not busy
            and not connect_busy
        )
        controller_busy = self.controller.state in {
            ServoState.MOVING,
            ServoState.CENTERING,
            ServoState.APPROACHING,
            ServoState.FINAL_APPROACH,
            ServoState.RANGE_WAIT,
        }
        record_ready = (
            base_ready
            and not self.arm.torque_enabled
            and self.arm.torque_off_confirmed
            and not controller_busy
        )
        powered_motion_ready = (
            base_ready and self.arm.torque_enabled and not controller_busy
        )
        read_ready = base_ready and not controller_busy
        self.arm_port_combo.setEnabled(not active and not busy and not connect_busy)
        self.arm_connect_button.setEnabled(
            not self.arm.connected and not active and not busy and not connect_busy
        )
        self.arm_disconnect_button.setEnabled(
            self.arm.connected and not active and not busy and not connect_busy
        )
        self.torque_check.setEnabled(base_ready)
        for widget in self.arm_read_widgets:
            widget.setEnabled(read_ready)
        for widget in self.arm_record_widgets:
            widget.setEnabled(record_ready)
        for widget in self.arm_motion_widgets:
            widget.setEnabled(powered_motion_ready)
        self.demo_start_button.setEnabled(base_ready and not self.controller.demo_recording)
        self.demo_stop_button.setEnabled(base_ready and self.controller.demo_recording)

    def _connect_tof(self) -> None:
        port = self._combo_port(self.tof_port_combo)
        self.config["tof_port"] = port
        self.tof.configure_filter(int(self.config["tof_filter_window"]))
        success, message = self.tof.connect(port)
        self._log(message)
        if not success:
            QMessageBox.warning(self, "ToF", message)

    def _disconnect_tof(self) -> None:
        self.tof.disconnect()
        self._log("ToF 已断开")

    def _start_tracking(self) -> None:
        if self.camera.frame() is None:
            QMessageBox.warning(self, "目标跟踪", "请先连接摄像头并确认有画面")
            return
        success, message = self.tracker.set_target(self.target_edit.text())
        if success:
            self.last_detection = None
            self.controller.set_tracking()
            message = f"{message}；当前仅识别跟踪，不会移动机械臂"
        self._log(message)
        if not success:
            QMessageBox.warning(self, "目标跟踪", message)

    def _stop_tracking(self) -> None:
        self.controller.stop()
        self.tracker.clear_target()
        self.last_detection = None
        self._log("目标跟踪已停止")

    def _start_grasp(self) -> None:
        self._apply_runtime_controls()
        if self.camera.frame() is None:
            QMessageBox.warning(self, "自动抓取", "请先连接摄像头并确认有画面")
            return
        success, message = self.controller.validate_grasp_start()
        if not success:
            self._log(message)
            QMessageBox.warning(self, "自动抓取未启动", message)
            return
        if "pregrasp" not in self.controller.positions:
            message = "请先在扭矩关闭状态下录制待抓取位"
            self._log(message)
            QMessageBox.warning(self, "自动抓取未启动", message)
            return
        success, message = self.tracker.set_target(self.target_edit.text())
        if not success:
            self._log(message)
            QMessageBox.warning(self, "自动抓取未启动", message)
            return
        self.last_detection = None
        success, message = self.arm.set_gripper(float(self.config["gripper_open"]))
        self._log(f"自动抓取准备：{message}")
        if not success:
            QMessageBox.warning(self, "自动抓取未启动", message)
            return
        success, message = self.controller.start_grasp_sequence()
        self._log(message)
        if not success:
            QMessageBox.warning(self, "自动抓取未启动", message)

    def _stop_motion(self) -> None:
        _, message = self.controller.stop(keep_tracking=bool(self.tracker.target))
        self._log(message)

    def _emergency_stop(self) -> None:
        self.controller.stop(hold_position=False)
        success, message = self.arm.disable_torque()
        self.torque_check.blockSignals(True)
        self.torque_check.setChecked(False)
        self.torque_check.blockSignals(False)
        self._log(f"紧急停止：{message}")
        if not success:
            QMessageBox.critical(
                self,
                "紧急停止：断扭矩未确认",
                f"{message}\n软件无法确认所有电机已经卸力，请立即物理关闭机械臂电源。",
            )
        elif self.arm.torque_fault_message:
            QMessageBox.warning(
                self,
                "紧急停止：扭矩已关闭但电机过热",
                f"{message}\n保持物理断电，等待电机完全冷却后再检查和校准。",
            )

    def _read_joints_to_targets(self) -> None:
        joints = self.arm.get_joints()
        if not joints:
            self._log("无法读取机械臂关节")
            return
        for name, value in joints.items():
            if name in self.joint_target_spins:
                self.joint_target_spins[name].setValue(float(value))
        self._show_joints(joints)
        self._log("已读取当前关节位置")

    def _send_arm_targets(self) -> None:
        desired = {name: self.joint_target_spins[name].value() for name in ARM_JOINTS}
        success, message = self.arm.send_arm_joints(desired)
        self._log(message)
        if not success:
            QMessageBox.warning(self, "关节命令", message)

    def _send_gripper_target(self) -> None:
        self._set_gripper(self.joint_target_spins["gripper.pos"].value())

    def _set_gripper(self, position: float) -> None:
        success, message = self.arm.set_gripper(position)
        self._log(f"夹爪 {position:.1f}：{message}")
        if not success:
            QMessageBox.warning(self, "夹爪", message)

    def _record_position(self, name: str) -> None:
        success, message = self.controller.record_position(name)
        if success:
            self.store.save_positions(self.controller.positions)
        self._log(message)

    def _go_position(self, name: str) -> None:
        completion_state = ServoState.TRACKING if self.tracker.target else ServoState.IDLE
        success, message = self.controller.go_to_position(
            name,
            completion_state=completion_state,
        )
        self._log(message)
        if not success:
            QMessageBox.warning(self, "预设姿态", message)

    def _start_demo(self) -> None:
        frame = self.camera.frame()
        if frame is None:
            QMessageBox.warning(self, "视觉示教", "请先连接摄像头并确认有画面")
            return
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "视觉示教", "请先填写 YOLO World 目标文本")
            return
        if self.tracker.target != target:
            success, message = self.tracker.set_target(target)
            self._log(message)
            if not success:
                QMessageBox.warning(self, "视觉示教", message)
                return
            self.last_detection = None
            QMessageBox.information(
                self,
                "视觉示教",
                "YOLO 跟踪已启动。请等待绿色目标框稳定后，再点击一次开始视觉卸力示教。",
            )
            return
        if self.last_detection is None or not self.last_detection.trusted:
            QMessageBox.warning(
                self,
                "视觉示教",
                "当前没有稳定可信的绿色 YOLO 框，请等待目标识别稳定后再开始。",
            )
            return
        self.controller.stop()
        success, message = self.arm.disable_torque()
        if not success:
            QMessageBox.warning(self, "示教录制", message)
            return
        self.torque_check.blockSignals(True)
        self.torque_check.setChecked(False)
        self.torque_check.blockSignals(False)
        self.controller.start_demo_recording(target)
        self.demo_status_label.setText("正在录制：0 个样本")
        self._log(
            "YOLO 视觉卸力示教已开始：扭矩关闭，目标跟踪保持运行；"
            "请缓慢示教完整的居中、水平逼近和抓取视角"
        )
        self.debug_log.write(
            "visual_demo_started",
            {
                "target": target,
                "frame_size": [int(frame.shape[1]), int(frame.shape[0])],
                "config": {
                    "center_offset_x": self.config["center_offset_x"],
                    "center_offset_y": self.config["center_offset_y"],
                    "tof_grasp_distance_m": self.config["tof_grasp_distance_m"],
                },
            },
        )

    def _stop_demo(self) -> None:
        if not self.controller.demo_recording:
            self._log("当前没有进行示教录制")
            return
        rows = self.controller.stop_demo_recording()
        demo_dir = self.store.root / "demos"
        demo_dir.mkdir(parents=True, exist_ok=True)
        path = demo_dir / f"visual_demo_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        if rows:
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=DEMO_CSV_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
        success, message = self.arm.enable_torque()
        self.torque_check.blockSignals(True)
        self.torque_check.setChecked(self.arm.torque_enabled)
        self.torque_check.blockSignals(False)
        trusted_rows = [row for row in rows if row["detection_trusted"]]
        area_values = [
            float(row["bbox_area_ratio"])
            for row in trusted_rows
            if row["bbox_area_ratio"] is not None
        ]
        summary = (
            f"样本 {len(rows)}，可信视觉样本 {len(trusted_rows)}"
            + (
                f"，面积比例 {min(area_values):.4f} → {max(area_values):.4f}"
                if area_values
                else "，没有有效 bbox 面积"
            )
        )
        if rows:
            self._log(f"视觉示教已保存到 {path}；{summary}；{message}")
            self.demo_status_label.setText(f"已保存：{path.name}；{summary}")
        else:
            self._log(f"视觉示教没有采集到样本，未写入 CSV；{message}")
            self.demo_status_label.setText("未采集到样本")
        self.debug_log.write(
            "visual_demo_stopped",
            {
                "path": str(path) if rows else "",
                "samples": len(rows),
                "trusted_samples": len(trusted_rows),
                "area_ratio_min": min(area_values) if area_values else None,
                "area_ratio_max": max(area_values) if area_values else None,
            },
        )
        if not success:
            QMessageBox.warning(self, "恢复扭矩失败", message)

    def _apply_runtime_controls(self) -> None:
        self.config["tof_enabled"] = self.tof_enabled_check.isChecked()
        self.config["tof_control_enabled"] = self.tof_control_check.isChecked()
        self.config["tof_calibrated"] = self.tof_calibrated_check.isChecked()
        self.controller.update_config(self.config)

    def _browse_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 YOLO-World 模型",
            self.model_path_edit.text(),
            "PyTorch model (*.pt);;All files (*)",
        )
        if path:
            self.model_path_edit.setText(path)

    def _save_parameters(self) -> None:
        previous_model = str(self.config["model_path"])
        for name, widget in self.parameter_widgets.items():
            self.config[name] = widget.value()
        self.config["model_path"] = self.model_path_edit.text().strip()
        self.config["camera_index"] = self.camera_index_spin.value()
        self.config["robot_port"] = self._combo_port(self.arm_port_combo)
        self.config["tof_port"] = self._combo_port(self.tof_port_combo)
        requested_debug = self.debug_log_check.isChecked()
        if requested_debug and not self.debug_log.enabled:
            self.debug_log.set_enabled(True)
        self.config["debug_log_enabled"] = requested_debug
        self._apply_runtime_controls()
        self.tracker.update_config(self.config)
        self.controller.update_config(self.config)
        self.tof.configure_filter(int(self.config["tof_filter_window"]))
        self.store.save_config(self.config)
        self.debug_log.write(
            "parameters_saved",
            {
                "config": dict(self.config),
                "positions": dict(self.controller.positions),
            },
        )
        if not requested_debug and self.debug_log.enabled:
            self.debug_log.write("debug_logging_disabled")
            self.debug_log.set_enabled(False)
        self._update_debug_log_ui()
        self._log(f"参数已保存到 {self.store.config_path}")
        if previous_model != str(self.config["model_path"]):
            QMessageBox.information(self, "模型路径", "模型路径已保存，重启上位机后加载新模型。")

    def _tick(self) -> None:
        frame = self.camera.frame()
        if frame is None:
            return
        detection = self.tracker.submit(frame)
        self.last_detection = detection
        reading = self.tof.reading(
            float(self.config["tof_stale_timeout_sec"]),
            int(self.config["tof_min_valid_samples"]),
        )
        self.controller.update(detection, (frame.shape[1], frame.shape[0]), reading)
        if self.controller.demo_recording:
            self.demo_status_label.setText(
                f"正在录制：{len(self.controller.demo_rows)} 个样本；"
                + ("YOLO 可信" if detection is not None and detection.trusted else "等待可信 YOLO")
            )
        self._log_controller_transition()
        annotated = frame.copy()
        self.video_label.set_frame_size(frame.shape[1], frame.shape[0])
        self._update_center_point_label(frame.shape[1], frame.shape[0])
        active_target_center = self.controller.visual_target_center(
            (frame.shape[1], frame.shape[0])
        )
        target_center = (
            int(active_target_center[0]),
            int(active_target_center[1]),
        )
        cv2.drawMarker(
            annotated,
            target_center,
            (0, 215, 255),
            cv2.MARKER_CROSS,
            28,
            2,
        )
        cv2.putText(
            annotated,
            f"servo center {target_center[0]},{target_center[1]}",
            (max(8, target_center[0] - 105), max(24, target_center[1] - 18)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 215, 255),
            1,
        )
        if detection is not None:
            x, y, width, height = detection.bbox
            detection_center = (int(x + width / 2), int(y + height / 2))
            detection_color = (0, 200, 0) if detection.trusted else (0, 165, 255)
            cv2.rectangle(
                annotated,
                (x, y),
                (x + width, y + height),
                detection_color,
                2,
            )
            cv2.circle(annotated, detection_center, 5, detection_color, -1)
            cv2.line(annotated, detection_center, target_center, (255, 180, 0), 1)
            cv2.putText(
                annotated,
                f"{self.tracker.target} {detection.confidence:.2f}"
                + (" HELD" if not detection.trusted else ""),
                (x, max(25, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                detection_color,
                2,
            )
        tof_text = "ToF ---"
        if reading.valid and reading.range_m is not None:
            tof_text = f"ToF {reading.range_m:.3f} m"
        cv2.putText(
            annotated,
            f"{self.controller.state.value} | {tof_text}",
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (20, 20, 230),
            2,
        )
        if self.controller.demo_recording:
            cv2.putText(
                annotated,
                f"VISUAL DEMO REC {len(self.controller.demo_rows)}",
                (12, 58),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (0, 0, 230),
                2,
            )
        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        image = QImage(
            rgb.data,
            rgb.shape[1],
            rgb.shape[0],
            rgb.strides[0],
            QImage.Format_RGB888,
        )
        pixmap = QPixmap.fromImage(image.copy())
        self.video_label.setPixmap(
            pixmap.scaled(
                self.video_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def _refresh_status(self) -> None:
        reading = self.tof.reading(
            float(self.config["tof_stale_timeout_sec"]),
            int(self.config["tof_min_valid_samples"]),
        )
        stats = self.tof.stats()
        self.controller_state_label.setText(self.controller.state.value)
        self.model_state_label.setText(self.tracker.message)
        self.controller_message_label.setText(self.controller.message)
        if reading.valid and reading.range_m is not None:
            self.tof_distance_label.setText(f"{reading.range_m:.3f} m")
        else:
            self.tof_distance_label.setText(f"无效：{reading.reason}")
        self.tof_stats_label.setText(
            f"{stats['state']}，{stats['rate_hz']:.1f} Hz，"
            f"有效 {stats['accepted']}，拒绝 {stats['rejected']}"
        )
        self.summary_label.setText(
            "摄像头:{0} | 机械臂:{1} | 校准:{2} | 扭矩:{3} | ToF:{4} | 控制器:{5}".format(
                "已连接" if self.camera.running else "未连接",
                "已连接" if self.arm.connected else "未连接",
                self.arm.calibration_state,
                (
                    "开启"
                    if self.arm.torque_enabled
                    else "关闭"
                    if self.arm.torque_off_confirmed
                    else "关闭未确认"
                ),
                "有效" if reading.valid else reading.reason,
                self.controller.state.value,
            )
        )
        self.torque_check.blockSignals(True)
        self.torque_check.setChecked(self.arm.torque_enabled)
        self.torque_check.blockSignals(False)
        self._update_calibration_ui()

    def _log_controller_transition(self) -> None:
        state = self.controller.state.value
        message = self.controller.message
        state_changed = state != self.last_logged_controller_state
        error_changed = state == ServoState.ERROR.value and (
            message != self.last_logged_controller_message
        )
        if state_changed or error_changed:
            prefix = "控制器错误" if state == ServoState.ERROR.value else "控制器状态"
            self._log(f"{prefix}：{state}；{message}")
            if state == ServoState.ERROR.value:
                self._capture_arm_debug_snapshot("controller_error")
        self.last_logged_controller_state = state
        self.last_logged_controller_message = message
        if (
            self.arm.connected
            and not self.arm.calibration_active
            and self.connect_worker is None
            and self.calibration_worker is None
            and time.monotonic() - self.last_joint_read > 0.5
        ):
            self.last_joint_read = time.monotonic()
            self._show_joints(self.arm.get_joints())

    def _show_joints(self, joints: dict) -> None:
        for name, label in self.joint_current_labels.items():
            label.setText(f"{joints[name]:.2f}" if name in joints else "---")

    def _controller_debug_event(self, event: str, payload: dict) -> None:
        self.debug_log.write(event, payload)

    def _capture_arm_debug_snapshot(self, reason: str) -> None:
        try:
            snapshot = self.arm.diagnostic_snapshot()
        except Exception as exc:
            snapshot = {"snapshot_error": str(exc)}
        self.debug_log.write(
            "arm_diagnostic_snapshot",
            {
                "reason": reason,
                "controller_state": self.controller.state.value,
                "controller_message": self.controller.message,
                "positions": dict(self.controller.positions),
                "config": dict(self.config),
                "arm": snapshot,
            },
        )
        self._update_debug_log_ui()

    def _update_debug_log_ui(self) -> None:
        if not hasattr(self, "debug_log_path_label"):
            return
        if self.debug_log.enabled and self.debug_log.path is not None:
            self.debug_log_path_label.setText(f"当前详细日志：{self.debug_log.path}")
        else:
            self.debug_log_path_label.setText("详细日志未启用")

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")
        self.statusBar().showMessage(message, 6000)
        self.debug_log.write("ui_log", {"message": message})

    def closeEvent(self, event) -> None:
        if (
            (self.connect_worker is not None and self.connect_worker.isRunning())
            or self.calibration_worker is not None
        ):
            QMessageBox.information(self, "请稍候", "机械臂串口操作尚未结束，请等待完成后再关闭窗口。")
            event.ignore()
            return
        self.tick_timer.stop()
        self.status_timer.stop()
        self.calibration_timer.stop()
        self.controller.stop()
        self.debug_log.write(
            "session_closing",
            {
                "config": dict(self.config),
                "positions": dict(self.controller.positions),
            },
        )
        self.camera.stop()
        self.tof.disconnect()
        self.tracker.stop()
        if self.arm.calibration_active:
            self.arm.cancel_calibration()
        else:
            self.arm.disconnect()
        self.store.save_config(self.config)
        self.debug_log.close()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = VisualGraspLab()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
