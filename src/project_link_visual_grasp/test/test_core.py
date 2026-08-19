from pathlib import Path
import threading
import time

from project_link_visual_grasp.core import (
    ARM_JOINTS,
    DEMO_CSV_FIELDS,
    bbox_area_change_ratio,
    bbox_center_jump_ratio,
    bbox_iou,
    CalibrationRangeRecorder,
    Detection,
    decode_feetech_position,
    recenter_feetech_calibration_range,
    RuntimeStore,
    SO101Arm,
    ServoState,
    TofReading,
    VisualServoController,
)


def test_runtime_store_round_trip(tmp_path: Path):
    store = RuntimeStore(str(tmp_path / "override.yaml"), str(tmp_path / "positions.json"))
    store.save_overrides({"pan_gain": 12.5, "camera_device": "/dev/video0"})
    assert store.load_overrides()["pan_gain"] == 12.5
    positions = {"standby": {"shoulder_pan.pos": 1.0}}
    store.save_positions(positions)
    assert store.load_positions() == positions


class FakeArm:
    connected = True
    torque_enabled = True

    def __init__(self):
        self.gripper_commands = []
        self.arm_commands = []

    def get_joints(self):
        return {
            "shoulder_pan.pos": 0.0,
            "shoulder_lift.pos": 0.0,
            "elbow_flex.pos": 0.0,
            "wrist_flex.pos": 0.0,
            "wrist_roll.pos": 0.0,
        }

    def send_arm_joints(self, desired):
        self.arm_commands.append(desired)
        return True, "ok"

    def set_gripper(self, position):
        self.gripper_commands.append(position)
        return True, "ok"


def controller_config(**overrides):
    config = {
        "tof_enabled": True,
        "tof_control_enabled": True,
        "tof_calibrated": True,
        "tof_grasp_distance_m": 0.06,
        "grasp_area_threshold": 0.45,
        "gripper_close": 0.0,
        "approach_step": 1.5,
        "approach_max_command_lead": 4.0,
        "approach_profile_max_lift_delta": 34.0,
        "approach_profile_elbow_delta": 12.3,
        "approach_profile_wrist_delta": -54.0,
        "approach_profile_wrist_trim": 0.0,
        "visual_servo_max_joint_step": 6.0,
        "centering_tilt_motion_enabled": False,
        "auto_lock_vertical_center_on_pregrasp": True,
        "auto_lock_vertical_center_offset_ratio": 0.10,
        "visual_handoff_enabled": True,
        "visual_handoff_bbox_height_ratio": 0.85,
        "visual_handoff_area_ratio": 0.18,
        "visual_handoff_tof_m": 0.19,
        "visual_handoff_max_tof_m": 0.25,
        "final_grasp_tof_m": 0.16,
        "final_approach_step": 1.0,
        "final_approach_max_command_lead": 4.0,
        "final_approach_max_lift_delta": 20.0,
        "final_approach_command_interval_sec": 0.0,
        "final_approach_timeout_sec": 6.0,
        "final_approach_endpoint_settle_sec": 0.0,
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
        "center_offset_x": 0.0,
        "center_offset_y": 0.0,
        "detection_anchor_x_ratio": 0.5,
        "detection_anchor_y_ratio": 0.5,
        "centering_threshold": 0.04,
        "centering_limit_hold_cycles": 3,
        "centering_error_window": 3,
        "centering_min_samples": 2,
        "centering_confirm_cycles": 2,
        "centering_step_limit": 1.5,
        "centering_min_step_limit": 0.25,
        "centering_slow_zone": 0.12,
        "centering_max_command_lead": 4.0,
        "centering_command_interval_sec": 0.0,
        "pan_gain": 25.0,
        "tilt_gain": 15.0,
        "pan_direction": 1.0,
        "tilt_direction": -1.0,
    }
    config.update(overrides)
    return config


def test_detection_anchor_can_use_a_box_edge_instead_of_only_the_center():
    controller = VisualServoController(
        FakeArm(),
        controller_config(
            detection_anchor_x_ratio=1.0,
            detection_anchor_y_ratio=0.0,
        ),
        {},
    )

    assert controller.detection_anchor(Detection((10, 20, 50, 40), 0.9)) == (
        60.0,
        20.0,
    )


def test_visual_servo_keeps_tracking_after_the_selected_points_align():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            auto_lock_vertical_center_on_pregrasp=False,
            centering_min_samples=1,
            centering_confirm_cycles=1,
        ),
        {},
    )

    success, _message = controller.start_visual_servo()
    assert success
    controller.update(
        Detection((40, 40, 20, 20), 0.9, trusted=True, sequence=1),
        (100, 100),
        None,
    )

    assert controller.state == ServoState.VISUAL_SERVO
    assert "aligned" in controller.message
    assert not arm.arm_commands


