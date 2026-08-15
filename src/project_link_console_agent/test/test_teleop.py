from project_link_console_agent.teleop import TeleopLease, clamp


def test_teleop_lease_is_bounded_and_expires():
    lease = TeleopLease(timeout_sec=0.25, max_linear_mps=0.18, max_angular_rps=0.60)
    lease.update(enabled=True, deadman=True, linear_x=2.0, angular_z=-2.0, sequence=1, now=10.0)
    assert lease.linear_x == 0.18
    assert lease.angular_z == -0.60
    assert lease.active(10.20, mapping_mode=True, emergency_latched=False)
    assert not lease.active(10.26, mapping_mode=True, emergency_latched=False)
    assert not lease.active(10.20, mapping_mode=False, emergency_latched=False)
    assert not lease.active(10.20, mapping_mode=True, emergency_latched=True)
    assert clamp(float("nan"), 1.0) == 0.0


def test_old_teleop_sequence_is_ignored():
    lease = TeleopLease()
    lease.update(enabled=True, deadman=True, linear_x=0.1, angular_z=0.0, sequence=3, now=1.0)
    lease.update(enabled=False, deadman=False, linear_x=0.0, angular_z=0.0, sequence=2, now=2.0)
    assert lease.sequence == 3
    assert lease.enabled


def test_replayed_teleop_sequence_does_not_refresh_lease():
    lease = TeleopLease()
    lease.update(enabled=True, deadman=True, linear_x=0.1, angular_z=0.0, sequence=3, now=1.0)
    lease.update(enabled=True, deadman=True, linear_x=0.1, angular_z=0.0, sequence=3, now=2.0)
    assert lease.last_update_monotonic == 1.0
