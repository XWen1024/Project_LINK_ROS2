import math
import unittest

from project_link_uwb_navigation.protocol import (
    PayloadRejected,
    ProtocolConfig,
    TagClockGuard,
    parse_payload,
)


PAYLOAD = (
    b'{"TWR":{"a16":"4096","R":128,"T":1490981,"D":37,"P":56,'
    b'"Xcm":14,"Ycm":32,"O":0,"V":49152,"X":0,"Y":0,"Z":0}}'
)


class ProtocolTests(unittest.TestCase):
    def test_sample_normalizes_to_metres(self) -> None:
        sample = parse_payload(PAYLOAD, 1_000_000_000, ProtocolConfig(tag_address="4096"))
        self.assertAlmostEqual(sample.x_m, 0.14)
        self.assertAlmostEqual(sample.y_m, 0.32)
        self.assertAlmostEqual(sample.range_m, 0.37)
        self.assertAlmostEqual(sample.coordinate_range_m, 0.34928498)
        self.assertAlmostEqual(sample.range_residual_m, 0.02071502)
        self.assertAlmostEqual(math.degrees(sample.debug_bearing_rad), 23.6293777)

    def test_wrong_tag_and_large_residual_are_rejected(self) -> None:
        with self.assertRaisesRegex(PayloadRejected, "wrong_tag"):
            parse_payload(PAYLOAD, 1, ProtocolConfig(tag_address="FFFF"))
        with self.assertRaisesRegex(PayloadRejected, "range_residual_too_large"):
            parse_payload(PAYLOAD, 1, ProtocolConfig(tag_address="4096", max_range_residual_m=0.01))

    def test_tag_clock_requires_strict_progression(self) -> None:
        guard = TagClockGuard()
        self.assertTrue(guard.accept(10))
        self.assertFalse(guard.accept(10))
        self.assertFalse(guard.accept(9))
        self.assertTrue(guard.accept(11))