def test_visual_servo_commands_a_bounded_correction_toward_the_selected_point():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            auto_lock_vertical_center_on_pregrasp=False,
            centering_min_samples=1,
            centering_confirm_cycles=1,
        ),
        {},
    )
    controller.start_visual_servo()

    controller.update(
        Detection((70, 40, 20, 20), 0.9, trusted=True, sequence=1),
        (100, 100),
        None,
    )

    assert controller.state == ServoState.VISUAL_SERVO
    assert len(arm.arm_commands) == 1
    assert 0.0 < arm.arm_commands[0]["shoulder_pan.pos"] <= 1.5


def test_tof_control_closes_gripper_at_calibrated_distance():
    arm = FakeArm()
    controller = VisualServoController(arm, controller_config(), {})
    controller.state = ServoState.APPROACHING
    detection = Detection((10, 10, 20, 20), 0.9)

    controller.update(detection, (100, 100), TofReading(0.05, 0.01, True, "valid"))

    assert controller.state == ServoState.GRASPED
    assert arm.gripper_commands == [0.0]
    assert not arm.arm_commands


def test_visual_demo_records_flat_bbox_area_error_tof_and_joint_fields():
    controller = VisualServoController(
        FakeArm(),
        controller_config(center_offset_x=10.0, center_offset_y=-5.0),
        {},
    )
    controller.start_demo_recording("water bottle")

    controller.update(
        Detection((20, 10, 40, 30), 0.85, trusted=True, sequence=7),
        (200, 100),
        TofReading(0.18, 0.02, True, "valid"),
    )
    rows = controller.stop_demo_recording()

    assert len(rows) == 1
    row = rows[0]
    assert tuple(row) == DEMO_CSV_FIELDS
    assert row["target"] == "water bottle"
    assert row["bbox_center_x"] == 40.0
    assert row["bbox_center_y"] == 25.0
    assert row["bbox_area_ratio"] == 0.06
    assert row["error_x"] == -0.35
    assert row["error_y"] == -0.2
    assert row["detection_trusted"] is True
    assert row["detection_sequence"] == 7
    assert row["tof_range_m"] == 0.18
    assert row["shoulder_pan_pos"] == 0.0


def test_tof_control_holds_when_range_is_stale():
    arm = FakeArm()
    controller = VisualServoController(arm, controller_config(), {})
    controller.state = ServoState.APPROACHING
    detection = Detection((5, 5, 80, 80), 0.9)

    controller.update(detection, (100, 100), TofReading(None, 0.5, False, "stale"))

    assert controller.state == ServoState.RANGE_WAIT
    assert not arm.gripper_commands
    assert not arm.arm_commands


def test_shadow_mode_preserves_legacy_area_trigger():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(tof_control_enabled=False),
        {},
    )
    controller.state = ServoState.APPROACHING
    detection = Detection((5, 5, 80, 80), 0.9)

    controller.update(detection, (100, 100), TofReading(0.10, 0.01, True, "valid"))

    assert controller.state == ServoState.GRASPED
    assert arm.gripper_commands == [0.0]


def test_bbox_association_metrics_reject_large_detector_jumps():
    previous = (100.0, 100.0, 200.0, 300.0)
    nearby = (110.0, 105.0, 195.0, 295.0)
    jumped = (700.0, 20.0, 500.0, 680.0)

    assert bbox_iou(previous, nearby) > 0.8
    assert bbox_center_jump_ratio(previous, nearby, (1280, 720)) < 0.02
    assert bbox_center_jump_ratio(previous, jumped, (1280, 720)) > 0.2
    assert bbox_area_change_ratio(previous, jumped) > 5.0


def test_tof_control_requires_explicit_calibration_gate():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(tof_calibrated=False),
        {},
    )

    accepted, message = controller.start_approach()

    assert not accepted
    assert "tof_calibrated=true" in message


def test_visual_handoff_requires_all_tof_switches_before_start():
    controller = VisualServoController(
        FakeArm(),
        controller_config(
            visual_handoff_enabled=True,
            tof_enabled=True,
            tof_control_enabled=False,
            tof_calibrated=False,
        ),
        {},
    )

    accepted, message = controller.validate_grasp_start()

    assert not accepted
    assert "tof_control_enabled" in message
    assert "tof_calibrated" in message


def test_stop_defaults_to_idle_and_can_explicitly_keep_tracking():
    arm = FakeArm()
    controller = VisualServoController(arm, controller_config(), {})
    controller.state = ServoState.APPROACHING

    success, _ = controller.stop()
    assert success
    assert controller.state == ServoState.IDLE
    assert arm.arm_commands[-1] == arm.get_joints()

    controller.stop(keep_tracking=True)
    assert controller.state == ServoState.TRACKING


