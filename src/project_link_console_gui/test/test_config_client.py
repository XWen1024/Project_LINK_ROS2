import os
from unittest.mock import patch

from project_link_console_gui.config_client import ConfigClient


def test_runtime_ssh_target_overrides_saved_setting():
    with patch.dict(os.environ, {"PROJECT_LINK_ORIN_SSH_TARGET": "wte@192.168.55.1"}):
        client = ConfigClient()
        assert client.ssh_target == "wte@192.168.55.1"
