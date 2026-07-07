import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hwp_alimi.phase3 import (
    build_phase3_payload,
    compose_teacher_story,
    expected_checkbox_count,
    find_school_info_placeholders,
    find_student_placeholders,
    has_student_placeholder,
    validate_school_info,
    validate_school_info_placeholders,
    validate_student_placeholder,
)


def phase1_payload(source_hwp: str = "template.hwp") -> dict:
    return {
        "source_hwp": source_hwp,
        "block_count": 2,
        "blocks": [
            {
                "index": 1,
                "subject": "국어",
                "area": "읽기",
                "evaluation_element": "읽기 평가",
                "levels": [
                    {"label": "도달", "text": "잘 읽을 수 있다."},
                    {"label": "부분도달", "text": "일부 읽을 수 있다."},
                    {"label": "노력중", "text": "도움을 받아 읽을 수 있다."},
                ],
            },
            {
                "index": 2,
                "subject": "수학",
                "area": "수와 연산",
                "evaluation_element": "수학 평가",
                "levels": [
                    {"label": "도달", "text": "잘 계산할 수 있다."},
                    {"label": "부분도달", "text": "일부 계산할 수 있다."},
                    {"label": "노력중", "text": "도움을 받아 계산할 수 있다."},
                ],
            },
        ],
    }


def phase2_payload() -> dict:
    return {
        "source_phase1_json": "phase1.json",
        "error_count": 0,
        "metadata": {"expected_column_count": 2, "imported_column_count": 2},
        "columns": [
            {"index": 1, "subject": "국어", "area": "읽기", "column_label": "1. 국어 / 읽기"},
            {"index": 2, "subject": "수학", "area": "수와 연산", "column_label": "2. 수학 / 수와 연산"},
        ],
        "students": [
            {
                "source_row": 5,
                "number": "1",
                "name": "김대실",
                "assessments": [
                    {
                        "block_index": 1,
                        "subject": "국어",
                        "area": "읽기",
                        "evaluation_element": "읽기 평가",
                        "level": "도달",
                        "raw_value": "상",
                    },
                    {
                        "block_index": 2,
                        "subject": "수학",
                        "area": "수와 연산",
                        "evaluation_element": "수학 평가",
                        "level": None,
                        "raw_value": "",
                    },
                ],
            }
        ],
        "issues": [],
    }