def test_automatic_grasp_moves_to_pregrasp_before_centering():
    arm = FakeArm()
    pregrasp = arm.get_joints()
    controller = VisualServoController(
        arm,
        controller_config(),
        {"pregrasp": pregrasp},
    )

    accepted, message = controller.start_grasp_sequence()

    assert accepted
    assert controller.state == ServoState.MOVING
    assert "pregrasp" in message

    controller._move_started -= controller._move_duration
    for _ in range(3):
        controller.update(None, (100, 100), None)

    assert controller.state == ServoState.CENTERING
    assert "centering" in controller.message.lower()


def test_preset_move_interpolates_all_joints_together():
    arm = FakeArm()
    target = {name: 60.0 for name in ARM_JOINTS}
    controller = VisualServoController(
        arm,
        controller_config(),
        {"standby": target},
    )

    accepted, _ = controller.go_to_position("standby")
    assert accepted
    controller._move_started -= controller._move_duration / 2.0
    controller.update(None, (100, 100), None)

    command = arm.arm_commands[-1]
    assert all(25.0 <= command[name] <= 35.0 for name in ARM_JOINTS)


class FollowingArm(FakeArm):
    def __init__(self):
        super().__init__()
        self.joints = super().get_joints()

    def get_joints(self):
        return dict(self.joints)

    def send_arm_joints(self, desired):
        self.arm_commands.append(dict(desired))
        self.joints.update(desired)
        return True, "ok"


def test_preset_at_target_wins_over_timeout_boundary():
    arm = FollowingArm()
    target = {name: 10.0 for name in ARM_JOINTS}
    controller = VisualServoController(
        arm,
        controller_config(move_timeout_sec=3.0),
        {"pregrasp": target},
    )

    accepted, _ = controller.go_to_position("pregrasp")
    assert accepted
    controller._move_started -= 3.1
    controller.update(None, (100, 100), None)

    assert controller.state == ServoState.IDLE
    assert "complete" in controller.message.lower()


def test_preset_timeout_reports_remaining_joint_errors():
    arm = FakeArm()
    target = {name: 10.0 for name in ARM_JOINTS}
    controller = VisualServoController(
        arm,
        controller_config(move_timeout_sec=3.0),
        {"pregrasp": target},
    )

    accepted, _ = controller.go_to_position("pregrasp")
    assert accepted
    controller._move_started -= 3.1
    controller.update(None, (100, 100), None)

    assert controller.state == ServoState.ERROR
    assert "shoulder_pan.pos error=+10.00 tolerance=2.00" in controller.message


class ElbowComplianceArm(FakeArm):
    def get_joints(self):
        joints = super().get_joints()
        joints["elbow_flex.pos"] = 6.29
        return joints


def test_elbow_uses_separate_arrival_tolerance():
    arm = ElbowComplianceArm()
    target = arm.get_joints()
    target["elbow_flex.pos"] = 11.0
    controller = VisualServoController(
        arm,
        controller_config(
            arrive_threshold=2.0,
            elbow_arrive_threshold=5.0,
        ),
        {"pregrasp": target},
    )

    accepted, _ = controller.go_to_position("pregrasp")
    assert accepted
    controller._move_started -= controller._move_duration
    for _ in range(3):
        controller.update(None, (100, 100), None)

    assert controller.state == ServoState.IDLE
    assert "complete" in controller.message.lower()


class WristResidualArm(FakeArm):
    def get_joints(self):
        joints = super().get_joints()
        joints["wrist_flex.pos"] = 0.0
        return joints


def test_stable_near_target_hysteresis_accepts_small_wrist_residual():
    arm = WristResidualArm()
    target = arm.get_joints()
    target["wrist_flex.pos"] = 2.03
    events = []
    controller = VisualServoController(
        arm,
        controller_config(
            arrive_threshold=2.0,
            arrive_stable_margin=0.75,
            arrive_stable_delta=0.35,
            arrive_stable_cycles=5,
        ),
        {"placement": target},
    )
    controller.set_debug_callback(
        lambda event, payload: events.append((event, payload))
    )

    accepted, _ = controller.go_to_position("placement")
    assert accepted
    controller._move_started -= controller._move_duration
    for _ in range(6):
        controller.update(None, (100, 100), None)

    assert controller.state == ServoState.IDLE
    assert "stable near target" in controller.message
    assert any(event == "preset_move_tick" for event, _ in events)
    completed = [payload for event, payload in events if event == "preset_move_completed"]
    assert completed[-1]["arrival_mode"] == "stable_near"


