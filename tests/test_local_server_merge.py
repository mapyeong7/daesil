import sys
import tempfile
import types
import unittest
import zipfile
from http import HTTPStatus
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import hwp_alimi.local_server as local_server
from hwp_alimi.local_server import (
    api_error_response,
    clear_phase3_generation_metadata,
    clear_report_output_files,
    columns_for_expected_subject,
    create_reports_zip,
    merge_phase2_payload,
    parse_multipart_file,
    phase3_for_response,
    report_output_dir,
    resolve_expected_subject,
    validate_created_files,
    validate_expected_subject,
)
from hwp_alimi.phase1 import AssessmentBlock, LevelText
from hwp_alimi.phase2 import AssessmentColumn


PHASE1_PATH = Path("sample.phase1.json")


def column(block_index: int, subject: str) -> dict:
    return {
        "index": block_index,
        "subject": subject,
        "area": f"영역{block_index}",
        "evaluation_element": f"{subject} 평가{block_index}",
        "column_label": f"{block_index}. {subject} / 영역{block_index}",
    }


def assessment(block_index: int, subject: str, raw_value: str) -> dict:
    level_map = {"상": "도달", "중": "부분도달", "하": "노력중"}
    return {
        "block_index": block_index,
        "column_label": f"{block_index}. {subject} / 영역{block_index}",
        "subject": subject,
        "area": f"영역{block_index}",
        "evaluation_element": f"{subject} 평가{block_index}",
        "level": level_map.get(raw_value),
        "raw_value": raw_value,
    }


def student(source_row: int, number: str, name: str, block_index: int, subject: str, raw_value: str) -> dict:
    return {
        "source_row": source_row,
        "number": number,
        "name": name,
        "assessments": [assessment(block_index, subject, raw_value)],
    }


def payload(subject: str, block_index: int, students: list[dict]) -> dict:
    return {
        "source_phase1_json": str(PHASE1_PATH),
        "paste_source": f"{subject}.tsv",
        "created_at": "2026-07-03T09:00:00",
        "block_count": 1,
        "student_count": len(students),
        "has_errors": False,
        "has_warnings": False,
        "error_count": 0,
        "warning_count": 0,
        "validation_mode": "excel_fixed_verified",
        "header_exists": True,
        "header_message": "",
        "header_checks": [],
        "source_format": "neis_excel_fixed",
        "metadata": {"excel_layout": {"subject": subject, "title": f"{subject} 교과 성적 일람표"}},
        "columns": [column(block_index, subject)],
        "students": students,
        "issues": [],
    }


def block_from_column(block_index: int, subject: str) -> AssessmentBlock:
    item = column(block_index, subject)
    return AssessmentBlock(
        item["index"],
        item["subject"],
        item["area"],
        item["evaluation_element"],
        [
            LevelText("level-a", False, None, "level a"),
            LevelText("level-b", False, None, "level b"),
            LevelText("level-c", False, None, "level c"),
        ],
    )