class Phase3ManifestTest(unittest.TestCase):
    def test_builds_student_output_with_selected_level_text_and_blank_unmarked(self):
        payload = build_phase3_payload(
            Path("phase1.json"),
            phase1_payload("C:/fake/template.hwp"),
            Path("phase2.json"),
            phase2_payload(),
            Path("phase3_output"),
        )

        self.assertFalse(payload["ready"])
        student = payload["students"][0]
        self.assertEqual(student["name"], "김대실")
        self.assertEqual(student["assessments"][0]["selected_text"], "잘 읽을 수 있다.")
        self.assertTrue(student["assessments"][0]["should_mark"])
        self.assertEqual(student["assessments"][0]["level_index"], 0)
        self.assertEqual(student["assessments"][0]["checkbox_ordinal"], 1)
        self.assertEqual(student["assessments"][1]["selected_text"], "")
        self.assertFalse(student["assessments"][1]["should_mark"])
        self.assertIsNone(student["assessments"][1]["level_index"])
        self.assertIsNone(student["assessments"][1]["checkbox_ordinal"])

    def test_composes_teacher_story_from_common_and_individual_text(self):
        self.assertEqual(compose_teacher_story("공통", "개별"), "  공통 개별")
        self.assertEqual(compose_teacher_story("공통", ""), "  공통")
        self.assertEqual(compose_teacher_story("", "개별"), "  개별")
        self.assertEqual(compose_teacher_story("", ""), "")

    def test_build_phase3_payload_includes_student_teacher_story(self):
        payload = build_phase3_payload(
            Path("phase1.json"),
            phase1_payload("C:/fake/template.hwp"),
            Path("phase2.json"),
            phase2_payload(),
            Path("phase3_output"),
            student_stories=[
                {
                    "number": "1",
                    "name": "김대실",
                    "common_story": "공통 성장 문장",
                    "individual_story": "개별 관찰 문장",
                }
            ],
        )

        student = payload["students"][0]
        self.assertEqual(student["common_story"], "공통 성장 문장")
        self.assertEqual(student["individual_story"], "개별 관찰 문장")
        self.assertEqual(student["teacher_story"], "  공통 성장 문장 개별 관찰 문장")

    def test_blocks_output_when_phase2_has_errors_or_missing_columns(self):
        phase2 = phase2_payload()
        phase2["error_count"] = 1
        phase2["issues"] = [{"severity": "error", "message": "학생명단 오류"}]
        phase2["metadata"]["imported_column_count"] = 1
        phase2["columns"] = phase2["columns"][:1]

        payload = build_phase3_payload(
            Path("phase1.json"),
            phase1_payload("C:/fake/template.hwp"),
            Path("phase2.json"),
            phase2,
            Path("phase3_output"),
        )

        messages = "\n".join(issue["message"] for issue in payload["blocking_issues"])
        self.assertIn("성적 입력 오류", messages)
        self.assertIn("학생명단 오류", messages)
        self.assertIn("2개 중 1개만 입력", messages)
        self.assertIn("2. 수학 / 수와 연산", messages)

    def test_blocks_output_when_student_is_missing_an_assessment_after_all_columns_imported(self):
        phase2 = phase2_payload()
        phase2["students"][0]["assessments"] = phase2["students"][0]["assessments"][:1]

        payload = build_phase3_payload(
            Path("phase1.json"),
            phase1_payload("C:/fake/template.hwp"),
            Path("phase2.json"),
            phase2,
            Path("phase3_output"),
        )

        messages = "\n".join(issue["message"] for issue in payload["blocking_issues"])
        self.assertIn("학생별 평가 결과가 일부 빠졌습니다", messages)
        self.assertIn("1번 김대실", messages)
        self.assertIn("2. 수학 / 수와 연산", messages)

    def test_checkbox_ordinals_follow_template_order_not_block_index_math(self):
        phase1 = {
            "source_hwp": "C:/fake/template.hwp",
            "blocks": [
                {
                    "index": 10,
                    "subject": "국어",
                    "area": "읽기",
                    "evaluation_element": "읽기 평가",
                    "levels": [
                        {"label": "도달", "text": "도달"},
                        {"label": "노력중", "text": "노력중"},
                    ],
                },
                {
                    "index": 20,
                    "subject": "수학",
                    "area": "연산",
                    "evaluation_element": "연산 평가",
                    "levels": [
                        {"label": "도달", "text": "도달"},
                        {"label": "부분도달", "text": "부분도달"},
                        {"label": "노력중", "text": "노력중"},
                    ],
                },
            ],
        }
        phase2 = {
            "source_phase1_json": "phase1.json",
            "metadata": {"expected_column_count": 2, "imported_column_count": 2},
            "columns": [
                {"index": 10, "subject": "국어", "area": "읽기"},
                {"index": 20, "subject": "수학", "area": "연산"},
            ],
            "students": [
                {
                    "source_row": 5,
                    "number": "1",
                    "name": "김대실",
                    "assessments": [
                        {"block_index": 10, "subject": "국어", "area": "읽기", "level": "노력중", "raw_value": "하"},
                        {"block_index": 20, "subject": "수학", "area": "연산", "level": "노력중", "raw_value": "하"},
                    ],
                }
            ],
            "issues": [],
        }

        payload = build_phase3_payload(Path("phase1.json"), phase1, Path("phase2.json"), phase2, Path("phase3_output"))

        assessments = payload["students"][0]["assessments"]
        self.assertEqual(assessments[0]["checkbox_ordinal"], 2)
        self.assertEqual(assessments[1]["checkbox_ordinal"], 5)

    def test_expected_checkbox_count_uses_non_empty_level_labels(self):
        phase1 = phase1_payload()
        phase1["blocks"][0]["levels"].append({"label": "", "text": "빈 수준"})

        self.assertEqual(expected_checkbox_count(phase1), 6)

    def test_detects_student_placeholder_variants(self):
        self.assertTrue(has_student_placeholder("대구대실초등학교 0학년 0반 0번 이름: 000"))
        self.assertTrue(has_student_placeholder("이름: 000"))
        self.assertTrue(has_student_placeholder("0번 이름 : 000"))
        self.assertTrue(has_student_placeholder("성명: OOO"))
        self.assertTrue(has_student_placeholder("0번 학생명 : ○○○"))
        self.assertFalse(has_student_placeholder("이름: 홍길동"))

    def test_extracts_student_placeholders_for_generation_manifest(self):
        placeholders = find_student_placeholders("대구대실초 0번 이름 : 000 / 성명: OOO")

        self.assertEqual(
            placeholders,
            [
                {"find": "0번 이름 : 000", "label": "이름", "includes_number": True},
                {"find": "성명: OOO", "label": "성명", "includes_number": False},
            ],
        )

    def test_extracts_school_info_placeholders_for_generation_manifest(self):
        placeholders = find_school_info_placeholders("대구대실초 0학년 0반 / 담임교사: OOO / 담임 0 0 0 (인)")

        self.assertEqual(
            placeholders,
            [
                {"kind": "grade_class", "find": "0학년 0반"},
                {"kind": "teacher", "find": "담임교사: OOO", "label": "담임교사", "separator": ": "},
                {"kind": "teacher", "find": "담임 0 0 0", "label": "담임", "separator": ""},
            ],
        )

    def test_build_phase3_payload_includes_detected_student_placeholders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            text_path = Path(temp_dir) / "template.txt"
            text_path.write_text("대구대실초 0학년 0반 담임: 000 / 0번 이름 : 000", encoding="utf-8")
            phase1 = phase1_payload("C:/fake/template.hwp")
            phase1["extracted_text"] = str(text_path)

            payload = build_phase3_payload(
                Path("phase1.json"),
                phase1,
                Path("phase2.json"),
                phase2_payload(),
                Path("phase3_output"),
                {"grade": "3", "class_name": "2", "teacher_name": "홍길동"},
            )

        self.assertEqual(
            payload["student_placeholders"],
            [{"find": "0번 이름 : 000", "label": "이름", "includes_number": True}],
        )
        self.assertEqual(payload["school_info"], {"grade": "3", "class_name": "2", "teacher_name": "홍길동"})
        self.assertEqual(
            payload["school_info_placeholders"],
            [
                {"kind": "grade_class", "find": "0학년 0반"},
                {"kind": "teacher", "find": "담임: 000", "label": "담임", "separator": ": "},
            ],
        )

    def test_blocks_output_when_school_info_is_missing(self):
        issue = validate_school_info({"grade": "3", "class_name": "", "teacher_name": ""})

        self.assertIsNotNone(issue)
        self.assertIn("기본 정보", issue["message"])
        self.assertIn("반", issue["value"])
        self.assertIn("교사 이름", issue["value"])

    def test_blocks_output_when_school_info_placeholders_are_missing(self):
        phase1 = phase1_payload()
        with tempfile.TemporaryDirectory() as temp_dir:
            text_path = Path(temp_dir) / "template.txt"
            text_path.write_text("대구대실초 0번 이름: 000", encoding="utf-8")
            phase1["extracted_text"] = str(text_path)

            issue = validate_school_info_placeholders(phase1)

        self.assertIsNotNone(issue)
        self.assertIn("학년/반/교사 이름 자리표시자", issue["message"])

    def test_blocks_output_when_student_placeholder_is_missing(self):
        phase1 = phase1_payload()
        with tempfile.TemporaryDirectory() as temp_dir:
            text_path = Path(temp_dir) / "template.txt"
            text_path.write_text("학생명: 홍길동", encoding="utf-8")
            phase1["extracted_text"] = str(text_path)

            issue = validate_student_placeholder(phase1)

        self.assertIsNotNone(issue)
        self.assertIn("학생 이름 자리표시자", issue["message"])

    def test_blocks_output_when_hwp_checkbox_count_cannot_be_verified(self):
        phase2 = phase2_payload()

        with tempfile.TemporaryDirectory() as temp_dir:
            fake_hwp = Path(temp_dir) / "template.hwp"
            fake_hwp.write_text("not an ole hwp", encoding="utf-8")
            payload = build_phase3_payload(
                Path("phase1.json"),
                phase1_payload(str(fake_hwp)),
                Path("phase2.json"),
                phase2,
                Path("phase3_output"),
            )

        messages = "\n".join(issue["message"] for issue in payload["blocking_issues"])
        self.assertIn("체크박스 수를 확인하지 못했습니다", messages)


if __name__ == "__main__":
    unittest.main()
