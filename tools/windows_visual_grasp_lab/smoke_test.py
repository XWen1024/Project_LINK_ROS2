"""Offscreen construction test for the Windows visual grasp lab."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app import VisualGraspLab


def main() -> None:
    application = QApplication([])
    window = VisualGraspLab()
    window.show()
    application.processEvents()
    assert not window.refresh_ports_button.geometry().intersects(
        window.torque_check.geometry()
    ), "Refresh and torque controls overlap"
    assert not window.calibration_middle_button.isEnabled()
    assert not window.calibration_finish_button.isEnabled()
    QTimer.singleShot(250, application.quit)
    application.exec()
    window.close()
    print("Windows visual grasp lab GUI smoke test passed.")


if __name__ == "__main__":
    main()
