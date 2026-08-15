import subprocess

from project_link_console_agent.systemd import SystemdManager, UNITS


def test_systemd_allowlist_and_state_parse():
    commands = []

    def runner(command, timeout):
        commands.append((command, timeout))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Id=project-link-nav2.service\nActiveState=active\nSubState=running\nResult=success\n"
                "ActiveEnterTimestamp=Sat 2026-08-15 12:00:00 CST\n"
                "NRestarts=2\nDescription=Project LINK Nav2\n"
            ),
            stderr="",
        )

    manager = SystemdManager(runner=runner)
    state = manager.state(UNITS["nav2"])
    assert state.active
    assert state.restart_count == 2
    assert commands[0][0][:3] == ["systemctl", "--user", "show"]


def test_systemd_batch_state_uses_one_command():
    commands = []

    def runner(command, timeout):
        commands.append((command, timeout))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Id=project-link-base.service\nActiveState=active\nSubState=running\nResult=success\n\n"
                "Id=project-link-nav2.service\nActiveState=inactive\nSubState=dead\nResult=success\n"
            ),
            stderr="",
        )

    manager = SystemdManager(runner=runner)
    states = manager.states([UNITS["base"], UNITS["nav2"]])
    assert len(commands) == 1
    assert states[UNITS["base"]].active
    assert not states[UNITS["nav2"]].active


def test_systemd_rejects_arbitrary_unit():
    manager = SystemdManager(runner=lambda *_args: None)
    try:
        manager.start("ssh.service")
    except ValueError as exc:
        assert "unit_not_allowed" in str(exc)
    else:
        raise AssertionError("arbitrary unit was accepted")