def test_automatic_grasp_rejects_pregrasp_outside_servo_soft_limit():
    arm = FakeArm()
    pregrasp = arm.get_joints()
    pregrasp["shoulder_lift.pos"] = 96.0
    controller = VisualServoController(
        arm,
        controller_config(joint_command_limit=95.0),
        {"pregrasp": pregrasp},
    )

    accepted, message = controller.start_grasp_sequence()

    assert not accepted
    assert controller.state == ServoState.IDLE
    assert "shoulder_lift.pos" in message
    assert "soft limit" in message
    assert not arm.arm_commands


class EndpointArm(FakeArm):
    def get_joints(self):
        joints = super().get_joints()
        joints["elbow_flex.pos"] = -98.84
        return joints


def test_record_position_rejects_pose_near_calibrated_endpoint():
    controller = VisualServoController(
        EndpointArm(),
        controller_config(preset_joint_limit=95.0),
        {},
    )

    accepted, message = controller.record_position("placement")

    assert not accepted
    assert "elbow_flex.pos" in message
    assert "endpoint" in message
    assert "placement" not in controller.positions


def test_standby_can_use_extended_supervised_endpoint_allowance():
    arm = EndpointArm()
    controller = VisualServoController(
        arm,
        controller_config(
            preset_joint_limit=95.0,
            standby_joint_limit=99.5,
        ),
        {},
    )

    accepted, message = controller.record_position("standby")

    assert accepted
    assert "extended supervised endpoint allowance" in message
    assert controller.positions["standby"]["elbow_flex.pos"] == -98.84


def test_standby_still_rejects_the_absolute_calibration_endpoint():
    arm = EndpointArm()
    joints = arm.get_joints()
    joints["elbow_flex.pos"] = -99.8
    controller = VisualServoController(
        arm,
        controller_config(
            preset_joint_limit=95.0,
            standby_joint_limit=99.5,
        ),
        {"standby": joints},
    )

    accepted, message = controller.go_to_position("standby")

    assert not accepted
    assert "99.5" in message


def test_existing_endpoint_preset_is_rejected_before_motion():
    arm = FakeArm()
    placement = arm.get_joints()
    placement["elbow_flex.pos"] = -98.84
    controller = VisualServoController(
        arm,
        controller_config(preset_joint_limit=95.0),
        {"placement": placement},
    )

    accepted, message = controller.go_to_position("placement")

    assert not accepted
    assert "elbow_flex.pos" in message
    assert "record the preset again" in message
    assert controller.state == ServoState.IDLE
    assert not arm.arm_commands


class NearLimitArm(FakeArm):
    def get_joints(self):
        joints = super().get_joints()
        joints["shoulder_lift.pos"] = 94.5
        return joints


class CenteringNearLimitArm(FakeArm):
    def get_joints(self):
        joints = super().get_joints()
        joints["shoulder_lift.pos"] = -94.70
        return joints


def test_centering_clamps_small_soft_limit_overshoot_instead_of_error():
    arm = CenteringNearLimitArm()
    controller = VisualServoController(
        arm,
        controller_config(
            joint_command_limit=95.0,
            tilt_gain=15.0,
            centering_tilt_motion_enabled=True,
            auto_lock_vertical_center_on_pregrasp=False,
            centering_limit_hold_cycles=30,
        ),
        {},
    )
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()

    detection = Detection((45, 57, 10, 10), 0.9)
    for _ in range(3):
        controller.update(detection, (100, 100), None)

    assert controller.state == ServoState.CENTERING
    assert arm.arm_commands[-1]["shoulder_lift.pos"] == -95.0
    assert "clamped safely" in controller.message


def test_centering_errors_only_after_repeated_soft_limit_hold():
    arm = CenteringNearLimitArm()
    controller = VisualServoController(
        arm,
        controller_config(
            joint_command_limit=95.0,
            tilt_gain=15.0,
            centering_tilt_motion_enabled=True,
            auto_lock_vertical_center_on_pregrasp=False,
            centering_limit_hold_cycles=3,
        ),
        {},
    )
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()
    detection = Detection((45, 57, 10, 10), 0.9)

    for _ in range(5):
        controller.update(detection, (100, 100), None)

    assert controller.state == ServoState.ERROR
    assert "could not converge" in controller.message
    assert len(arm.arm_commands) == 2


def test_centering_requires_consecutive_stable_frames_before_approach():
    controller = VisualServoController(
        FakeArm(),
        controller_config(
            centering_error_window=3,
            centering_min_samples=3,
            centering_confirm_cycles=3,
        ),
        {},
    )
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()
    centered = Detection((45, 45, 10, 10), 0.9)

    for _ in range(4):
        controller.update(centered, (100, 100), None)
        assert controller.state == ServoState.CENTERING
    controller.update(centered, (100, 100), None)

    assert controller.state == ServoState.APPROACHING


