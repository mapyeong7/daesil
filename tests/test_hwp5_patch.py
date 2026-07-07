import sys
import unittest
import zlib
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hwp_alimi.hwp5_patch import (
    TEACHER_STORY_TITLE,
    count_decoded_checkbox_states,
    count_decoded_checkboxes,
    count_decoded_text_occurrences,
    decode_section_stream,
    encode_section_stream,
    find_teacher_story_record,
    hwp_record_header,
    iter_hwp_records,
    ole_directory_sort_key,
    para_text_from_record,
    replace_decoded_text,
    replace_hwp_record_payload,
    same_size_utf16_replacement_bytes,
    teacher_story_cleanup_find_text,
    teacher_story_patched_text,
    teacher_story_payload_text,
    school_info_placeholder_replacement,
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

    def test_ole_directory_sort_key_orders_section10_after_section9(self):
        names = ["Section10", "Section2", "Section1", "Section9"]

        self.assertEqual(sorted(names, key=ole_directory_sort_key), ["Section1", "Section2", "Section9", "Section10"])

    def test_counts_decoded_utf16_text_occurrences(self):
        decoded = "0번 이름: 000 / 0번 이름: 000".encode("utf-16le")

        self.assertEqual(count_decoded_text_occurrences(decoded, "0번 이름: 000"), 2)
        self.assertEqual(count_decoded_text_occurrences(decoded, "이름: 홍길동"), 0)

    def test_replaces_decoded_utf16_text_without_changing_byte_length(self):
        original = "담임 0 0 0 □".encode("utf-16le")
        decoded = bytearray(original)

        count = replace_decoded_text(decoded, "담임 0 0 0", "담임 홍길동")

        self.assertEqual(count, 1)
        self.assertEqual(len(decoded), len(original))
        self.assertIn("담임 홍길동  ".encode("utf-16le"), decoded)

    def test_skips_absent_longer_replacement(self):
        decoded = bytearray("담임 0 0 0 □".encode("utf-16le"))

        count = replace_decoded_text(decoded, "없는 말", "자리표시자보다 긴 문구")

        self.assertEqual(count, 0)

    def test_rejects_present_decoded_utf16_text_replacement_when_longer(self):
        payload = "담임 0\r".encode("utf-16le")
        decoded = bytearray(hwp_record_header(67, 3, len(payload)) + payload)

        with self.assertRaisesRegex(ValueError, "자리표시자보다 긴 문구"):
            replace_decoded_text(decoded, "담임 0", "담임 채우준")

    def test_same_size_utf16_replacement_bytes_pads_shorter_text(self):
        replacement = same_size_utf16_replacement_bytes("담임 0 0 0", "담임 홍길동")

        self.assertEqual(len(replacement), len("담임 0 0 0".encode("utf-16le")))
        self.assertEqual(replacement.decode("utf-16le"), "담임 홍길동  ")

    def test_student_placeholder_replacement_respects_numbered_placeholder(self):
        numbered = {"label": "이름", "includes_number": True}
        name_only = {"label": "성명", "includes_number": False}

        self.assertEqual(student_placeholder_replacement(numbered, "7", "김대실"), "7번 이름: 김대실")
        self.assertEqual(student_placeholder_replacement(name_only, "7", "김대실"), "성명: 김대실")

    def test_student_placeholder_replacement_compacts_to_placeholder_length(self):
        numbered = {"label": "이름", "includes_number": True, "find": "0번 이름: 000"}

        replacement = student_placeholder_replacement(numbered, "10", "박서은")

        self.assertEqual(replacement, "10번 이름:박서은")
        self.assertLessEqual(
            len(replacement.encode("utf-16le")),
            len(numbered["find"].encode("utf-16le")),
        )

    def test_school_info_placeholder_replacement(self):
        school_info = {"grade": "3", "class_name": "2", "teacher_name": "홍길동"}

        self.assertEqual(
            school_info_placeholder_replacement({"kind": "grade_class"}, school_info),
            "3학년 2반",
        )
        self.assertEqual(
            school_info_placeholder_replacement(
                {"kind": "teacher", "label": "담임교사", "separator": ": "},
                school_info,
            ),
            "담임교사: 홍길동",
        )
        self.assertEqual(
            school_info_placeholder_replacement(
                {"kind": "teacher", "label": "담임", "separator": ""},
                school_info,
            ),
            "담임 홍길동",
        )

    def test_finds_teacher_story_record_after_title(self):
        decoded = bytearray()
        for level, text in [
            (3, "머리말\r"),
            (7, f" {TEACHER_STORY_TITLE}\r"),
            (3, "  예시 문장입니다.\r"),
            (7, " 학생의 성장을 격려하는 부모님의 이야기\r"),
        ]:
            payload = text.encode("utf-16le")
            decoded.extend(hwp_record_header(67, level, len(payload)))
            decoded.extend(payload)

        found = find_teacher_story_record(decoded)

        self.assertIsNotNone(found)
        title_record, story_record = found
        self.assertIn(TEACHER_STORY_TITLE, para_text_from_record(decoded, title_record))
        self.assertEqual(para_text_from_record(decoded, story_record), "  예시 문장입니다.\r")

    def test_teacher_story_payload_text_does_not_pad_trailing_spaces(self):
        self.assertEqual(teacher_story_payload_text("  공통 문장 개별 문장"), "  공통 문장 개별 문장\r")
        self.assertEqual(teacher_story_payload_text(""), "\r")

    def test_teacher_story_patched_text_pads_before_paragraph_end_for_hwp_compatibility(self):
        patched = teacher_story_patched_text("  공통", original_payload_size=16)

        self.assertEqual(patched.removesuffix("\r").rstrip(" "), "  공통")
        self.assertEqual(patched[-1], "\r")
        self.assertEqual(len(patched.encode("utf-16le")), 16)

    def test_teacher_story_cleanup_find_text_targets_padded_story_only(self):
        find_text = teacher_story_cleanup_find_text("  공통", "예시문장입니다")

        self.assertEqual(find_text.rstrip(" "), "  공통")
        self.assertGreater(len(find_text), len("  공통"))
        self.assertEqual(teacher_story_cleanup_find_text("  예시문장보다 긴 문장", "예시"), "")

    def test_replaces_hwp_record_payload_with_longer_text(self):
        payload = "짧은 문장\r".encode("utf-16le")
        decoded = bytearray(hwp_record_header(67, 3, len(payload)) + payload)
        record = iter_hwp_records(decoded)[0]
        replacement = "  긴 공통 문장과 학생별 문장이 하나의 문단으로 이어집니다.\r".encode("utf-16le")

        replace_hwp_record_payload(decoded, record, replacement)

        record = iter_hwp_records(decoded)[0]
        self.assertEqual(para_text_from_record(decoded, record), "  긴 공통 문장과 학생별 문장이 하나의 문단으로 이어집니다.\r")

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

