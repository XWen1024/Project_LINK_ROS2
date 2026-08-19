from project_link_visual_grasp.camera import native_mjpeg_command, pop_native_jpeg


def _jpeg(payload_size: int = 2048) -> bytes:
    return b"\xff\xd8" + b"x" * payload_size + b"\xff\xd9"


def test_native_mjpeg_command_requests_the_verified_arm_camera_mode():
    assert native_mjpeg_command(
        "/dev/project_link_arm_camera",
        1280,
        720,
        30.0,
    ) == [
        "v4l2-ctl",
        "-d",
        "/dev/project_link_arm_camera",
        "--set-fmt-video=width=1280,height=720,pixelformat=MJPG",
        "--set-parm=30",
        "--stream-mmap=3",
        "--stream-count=0",
        "--stream-to=-",
    ]


def test_native_mjpeg_parser_keeps_partial_data_until_the_frame_is_complete():
    expected = _jpeg()
    buffer = bytearray(b"noise" + expected[:1200])
    assert pop_native_jpeg(buffer) is None
    buffer.extend(expected[1200:])
    assert pop_native_jpeg(buffer) == expected
    assert buffer == bytearray()


def test_native_mjpeg_parser_drops_a_truncated_frame_before_a_new_soi():
    expected = _jpeg(3000)
    buffer = bytearray(b"\xff\xd8truncated" + expected)
    assert pop_native_jpeg(buffer) == expected
    assert buffer == bytearray()