def test_centering_does_not_extend_shoulder_when_tilt_motion_is_disabled():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            centering_min_samples=2,
            centering_tilt_motion_enabled=False,
            auto_lock_vertical_center_on_pregrasp=False,
        ),
        {},
    )
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()

    controller.update(Detection((45, 30, 10, 10), 0.9, sequence=1), (100, 100), None)
    controller.update(Detection((45, 30, 10, 10), 0.9, sequence=2), (100, 100), None)

    assert controller.state == ServoState.CENTERING
    assert not arm.arm_commands
    assert "Confirming centered target" in controller.message


def test_pregrasp_auto_locks_vertical_center_without_persisting_old_offset():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            center_offset_x=0.0,
            center_offset_y=208.0,
            centering_min_samples=2,
            centering_confirm_cycles=2,
            centering_tilt_motion_enabled=False,
            auto_lock_vertical_center_on_pregrasp=True,
        ),
        {},
    )
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()
    detection = Detection((45, 10, 10, 40), 0.9)

    for _ in range(3):
        controller.update(detection, (100, 100), None)

    assert controller.state == ServoState.APPROACHING
    assert controller.visual_target_center((100, 100)) == (50.0, 34.0)
    assert controller.config["center_offset_y"] == 208.0
    assert not arm.arm_commands


def test_manual_visual_center_clears_temporary_auto_lock():
    controller = VisualServoController(
        FakeArm(),
        controller_config(center_offset_x=10.0, center_offset_y=20.0),
        {},
    )
    controller._session_target_center_y = 15.0

    controller.use_configured_visual_center()

    assert controller.visual_target_center((100, 100)) == (60.0, 70.0)


def test_repeated_detection_sequence_preserves_meaningful_status_message():
    controller = VisualServoController(FakeArm(), controller_config(), {})
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()
    controller._last_visual_detection_sequence = 5
    controller.message = "Horizontal center reached"

    controller.update(
        Detection((45, 45, 10, 10), 0.9, sequence=5),
        (100, 100),
        None,
    )

    assert controller.message == "Horizontal center reached"


def test_untrusted_detector_jump_holds_visual_servo_motion():
    arm = FakeArm()
    controller = VisualServoController(arm, controller_config(), {})
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()

    controller.update(
        Detection((10, 10, 80, 80), 0.9, trusted=False),
        (100, 100),
        None,
    )

    assert controller.state == ServoState.CENTERING
    assert "holding" in controller.message
    assert not arm.arm_commands


def test_centering_uses_each_fresh_detection_only_once():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            centering_min_samples=2,
            centering_tilt_motion_enabled=True,
            auto_lock_vertical_center_on_pregrasp=False,
        ),
        {},
    )
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()

    controller.update(Detection((45, 30, 10, 10), 0.9, sequence=1), (100, 100), None)
    controller.update(Detection((45, 30, 10, 10), 0.9, sequence=2), (100, 100), None)
    meaningful_message = controller.message
    controller.update(Detection((45, 30, 10, 10), 0.9, sequence=2), (100, 100), None)

    assert len(arm.arm_commands) == 1
    assert controller.message == meaningful_message


def test_centering_accumulates_small_targets_with_bounded_feedback_lead():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            centering_min_samples=1,
            centering_max_command_lead=4.0,
            centering_tilt_motion_enabled=True,
            auto_lock_vertical_center_on_pregrasp=False,
        ),
        {},
    )
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()

    for sequence in range(1, 5):
        controller.update(
            Detection((45, 30, 10, 10), 0.9, sequence=sequence),
            (100, 100),
            None,
        )

    lift_targets = [
        command["shoulder_lift.pos"] for command in arm.arm_commands
    ]
    assert lift_targets == [1.5, 3.0, 4.0, 4.0]


def test_visual_approach_uses_taught_shoulder_elbow_wrist_profile():
    arm = FakeArm()
    controller = VisualServoController(arm, controller_config(), {})
    controller.state = ServoState.APPROACHING
    controller._grasp_started = time.monotonic()

    controller.update(
        Detection((40, 40, 10, 10), 0.9, sequence=1),
        (100, 100),
        TofReading(0.20, 0.01, True, "valid"),
    )

    command = arm.arm_commands[-1]
    expected_progress = 1.5 / 34.0
    assert command["shoulder_lift.pos"] == 1.5
    assert abs(command["elbow_flex.pos"] - 12.3 * expected_progress) < 1e-9
    assert abs(command["wrist_flex.pos"] - -54.0 * expected_progress) < 1e-9
    assert abs(command["elbow_flex.pos"]) < 1.0
    assert abs(command["wrist_flex.pos"]) < 3.0


