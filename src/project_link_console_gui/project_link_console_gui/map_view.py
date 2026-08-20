"""Lightweight Qt 2D renderer for map, costmap, scan, path and goals."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPainterPath, QPen, QPolygonF, qRgba
from PySide6.QtWidgets import QWidget

from .models import GridLayer, LayerVisibility, Pose2D


class MapView(QWidget):
    goal_selected = Signal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(640, 420)
        self.setMouseTracking(True)
        self._grids: dict[str, GridLayer] = {}
        self._images: dict[str, QImage] = {}
        self._scan: list[tuple[float, float]] = []
        self._cloud: list[tuple[float, float]] = []
        self._path: list[tuple[float, float]] = []
        self._robot: Pose2D | None = None
        self._goal: Pose2D | None = None
        self._visibility = LayerVisibility()
        self._goal_selection_enabled = False
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._drag_origin: QPoint | None = None

    def set_grid(self, name: str, grid: GridLayer) -> None:
        self._grids[name] = grid
        self._images[name] = self._grid_image(name, grid)
        self.update()

    def set_scan(self, points: list[tuple[float, float]]) -> None:
        self._scan = points
        self.update()

    def set_cloud(self, points: list[tuple[float, float]]) -> None:
        self._cloud = points
        self.update()

    def set_path(self, points: list[tuple[float, float]]) -> None:
        self._path = points
        self.update()

    def set_robot(self, pose: Pose2D) -> None:
        self._robot = pose
        self.update()

    def set_goal(self, pose: Pose2D | None) -> None:
        self._goal = pose
        self.update()

    def set_goal_selection_enabled(self, enabled: bool) -> None:
        self._goal_selection_enabled = bool(enabled)
        self.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)

    def set_layer_visible(self, name: str, visible: bool) -> None:
        if hasattr(self._visibility, name):
            setattr(self._visibility, name, bool(visible))
            self.update()

    @staticmethod
    def _grid_image(name: str, grid: GridLayer) -> QImage:
        # OccupancyGrid is signed int8. Converting it to bytes is a C-level
        # copy and lets Qt apply a 256-entry palette instead of running a
        # Python loop for every pixel on every costmap update.
        try:
            pixels = memoryview(grid.cells).cast("B").tobytes()
        except (TypeError, ValueError):
            # Offline demo/test fixtures use tuples instead of ROS array('b').
            pixels = bytes((value & 0xFF for value in grid.cells))
        image = QImage(pixels, grid.width, grid.height, grid.width, QImage.Format_Indexed8).copy()
        palette: list[int] = []
        for encoded in range(256):
            value = encoded if encoded < 128 else encoded - 256
            if name == "occupancy_map":
                if value < 0:
                    palette.append(qRgba(58, 62, 69, 255))
                else:
                    shade = max(32, 238 - int(max(0, min(100, value)) * 2.0))
                    palette.append(qRgba(shade, shade, shade, 255))
            elif value <= 0:
                palette.append(qRgba(0, 0, 0, 0))
            elif name == "local_costmap":
                palette.append(qRgba(239, 83, 80, min(180, 30 + value)))
            else:
                palette.append(qRgba(255, 167, 38, min(150, 24 + value)))
        image.setColorTable(palette)
        return image.mirrored(False, True)

    def _bounds(self) -> tuple[float, float, float, float]:
        for name in ("occupancy_map", "global_costmap", "local_costmap"):
            if name in self._grids:
                return self._grids[name].bounds
        if self._robot:
            return self._robot.x - 5.0, self._robot.y - 5.0, self._robot.x + 5.0, self._robot.y + 5.0
        return -5.0, -5.0, 5.0, 5.0

    def _transform(self) -> tuple[float, float, float]:
        min_x, min_y, max_x, max_y = self._bounds()
        world_width = max(0.1, max_x - min_x)
        world_height = max(0.1, max_y - min_y)
        margin = 24.0
        base_scale = min(
            max(1.0, self.width() - margin * 2.0) / world_width,
            max(1.0, self.height() - margin * 2.0) / world_height,
        )
        scale = base_scale * self._zoom
        center_x = (min_x + max_x) * 0.5
        center_y = (min_y + max_y) * 0.5
        return scale, center_x, center_y

    def _screen(self, x: float, y: float) -> QPointF:
        scale, center_x, center_y = self._transform()
        return QPointF(
            self.width() * 0.5 + self._pan.x() + (x - center_x) * scale,
            self.height() * 0.5 + self._pan.y() - (y - center_y) * scale,
        )

    def _world(self, point: QPointF) -> tuple[float, float]:
        scale, center_x, center_y = self._transform()
        return (
            center_x + (point.x() - self.width() * 0.5 - self._pan.x()) / scale,
            center_y - (point.y() - self.height() * 0.5 - self._pan.y()) / scale,
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#171a1f"))

        for name in ("occupancy_map", "global_costmap", "local_costmap"):
            if not getattr(self._visibility, name) or name not in self._grids:
                continue
            grid = self._grids[name]
            left_top = self._screen(grid.bounds[0], grid.bounds[3])
            right_bottom = self._screen(grid.bounds[2], grid.bounds[1])
            painter.drawImage(QRectF(left_top, right_bottom), self._images[name])

        if self._visibility.path and len(self._path) >= 2:
            path = QPainterPath(self._screen(*self._path[0]))
            for point in self._path[1:]:
                path.lineTo(self._screen(*point))
            painter.setPen(QPen(QColor("#42a5f5"), 3.0))
            painter.drawPath(path)

        if self._visibility.laser_scan:
            painter.setPen(QPen(QColor("#26c6da"), 2.0))
            for point in self._scan:
                painter.drawPoint(self._screen(*point))

        if self._visibility.point_cloud:
            painter.setPen(QPen(QColor("#ab7df6"), 1.5))
            for point in self._cloud:
                painter.drawPoint(self._screen(*point))

        if self._goal:
            center = self._screen(self._goal.x, self._goal.y)
            painter.setPen(QPen(QColor("#ffca28"), 3.0))
            painter.drawLine(center + QPointF(-8, 0), center + QPointF(8, 0))
            painter.drawLine(center + QPointF(0, -8), center + QPointF(0, 8))

        if self._robot:
            center = self._screen(self._robot.x, self._robot.y)
            heading = QPointF(math.cos(self._robot.yaw), -math.sin(self._robot.yaw))
            side = QPointF(-heading.y(), heading.x())
            polygon = QPolygonF(
                [
                    center + heading * 13.0,
                    center - heading * 8.0 + side * 8.0,
                    center - heading * 8.0 - side * 8.0,
                ]
            )
            painter.setBrush(QColor("#66bb6a"))
            painter.setPen(QPen(QColor("#dcedc8"), 1.5))
            painter.drawPolygon(polygon)

        painter.setPen(QColor("#aeb6c2"))
        painter.drawText(14, 22, "滚轮缩放 · 右键拖动 · 双击复位")

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self._zoom = max(0.3, min(8.0, self._zoom * factor))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.RightButton:
            self._drag_origin = event.position().toPoint()
        elif event.button() == Qt.LeftButton and self._goal_selection_enabled:
            x, y = self._world(event.position())
            self.goal_selected.emit(x, y)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            current = event.position().toPoint()
            delta = current - self._drag_origin
            self._pan += QPointF(delta)
            self._drag_origin = current
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.RightButton:
            self._drag_origin = None

    def mouseDoubleClickEvent(self, _event: QMouseEvent) -> None:
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()