class LocalServerMergeTest(unittest.TestCase):
    def test_api_error_response_keeps_user_fixable_messages(self):
        status, message = api_error_response(ValueError("엑셀 파일을 먼저 선택하세요."))

        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(message, "엑셀 파일을 먼저 선택하세요.")

    def test_api_error_response_hides_unexpected_internal_details(self):
        status, message = api_error_response(KeyError("secret internal key"))

        self.assertEqual(status, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertIn("예기치 못한 오류", message)
        self.assertNotIn("secret internal key", message)

    def test_validate_upload_file_rejects_wrong_extension_and_empty_content(self):
        with self.assertRaisesRegex(ValueError, "엑셀 성적표 파일 형식"):
            local_server.validate_upload_file("scores.csv", b"data", {".xls", ".xlsx"}, "엑셀 성적표")
        with self.assertRaisesRegex(ValueError, "비어 있습니다"):
            local_server.validate_upload_file("scores.xlsx", b"", {".xls", ".xlsx"}, "엑셀 성적표")

    def test_import_excel_failure_preserves_state_and_removes_temp_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            phase1 = temp_root / "sample.phase1.json"
            phase1.write_text("{}", encoding="utf-8")
            old_phase2 = temp_root / "old.phase2.json"
            old_phase2.write_text("{}", encoding="utf-8")
            old_state = {"current_phase1_json": str(phase1), "current_phase2_json": str(old_phase2), "current_phase3_json": "old.phase3.json"}
            original_upload_dir = local_server.UPLOAD_DIR
            original_extract_dir = local_server.EXTRACT_DIR
            original_phase2_dir = local_server.PHASE2_DIR
            original_state_path = local_server.STATE_PATH
            original_extract_excel_tsv = local_server.extract_excel_tsv
            original_load_columns = local_server.load_assessment_columns
            original_parse_excel = local_server.parse_neis_excel_tsv
            original_result_to_payload = local_server.result_to_payload
            try:
                local_server.UPLOAD_DIR = temp_root / "uploads"
                local_server.EXTRACT_DIR = temp_root / "extracted"
                local_server.PHASE2_DIR = temp_root / "phase2"
                local_server.STATE_PATH = temp_root / "server_state.json"
                local_server.write_json_file(local_server.STATE_PATH, old_state)
                local_server.extract_excel_tsv = lambda excel_path, tsv_path: tsv_path.write_text("tsv", encoding="utf-8")
                local_server.load_assessment_columns = lambda path: [
                    AssessmentColumn(1, "국어", "읽기", "국어 평가", "1. 국어 / 읽기"),
                    AssessmentColumn(2, "수학", "수", "수학 평가", "2. 수학 / 수"),
                ]
                local_server.parse_neis_excel_tsv = lambda text, columns: (columns, object())
                local_server.result_to_payload = lambda phase1_path, source, columns, result: payload(
                    "수학",
                    2,
                    [student(5, "1", "김대실", 2, "수학", "상")],
                )

                with self.assertRaisesRegex(ValueError, "국어.*수학"):
                    local_server.import_excel("scores.xls", b"excel", "국어")

                self.assertEqual(local_server.read_json_file(local_server.STATE_PATH), old_state)
                self.assertEqual(list(local_server.UPLOAD_DIR.glob("*")), [])
                self.assertEqual(list(local_server.EXTRACT_DIR.glob("*")), [])
                self.assertEqual(list(local_server.PHASE2_DIR.glob("*")), [])
            finally:
                local_server.UPLOAD_DIR = original_upload_dir
                local_server.EXTRACT_DIR = original_extract_dir
                local_server.PHASE2_DIR = original_phase2_dir
                local_server.STATE_PATH = original_state_path
                local_server.extract_excel_tsv = original_extract_excel_tsv
                local_server.load_assessment_columns = original_load_columns
                local_server.parse_neis_excel_tsv = original_parse_excel
                local_server.result_to_payload = original_result_to_payload

    def test_import_excel_extract_failure_removes_uploaded_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            phase1 = temp_root / "sample.phase1.json"
            phase1.write_text("{}", encoding="utf-8")
            old_state = {"current_phase1_json": str(phase1), "current_phase2_json": None, "current_phase3_json": None}
            original_upload_dir = local_server.UPLOAD_DIR
            original_extract_dir = local_server.EXTRACT_DIR
            original_phase2_dir = local_server.PHASE2_DIR
            original_state_path = local_server.STATE_PATH
            original_extract_excel_tsv = local_server.extract_excel_tsv
            try:
                local_server.UPLOAD_DIR = temp_root / "uploads"
                local_server.EXTRACT_DIR = temp_root / "extracted"
                local_server.PHASE2_DIR = temp_root / "phase2"
                local_server.STATE_PATH = temp_root / "server_state.json"
                local_server.write_json_file(local_server.STATE_PATH, old_state)
                local_server.extract_excel_tsv = lambda excel_path, tsv_path: (_ for _ in ()).throw(RuntimeError("extract failed"))

                with self.assertRaisesRegex(RuntimeError, "extract failed"):
                    local_server.import_excel("scores.xlsx", b"excel", "국어")

                self.assertEqual(local_server.read_json_file(local_server.STATE_PATH), old_state)
                self.assertEqual(list(local_server.UPLOAD_DIR.glob("*")), [])
                self.assertEqual(list(local_server.EXTRACT_DIR.glob("*")), [])
                self.assertEqual(list(local_server.PHASE2_DIR.glob("*")), [])
            finally:
                local_server.UPLOAD_DIR = original_upload_dir
                local_server.EXTRACT_DIR = original_extract_dir
                local_server.PHASE2_DIR = original_phase2_dir
                local_server.STATE_PATH = original_state_path
                local_server.extract_excel_tsv = original_extract_excel_tsv

    def test_recognize_hwp_rejects_template_without_blocks_without_changing_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            old_state = {"current_phase1_json": "old.phase1.json", "current_phase2_json": "old.phase2.json"}
            original_upload_dir = local_server.UPLOAD_DIR
            original_phase1_dir = local_server.PHASE1_DIR
            original_state_path = local_server.STATE_PATH
            original_extract = local_server.extract_hwp_text
            try:
                local_server.UPLOAD_DIR = temp_root / "uploads"
                local_server.PHASE1_DIR = temp_root / "phase1"
                local_server.STATE_PATH = temp_root / "server_state.json"
                local_server.write_json_file(local_server.STATE_PATH, old_state)
                local_server.extract_hwp_text = lambda upload_path, text_path: text_path.write_text(
                    "평가 항목이 없는 파일",
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(ValueError, "평가 항목을 찾지 못했습니다"):
                    local_server.recognize_hwp("template.hwp", b"fake hwp")

                self.assertEqual(local_server.read_json_file(local_server.STATE_PATH), old_state)
                self.assertEqual(list(local_server.UPLOAD_DIR.glob("*")), [])
                self.assertEqual(list(local_server.PHASE1_DIR.glob("*")), [])
            finally:
                local_server.UPLOAD_DIR = original_upload_dir
                local_server.PHASE1_DIR = original_phase1_dir
                local_server.STATE_PATH = original_state_path
                local_server.extract_hwp_text = original_extract

    def test_recognize_hwp_extract_failure_removes_uploaded_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            old_state = {"current_phase1_json": "old.phase1.json", "current_phase2_json": "old.phase2.json"}
            original_upload_dir = local_server.UPLOAD_DIR
            original_phase1_dir = local_server.PHASE1_DIR
            original_state_path = local_server.STATE_PATH
            original_extract = local_server.extract_hwp_text
            try:
                local_server.UPLOAD_DIR = temp_root / "uploads"
                local_server.PHASE1_DIR = temp_root / "phase1"
                local_server.STATE_PATH = temp_root / "server_state.json"
                local_server.write_json_file(local_server.STATE_PATH, old_state)
                local_server.extract_hwp_text = lambda upload_path, text_path: (_ for _ in ()).throw(RuntimeError("hwp extract failed"))

                with self.assertRaisesRegex(RuntimeError, "hwp extract failed"):
                    local_server.recognize_hwp("template.hwp", b"fake hwp")

                self.assertEqual(local_server.read_json_file(local_server.STATE_PATH), old_state)
                self.assertEqual(list(local_server.UPLOAD_DIR.glob("*")), [])
                self.assertEqual(list(local_server.PHASE1_DIR.glob("*")), [])
            finally:
                local_server.UPLOAD_DIR = original_upload_dir
                local_server.PHASE1_DIR = original_phase1_dir
                local_server.STATE_PATH = original_state_path
                local_server.extract_hwp_text = original_extract

    def test_recognize_hwp_preserves_compatible_phase2_for_same_template(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            old_phase1 = temp_root / "old.phase1.json"
            old_phase1.write_text("{}", encoding="utf-8")
            old_phase2 = temp_root / "old.phase2.json"
            local_server.write_json_file(
                old_phase2,
                payload("Korean", 1, [student(5, "1", "Student One", 1, "Korean", "A")]),
            )
            old_state = {
                "current_phase1_json": str(old_phase1),
                "current_phase2_json": str(old_phase2),
                "current_phase3_json": str(temp_root / "old.phase3.json"),
            }
            original_upload_dir = local_server.UPLOAD_DIR
            original_phase1_dir = local_server.PHASE1_DIR
            original_phase2_dir = local_server.PHASE2_DIR
            original_state_path = local_server.STATE_PATH
            original_extract = local_server.extract_hwp_text
            original_parse = local_server.parse_assessment_blocks
            try:
                local_server.UPLOAD_DIR = temp_root / "uploads"
                local_server.PHASE1_DIR = temp_root / "phase1"
                local_server.PHASE2_DIR = temp_root / "phase2"
                local_server.STATE_PATH = temp_root / "server_state.json"
                local_server.write_json_file(local_server.STATE_PATH, old_state)
                local_server.extract_hwp_text = lambda upload_path, text_path: text_path.write_text(
                    "extracted",
                    encoding="utf-8",
                )
                local_server.parse_assessment_blocks = lambda text: [block_from_column(1, "Korean")]

                result = local_server.recognize_hwp("template.hwp", b"fake hwp")
                state = local_server.read_json_file(local_server.STATE_PATH)
                preserved_phase2 = local_server.read_json_file(Path(state["current_phase2_json"]))

                self.assertIsNotNone(result["phase2"])
                self.assertNotEqual(state["current_phase2_json"], str(old_phase2))
                self.assertEqual(preserved_phase2["source_phase1_json"], state["current_phase1_json"])
                self.assertEqual(preserved_phase2["students"][0]["name"], "Student One")
                self.assertIsNone(state["current_phase3_json"])
            finally:
                local_server.UPLOAD_DIR = original_upload_dir
                local_server.PHASE1_DIR = original_phase1_dir
                local_server.PHASE2_DIR = original_phase2_dir
                local_server.STATE_PATH = original_state_path
                local_server.extract_hwp_text = original_extract
                local_server.parse_assessment_blocks = original_parse

    def test_recognize_hwp_clears_phase2_when_template_columns_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            old_phase2 = temp_root / "old.phase2.json"
            local_server.write_json_file(
                old_phase2,
                payload("Korean", 1, [student(5, "1", "Student One", 1, "Korean", "A")]),
            )
            old_state = {
                "current_phase1_json": str(temp_root / "old.phase1.json"),
                "current_phase2_json": str(old_phase2),
                "current_phase3_json": str(temp_root / "old.phase3.json"),
            }
            original_upload_dir = local_server.UPLOAD_DIR
            original_phase1_dir = local_server.PHASE1_DIR
            original_phase2_dir = local_server.PHASE2_DIR
            original_state_path = local_server.STATE_PATH
            original_extract = local_server.extract_hwp_text
            original_parse = local_server.parse_assessment_blocks
            try:
                local_server.UPLOAD_DIR = temp_root / "uploads"
                local_server.PHASE1_DIR = temp_root / "phase1"
                local_server.PHASE2_DIR = temp_root / "phase2"
                local_server.STATE_PATH = temp_root / "server_state.json"
                local_server.write_json_file(local_server.STATE_PATH, old_state)
                local_server.extract_hwp_text = lambda upload_path, text_path: text_path.write_text(
                    "extracted",
                    encoding="utf-8",
                )
                local_server.parse_assessment_blocks = lambda text: [block_from_column(1, "Math")]

                result = local_server.recognize_hwp("template.hwp", b"fake hwp")
                state = local_server.read_json_file(local_server.STATE_PATH)

                self.assertIsNone(result["phase2"])
                self.assertIsNone(state["current_phase2_json"])
                self.assertIsNone(state["current_phase3_json"])
            finally:
                local_server.UPLOAD_DIR = original_upload_dir
                local_server.PHASE1_DIR = original_phase1_dir
                local_server.PHASE2_DIR = original_phase2_dir
                local_server.STATE_PATH = original_state_path
                local_server.extract_hwp_text = original_extract
                local_server.parse_assessment_blocks = original_parse

    def test_unique_upload_path_avoids_overwriting_same_second(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload_dir = Path(temp_dir)
            first = local_server.unique_upload_path(upload_dir, "els_screr00_r10.xls", "20260703-120000")
            first.write_bytes(b"first")
            second = local_server.unique_upload_path(upload_dir, "els_screr00_r10.xls", "20260703-120000")
            second.write_bytes(b"second")
            third = local_server.unique_upload_path(upload_dir, "els_screr00_r10.xls", "20260703-120000")

        self.assertEqual(first.name, "20260703-120000_els_screr00_r10.xls")
        self.assertEqual(second.name, "20260703-120000_els_screr00_r10_2.xls")
        self.assertEqual(third.name, "20260703-120000_els_screr00_r10_3.xls")

    def test_parse_multipart_file_preserves_binary_edges(self):
        boundary = "----CodexBoundary"
        original = b"\r\n \x00HWP-BINARY\r\n"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="note"\r\n\r\n'
            "ignored\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="template"; filename="sample.hwp"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + original + f"\r\n--{boundary}--\r\n".encode("utf-8")

        filename, content = parse_multipart_file(
            f'multipart/form-data; boundary="{boundary}"',
            body,
            "template",
        )

        self.assertEqual(filename, "sample.hwp")
        self.assertEqual(content, original)

    def test_parse_multipart_file_matches_exact_field_name(self):
        boundary = "----CodexBoundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="template_extra"; filename="wrong.hwp"\r\n\r\n'
            "wrong\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="template"; filename="right.hwp"\r\n\r\n'
            "right\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")

        filename, content = parse_multipart_file(
            f"multipart/form-data; boundary={boundary}",
            body,
            "template",
        )

        self.assertEqual(filename, "right.hwp")
        self.assertEqual(content, b"right")

    def test_phase3_response_links_only_existing_project_files(self):
        local_server.PHASE3_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local_server.PHASE3_DIR) as project_temp, tempfile.TemporaryDirectory() as outside_temp:
            project_dir = Path(project_temp)
            outside_dir = Path(outside_temp)
            existing_hwp = project_dir / "01_김대실.hwp"
            missing_hwp = project_dir / "02_이대실.hwp"
            outside_hwp = outside_dir / "outside.hwp"
            zip_path = project_dir / "hwp_reports.zip"
            combined_hwp = project_dir / "3\ud559\ub144 3\ubc18 \uc804\uccb4.hwp"
            existing_hwp.write_bytes(b"hwp")
            outside_hwp.write_bytes(b"hwp")
            zip_path.write_bytes(b"zip")
            combined_hwp.write_bytes(b"combined")
            phase3_path = project_dir / "sample.phase3.json"
            local_server.write_json_file(
                phase3_path,
                {
                    "generated_files": [str(existing_hwp), str(missing_hwp), str(outside_hwp)],
                    "generated_combined_hwp": str(combined_hwp),
                    "generated_zip": str(zip_path),
                },
            )

            payload = phase3_for_response(phase3_path)

            self.assertEqual([item["name"] for item in payload["generated_file_links"]], ["01_김대실.hwp"])
            self.assertTrue(payload["generated_file_links"][0]["url"].endswith("/01_%EA%B9%80%EB%8C%80%EC%8B%A4.hwp"))
            self.assertEqual(payload["generated_output_dir"], str(project_dir))
            self.assertTrue(payload["generated_output_dir_url"])
            self.assertEqual(payload["generated_combined_hwp_link"]["name"], "3\ud559\ub144 3\ubc18 \uc804\uccb4.hwp")
            self.assertTrue(payload["generated_combined_hwp_link"]["url"].endswith(".hwp"))
            self.assertTrue(payload["generated_zip_url"])
            self.assertEqual(payload["generated_zip_name"], "hwp_reports.zip")

    def test_get_results_hides_missing_state_files(self):
        local_server.PHASE3_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local_server.PHASE3_DIR) as project_temp:
            project_dir = Path(project_temp)
            state_path = project_dir / "server_state.json"
            local_server.write_json_file(
                state_path,
                {
                    "current_phase1_json": str(project_dir / "missing.phase1.json"),
                    "current_phase2_json": str(project_dir / "missing.phase2.json"),
                    "current_phase3_json": str(project_dir / "missing.phase3.json"),
                },
            )
            original_state_path = local_server.STATE_PATH
            try:
                local_server.STATE_PATH = state_path
                payload = local_server.get_results()
            finally:
                local_server.STATE_PATH = original_state_path

            self.assertIsNone(payload["phase1"])
            self.assertIsNone(payload["phase2"])
            self.assertIsNone(payload["phase3"])
            self.assertEqual(payload["paths"], {"phase1_json": None, "phase2_json": None, "phase3_json": None})

    def test_get_results_hides_phase2_and_phase3_when_sources_do_not_match(self):
        local_server.PHASE3_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local_server.PHASE3_DIR) as project_temp:
            project_dir = Path(project_temp)
            phase1_path = project_dir / "current.phase1.json"
            old_phase1_path = project_dir / "old.phase1.json"
            phase2_path = project_dir / "stale.phase2.json"
            phase3_path = project_dir / "stale.phase3.json"
            state_path = project_dir / "server_state.json"
            local_server.write_json_file(phase1_path, {"block_count": 1, "blocks": []})
            local_server.write_json_file(old_phase1_path, {"block_count": 1, "blocks": []})
            local_server.write_json_file(
                phase2_path,
                {
                    "source_phase1_json": str(old_phase1_path),
                    "columns": [],
                    "students": [],
                    "issues": [],
                },
            )
            local_server.write_json_file(
                phase3_path,
                {
                    "source_phase1_json": str(phase1_path),
                    "source_phase2_json": str(phase2_path),
                    "ready": True,
                    "generated_files": [],
                },
            )
            local_server.write_json_file(
                state_path,
                {
                    "current_phase1_json": str(phase1_path),
                    "current_phase2_json": str(phase2_path),
                    "current_phase3_json": str(phase3_path),
                },
            )
            original_state_path = local_server.STATE_PATH
            try:
                local_server.STATE_PATH = state_path
                payload = local_server.get_results()
            finally:
                local_server.STATE_PATH = original_state_path

            self.assertEqual(payload["phase1"]["block_count"], 1)
            self.assertIsNone(payload["phase2"])
            self.assertIsNone(payload["phase3"])
            self.assertTrue(payload["paths"]["phase1_json"].endswith("/current.phase1.json"))
            self.assertIsNone(payload["paths"]["phase2_json"])
            self.assertIsNone(payload["paths"]["phase3_json"])

    def test_direct_hwp_generation_copies_source_and_patches_each_student(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_hwp = temp_root / "template.hwp"
            output_dir = temp_root / "out"
            source_hwp.write_bytes(b"fake hwp")
            manifest = {
                "source_hwp": str(source_hwp),
                "student_placeholders": [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                "students": [
                    {
                        "number": "1",
                        "name": "김대실",
                        "assessments": [{"should_mark": True, "checkbox_ordinal": 2}],
                    },
                    {
                        "number": "1",
                        "name": "김대실",
                        "assessments": [{"should_mark": True, "checkbox_ordinal": 5}],
                    },
                ],
            }
            name_calls: list[tuple[str, str, str]] = []
            checkbox_calls: list[tuple[str, list[int]]] = []
            validation_calls: list[tuple[str, list[int]]] = []
            original_name_patch = local_server.patch_hwp_student_placeholders
            original_checkbox_patch = local_server.patch_hwp_checkboxes
            original_validation = local_server.validate_direct_generated_hwp
            try:
                local_server.patch_hwp_student_placeholders = lambda path, placeholders, number, name: name_calls.append((Path(path).name, str(number), str(name))) or 1
                local_server.patch_hwp_checkboxes = lambda path, ordinals: checkbox_calls.append((Path(path).name, list(ordinals)))
                local_server.validate_direct_generated_hwp = lambda path, manifest, student, placeholders, ordinals: validation_calls.append((Path(path).name, list(ordinals)))

                created = local_server.run_hwp_report_generation_direct(manifest, output_dir)
            finally:
                local_server.patch_hwp_student_placeholders = original_name_patch
                local_server.patch_hwp_checkboxes = original_checkbox_patch
                local_server.validate_direct_generated_hwp = original_validation

        self.assertEqual([Path(path).name for path in created], ["01_김대실.hwp", "01_김대실_2.hwp"])
        self.assertEqual(name_calls, [("01_김대실.hwp", "1", "김대실"), ("01_김대실_2.hwp", "1", "김대실")])
        self.assertEqual(checkbox_calls, [("01_김대실.hwp", [2]), ("01_김대실_2.hwp", [5])])
        self.assertEqual(validation_calls, [("01_김대실.hwp", [2]), ("01_김대실_2.hwp", [5])])

    def test_generation_student_placeholders_adds_current_hwp_name_line(self):
        source_hwp = Path("template.hwp")
        original_find_placeholders = local_server.find_hwp_student_placeholders
        try:
            local_server.find_hwp_student_placeholders = lambda path: [
                {"find": "1번 이름: 김경우", "label": "이름", "includes_number": True}
            ]

            placeholders = local_server.generation_student_placeholders(
                {
                    "student_placeholders": [
                        {"find": "0번 이름: 000", "label": "이름", "includes_number": True}
                    ]
                },
                source_hwp,
            )
        finally:
            local_server.find_hwp_student_placeholders = original_find_placeholders

        self.assertEqual(
            placeholders,
            [
                {"find": "0번 이름: 000", "label": "이름", "includes_number": True},
                {"find": "1번 이름: 김경우", "label": "이름", "includes_number": True},
            ],
        )

    def test_generation_school_info_placeholders_adds_current_hwp_values(self):
        source_hwp = Path("template.hwp")
        original_find_placeholders = local_server.find_hwp_school_info_placeholders
        try:
            local_server.find_hwp_school_info_placeholders = lambda path: [
                {"kind": "grade_class", "find": "3학년 3반"},
                {"kind": "teacher", "find": "담임 채우준", "label": "담임", "separator": " "},
            ]

            placeholders = local_server.generation_school_info_placeholders(
                {
                    "school_info_placeholders": [
                        {"kind": "grade_class", "find": "0학년 0반"}
                    ]
                },
                source_hwp,
            )
        finally:
            local_server.find_hwp_school_info_placeholders = original_find_placeholders

        self.assertEqual(
            placeholders,
            [
                {"kind": "grade_class", "find": "0학년 0반"},
                {"kind": "grade_class", "find": "3학년 3반"},
                {"kind": "teacher", "find": "담임 채우준", "label": "담임", "separator": " "},
            ],
        )

    def test_direct_hwp_generation_patches_school_info_each_student(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_hwp = temp_root / "template.hwp"
            output_dir = temp_root / "out"
            source_hwp.write_bytes(b"fake hwp")
            manifest = {
                "source_hwp": str(source_hwp),
                "student_placeholders": [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                "school_info": {"grade": "3", "class_name": "2", "teacher_name": "홍길동"},
                "school_info_placeholders": [
                    {"kind": "grade_class", "find": "0학년 0반"},
                    {"kind": "teacher", "find": "담임: 000", "label": "담임", "separator": ": "},
                ],
                "students": [{"number": "1", "name": "김대실", "assessments": []}],
            }
            school_calls: list[tuple[str, dict, int]] = []
            original_name_patch = local_server.patch_hwp_student_placeholders
            original_school_patch = local_server.patch_hwp_school_info_placeholders
            original_checkbox_patch = local_server.patch_hwp_checkboxes
            original_validation = local_server.validate_direct_generated_hwp
            try:
                local_server.patch_hwp_student_placeholders = lambda path, placeholders, number, name: 1
                local_server.patch_hwp_school_info_placeholders = lambda path, placeholders, info: school_calls.append((Path(path).name, dict(info), len(placeholders))) or 2
                local_server.patch_hwp_checkboxes = lambda path, ordinals: None
                local_server.validate_direct_generated_hwp = lambda path, manifest, student, placeholders, ordinals: None

                created = local_server.run_hwp_report_generation_direct(manifest, output_dir)
            finally:
                local_server.patch_hwp_student_placeholders = original_name_patch
                local_server.patch_hwp_school_info_placeholders = original_school_patch
                local_server.patch_hwp_checkboxes = original_checkbox_patch
                local_server.validate_direct_generated_hwp = original_validation

        self.assertEqual([Path(path).name for path in created], ["01_김대실.hwp"])
        self.assertEqual(school_calls, [("01_김대실.hwp", {"grade": "3", "class_name": "2", "teacher_name": "홍길동"}, 2)])

    def test_direct_hwp_generation_patches_teacher_story_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_hwp = temp_root / "template.hwp"
            output_dir = temp_root / "out"
            source_hwp.write_bytes(b"fake hwp")
            manifest = {
                "source_hwp": str(source_hwp),
                "student_placeholders": [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                "teacher_story_slot": {"section": "Section0", "story_record_index": 1731},
                "students": [
                    {
                        "number": "1",
                        "name": "김대실",
                        "teacher_story": "  공통 문장 개별 문장",
                        "assessments": [],
                    }
                ],
            }
            story_calls: list[tuple[str, str]] = []
            original_name_patch = local_server.patch_hwp_student_placeholders
            original_story_patch = local_server.patch_hwp_teacher_story
            original_checkbox_patch = local_server.patch_hwp_checkboxes
            original_validation = local_server.validate_direct_generated_hwp
            try:
                local_server.patch_hwp_student_placeholders = lambda path, placeholders, number, name: 1
                local_server.patch_hwp_teacher_story = lambda path, slot, story: story_calls.append((Path(path).name, story)) or 1
                local_server.patch_hwp_checkboxes = lambda path, ordinals: None
                local_server.validate_direct_generated_hwp = lambda path, manifest, student, placeholders, ordinals: None

                created = local_server.run_hwp_report_generation_direct(manifest, output_dir)
            finally:
                local_server.patch_hwp_student_placeholders = original_name_patch
                local_server.patch_hwp_teacher_story = original_story_patch
                local_server.patch_hwp_checkboxes = original_checkbox_patch
                local_server.validate_direct_generated_hwp = original_validation

        self.assertEqual([Path(path).name for path in created], ["01_김대실.hwp"])
        self.assertEqual(story_calls, [("01_김대실.hwp", "  공통 문장 개별 문장")])

    def test_save_school_info_normalizes_and_clears_phase3(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "server_state.json"
            local_server.write_json_file(
                state_path,
                {
                    "school_info": {"grade": "2", "class_name": "1", "teacher_name": "이전"},
                    "current_phase3_json": "old.phase3.json",
                },
            )

            original_state_path = local_server.STATE_PATH
            try:
                local_server.STATE_PATH = state_path
                payload = local_server.save_school_info(
                    {"grade": " 3 ", "class_name": " 2 ", "teacher_name": " 홍길동 "}
                )
            finally:
                local_server.STATE_PATH = original_state_path

            state = local_server.read_json_file(state_path)

        self.assertEqual(payload["school_info"], {"grade": "3", "class_name": "2", "teacher_name": "홍길동"})
        self.assertEqual(state["school_info"], {"grade": "3", "class_name": "2", "teacher_name": "홍길동"})
        self.assertIsNone(state["current_phase3_json"])

    def test_save_school_info_stores_roster_and_clears_scores_when_roster_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "server_state.json"
            local_server.write_json_file(
                state_path,
                {
                    "school_info": {"grade": "3", "class_name": "1", "teacher_name": "Teacher"},
                    "student_roster": [{"number": "1", "name": "Student One"}],
                    "current_phase2_json": "old.phase2.json",
                    "current_phase3_json": "old.phase3.json",
                },
            )

            original_state_path = local_server.STATE_PATH
            try:
                local_server.STATE_PATH = state_path
                payload = local_server.save_school_info(
                    {
                        "grade": "3",
                        "class_name": "2",
                        "teacher_name": "Teacher",
                        "student_roster": [
                            {"number": "1", "name": "Student One"},
                            {"number": "2", "name": "Student Two"},
                        ],
                    }
                )
            finally:
                local_server.STATE_PATH = original_state_path

            state = local_server.read_json_file(state_path)

        self.assertEqual([item["number"] for item in payload["student_roster"]], ["1", "2"])
        self.assertEqual([item["name"] for item in state["student_roster"]], ["Student One", "Student Two"])
        self.assertIsNone(state["current_phase2_json"])
        self.assertIsNone(state["current_phase3_json"])

    def test_direct_hwp_generation_removes_failed_candidate_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_hwp = temp_root / "template.hwp"
            output_dir = temp_root / "out"
            source_hwp.write_bytes(b"fake hwp")
            manifest = {
                "source_hwp": str(source_hwp),
                "student_placeholders": [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                "students": [{"number": "1", "name": "김대실", "assessments": []}],
            }
            original_name_patch = local_server.patch_hwp_student_placeholders
            original_checkbox_patch = local_server.patch_hwp_checkboxes
            original_validation = local_server.validate_direct_generated_hwp
            try:
                local_server.patch_hwp_student_placeholders = lambda path, placeholders, number, name: 1
                local_server.patch_hwp_checkboxes = lambda path, ordinals: None
                local_server.validate_direct_generated_hwp = lambda path, manifest, student, placeholders, ordinals: (_ for _ in ()).throw(ValueError("validation failed"))

                with self.assertRaisesRegex(ValueError, "validation failed"):
                    local_server.run_hwp_report_generation_direct(manifest, output_dir)
            finally:
                local_server.patch_hwp_student_placeholders = original_name_patch
                local_server.patch_hwp_checkboxes = original_checkbox_patch
                local_server.validate_direct_generated_hwp = original_validation

            self.assertFalse((output_dir / "01_김대실.hwp").exists())

    def test_validate_direct_generated_hwp_checks_counts_and_student_name(self):
        original_count_states = local_server.count_hwp_checkbox_states
        original_count_text = local_server.count_hwp_text_occurrences
        try:
            local_server.count_hwp_checkbox_states = lambda path: {"empty": 1, "filled": 2, "total": 3}
            local_server.count_hwp_text_occurrences = lambda path, text: {
                "1번 이름: 김대실": 1,
                "0번 이름: 000": 0,
            }.get(text, 0)

            local_server.validate_direct_generated_hwp(
                Path("01_김대실.hwp"),
                {"expected_checkbox_count": 3},
                {"number": "1", "name": "김대실"},
                [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                [1, 3],
            )
        finally:
            local_server.count_hwp_checkbox_states = original_count_states
            local_server.count_hwp_text_occurrences = original_count_text

    def test_validate_direct_generated_hwp_allows_current_name_when_it_is_final_text(self):
        original_count_states = local_server.count_hwp_checkbox_states
        original_count_text = local_server.count_hwp_text_occurrences
        try:
            local_server.count_hwp_checkbox_states = lambda path: {"empty": 0, "filled": 0, "total": 0}
            local_server.count_hwp_text_occurrences = lambda path, text: 1 if text == "1번 이름: 김경우" else 0

            local_server.validate_direct_generated_hwp(
                Path("01_김경우.hwp"),
                {"expected_checkbox_count": 0},
                {"number": "1", "name": "김경우"},
                [{"find": "1번 이름: 김경우", "label": "이름", "includes_number": True}],
                [],
            )
        finally:
            local_server.count_hwp_checkbox_states = original_count_states
            local_server.count_hwp_text_occurrences = original_count_text

    def test_validate_direct_generated_hwp_allows_teacher_story_matching_template_text(self):
        original_count_states = local_server.count_hwp_checkbox_states
        original_count_text = local_server.count_hwp_text_occurrences
        try:
            local_server.count_hwp_checkbox_states = lambda path: {"empty": 0, "filled": 0, "total": 0}
            local_server.count_hwp_text_occurrences = lambda path, text: 1

            local_server.validate_direct_generated_hwp(
                Path("01_김경우.hwp"),
                {
                    "expected_checkbox_count": 0,
                    "teacher_story_slot": {"example_text": "밥먹어라. 안뇽"},
                },
                {"number": "1", "name": "김경우", "teacher_story": "  밥먹어라. 안뇽"},
                [{"find": "1번 이름: 김경우", "label": "이름", "includes_number": True}],
                [],
            )
        finally:
            local_server.count_hwp_checkbox_states = original_count_states
            local_server.count_hwp_text_occurrences = original_count_text

    def test_validate_direct_generated_hwp_rejects_wrong_filled_count(self):
        original_count_states = local_server.count_hwp_checkbox_states
        original_count_text = local_server.count_hwp_text_occurrences
        try:
            local_server.count_hwp_checkbox_states = lambda path: {"empty": 2, "filled": 1, "total": 3}
            local_server.count_hwp_text_occurrences = lambda path, text: 1 if text == "1번 이름: 김대실" else 0

            with self.assertRaisesRegex(ValueError, "체크박스 표시 수"):
                local_server.validate_direct_generated_hwp(
                    Path("01_김대실.hwp"),
                    {"expected_checkbox_count": 3},
                    {"number": "1", "name": "김대실"},
                    [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                    [1, 3],
                )
        finally:
            local_server.count_hwp_checkbox_states = original_count_states
            local_server.count_hwp_text_occurrences = original_count_text

    def test_hwp_generation_reports_direct_method_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "out"
            source_hwp = temp_root / "template.hwp"
            source_hwp.write_bytes(b"fake hwp")
            local_server.write_json_file(
                manifest_path,
                {
                    "source_hwp": str(source_hwp),
                    "student_placeholders": [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                    "students": [{"number": "1", "name": "김대실", "assessments": []}],
                },
            )
            original_direct = local_server.run_hwp_report_generation_direct
            try:
                local_server.run_hwp_report_generation_direct = lambda payload, out, limit=0: [str(output_dir / "01_김대실.hwp")]

                created, info = local_server.run_hwp_report_generation(manifest_path, output_dir)
            finally:
                local_server.run_hwp_report_generation_direct = original_direct

        self.assertEqual(created, [str(output_dir / "01_김대실.hwp")])
        self.assertEqual(info, {"method": "direct_hwp_patch"})

    def test_hwp_generation_uses_direct_patch_when_teacher_story_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "out"
            output_file = output_dir / "01_김대실.hwp"
            local_server.write_json_file(
                manifest_path,
                {
                    "source_hwp": str(temp_root / "template.hwp"),
                    "teacher_story_slot": {"section": "Section0", "story_record_index": 1731, "find_text": "예시"},
                    "student_placeholders": [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                    "students": [{"number": "1", "name": "김대실", "teacher_story": "  공통 개별", "assessments": []}],
                },
            )
            direct_payloads: list[dict] = []
            subprocess_calls: list[tuple[tuple, dict]] = []
            original_direct = local_server.run_hwp_report_generation_direct
            original_subprocess_run = local_server.subprocess.run
            try:
                def fake_direct(payload, out, limit=0):
                    direct_payloads.append(dict(payload))
                    return [str(output_file)]

                def fake_run(*args, **kwargs):
                    subprocess_calls.append((args, kwargs))
                    return types.SimpleNamespace(returncode=0, stdout=f"{output_file}\n", stderr="")

                local_server.run_hwp_report_generation_direct = fake_direct
                local_server.subprocess.run = fake_run

                created, info = local_server.run_hwp_report_generation(manifest_path, output_dir)
            finally:
                local_server.run_hwp_report_generation_direct = original_direct
                local_server.subprocess.run = original_subprocess_run

        self.assertEqual(len(direct_payloads), 1)
        self.assertEqual(created, [str(output_file)])
        self.assertEqual(info, {"method": "direct_hwp_patch"})
        self.assertEqual(subprocess_calls, [])

    def test_hwp_generation_does_not_use_com_fallback_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "out"
            local_server.write_json_file(
                manifest_path,
                {
                    "source_hwp": str(temp_root / "template.hwp"),
                    "students": [{"number": "1", "name": "김대실", "assessments": []}],
                },
            )
            subprocess_calls: list[tuple[tuple, dict]] = []
            original_direct = local_server.run_hwp_report_generation_direct
            original_subprocess_run = local_server.subprocess.run
            try:
                local_server.run_hwp_report_generation_direct = lambda payload, out, limit=0: (_ for _ in ()).throw(ValueError("direct unavailable"))

                def fake_run(*args, **kwargs):
                    subprocess_calls.append((args, kwargs))
                    return types.SimpleNamespace(returncode=0, stdout="", stderr="")

                local_server.subprocess.run = fake_run

                with self.assertRaisesRegex(RuntimeError, "HWP 직접 출력 생성에 실패했습니다"):
                    local_server.run_hwp_report_generation(manifest_path, output_dir)
            finally:
                local_server.run_hwp_report_generation_direct = original_direct
                local_server.subprocess.run = original_subprocess_run

        self.assertEqual(subprocess_calls, [])

    def test_hwp_generation_reports_fallback_method_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "out"
            output_file = output_dir / "01_김대실.hwp"
            local_server.write_json_file(
                manifest_path,
                {
                    "source_hwp": str(temp_root / "template.hwp"),
                    "students": [{"number": "1", "name": "김대실", "assessments": []}],
                },
            )
            original_direct = local_server.run_hwp_report_generation_direct
            original_subprocess_run = local_server.subprocess.run
            original_checkbox_patch = local_server.patch_hwp_checkboxes
            original_validation = local_server.validate_direct_generated_hwp
            original_fallback_enabled = local_server.hwp_com_fallback_enabled
            try:
                local_server.hwp_com_fallback_enabled = lambda: True
                local_server.run_hwp_report_generation_direct = lambda payload, out, limit=0: (_ for _ in ()).throw(ValueError("direct unavailable"))
                local_server.subprocess.run = lambda *args, **kwargs: types.SimpleNamespace(
                    returncode=0,
                    stdout=f"{output_file}\n",
                    stderr="",
                )
                local_server.patch_hwp_checkboxes = lambda path, ordinals: None
                local_server.validate_direct_generated_hwp = lambda path, manifest, student, placeholders, ordinals: None

                created, info = local_server.run_hwp_report_generation(manifest_path, output_dir)
            finally:
                local_server.run_hwp_report_generation_direct = original_direct
                local_server.subprocess.run = original_subprocess_run
                local_server.patch_hwp_checkboxes = original_checkbox_patch
                local_server.validate_direct_generated_hwp = original_validation
                local_server.hwp_com_fallback_enabled = original_fallback_enabled

        self.assertEqual(created, [str(output_file)])
        self.assertEqual(info["method"], "hwp_com_fallback")
        self.assertIn("direct unavailable", info["fallback_reason"])

    def test_hwp_fallback_generation_validates_created_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "out"
            output_file = output_dir / "01_김대실.hwp"
            local_server.write_json_file(
                manifest_path,
                {
                    "source_hwp": str(temp_root / "template.hwp"),
                    "student_placeholders": [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                    "students": [{"number": "1", "name": "김대실", "assessments": [{"should_mark": True, "checkbox_ordinal": 2}]}],
                },
            )
            validation_calls: list[tuple[str, list[int]]] = []
            original_direct = local_server.run_hwp_report_generation_direct
            original_subprocess_run = local_server.subprocess.run
            original_checkbox_patch = local_server.patch_hwp_checkboxes
            original_validation = local_server.validate_direct_generated_hwp
            original_fallback_enabled = local_server.hwp_com_fallback_enabled
            try:
                local_server.hwp_com_fallback_enabled = lambda: True
                local_server.run_hwp_report_generation_direct = lambda payload, out, limit=0: (_ for _ in ()).throw(ValueError("direct unavailable"))
                local_server.subprocess.run = lambda *args, **kwargs: types.SimpleNamespace(
                    returncode=0,
                    stdout=f"{output_file}\n",
                    stderr="",
                )
                local_server.patch_hwp_checkboxes = lambda path, ordinals: None
                local_server.validate_direct_generated_hwp = lambda path, manifest, student, placeholders, ordinals: validation_calls.append((Path(path).name, list(ordinals)))

                created, info = local_server.run_hwp_report_generation(manifest_path, output_dir)
            finally:
                local_server.run_hwp_report_generation_direct = original_direct
                local_server.subprocess.run = original_subprocess_run
                local_server.patch_hwp_checkboxes = original_checkbox_patch
                local_server.validate_direct_generated_hwp = original_validation
                local_server.hwp_com_fallback_enabled = original_fallback_enabled

        self.assertEqual(created, [str(output_file)])
        self.assertEqual(info["method"], "hwp_com_fallback")
        self.assertEqual(validation_calls, [("01_김대실.hwp", [2])])

    def test_hwp_fallback_generation_removes_files_when_validation_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "out"
            output_dir.mkdir()
            output_file = output_dir / "01_김대실.hwp"
            local_server.write_json_file(
                manifest_path,
                {
                    "source_hwp": str(temp_root / "template.hwp"),
                    "student_placeholders": [{"find": "0번 이름: 000", "label": "이름", "includes_number": True}],
                    "students": [{"number": "1", "name": "김대실", "assessments": []}],
                },
            )
            original_direct = local_server.run_hwp_report_generation_direct
            original_subprocess_run = local_server.subprocess.run
            original_checkbox_patch = local_server.patch_hwp_checkboxes
            original_validation = local_server.validate_direct_generated_hwp
            original_fallback_enabled = local_server.hwp_com_fallback_enabled
            try:
                local_server.hwp_com_fallback_enabled = lambda: True
                local_server.run_hwp_report_generation_direct = lambda payload, out, limit=0: (_ for _ in ()).throw(ValueError("direct unavailable"))

                def fake_run(*args, **kwargs):
                    output_file.write_bytes(b"bad hwp")
                    return types.SimpleNamespace(returncode=0, stdout=f"{output_file}\n", stderr="")

                local_server.subprocess.run = fake_run
                local_server.patch_hwp_checkboxes = lambda path, ordinals: None
                local_server.validate_direct_generated_hwp = lambda path, manifest, student, placeholders, ordinals: (_ for _ in ()).throw(ValueError("fallback validation failed"))

                with self.assertRaisesRegex(ValueError, "fallback validation failed"):
                    local_server.run_hwp_report_generation(manifest_path, output_dir)
            finally:
                local_server.run_hwp_report_generation_direct = original_direct
                local_server.subprocess.run = original_subprocess_run
                local_server.patch_hwp_checkboxes = original_checkbox_patch
                local_server.validate_direct_generated_hwp = original_validation
                local_server.hwp_com_fallback_enabled = original_fallback_enabled

            self.assertFalse(output_file.exists())

    def test_hwp_fallback_generation_removes_files_when_subprocess_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "out"
            output_dir.mkdir()
            output_file = output_dir / "01_김대실.hwp"
            local_server.write_json_file(
                manifest_path,
                {
                    "source_hwp": str(temp_root / "template.hwp"),
                    "students": [{"number": "1", "name": "김대실", "assessments": []}],
                },
            )
            original_direct = local_server.run_hwp_report_generation_direct
            original_subprocess_run = local_server.subprocess.run
            original_fallback_enabled = local_server.hwp_com_fallback_enabled
            try:
                local_server.hwp_com_fallback_enabled = lambda: True
                local_server.run_hwp_report_generation_direct = lambda payload, out, limit=0: (_ for _ in ()).throw(ValueError("direct unavailable"))

                def fake_run(*args, **kwargs):
                    output_file.write_bytes(b"partial hwp")
                    return types.SimpleNamespace(returncode=1, stdout="", stderr="fallback failed")

                local_server.subprocess.run = fake_run

                with self.assertRaisesRegex(RuntimeError, "fallback failed"):
                    local_server.run_hwp_report_generation(manifest_path, output_dir)
            finally:
                local_server.run_hwp_report_generation_direct = original_direct
                local_server.subprocess.run = original_subprocess_run
                local_server.hwp_com_fallback_enabled = original_fallback_enabled

            self.assertFalse(output_file.exists())

    def test_resave_hwp_files_with_com_invokes_script_and_cleans_path_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            hwp_path = temp_root / "01_김대실.hwp"
            hwp_path.write_bytes(b"fake hwp")
            calls: list[tuple[list[str], dict]] = []
            list_paths: list[Path] = []
            original_subprocess_run = local_server.subprocess.run
            try:
                def fake_run(command, **kwargs):
                    calls.append((list(command), dict(kwargs)))
                    list_path = Path(command[-1])
                    list_paths.append(list_path)
                    self.assertEqual(local_server.read_json_file(list_path), [str(hwp_path)])
                    return types.SimpleNamespace(returncode=0, stdout=f"{hwp_path}\n", stderr="")

                local_server.subprocess.run = fake_run

                result = local_server.resave_hwp_files_with_com([hwp_path])
            finally:
                local_server.subprocess.run = original_subprocess_run

            self.assertEqual(result, [str(hwp_path)])
            self.assertEqual(calls[0][0][0], "powershell.exe")
            self.assertIn("resave_hwp_files.ps1", calls[0][0][5])
            self.assertFalse(list_paths[0].exists())

    def test_resave_hwp_files_with_com_writes_replacement_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            hwp_path = temp_root / "01_student.hwp"
            hwp_path.write_bytes(b"fake hwp")
            replacements = {str(hwp_path): [{"find": "  공통   ", "replace": "  공통"}]}
            list_paths: list[Path] = []
            original_subprocess_run = local_server.subprocess.run
            try:
                def fake_run(command, **kwargs):
                    list_path = Path(command[-1])
                    list_paths.append(list_path)
                    self.assertEqual(
                        local_server.read_json_file(list_path),
                        [{"path": str(hwp_path), "replacements": replacements[str(hwp_path)]}],
                    )
                    return types.SimpleNamespace(returncode=0, stdout=f"{hwp_path}\n", stderr="")

                local_server.subprocess.run = fake_run

                result = local_server.resave_hwp_files_with_com([hwp_path], replacements)
            finally:
                local_server.subprocess.run = original_subprocess_run

            self.assertEqual(result, [str(hwp_path)])
            self.assertFalse(list_paths[0].exists())
    def test_hwp_fallback_generation_removes_files_when_count_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "out"
            output_dir.mkdir()
            output_file = output_dir / "01_김대실.hwp"
            local_server.write_json_file(
                manifest_path,
                {
                    "source_hwp": str(temp_root / "template.hwp"),
                    "students": [{"number": "1", "name": "김대실", "assessments": []}],
                },
            )
            original_direct = local_server.run_hwp_report_generation_direct
            original_subprocess_run = local_server.subprocess.run
            original_fallback_enabled = local_server.hwp_com_fallback_enabled
            try:
                local_server.hwp_com_fallback_enabled = lambda: True
                local_server.run_hwp_report_generation_direct = lambda payload, out, limit=0: (_ for _ in ()).throw(ValueError("direct unavailable"))

                def fake_run(*args, **kwargs):
                    output_file.write_bytes(b"unlisted hwp")
                    return types.SimpleNamespace(returncode=0, stdout="", stderr="")

                local_server.subprocess.run = fake_run

                with self.assertRaisesRegex(RuntimeError, "개수가 학생 수와 맞지 않습니다"):
                    local_server.run_hwp_report_generation(manifest_path, output_dir)
            finally:
                local_server.run_hwp_report_generation_direct = original_direct
                local_server.subprocess.run = original_subprocess_run
                local_server.hwp_com_fallback_enabled = original_fallback_enabled

            self.assertFalse(output_file.exists())

    def test_first_subject_excel_becomes_baseline_roster(self):
        first = payload(
            "국어",
            1,
            [
                student(5, "1", "김대실", 1, "국어", "상"),
                student(6, "2", "이대실", 1, "국어", "중"),
            ],
        )

        merged = merge_phase2_payload(PHASE1_PATH, [1, 2], None, first)

        self.assertEqual(merged["metadata"]["roster"][0]["name"], "김대실")
        self.assertEqual(merged["metadata"]["roster"][1]["number"], "2")
        self.assertFalse(merged["issues"])

    def test_configured_roster_is_baseline_for_first_subject_excel(self):
        configured_roster = [
            {"number": "1", "name": "Student One"},
            {"number": "2", "name": "Student Two"},
        ]
        first = payload(
            "Math",
            1,
            [
                student(5, "1", "Student One", 1, "Math", "상"),
                student(6, "3", "Extra Student", 1, "Math", "중"),
            ],
        )

        merged = merge_phase2_payload(PHASE1_PATH, [1, 2], None, first, configured_roster)

        self.assertEqual([item["number"] for item in merged["metadata"]["roster"]], ["1", "2"])
        self.assertEqual(merged["metadata"]["roster_source"], "basic_info")
        self.assertEqual([item["number"] for item in merged["students"]], ["1", "2"])
        student_one = next(item for item in merged["students"] if item["number"] == "1")
        student_two = next(item for item in merged["students"] if item["number"] == "2")
        self.assertEqual([item["block_index"] for item in student_one["assessments"]], [1])
        self.assertEqual(student_two["assessments"], [])
        self.assertFalse(any(item["number"] == "3" for item in merged["students"]))
        self.assertTrue(any("기준 명단" in issue["message"] for issue in merged["issues"]))

    def test_save_score_grid_merges_manual_values_against_saved_roster(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            phase1_path = temp_root / "template.phase1.json"
            phase1_path.write_text('{"blocks": []}', encoding="utf-8")
            state_path = temp_root / "server_state.json"
            phase2_dir = temp_root / "phase2"
            local_server.write_json_file(
                state_path,
                {
                    "current_phase1_json": str(phase1_path),
                    "current_phase2_json": None,
                    "school_info": {"grade": "3", "class_name": "2", "teacher_name": "Teacher"},
                    "student_roster": [
                        {"number": "1", "name": "Student One"},
                        {"number": "2", "name": "Student Two"},
                    ],
                },
            )
            original_state_path = local_server.STATE_PATH
            original_phase2_dir = local_server.PHASE2_DIR
            original_load_columns = local_server.load_assessment_columns
            try:
                local_server.STATE_PATH = state_path
                local_server.PHASE2_DIR = phase2_dir
                local_server.load_assessment_columns = lambda path: [
                    AssessmentColumn(1, "Math", "Area 1", "Element 1", "1. Math / Area 1"),
                    AssessmentColumn(2, "Math", "Area 2", "Element 2", "2. Math / Area 2"),
                    AssessmentColumn(3, "Korean", "Area 3", "Element 3", "3. Korean / Area 3"),
                ]

                payload = local_server.save_score_grid(
                    {
                        "subject": "Math",
                        "rows": [
                            {"number": "1", "name": "Student One", "values": {"1": "상", "2": ""}},
                            {"number": "2", "name": "Student Two", "values": {"1": "하", "2": "중"}},
                        ],
                    }
                )
            finally:
                local_server.STATE_PATH = original_state_path
                local_server.PHASE2_DIR = original_phase2_dir
                local_server.load_assessment_columns = original_load_columns

            phase2 = payload["phase2"]

        self.assertEqual(phase2["metadata"]["roster_source"], "basic_info")
        self.assertEqual(phase2["metadata"]["imported_column_count"], 2)
        self.assertEqual([student["number"] for student in phase2["students"]], ["1", "2"])
        first = next(student for student in phase2["students"] if student["number"] == "1")
        second = next(student for student in phase2["students"] if student["number"] == "2")
        self.assertEqual([item["raw_value"] for item in first["assessments"]], ["상", ""])
        self.assertEqual([item["raw_value"] for item in second["assessments"]], ["하", "중"])

    def test_name_mismatch_and_extra_student_are_excluded_from_merge(self):
        first = payload(
            "국어",
            1,
            [
                student(5, "1", "김대실", 1, "국어", "상"),
                student(6, "2", "이대실", 1, "국어", "중"),
            ],
        )
        aggregate = merge_phase2_payload(PHASE1_PATH, [1, 2], None, first)
        second = payload(
            "수학",
            2,
            [
                student(5, "1", "김대실", 2, "수학", "하"),
                student(6, "2", "이름오타", 2, "수학", "상"),
                student(7, "3", "박대실", 2, "수학", "중"),
            ],
        )

        merged = merge_phase2_payload(PHASE1_PATH, [1, 2], aggregate, second)

        messages = "\n".join(issue["message"] for issue in merged["issues"])
        self.assertIn("이름이 기준 명단과 다릅니다", messages)
        self.assertIn("기준 명단에 없는 학생", messages)
        student_two = next(item for item in merged["students"] if item["number"] == "2")
        self.assertEqual([item["block_index"] for item in student_two["assessments"]], [1])
        self.assertFalse(any(item["number"] == "3" for item in merged["students"]))

    def test_missing_student_is_reported(self):
        first = payload(
            "국어",
            1,
            [
                student(5, "1", "김대실", 1, "국어", "상"),
                student(6, "2", "이대실", 1, "국어", "중"),
            ],
        )
        aggregate = merge_phase2_payload(PHASE1_PATH, [1, 2], None, first)
        second = payload("수학", 2, [student(5, "1", "김대실", 2, "수학", "하")])

        merged = merge_phase2_payload(PHASE1_PATH, [1, 2], aggregate, second)

        self.assertTrue(any(issue["value"] == "2 이대실" for issue in merged["issues"]))
        self.assertTrue(any("기준 명단 학생이 없습니다" in issue["message"] for issue in merged["issues"]))

    def test_reimporting_subject_removes_old_global_issues_for_that_subject(self):
        first = payload(
            "국어",
            1,
            [
                student(5, "1", "김대실", 1, "국어", "상"),
                student(6, "2", "이대실", 1, "국어", "중"),
            ],
        )
        aggregate = merge_phase2_payload(PHASE1_PATH, [1, 2], None, first)
        aggregate["metadata"]["global_issues"] = [
            {
                "source_row": 4,
                "column": "1. 국어 / 영역1",
                "message": "국어 엑셀에서 HWP 평가 항목을 찾지 못했습니다.",
                "value": "영역1",
                "severity": "error",
            },
            {
                "source_row": 4,
                "column": "사회열",
                "message": "사회 엑셀의 열은 HWP 양식 평가 목록에 없어 사용하지 않았습니다.",
                "value": "사회열",
                "severity": "warning",
            },
        ]

        clean_reimport = payload(
            "국어",
            1,
            [
                student(5, "1", "김대실", 1, "국어", "상"),
                student(6, "2", "이대실", 1, "국어", "중"),
            ],
        )
        merged = merge_phase2_payload(PHASE1_PATH, [1, 2], aggregate, clean_reimport)

        messages = "\n".join(issue["message"] for issue in merged["issues"])
        self.assertNotIn("국어 엑셀", messages)
        self.assertIn("사회 엑셀", messages)

    def test_reimporting_subject_removes_old_scores_for_missing_students(self):
        first = payload(
            "국어",
            1,
            [
                student(5, "1", "김대실", 1, "국어", "상"),
                student(6, "2", "이대실", 1, "국어", "중"),
            ],
        )
        aggregate = merge_phase2_payload(PHASE1_PATH, [1, 2], None, first)
        reimport = payload("국어", 1, [student(5, "1", "김대실", 1, "국어", "하")])

        merged = merge_phase2_payload(PHASE1_PATH, [1, 2], aggregate, reimport)

        student_one = next(item for item in merged["students"] if item["number"] == "1")
        student_two = next(item for item in merged["students"] if item["number"] == "2")
        self.assertEqual(student_one["assessments"][0]["raw_value"], "하")
        self.assertEqual(student_two["assessments"], [])
        self.assertTrue(any(issue["value"] == "2 이대실" for issue in merged["issues"]))

    def test_duplicate_student_number_in_excel_is_blocked_and_excluded(self):
        first = payload(
            "국어",
            1,
            [
                student(5, "1", "김대실", 1, "국어", "상"),
                student(6, "1", "김대실", 1, "국어", "중"),
                student(7, "2", "이대실", 1, "국어", "하"),
            ],
        )

        merged = merge_phase2_payload(PHASE1_PATH, [1, 2], None, first)

        messages = "\n".join(issue["message"] for issue in merged["issues"])
        self.assertIn("1번 학생이 두 번 이상 있습니다", messages)
        self.assertFalse(any(item["number"] == "1" for item in merged["students"]))
        self.assertTrue(any(item["number"] == "2" for item in merged["students"]))
        self.assertEqual([item["number"] for item in merged["metadata"]["roster"]], ["2"])

    def test_create_reports_zip_contains_generated_hwp_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            first = output_dir / "01_김대실.hwp"
            second = output_dir / "02_이대실.hwp"
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            zip_path = create_reports_zip([str(first), str(second)], output_dir)

            self.assertEqual(zip_path, output_dir / "hwp_reports.zip")
            self.assertTrue(zip_path.exists())
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(sorted(archive.namelist()), ["01_김대실.hwp", "02_이대실.hwp"])
                self.assertEqual(archive.read("01_김대실.hwp"), b"first")

    def test_combined_report_base_name_uses_grade_and_class(self):
        self.assertEqual(
            local_server.combined_report_base_name({"grade": "3", "class_name": "3"}),
            "3\ud559\ub144 3\ubc18 \uc804\uccb4",
        )

    def test_create_combined_hwp_report_merges_created_files_and_validates_checkboxes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            first = output_dir / "01_student.hwp"
            second = output_dir / "02_student.hwp"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            calls: list[tuple[list[str], str]] = []
            combined_name = "3\ud559\ub144 3\ubc18 \uc804\uccb4.hwp"

            original_combine = local_server.combine_hwp_files_as_sections
            original_count_states = local_server.count_hwp_checkbox_states
            original_resave = local_server.resave_hwp_files_with_com
            resave_calls: list[list[str]] = []
            try:
                def fake_combine(files, output_path):
                    calls.append(([Path(path).name for path in files], output_path.name))
                    output_path.write_bytes(b"combined")
                    return len(files)

                def fake_count_states(path):
                    if Path(path).name == combined_name:
                        return {"empty": 6, "filled": 2, "total": 8}
                    return {"empty": 3, "filled": 1, "total": 4}

                local_server.combine_hwp_files_as_sections = fake_combine
                local_server.count_hwp_checkbox_states = fake_count_states
                local_server.resave_hwp_files_with_com = lambda paths: resave_calls.append([Path(path).name for path in paths]) or []

                combined = local_server.create_combined_hwp_report(
                    [str(first), str(second)],
                    output_dir,
                    {"grade": "3", "class_name": "3"},
                )
            finally:
                local_server.combine_hwp_files_as_sections = original_combine
                local_server.count_hwp_checkbox_states = original_count_states
                local_server.resave_hwp_files_with_com = original_resave

            self.assertEqual(combined.name, combined_name)
            self.assertEqual(calls, [(["01_student.hwp", "02_student.hwp"], combined_name)])
            self.assertEqual(resave_calls, [[combined_name]])
            self.assertEqual(combined.read_bytes(), b"combined")

    def test_create_combined_hwp_report_removes_output_when_checkbox_counts_do_not_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            first = output_dir / "01_student.hwp"
            second = output_dir / "02_student.hwp"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            combined_name = "3\ud559\ub144 3\ubc18 \uc804\uccb4.hwp"

            original_combine = local_server.combine_hwp_files_as_sections
            original_count_states = local_server.count_hwp_checkbox_states
            try:
                def fake_combine(files, output_path):
                    output_path.write_bytes(b"bad combined")
                    return len(files)

                def fake_count_states(path):
                    if Path(path).name == combined_name:
                        return {"empty": 5, "filled": 2, "total": 7}
                    return {"empty": 3, "filled": 1, "total": 4}

                local_server.combine_hwp_files_as_sections = fake_combine
                local_server.count_hwp_checkbox_states = fake_count_states

                with self.assertRaisesRegex(ValueError, "HWP"):
                    local_server.create_combined_hwp_report(
                        [str(first), str(second)],
                        output_dir,
                        {"grade": "3", "class_name": "3"},
                    )
            finally:
                local_server.combine_hwp_files_as_sections = original_combine
                local_server.count_hwp_checkbox_states = original_count_states

            self.assertFalse((output_dir / combined_name).exists())

    def test_report_output_dir_separates_sample_from_full_batch(self):
        self.assertEqual(report_output_dir(0).name, "hwp_reports")
        self.assertEqual(report_output_dir(1).name, "hwp_reports_sample")

    def test_unique_output_path_skips_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "01_김대실.hwp").mkdir()
            used_names: set[str] = set()

            output_path = local_server.unique_output_path(output_dir, "01_김대실", used_names)

            self.assertEqual(output_path.name, "01_김대실_2.hwp")
            self.assertEqual(used_names, {"01_김대실_2.hwp".casefold()})

    def test_phase3_needs_refresh_when_generation_metadata_is_missing(self):
        self.assertTrue(local_server.phase3_needs_refresh({"students": []}))
        self.assertTrue(local_server.phase3_needs_refresh({"expected_checkbox_count": 3, "students": []}))
        self.assertTrue(
            local_server.phase3_needs_refresh(
                {
                    "expected_checkbox_count": 3,
                    "student_placeholders": [{"find": "0번 이름: 000"}],
                    "school_info": {"grade": "3", "class_name": "2", "teacher_name": "홍길동"},
                    "school_info_placeholders": [{"kind": "grade_class", "find": "0학년 0반"}],
                    "students": [{"assessments": [{"should_mark": True, "checkbox_ordinal": 1}]}],
                }
            )
        )
        self.assertFalse(
            local_server.phase3_needs_refresh(
                {
                    "expected_checkbox_count": 3,
                    "student_placeholders": [{"find": "0번 이름: 000"}],
                    "school_info": {"grade": "3", "class_name": "2", "teacher_name": "홍길동"},
                    "school_info_placeholders": [{"kind": "grade_class", "find": "0학년 0반"}],
                    "teacher_story_slot": None,
                    "students": [{"teacher_story": "", "assessments": [{"should_mark": True, "checkbox_ordinal": 1}]}],
                }
            )
        )

    def test_clear_phase3_generation_metadata_removes_stale_output_fields(self):
        payload = {
            "ready": True,
            "generated_at": "old",
            "generated_mode": "all",
            "generated_limit": 0,
            "generation_method": "direct_hwp_patch",
            "generation_fallback_reason": "old reason",
            "generated_files": ["old.hwp"],
            "generated_combined_hwp": "all.hwp",
            "generated_zip": "old.zip",
            "students": [],
        }

        cleared = clear_phase3_generation_metadata(payload)

        self.assertIs(cleared, payload)
        for key in (
            "generated_at",
            "generated_mode",
            "generated_limit",
            "generation_method",
            "generation_fallback_reason",
            "generated_files",
            "generated_combined_hwp",
            "generated_zip",
        ):
            self.assertNotIn(key, payload)
        self.assertTrue(payload["ready"])

    def test_generate_reports_creates_combined_hwp_for_full_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            phase3_dir = temp_root / "phase3"
            phase3_dir.mkdir()
            manifest_path = phase3_dir / "manifest.phase3.json"
            state_path = temp_root / "server_state.json"
            local_server.write_json_file(
                manifest_path,
                {
                    "ready": True,
                    "expected_checkbox_count": 3,
                    "student_placeholders": [{"find": "student"}],
                    "school_info": {"grade": "3", "class_name": "3", "teacher_name": "teacher"},
                    "school_info_placeholders": [{"kind": "grade_class", "find": "grade"}],
                    "teacher_story_slot": None,
                    "students": [{"number": "1", "name": "student", "teacher_story": "", "assessments": []}],
                },
            )
            local_server.write_json_file(state_path, {"current_phase3_json": str(manifest_path)})
            combined_name = "3\ud559\ub144 3\ubc18 \uc804\uccb4.hwp"
            combined_calls: list[tuple[list[str], str, dict]] = []

            original_state_path = local_server.STATE_PATH
            original_phase3_dir = local_server.PHASE3_DIR
            original_run_generation = local_server.run_hwp_report_generation
            original_create_combined = local_server.create_combined_hwp_report
            try:
                def fake_generation(phase3_path_arg, output_dir, limit=0):
                    first = output_dir / "01_student.hwp"
                    second = output_dir / "02_student.hwp"
                    output_dir.mkdir(parents=True, exist_ok=True)
                    first.write_bytes(b"first")
                    second.write_bytes(b"second")
                    return [str(first), str(second)], {"method": "direct_hwp_patch"}

                def fake_create_combined(created_files, output_dir, school_info):
                    combined_calls.append(([Path(path).name for path in created_files], output_dir.name, dict(school_info)))
                    combined = output_dir / combined_name
                    combined.write_bytes(b"combined")
                    return combined

                local_server.STATE_PATH = state_path
                local_server.PHASE3_DIR = phase3_dir
                local_server.run_hwp_report_generation = fake_generation
                local_server.create_combined_hwp_report = fake_create_combined

                local_server.generate_reports(0)
            finally:
                local_server.STATE_PATH = original_state_path
                local_server.PHASE3_DIR = original_phase3_dir
                local_server.run_hwp_report_generation = original_run_generation
                local_server.create_combined_hwp_report = original_create_combined

            payload = local_server.read_json_file(manifest_path)
            self.assertEqual(payload["generated_mode"], "all")
            self.assertEqual(Path(payload["generated_combined_hwp"]).name, combined_name)
            self.assertEqual(combined_calls, [(["01_student.hwp", "02_student.hwp"], "hwp_reports", {"grade": "3", "class_name": "3", "teacher_name": "teacher"})])
            with zipfile.ZipFile(payload["generated_zip"]) as archive:
                self.assertEqual(sorted(archive.namelist()), ["01_student.hwp", "02_student.hwp", combined_name])

    def test_generate_reports_clears_stale_metadata_when_generation_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            phase3_dir = temp_root / "phase3"
            phase3_dir.mkdir()
            manifest_path = phase3_dir / "manifest.phase3.json"
            state_path = temp_root / "server_state.json"
            old_hwp = phase3_dir / "hwp_reports_sample" / "01_김대실.hwp"
            old_zip = phase3_dir / "hwp_reports_sample" / "hwp_reports.zip"
            old_combined_hwp = phase3_dir / "hwp_reports_sample" / "all.hwp"
            old_hwp.parent.mkdir()
            old_hwp.write_bytes(b"old")
            old_zip.write_bytes(b"zip")
            old_combined_hwp.write_bytes(b"combined")
            local_server.write_json_file(
                manifest_path,
                {
                    "ready": True,
                    "expected_checkbox_count": 3,
                    "student_placeholders": [{"find": "0번 이름: 000"}],
                    "school_info": {"grade": "3", "class_name": "2", "teacher_name": "홍길동"},
                    "school_info_placeholders": [{"kind": "grade_class", "find": "0학년 0반"}],
                    "teacher_story_slot": None,
                    "students": [{"number": "1", "name": "김대실", "teacher_story": "", "assessments": []}],
                    "generated_at": "old",
                    "generated_mode": "sample",
                    "generated_limit": 1,
                    "generation_method": "direct_hwp_patch",
                    "generation_fallback_reason": "old reason",
                    "generated_files": [str(old_hwp)],
                    "generated_combined_hwp": str(old_combined_hwp),
                    "generated_zip": str(old_zip),
                },
            )
            local_server.write_json_file(state_path, {"current_phase3_json": str(manifest_path)})

            original_state_path = local_server.STATE_PATH
            original_phase3_dir = local_server.PHASE3_DIR
            original_run_generation = local_server.run_hwp_report_generation
            try:
                local_server.STATE_PATH = state_path
                local_server.PHASE3_DIR = phase3_dir
                local_server.run_hwp_report_generation = lambda path, out, limit=0: (_ for _ in ()).throw(RuntimeError("generation failed"))

                with self.assertRaisesRegex(RuntimeError, "generation failed"):
                    local_server.generate_reports(1)
            finally:
                local_server.STATE_PATH = original_state_path
                local_server.PHASE3_DIR = original_phase3_dir
                local_server.run_hwp_report_generation = original_run_generation

            payload = local_server.read_json_file(manifest_path)
            self.assertFalse(old_hwp.exists())
            self.assertFalse(old_zip.exists())
            self.assertFalse(old_combined_hwp.exists())
            for key in (
                "generated_at",
                "generated_mode",
                "generated_limit",
                "generation_method",
                "generation_fallback_reason",
                "generated_files",
                "generated_combined_hwp",
                "generated_zip",
            ):
                self.assertNotIn(key, payload)

    def test_validate_created_files_rejects_duplicate_output_paths(self):
        with self.assertRaisesRegex(RuntimeError, "파일명이 중복"):
            validate_created_files(["C:/out/01_김대실.hwp", "C:/out/01_김대실.hwp"], 2)

    def test_validate_created_files_rejects_count_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, "개수가 학생 수와 맞지 않습니다"):
            validate_created_files(["C:/out/01_김대실.hwp"], 2)

    def test_clear_report_output_files_removes_only_generated_hwp_and_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            hwp_file = output_dir / "01_김대실.hwp"
            zip_file = output_dir / "hwp_reports.zip"
            note_file = output_dir / "notes.txt"
            nested_dir = output_dir / "nested"
            nested_dir.mkdir()
            nested_hwp = nested_dir / "keep.hwp"
            for path in (hwp_file, zip_file, note_file, nested_hwp):
                path.write_bytes(b"data")

            removed = clear_report_output_files(output_dir)

            self.assertEqual({path.name for path in removed}, {"01_김대실.hwp", "hwp_reports.zip"})
            self.assertFalse(hwp_file.exists())
            self.assertFalse(zip_file.exists())
            self.assertTrue(note_file.exists())
            self.assertTrue(nested_hwp.exists())

    def test_expected_subject_validation_rejects_wrong_subject_card(self):
        wrong_subject = payload("수학", 2, [student(5, "1", "김대실", 2, "수학", "상")])

        with self.assertRaisesRegex(ValueError, "국어.*수학"):
            validate_expected_subject("국어", wrong_subject)

    def test_expected_subject_validation_allows_spacing_variants(self):
        spacing_variant = payload("즐거운생활", 1, [student(5, "1", "김대실", 1, "즐거운생활", "상")])

        validate_expected_subject("즐거운 생활", spacing_variant)

    def test_expected_subject_validation_rejects_unidentified_subject_excel(self):
        unidentified = payload("국어", 1, [student(5, "1", "김대실", 1, "국어", "상")])
        unidentified["metadata"] = {"excel_layout": {"subject": "", "title": ""}}

        with self.assertRaisesRegex(ValueError, "과목명을 인식하지 못했습니다"):
            validate_expected_subject("국어", unidentified)

    def test_columns_for_expected_subject_limits_phase1_columns(self):
        hwp_columns = [
            AssessmentColumn(1, "국어", "읽기", "국어 평가", "1. 국어 / 읽기"),
            AssessmentColumn(2, "사회", "지리", "사회 평가", "2. 사회 / 지리"),
            AssessmentColumn(3, "국어", "쓰기", "국어 평가", "3. 국어 / 쓰기"),
        ]

        filtered = columns_for_expected_subject(hwp_columns, "국어")

        self.assertEqual([column.index for column in filtered], [1, 3])

    def test_columns_for_expected_subject_reports_missing_subject(self):
        hwp_columns = [AssessmentColumn(1, "국어", "읽기", "국어 평가", "1. 국어 / 읽기")]

        with self.assertRaisesRegex(ValueError, "사회 과목 평가 항목"):
            columns_for_expected_subject(hwp_columns, "사회")

    def test_resolve_expected_subject_requires_subject_when_template_has_multiple_subjects(self):
        hwp_columns = [
            AssessmentColumn(1, "국어", "읽기", "국어 평가", "1. 국어 / 읽기"),
            AssessmentColumn(2, "사회", "지리", "사회 평가", "2. 사회 / 지리"),
        ]

        with self.assertRaisesRegex(ValueError, "과목별 엑셀 선택 칸"):
            resolve_expected_subject(hwp_columns, "")

    def test_resolve_expected_subject_uses_only_subject_when_template_has_one_subject(self):
        hwp_columns = [
            AssessmentColumn(1, "국어", "읽기", "국어 평가", "1. 국어 / 읽기"),
            AssessmentColumn(2, "국어", "쓰기", "국어 평가", "2. 국어 / 쓰기"),
        ]

        self.assertEqual(resolve_expected_subject(hwp_columns, ""), "국어")

    def test_reset_scores_keeps_phase1_and_clears_phase2_phase3(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            phase1 = temp_root / "sample.phase1.json"
            phase1.write_text("{}", encoding="utf-8")
            state = {
                "current_phase1_json": str(phase1),
                "current_phase2_json": str(temp_root / "sample.phase2.json"),
                "current_phase3_json": str(temp_root / "sample.phase3.json"),
            }

            original_read_state = local_server.read_state
            original_write_state = local_server.write_state
            original_get_results = local_server.get_results
            try:
                local_server.read_state = lambda: dict(state)

                def write_state(new_state):
                    state.clear()
                    state.update(new_state)

                local_server.write_state = write_state
                local_server.get_results = lambda: {"state": dict(state)}

                result = local_server.reset_scores()
            finally:
                local_server.read_state = original_read_state
                local_server.write_state = original_write_state
                local_server.get_results = original_get_results

            self.assertEqual(result["state"]["current_phase1_json"], str(phase1))
            self.assertIsNone(result["state"]["current_phase2_json"])
            self.assertIsNone(result["state"]["current_phase3_json"])

    def test_save_student_stories_syncs_to_roster_and_clears_phase3(self):
        state = {
            "student_roster": [
                {"number": "1", "name": "김대실"},
                {"number": "2", "name": "이대실"},
            ],
            "student_stories": [],
            "current_phase3_json": "old.phase3.json",
        }

        original_read_state = local_server.read_state
        original_write_state = local_server.write_state
        original_get_results = local_server.get_results
        try:
            local_server.read_state = lambda: dict(state)

            def write_state(new_state):
                state.clear()
                state.update(new_state)

            local_server.write_state = write_state
            local_server.get_results = lambda: {"student_stories": state["student_stories"], "phase3": state.get("current_phase3_json")}

            result = local_server.save_student_stories(
                {
                    "student_stories": [
                        {"number": "1", "name": "김대실", "common_story": "공통", "individual_story": "개별"},
                        {"number": "9", "name": "전학생", "common_story": "제외", "individual_story": "제외"},
                    ]
                }
            )
        finally:
            local_server.read_state = original_read_state
            local_server.write_state = original_write_state
            local_server.get_results = original_get_results

        self.assertIsNone(state["current_phase3_json"])
        self.assertEqual(
            result["student_stories"],
            [
                {"number": "1", "name": "김대실", "common_story": "공통", "individual_story": "개별"},
                {"number": "2", "name": "이대실", "common_story": "", "individual_story": ""},
            ],
        )


if __name__ == "__main__":
    unittest.main()