def test_visual_approach_rejects_dangerous_profile_jump_before_sending():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            approach_profile_elbow_delta=1000.0,
            visual_servo_max_joint_step=6.0,
        ),
        {},
    )
    controller.state = ServoState.APPROACHING
    controller._grasp_started = time.monotonic()

    controller.update(
        Detection((40, 40, 10, 10), 0.9, sequence=1),
        (100, 100),
        TofReading(0.20, 0.01, True, "valid"),
    )

    assert controller.state == ServoState.ERROR
    assert "command jump rejected" in controller.message
    assert not arm.arm_commands


def test_taught_approach_endpoint_uses_saved_pregrasp_not_early_feedback():
    arm = FakeArm()
    joints = arm.get_joints()
    joints["shoulder_lift.pos"] = -3.0
    arm.get_joints = lambda: dict(joints)
    pregrasp = dict(joints)
    pregrasp["shoulder_lift.pos"] = 0.0
    controller = VisualServoController(
        arm,
        controller_config(approach_profile_max_lift_delta=34.0),
        {"pregrasp": pregrasp},
    )

    desired, progress, actual_travel = controller._taught_approach_target(
        joints,
        100.0,
    )

    assert desired["shoulder_lift.pos"] == 34.0
    assert progress == 1.0
    assert actual_travel == 37.0


def test_approach_wrist_trim_adjusts_only_the_taught_wrist_endpoint():
    arm = FakeArm()
    joints = arm.get_joints()
    controller = VisualServoController(
        arm,
        controller_config(approach_profile_wrist_trim=2.0),
        {},
    )

    desired, progress, _ = controller._taught_approach_target(joints, 34.0)

    assert progress == 1.0
    assert desired["shoulder_lift.pos"] == 34.0
    assert desired["elbow_flex.pos"] == 12.3
    assert desired["wrist_flex.pos"] == -52.0


def test_centering_vertical_direction_matches_current_camera_mount():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            centering_min_samples=2,
            tilt_direction=-1.0,
            centering_tilt_motion_enabled=True,
            auto_lock_vertical_center_on_pregrasp=False,
        ),
        {},
    )
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()

    controller.update(Detection((45, 30, 10, 10), 0.9, sequence=1), (100, 100), None)
    controller.update(Detection((45, 30, 10, 10), 0.9, sequence=2), (100, 100), None)

    assert arm.arm_commands[-1]["shoulder_lift.pos"] > 0.0


def test_logged_vertical_error_moves_away_from_negative_soft_limit():
    class LoggedArm(FakeArm):
        def get_joints(self):
            joints = super().get_joints()
            joints["shoulder_lift.pos"] = -93.591
            return joints

    arm = LoggedArm()
    controller = VisualServoController(
        arm,
        controller_config(
            center_offset_x=24.0,
            center_offset_y=208.0,
            centering_min_samples=2,
            tilt_direction=-1.0,
            centering_tilt_motion_enabled=True,
            auto_lock_vertical_center_on_pregrasp=False,
        ),
        {},
    )
    controller.state = ServoState.CENTERING
    controller._grasp_started = time.monotonic()

    controller.update(Detection((175, 0, 317, 679), 0.9, sequence=1), (1280, 720), None)
    controller.update(Detection((176, 0, 316, 688), 0.9, sequence=2), (1280, 720), None)

    sent_lift = arm.arm_commands[-1]["shoulder_lift.pos"]
    assert sent_lift > -93.591
    assert sent_lift <= 95.0
    assert controller.state == ServoState.CENTERING


def test_visual_approach_stops_before_joint_soft_limit():
    arm = NearLimitArm()
    controller = VisualServoController(
        arm,
        controller_config(approach_step=2.0, joint_command_limit=95.0),
        {},
    )
    controller.state = ServoState.APPROACHING
    controller._grasp_started = time.monotonic()

    controller.update(
        Detection((40, 40, 10, 10), 0.9),
        (100, 100),
        TofReading(0.20, 0.01, True, "valid"),
    )

    assert controller.state == ServoState.ERROR
    assert "safety limit" in controller.message
    assert not arm.arm_commands


def test_visual_approach_accumulates_with_bounded_feedback_lead():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            approach_step=1.5,
            approach_max_command_lead=4.0,
        ),
        {},
    )
    controller.state = ServoState.APPROACHING
    controller._grasp_started = time.monotonic()

    for sequence in range(1, 5):
        controller.update(
            Detection((40, 40, 10, 10), 0.9, sequence=sequence),
            (100, 100),
            TofReading(0.20, 0.01, True, "valid"),
        )

    lift_targets = [
        command["shoulder_lift.pos"] for command in arm.arm_commands
    ]
    assert lift_targets == [1.5, 3.0, 4.0, 4.0]


