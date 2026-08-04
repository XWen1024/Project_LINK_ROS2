import importlib.util
from pathlib import Path
import unittest


SCRIPT_PATH = (
    Path(__file__).parents[3] / "scripts" / "start_uwb_shadow_auto_tag.py"
)
SPEC = importlib.util.spec_from_file_location("start_uwb_shadow_auto_tag", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AutoShadowTests(unittest.TestCase):
    def test_fragmented_frame_discovers_one_address(self) -> None:
        payload = b'{"TWR":{"a16":"4096","Xcm":100,"Ycm":0,"D":100}}'
        frame = b"JS" + f"{len(payload):04X}".encode("ascii") + payload
        buffer = bytearray()

        self.assertEqual(MODULE.extract_addresses(buffer, frame[:7]), set())
        self.assertEqual(MODULE.extract_addresses(buffer, frame[7:]), {"4096"})

    def test_entrypoint_is_hard_locked_to_shadow(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn('"--shadow"', source)
        self.assertNotIn('"--enable-motion"', source)

