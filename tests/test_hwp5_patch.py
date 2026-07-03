import sys
import unittest
import zlib
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hwp_alimi.hwp5_patch import (
    count_decoded_checkbox_states,
    count_decoded_checkboxes,
    count_decoded_text_occurrences,
    decode_section_stream,
    encode_section_stream,
    replace_decoded_text,
    student_placeholder_replacement,
    write_bytes_with_retry,
)


class Hwp5PatchTest(unittest.TestCase):
    def test_decodes_and_reencodes_raw_deflate_section(self):
        original = "□■테스트".encode("utf-16le")
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        raw = compressor.compress(original) + compressor.flush()

        decoded, compressed = decode_section_stream(raw)
        encoded = encode_section_stream(decoded, compressed)

        self.assertTrue(compressed)
        self.assertEqual(bytes(decoded), original)
        self.assertEqual(zlib.decompress(encoded, -15), original)

    def test_keeps_uncompressed_section_uncompressed(self):
        original = "□■테스트".encode("utf-16le")

        decoded, compressed = decode_section_stream(original)
        encoded = encode_section_stream(decoded, compressed)

        self.assertFalse(compressed)
        self.assertEqual(bytes(decoded), original)
        self.assertEqual(encoded, original)

    def test_counts_empty_and_filled_checkboxes(self):
        decoded = "□내용■내용□".encode("utf-16le")

        self.assertEqual(count_decoded_checkboxes(decoded), 3)
        self.assertEqual(count_decoded_checkbox_states(decoded), {"empty": 2, "filled": 1, "total": 3})

    def test_counts_decoded_utf16_text_occurrences(self):
        decoded = "0번 이름: 000 / 0번 이름: 000".encode("utf-16le")

        self.assertEqual(count_decoded_text_occurrences(decoded, "0번 이름: 000"), 2)
        self.assertEqual(count_decoded_text_occurrences(decoded, "이름: 홍길동"), 0)

    def test_replaces_decoded_utf16_text_with_longer_student_name(self):
        decoded = bytearray("0번 이름: 000 □".encode("utf-16le"))

        count = replace_decoded_text(decoded, "0번 이름: 000", "99번 이름: 합성검증")

        self.assertEqual(count, 1)
        self.assertIn("99번 이름: 합성검증".encode("utf-16le"), decoded)

    def test_student_placeholder_replacement_respects_numbered_placeholder(self):
        numbered = {"label": "이름", "includes_number": True}
        name_only = {"label": "성명", "includes_number": False}

        self.assertEqual(student_placeholder_replacement(numbered, "7", "김대실"), "7번 이름: 김대실")
        self.assertEqual(student_placeholder_replacement(name_only, "7", "김대실"), "성명: 김대실")

    def test_write_bytes_with_retry_handles_transient_permission_error(self):
        class FlakyPath:
            def __init__(self):
                self.calls = 0
                self.data = None

            def write_bytes(self, data):
                self.calls += 1
                if self.calls == 1:
                    raise PermissionError("temporary lock")
                self.data = bytes(data)
                return len(data)

        path = FlakyPath()

        write_bytes_with_retry(path, b"ok", attempts=2, delay_seconds=0)

        self.assertEqual(path.calls, 2)
        self.assertEqual(path.data, b"ok")


if __name__ == "__main__":
    unittest.main()