def test_visual_handoff_allows_yolo_loss_until_tof_grasp():
    arm = FakeArm()
    controller = VisualServoController(arm, controller_config(), {})
    controller.state = ServoState.APPROACHING
    controller._grasp_started = time.monotonic()

    controller.update(
        Detection((40, 5, 20, 90), 0.8, sequence=1),
        (100, 100),
        TofReading(0.18, 0.01, True, "valid"),
    )

    assert controller.state == ServoState.FINAL_APPROACH
    assert arm.arm_commands

    controller.update(
        None,
        (100, 100),
        TofReading(0.155, 0.01, True, "valid"),
    )

    assert controller.state == ServoState.GRASPED
    assert arm.gripper_commands == [0.0]


def test_large_bbox_does_not_handoff_when_tof_is_still_far():
    arm = FakeArm()
    controller = VisualServoController(arm, controller_config(), {})
    controller.state = ServoState.APPROACHING
    controller._grasp_started = time.monotonic()

    controller.update(
        Detection((10, 5, 80, 90), 0.8, sequence=1),
        (100, 100),
        TofReading(0.40, 0.01, True, "valid"),
    )

    assert controller.state == ServoState.APPROACHING
    assert arm.arm_commands


def test_final_approach_holds_when_tof_is_invalid():
    arm = FakeArm()
    controller = VisualServoController(arm, controller_config(), {})
    controller.state = ServoState.FINAL_APPROACH
    controller._grasp_started = time.monotonic()

    controller.update(
        None,
        (100, 100),
        TofReading(None, 0.5, False, "stale"),
    )

    assert controller.state == ServoState.FINAL_APPROACH
    assert "holding for valid ToF" in controller.message
    assert not arm.arm_commands


def test_final_approach_stops_after_bounded_blind_travel():
    arm = FakeArm()
    controller = VisualServoController(
        arm,
        controller_config(
            final_approach_step=1.0,
            final_approach_max_command_lead=4.0,
            final_approach_max_lift_delta=2.0,
        ),
        {},
    )
    controller.state = ServoState.FINAL_APPROACH
    controller._grasp_started = time.monotonic()

    reading = TofReading(0.18, 0.01, True, "valid")
    controller.update(None, (100, 100), reading)
    controller.update(None, (100, 100), reading)
    controller.update(None, (100, 100), reading)

    assert controller.state == ServoState.ERROR
    assert "maximum taught travel" in controller.message
    assert len(arm.arm_commands) == 2


def test_calibration_range_recorder_tracks_all_joint_extremes():
    recorder = CalibrationRangeRecorder({"joint_a": 100, "joint_b": 200})
    recorder.update({"joint_a": 80, "joint_b": 240})
    recorder.update({"joint_a": 130, "joint_b": 150})

    minimums, maximums = recorder.result()

    assert minimums == {"joint_a": 80, "joint_b": 150}
    assert maximums == {"joint_a": 130, "joint_b": 240}


def test_calibration_range_recorder_rejects_unmoved_joint():
    recorder = CalibrationRangeRecorder({"joint_a": 100, "joint_b": 200})
    recorder.update({"joint_a": 80, "joint_b": 200})

    try:
        recorder.result()
    except ValueError as exc:
        assert "joint_b" in str(exc)
    else:
        raise AssertionError("Expected unmoved joint calibration to fail")


def test_feetech_position_decodes_sign_magnitude_wrap():
    assert decode_feetech_position(32952) == -184
    assert decode_feetech_position(32774) == -6
    assert decode_feetech_position(2047) == 2047


def test_feetech_calibration_range_is_recentered_inside_encoder_limits():
    homing_offset, range_min, range_max = recenter_feetech_calibration_range(
        homing_offset=1456,
        range_min=-184,
        range_max=3000,
        resolution=4096,
    )

    assert (homing_offset, range_min, range_max) == (816, 456, 3640)
    assert -2047 <= homing_offset <= 2047
    assert 0 <= range_min < range_max <= 4095


class FakeMotorDefinition:
    def __init__(self, motor_id):
        self.id = motor_id


class FakeTorqueBus:
    def __init__(self, verify=True, broadcast=True):
        self.is_connected = True
        self.verify = verify
        self.broadcast = broadcast
        self.disconnect_calls = []
        self.sync_write_calls = []
        self.motors = {
            "shoulder_pan": FakeMotorDefinition(1),
            "wrist_flex": FakeMotorDefinition(4),
            "wrist_roll": FakeMotorDefinition(5),
            "gripper": FakeMotorDefinition(6),
        }
        self.torque_states = dict.fromkeys(self.motors, 1)

    def disable_torque(self, motors=None, num_retry=0):
        if motors is None:
            raise RuntimeError("id=4 Overheat error")
        if motors in {"wrist_flex", "wrist_roll"}:
            raise RuntimeError(f"{motors} Overheat error")
        self.torque_states[motors] = 0

    def sync_write(self, data_name, values, normalize=True, num_retry=0):
        self.sync_write_calls.append((data_name, dict(values), normalize, num_retry))
        if not self.broadcast:
            raise RuntimeError("broadcast failed")
        self.torque_states.update(values)

    def sync_read(self, data_name, normalize=True, num_retry=0):
        if data_name == "Present_Temperature":
            return {
                "shoulder_pan": 32,
                "wrist_flex": 78,
                "wrist_roll": 76,
                "gripper": 31,
            }
        if not self.verify:
            raise RuntimeError("verification failed")
        return dict(self.torque_states)

    def disconnect(self, disable_torque=True):
        self.disconnect_calls.append(disable_torque)
        self.is_connected = False


