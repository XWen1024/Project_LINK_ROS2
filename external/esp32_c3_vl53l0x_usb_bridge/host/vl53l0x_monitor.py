#!/usr/bin/env python3
"""Small real-time desktop monitor for the ESP32-C3 VL53L0X bridge."""

from __future__ import annotations

import argparse
import csv
import queue
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import serial
from serial.tools import list_ports


@dataclass(frozen=True)
class Measurement:
    sequence: int
    sensor_time_ms: int
    distance_mm: int
    range_status: int
    received_time: float


def parse_measurement(line: str) -> Measurement | None:
    if not line.startswith("DATA,"):
        return None
    fields = line.split(",")
    if len(fields) != 5:
        return None
    try:
        return Measurement(
            sequence=int(fields[1]),
            sensor_time_ms=int(fields[2]),
            distance_mm=int(fields[3]),
            range_status=int(fields[4]),
            received_time=time.time(),
        )
    except ValueError:
        return None


class SerialReader(threading.Thread):
    def __init__(
        self,
        port: str,
        baud: int,
        messages: queue.Queue[tuple[str, object]],
        stop_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.messages = messages
        self.stop_event = stop_event
        self.serial_port: serial.Serial | None = None

    def run(self) -> None:
        try:
            self.serial_port = serial.Serial(
                self.port,
                self.baud,
                timeout=0.25,
                write_timeout=0.25,
            )
            self.serial_port.reset_input_buffer()
            self.messages.put(("connected", self.port))

            while not self.stop_event.is_set():
                raw = self.serial_port.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                measurement = parse_measurement(line)
                if measurement is not None:
                    self.messages.put(("measurement", measurement))
                elif line:
                    self.messages.put(("log", line))
        except (serial.SerialException, OSError) as exc:
            self.messages.put(("error", str(exc)))
        finally:
            if self.serial_port is not None and self.serial_port.is_open:
                self.serial_port.close()
            self.messages.put(("disconnected", self.port))


class MonitorApp:
    HISTORY_LENGTH = 300

    def __init__(self, root: tk.Tk, initial_port: str | None, baud: int) -> None:
        self.root = root
        self.root.title("VL53L0X 实时测距")
        self.root.geometry("900x620")
        self.root.minsize(720, 500)

        self.baud = baud
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.reader: SerialReader | None = None
        self.stop_event: threading.Event | None = None
        self.history: deque[Measurement] = deque(maxlen=self.HISTORY_LENGTH)
        self.csv_file = None
        self.csv_writer = None

        self.port_var = tk.StringVar(value=initial_port or "")
        self.connection_var = tk.StringVar(value="未连接")
        self.distance_var = tk.StringVar(value="--- mm")
        self.status_var = tk.StringVar(value="等待数据")
        self.rate_var = tk.StringVar(value="0.0 Hz")
        self.last_measurement_monotonic: float | None = None
        self.rate_times: deque[float] = deque(maxlen=50)

        self._build_ui()
        self.refresh_ports()
        if initial_port:
            self.port_var.set(initial_port)
        self.root.after(50, self.poll_messages)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        controls = ttk.Frame(self.root, padding=10)
        controls.pack(fill=tk.X)

        ttk.Label(controls, text="串口").pack(side=tk.LEFT)
        self.port_combo = ttk.Combobox(
            controls, textvariable=self.port_var, width=24, state="normal"
        )
        self.port_combo.pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(controls, text="刷新", command=self.refresh_ports).pack(side=tk.LEFT)
        self.connect_button = ttk.Button(
            controls, text="连接", command=self.toggle_connection
        )
        self.connect_button.pack(side=tk.LEFT, padx=(10, 6))
        self.log_button = ttk.Button(
            controls, text="开始记录 CSV", command=self.toggle_csv
        )
        self.log_button.pack(side=tk.LEFT, padx=6)
        ttk.Label(controls, textvariable=self.connection_var).pack(
            side=tk.RIGHT, padx=6
        )

        value_frame = ttk.Frame(self.root, padding=(10, 4))
        value_frame.pack(fill=tk.X)
        self.distance_label = tk.Label(
            value_frame,
            textvariable=self.distance_var,
            font=("Segoe UI", 42, "bold"),
            foreground="#167d2d",
        )
        self.distance_label.pack(side=tk.LEFT)
        info = ttk.Frame(value_frame)
        info.pack(side=tk.LEFT, padx=28)
        ttk.Label(info, textvariable=self.status_var, font=("Segoe UI", 13)).pack(
            anchor=tk.W
        )
        ttk.Label(info, textvariable=self.rate_var).pack(anchor=tk.W, pady=(6, 0))

        chart_frame = ttk.LabelFrame(self.root, text="最近测距曲线（毫米）", padding=6)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.canvas = tk.Canvas(chart_frame, background="#10151b", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.draw_chart())

        log_frame = ttk.LabelFrame(self.root, text="设备输出", padding=6)
        log_frame.pack(fill=tk.BOTH, padx=10, pady=(0, 10))
        self.log_text = tk.Text(log_frame, height=7, state=tk.DISABLED, wrap=tk.NONE)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def refresh_ports(self) -> None:
        ports = sorted(list_ports.comports(), key=lambda item: item.device)
        labels = [port.device for port in ports]
        self.port_combo["values"] = labels
        if not self.port_var.get() and labels:
            preferred = next(
                (
                    port.device
                    for port in ports
                    if port.vid == 0x303A or "Espressif" in (port.manufacturer or "")
                ),
                labels[0],
            )
            self.port_var.set(preferred)

    def toggle_connection(self) -> None:
        if self.reader is not None:
            self.disconnect()
            return
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("没有串口", "请选择 ESP32-C3 对应的 COM 口。")
            return
        self.stop_event = threading.Event()
        self.reader = SerialReader(port, self.baud, self.messages, self.stop_event)
        self.reader.start()
        self.connection_var.set(f"正在连接 {port}…")
        self.connect_button.config(text="断开")

    def disconnect(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
        self.connection_var.set("正在断开…")

    def toggle_csv(self) -> None:
        if self.csv_file is not None:
            self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None
            self.log_button.config(text="开始记录 CSV")
            self.append_log("CSV 记录已停止")
            return

        default_name = time.strftime("vl53l0x_%Y%m%d_%H%M%S.csv")
        selected = filedialog.asksaveasfilename(
            title="保存测距记录",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV", "*.csv"), ("所有文件", "*.*")],
        )
        if not selected:
            return
        self.csv_file = Path(selected).open("w", newline="", encoding="utf-8-sig")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(
            ["pc_time", "sequence", "sensor_time_ms", "distance_mm", "range_status"]
        )
        self.csv_file.flush()
        self.log_button.config(text="停止记录 CSV")
        self.append_log(f"CSV 记录：{selected}")

    def poll_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "measurement":
                    self.handle_measurement(payload)
                elif kind == "log":
                    self.append_log(str(payload))
                elif kind == "connected":
                    self.connection_var.set(f"已连接 {payload} @ {self.baud}")
                    self.append_log(f"已连接 {payload}")
                elif kind == "error":
                    self.append_log(f"串口错误：{payload}")
                    self.connection_var.set("串口错误")
                elif kind == "disconnected":
                    self.reader = None
                    self.stop_event = None
                    self.connect_button.config(text="连接")
                    if self.connection_var.get() != "串口错误":
                        self.connection_var.set("未连接")
        except queue.Empty:
            pass

        if (
            self.last_measurement_monotonic is not None
            and time.monotonic() - self.last_measurement_monotonic > 1.0
        ):
            self.status_var.set("数据超时")
            self.distance_label.config(foreground="#b36b00")
        self.root.after(50, self.poll_messages)

    def handle_measurement(self, measurement: Measurement) -> None:
        self.history.append(measurement)
        now = time.monotonic()
        self.last_measurement_monotonic = now
        self.rate_times.append(now)
        while self.rate_times and now - self.rate_times[0] > 2.0:
            self.rate_times.popleft()
        rate = 0.0
        if len(self.rate_times) >= 2:
            elapsed = self.rate_times[-1] - self.rate_times[0]
            if elapsed > 0:
                rate = (len(self.rate_times) - 1) / elapsed

        self.distance_var.set(f"{measurement.distance_mm} mm")
        self.rate_var.set(f"{rate:.1f} Hz · seq {measurement.sequence}")
        if measurement.range_status == 0:
            self.status_var.set("测距有效（status 0）")
            self.distance_label.config(foreground="#167d2d")
        else:
            self.status_var.set(f"传感器状态 {measurement.range_status}（当前值勿作控制）")
            self.distance_label.config(foreground="#b36b00")

        if self.csv_writer is not None and self.csv_file is not None:
            self.csv_writer.writerow(
                [
                    time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(measurement.received_time),
                    )
                    + f".{int(measurement.received_time % 1 * 1000):03d}",
                    measurement.sequence,
                    measurement.sensor_time_ms,
                    measurement.distance_mm,
                    measurement.range_status,
                ]
            )
            self.csv_file.flush()
        self.draw_chart()

    def draw_chart(self) -> None:
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 2)
        height = max(self.canvas.winfo_height(), 2)
        margin_left, margin_right, margin_top, margin_bottom = 58, 14, 14, 28
        plot_w = max(width - margin_left - margin_right, 1)
        plot_h = max(height - margin_top - margin_bottom, 1)

        values = [item.distance_mm for item in self.history]
        max_value = max(500, int(max(values, default=500) * 1.15 / 100) * 100)
        for index in range(5):
            y = margin_top + plot_h * index / 4
            value = max_value * (4 - index) / 4
            self.canvas.create_line(
                margin_left, y, width - margin_right, y, fill="#28333d"
            )
            self.canvas.create_text(
                margin_left - 8,
                y,
                text=f"{value:.0f}",
                fill="#9ba8b3",
                anchor=tk.E,
            )

        if len(values) >= 2:
            points: list[float] = []
            denominator = max(len(values) - 1, 1)
            for index, value in enumerate(values):
                x = margin_left + plot_w * index / denominator
                y = margin_top + plot_h * (1.0 - min(value, max_value) / max_value)
                points.extend((x, y))
            self.canvas.create_line(*points, fill="#45c4ff", width=2, smooth=True)

        self.canvas.create_text(
            width - margin_right,
            height - 8,
            text=f"最近 {len(values)} 点",
            fill="#9ba8b3",
            anchor=tk.SE,
        )

    def append_log(self, text: str) -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 250:
            self.log_text.delete("1.0", "50.0")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def close(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
        if self.csv_file is not None:
            self.csv_file.close()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="VL53L0X USB serial monitor")
    parser.add_argument("--port", help="COM port, for example COM7")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--autoconnect",
        action="store_true",
        help="connect immediately when --port is set",
    )
    args = parser.parse_args()

    root = tk.Tk()
    app = MonitorApp(root, args.port, args.baud)
    if args.autoconnect and args.port:
        root.after(100, app.toggle_connection)
    root.mainloop()


if __name__ == "__main__":
    main()
