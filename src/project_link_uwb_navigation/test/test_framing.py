import unittest

from project_link_uwb_navigation.framing import JsFrameDecoder


PAYLOAD = (
    b'{"TWR":{"a16":"4096","R":128,"T":1490981,"D":37,"P":56,'
    b'"Xcm":14,"Ycm":32,"O":0,"V":49152,"X":0,"Y":0,"Z":0}}'
)
FRAME = b"JS" + f"{len(PAYLOAD):04X}".encode("ascii") + PAYLOAD


class FramingTests(unittest.TestCase):
    def test_decoder_handles_fragment_at_every_byte(self) -> None:
        for split in range(len(FRAME) + 1):
            decoder = JsFrameDecoder()
            self.assertEqual(decoder.feed(FRAME[:split]) + decoder.feed(FRAME[split:]), [PAYLOAD])

    def test_decoder_handles_noise_and_back_to_back_frames(self) -> None:
        decoder = JsFrameDecoder()
        self.assertEqual(decoder.feed(b"noise" + FRAME + FRAME), [PAYLOAD, PAYLOAD])

    def test_decoder_remains_bounded_on_garbage(self) -> None:
        decoder = JsFrameDecoder(max_payload_bytes=32)
        decoder.feed(b"J" * 10000)
        self.assertLessEqual(decoder.buffered_bytes, 38)