class FakeSO101Robot:
    def __init__(self, bus):
        self.bus = bus
        self.cameras = {}
        self.is_calibrated = True

    @property
    def is_connected(self):
        return self.bus.is_connected


class FakeActionRobot(FakeSO101Robot):
    def __init__(self, bus):
        super().__init__(bus)
        self.actions = []
        self.observation_calls = 0

    def get_observation(self):
        self.observation_calls += 1
        raise RuntimeError("transient feedback failure")

    def send_action(self, action):
        self.actions.append(dict(action))
        return dict(action)


def test_complete_arm_command_does_not_require_redundant_feedback_read():
    arm = SO101Arm()
    robot = FakeActionRobot(FakeTorqueBus())
    arm._robot = robot
    desired = {name: float(index) for index, name in enumerate(ARM_JOINTS)}

    success, message = arm.send_arm_joints(desired)

    assert success
    assert message == "Arm command sent"
    assert robot.actions == [desired]
    assert robot.observation_calls == 0


def test_partial_arm_command_still_requires_feedback_for_missing_joints():
    arm = SO101Arm()
    robot = FakeActionRobot(FakeTorqueBus())
    arm._robot = robot

    success, message = arm.send_arm_joints({"shoulder_pan.pos": 5.0})

    assert not success
    assert "partial command" in message
    assert robot.observation_calls == 1
    assert not robot.actions


def test_torque_disable_uses_sync_broadcast_after_overheat_errors():
    arm = SO101Arm()
    bus = FakeTorqueBus()
    arm._robot = FakeSO101Robot(bus)
    arm._torque_enabled = True

    success, message = arm.disable_torque()

    assert success
    assert arm.torque_off_confirmed
    assert not arm.torque_enabled
    assert "hardware faults" in message
    assert "wrist_flex(id=4)" in arm.torque_fault_message
    assert "wrist_roll(id=5)" in arm.torque_fault_message
    assert bus.sync_write_calls[0][0] == "Torque_Enable"
    assert set(bus.sync_write_calls[0][1].values()) == {0}


def test_disconnect_releases_port_even_when_torque_off_is_unconfirmed():
    arm = SO101Arm()
    bus = FakeTorqueBus(verify=False, broadcast=False)
    arm._robot = FakeSO101Robot(bus)
    arm._torque_enabled = True

    success, message = arm.disconnect()

    assert not success
    assert "serial port closed" in message
    assert bus.disconnect_calls == [False]
    assert not arm.connected


class FakePortHandler:
    is_using = True


class FakeStaleBusyBus:
    def __init__(self):
        self.is_connected = True
        self.port_handler = FakePortHandler()
        self.disable_calls = 0
        self.motors = {"shoulder_pan": FakeMotorDefinition(1)}

    def disable_torque(self, motors=None, num_retry=0):
        self.disable_calls += 1
        if self.disable_calls == 1:
            raise RuntimeError("[TxRxResult] Port is in use!")


def test_torque_disable_clears_stale_port_busy_flag_and_retries():
    arm = SO101Arm()
    bus = FakeStaleBusyBus()
    arm._robot = FakeSO101Robot(bus)
    arm._torque_enabled = True

    success, message = arm.disable_torque()

    assert success
    assert "stale port-busy flag" in message
    assert bus.disable_calls == 2
    assert not bus.port_handler.is_using


class FakeObservationRobot(FakeSO101Robot):
    def __init__(self, bus):
        super().__init__(bus)
        self.observation_calls = 0

    def get_observation(self):
        self.observation_calls += 1
        return {"shoulder_pan.pos": 0.0}


def test_joint_poll_skips_when_another_thread_owns_arm_io():
    arm = SO101Arm()
    bus = FakeTorqueBus()
    robot = FakeObservationRobot(bus)
    arm._robot = robot
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        with arm._io_lock:
            lock_acquired.set()
            release_lock.wait(timeout=2.0)

    worker = threading.Thread(target=hold_lock)
    worker.start()
    assert lock_acquired.wait(timeout=1.0)
    try:
        assert arm.get_joints() == {}
        assert robot.observation_calls == 0
    finally:
        release_lock.set()
        worker.join(timeout=1.0)
