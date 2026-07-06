from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import re
import shutil
import subprocess
import time
import traceback
import zipfile
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from hwp_alimi.phase1 import (
    default_output_paths,
    extract_hwp_text,
    parse_assessment_blocks,
    read_text_file,
    write_json as write_phase1_json,
)
from hwp_alimi.phase2 import (
    default_output_path as default_phase2_output_path,
    load_assessment_columns,
    normalize_level,
    parse_neis_excel_tsv,
    parse_neis_paste,
    result_to_payload,
)
from hwp_alimi.phase3 import (
    build_phase3_payload,
    default_output_path as default_phase3_output_path,
    normalize_school_info,
)
from hwp_alimi.hwp5_patch import (
    combine_hwp_files_as_sections,
    count_hwp_checkbox_states,
    count_hwp_text_occurrences,
    patch_hwp_checkboxes,
    patch_hwp_school_info_placeholders,
    patch_hwp_student_placeholders,
    student_placeholder_replacement,
)
from hwp_alimi.io_utils import atomic_write_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = PROJECT_ROOT / "server_state.json"
UPLOAD_DIR = PROJECT_ROOT / "uploads"
PHASE1_DIR = PROJECT_ROOT / "phase1_output"
PHASE2_DIR = PROJECT_ROOT / "phase2_output"
PHASE3_DIR = PROJECT_ROOT / "phase3_output"
EXTRACT_DIR = PROJECT_ROOT / "extracted"

SAMPLE_PHASE1 = PHASE1_DIR / "2. 2차 배움성장알리미 양식 (1).phase1.json"
SAMPLE_PHASE2 = PHASE2_DIR / "2. 2차 배움성장알리미 양식 (1).phase2.json"


def safe_name(name: str) -> str:
    base = Path(name).name
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", base).strip()
    return base or "template.hwp"


def validate_upload_file(upload_name: str, content: bytes, allowed_suffixes: set[str], label: str) -> None:
    suffix = Path(upload_name or "").suffix.lower()
    if suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValueError(f"{label} 파일 형식이 아닙니다. {allowed} 파일을 선택해 주세요.")
    if not content:
        raise ValueError(f"{label} 파일이 비어 있습니다. 파일을 다시 선택해 주세요.")


def unique_upload_path(directory: Path, upload_name: str, timestamp: str | None = None) -> Path:
    timestamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
    safe = safe_name(upload_name)
    candidate = directory / f"{timestamp}_{safe}"
    if not candidate.exists():
        return candidate

    safe_path = Path(safe)
    stem = safe_path.stem or safe
    suffix = safe_path.suffix
    counter = 2
    while True:
        candidate = directory / f"{timestamp}_{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def read_json_file(path: Path | None) -> dict | None:
    if not path or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_file(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


def normalize_student_roster(roster: object) -> list[dict]:
    if not isinstance(roster, list):
        return []
    normalized: list[dict] = []
    for index, item in enumerate(roster, start=1):
        if not isinstance(item, dict):
            continue
        number = str(item.get("number") or "").strip()
        name = str(item.get("name") or "").strip()
        if not number and not name:
            continue
        normalized.append(
            {
                "number": number,
                "name": name,
                "source_row": item.get("source_row") or index,
            }
        )
    return sorted(normalized, key=student_sort_key)


def validate_student_roster(roster: list[dict]) -> list[dict]:
    normalized = normalize_student_roster(roster)
    if not normalized:
        raise ValueError("학생 명단을 1명 이상 입력하세요.")

    seen_numbers: dict[str, dict] = {}
    seen_names: dict[str, dict] = {}
    for index, student in enumerate(normalized, start=1):
        number = str(student.get("number") or "").strip()
        name = str(student.get("name") or "").strip()
        if not number or not name:
            raise ValueError(f"학생 명단 {index}행에 번호와 이름을 모두 입력하세요.")
        if number in seen_numbers:
            raise ValueError(f"학생 번호 {number}번이 중복되었습니다.")
        name_key = compact_student_name(name)
        if name_key in seen_names:
            raise ValueError(f"학생 이름 {name}이 중복되었습니다.")
        seen_numbers[number] = student
        seen_names[name_key] = student
    return normalized


def rosters_equal(left: object, right: object) -> bool:
    return normalize_student_roster(left) == normalize_student_roster(right)


def remove_files(paths: list[Path]) -> list[Path]:
    removed: list[Path] = []
    for path in paths:
        try:
            if path.exists() and path.is_file():
                path.unlink()
                removed.append(path)
        except OSError:
            pass
    return removed


def read_state() -> dict:
    state = read_json_file(STATE_PATH)
    if state:
        state["school_info"] = normalize_school_info(state.get("school_info"))
        state["student_roster"] = normalize_student_roster(state.get("student_roster"))
        return state
    return {
        "current_phase1_json": str(SAMPLE_PHASE1) if SAMPLE_PHASE1.exists() else None,
        "current_phase2_json": str(SAMPLE_PHASE2) if SAMPLE_PHASE2.exists() else None,
        "school_info": normalize_school_info(None),
        "student_roster": [],
    }


def write_state(state: dict) -> None:
    write_json_file(STATE_PATH, state)


def state_path(state: dict, key: str) -> Path | None:
    value = state.get(key)
    return Path(value) if value else None


def same_path(left: str | Path | None, right: str | Path | None) -> bool:
    if not left or not right:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return str(left) == str(right)


def payload_source_matches(payload: dict | None, source_key: str, expected_path: Path | None) -> bool:
    if not payload or not expected_path:
        return False
    return same_path(payload.get(source_key), expected_path)


def normalize_assessment_field(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def assessment_signature(item: dict) -> tuple[int, str, str, str]:
    index = int(item.get("index") or item.get("block_index") or 0)
    return (
        index,
        subject_key(item.get("subject")),
        normalize_assessment_field(item.get("area")),
        normalize_assessment_field(item.get("evaluation_element")),
    )


def phase2_columns_compatible_with_phase1(phase1_payload: dict | None, phase2_payload: dict | None) -> bool:
    if not phase1_payload or not phase2_payload:
        return False
    phase1_blocks = {
        int(block.get("index") or 0): assessment_signature(block)
        for block in phase1_payload.get("blocks", [])
    }
    phase2_columns = phase2_payload.get("columns", [])
    if not phase1_blocks or not phase2_columns:
        return False
    for column in phase2_columns:
        index = int(column.get("index") or 0)
        if phase1_blocks.get(index) != assessment_signature(column):
            return False
    return True


def migrate_phase2_payload_for_phase1(phase2_payload: dict, phase1_path: Path, phase1_payload: dict) -> dict | None:
    if not phase2_columns_compatible_with_phase1(phase1_payload, phase2_payload):
        return None

    migrated = copy.deepcopy(phase2_payload)
    previous_phase1 = migrated.get("source_phase1_json")
    expected_count = len(phase1_payload.get("blocks", []))
    imported_count = len(migrated.get("columns", []))
    errors, warnings = issue_counts_from_payload(migrated.get("issues", []))

    migrated["source_phase1_json"] = str(phase1_path)
    migrated["block_count"] = imported_count
    migrated["has_errors"] = errors > 0
    migrated["has_warnings"] = warnings > 0
    migrated["error_count"] = errors
    migrated["warning_count"] = warnings
    if errors:
        migrated["validation_mode"] = "subject_excel_error"
    elif imported_count < expected_count:
        migrated["validation_mode"] = "subject_excel_partial"
    else:
        migrated["validation_mode"] = "subject_excel_verified"

    imported_labels = ", ".join(column.get("column_label", "") for column in migrated.get("columns", [])) or "없음"
    migrated["header_message"] = (
        f"HWP 양식 기준으로 과목별 엑셀을 누적했습니다. "
        f"입력된 평가 항목 {imported_count}/{expected_count}: {imported_labels}"
    )

    metadata = migrated.setdefault("metadata", {})
    metadata["expected_column_count"] = expected_count
    metadata["imported_column_count"] = imported_count
    if previous_phase1 and not same_path(previous_phase1, phase1_path):
        metadata["migrated_from_phase1_json"] = previous_phase1
        metadata["migrated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return migrated


def preserve_compatible_phase2_for_phase1(
    previous_phase2_payload: dict | None,
    phase1_path: Path,
    phase1_payload: dict,
) -> Path | None:
    if not previous_phase2_payload:
        return None
    migrated_payload = migrate_phase2_payload_for_phase1(previous_phase2_payload, phase1_path, phase1_payload)
    if not migrated_payload:
        return None
    output_path = default_phase2_output_path(phase1_path, PHASE2_DIR)
    write_json_file(output_path, migrated_payload)
    return output_path


def api_error_response(exc: Exception) -> tuple[HTTPStatus, str]:
    if isinstance(exc, (ValueError, RuntimeError, json.JSONDecodeError, UnicodeDecodeError)):
        return HTTPStatus.BAD_REQUEST, str(exc)
    return (
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "처리 중 예기치 못한 오류가 발생했습니다. 같은 문제가 반복되면 서버 로그를 확인해 주세요.",
    )


def parse_multipart_file(content_type: str, body: bytes, field_name: str) -> tuple[str, bytes]:
    match = re.search(r"boundary=(?P<boundary>[^;]+)", content_type)
    if not match:
        raise ValueError("multipart boundary를 찾지 못했습니다.")
    boundary = match.group("boundary").strip().strip('"').encode("utf-8")
    delimiter = b"--" + boundary

    for part in body.split(delimiter):
        if not part:
            continue
        if part.startswith(b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        elif part.startswith(b"\n"):
            part = part[1:]

        header_blob, separator, data = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = header_blob.decode("utf-8", errors="replace")
        name_match = re.search(r'(?:^|;\s*)name="(?P<name>[^"]*)"', headers, re.IGNORECASE)
        if not name_match or name_match.group("name") != field_name:
            continue
        filename_match = re.search(r'filename="(?P<filename>[^"]*)"', headers)
        filename = filename_match.group("filename") if filename_match else "template.hwp"
        if data.endswith(b"\r\n"):
            data = data[:-2]
        elif data.endswith(b"\n"):
            data = data[:-1]
        return filename, data
    raise ValueError(f"{field_name!r} 파일 필드를 찾지 못했습니다.")


def recognize_hwp(upload_name: str, content: bytes) -> dict:
    validate_upload_file(upload_name, content, {".hwp", ".hwpx"}, "HWP 양식")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PHASE1_DIR.mkdir(parents=True, exist_ok=True)

    cleanup_on_error: list[Path] = []
    upload_path = unique_upload_path(UPLOAD_DIR, upload_name)
    try:
        cleanup_on_error.append(upload_path)
        upload_path.write_bytes(content)

        text_path, json_path = default_output_paths(upload_path, PHASE1_DIR)
        cleanup_on_error.extend([text_path, json_path])
        extract_hwp_text(upload_path, text_path)
        text = read_text_file(text_path)
        try:
            blocks = parse_assessment_blocks(text)
        except ValueError as exc:
            raise ValueError("HWP 양식에서 평가 항목을 찾지 못했습니다. 배움성장알리미 양식 파일인지 확인해 주세요.") from exc
        if not blocks:
            raise ValueError("HWP 양식에서 평가 항목을 찾지 못했습니다. 배움성장알리미 양식 파일인지 확인해 주세요.")
        write_phase1_json(json_path, upload_path, text_path, blocks)

        state = read_state()
        previous_phase2_payload = read_json_file(state_path(state, "current_phase2_json"))
        phase1_payload = read_json_file(json_path)
        preserved_phase2_path = preserve_compatible_phase2_for_phase1(
            previous_phase2_payload,
            json_path,
            phase1_payload,
        )
        if preserved_phase2_path:
            cleanup_on_error.append(preserved_phase2_path)

        state["current_phase1_json"] = str(json_path)
        state["current_phase2_json"] = str(preserved_phase2_path) if preserved_phase2_path else None
        state["current_phase3_json"] = None
        write_state(state)
        return get_results()
    except Exception:
        remove_files(cleanup_on_error)
        raise


def parse_neis_text(text: str) -> dict:
    state = read_state()
    phase1_path = state_path(state, "current_phase1_json")
    if phase1_path is None or not phase1_path.exists():
        raise ValueError("먼저 HWP 양식을 인식해야 합니다.")

    columns = load_assessment_columns(phase1_path)
    result = parse_neis_paste(text, columns)
    output_path = default_phase2_output_path(phase1_path, PHASE2_DIR)
    payload = result_to_payload(phase1_path, "web-paste", columns, result)
    write_json_file(output_path, payload)

    state["current_phase2_json"] = str(output_path)
    state["current_phase3_json"] = None
    write_state(state)
    return get_results()


def column_index(column: dict) -> int:
    return int(column.get("index", 0))


def student_sort_key(student: dict) -> tuple[int, str]:
    number = str(student.get("number", "")).strip()
    if number.isdigit():
        return (int(number), str(student.get("name", "")))
    return (9999, number or str(student.get("name", "")))


def compact_student_name(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "").strip())


def student_identity(student: dict) -> tuple[str, str, str]:
    number = str(student.get("number", "")).strip()
    name = str(student.get("name", "")).strip()
    key = number or f"name:{compact_student_name(name)}"
    return number, name, key


def roster_from_students(students: list[dict]) -> list[dict]:
    roster: list[dict] = []
    seen: set[str] = set()
    for student in students:
        number, name, key = student_identity(student)
        if not number and not name:
            continue
        if key in seen:
            continue
        seen.add(key)
        roster.append(
            {
                "number": number,
                "name": name,
                "source_row": student.get("source_row"),
            }
        )
    return sorted(roster, key=student_sort_key)


def import_display_name(summary: dict) -> str:
    return str(summary.get("subject") or summary.get("title") or "이번 엑셀")


def roster_lookup(roster: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_number = {str(item.get("number", "")).strip(): item for item in roster if str(item.get("number", "")).strip()}
    by_name: dict[str, dict] = {}
    duplicate_names: set[str] = set()
    for item in roster:
        name_key = compact_student_name(str(item.get("name", "")))
        if not name_key:
            continue
        if name_key in by_name:
            duplicate_names.add(name_key)
        else:
            by_name[name_key] = item
    for name_key in duplicate_names:
        by_name.pop(name_key, None)
    return by_number, by_name


def validate_incoming_roster(
    baseline_roster: list[dict],
    incoming_payload: dict,
    incoming_summary: dict,
) -> tuple[list[dict], set[str]]:
    if not baseline_roster:
        return [], set()

    issues: list[dict] = []
    excluded_student_keys: set[str] = set()
    covered_baseline_numbers: set[str] = set()
    covered_baseline_names: set[str] = set()
    baseline_by_number, baseline_by_name = roster_lookup(baseline_roster)
    label = import_display_name(incoming_summary)

    for student in incoming_payload.get("students", []):
        number, name, key = student_identity(student)
        if not number and not name:
            continue

        name_key = compact_student_name(name)
        if number and number in baseline_by_number:
            expected = baseline_by_number[number]
            expected_name = str(expected.get("name", "")).strip()
            expected_name_key = compact_student_name(expected_name)
            covered_baseline_numbers.add(number)
            if expected_name_key:
                covered_baseline_names.add(expected_name_key)
            if expected_name_key and name_key != expected_name_key:
                issues.append(
                    {
                        "source_row": student.get("source_row") or 0,
                        "column": "학생명단",
                        "message": (
                            f"{label} 엑셀의 {number}번 학생 이름이 기준 명단과 다릅니다. "
                            f"기준: {expected_name}, 엑셀: {name or '(빈칸)'}. 이 학생 성적은 반영하지 않았습니다."
                        ),
                        "value": name,
                        "severity": "error",
                    }
                )
                excluded_student_keys.add(key)
            continue

        if name_key and name_key in baseline_by_name:
            expected = baseline_by_name[name_key]
            expected_number = str(expected.get("number", "")).strip()
            if expected_number:
                covered_baseline_numbers.add(expected_number)
            covered_baseline_names.add(name_key)
            issues.append(
                {
                    "source_row": student.get("source_row") or 0,
                    "column": "학생명단",
                    "message": (
                        f"{label} 엑셀의 학생 번호가 기준 명단과 다릅니다. "
                        f"기준: {expected_number or '(번호 없음)'}번 {name}, 엑셀: {number or '(빈칸)'}번. "
                        "이 학생 성적은 반영하지 않았습니다."
                    ),
                    "value": number,
                    "severity": "error",
                }
            )
            excluded_student_keys.add(key)
            continue

        issues.append(
            {
                "source_row": student.get("source_row") or 0,
                "column": "학생명단",
                "message": f"{label} 엑셀에 기준 명단에 없는 학생이 있습니다. 이 학생 성적은 반영하지 않았습니다.",
                "value": f"{number} {name}".strip(),
                "severity": "error",
            }
        )
        excluded_student_keys.add(key)

    for expected in baseline_roster:
        expected_number = str(expected.get("number", "")).strip()
        expected_name = str(expected.get("name", "")).strip()
        expected_name_key = compact_student_name(expected_name)
        covered = bool(
            (expected_number and expected_number in covered_baseline_numbers)
            or (expected_name_key and expected_name_key in covered_baseline_names)
        )
        if covered:
            continue
        issues.append(
            {
                "source_row": 0,
                "column": "학생명단",
                "message": f"{label} 엑셀에 기준 명단 학생이 없습니다.",
                "value": f"{expected_number} {expected_name}".strip(),
                "severity": "error",
            }
        )

    return issues, excluded_student_keys


def validate_payload_roster_uniqueness(payload: dict, summary: dict) -> tuple[list[dict], set[str]]:
    issues: list[dict] = []
    excluded_student_keys: set[str] = set()
    seen_numbers: dict[str, dict] = {}
    seen_name_keys_without_number: dict[str, dict] = {}
    duplicate_keys: set[str] = set()
    label = import_display_name(summary)

    for student in payload.get("students", []):
        number, name, key = student_identity(student)
        if not number and not name:
            continue

        if number:
            previous = seen_numbers.get(number)
            if previous:
                duplicate_keys.add(key)
                duplicate_keys.add(student_identity(previous)[2])
                issues.append(
                    {
                        "source_row": student.get("source_row") or 0,
                        "column": "학생명단",
                        "message": (
                            f"{label} 엑셀에 {number}번 학생이 두 번 이상 있습니다. "
                            "중복된 학생의 성적은 반영하지 않았습니다."
                        ),
                        "value": f"{number} {name}".strip(),
                        "severity": "error",
                    }
                )
            else:
                seen_numbers[number] = student
            continue

        name_key = compact_student_name(name)
        if not name_key:
            continue
        previous = seen_name_keys_without_number.get(name_key)
        if previous:
            duplicate_keys.add(key)
            duplicate_keys.add(student_identity(previous)[2])
            issues.append(
                {
                    "source_row": student.get("source_row") or 0,
                    "column": "학생명단",
                    "message": (
                        f"{label} 엑셀에 번호 없이 이름이 같은 학생이 두 번 이상 있습니다. "
                        "번호가 없는 중복 학생의 성적은 반영하지 않았습니다."
                    ),
                    "value": name,
                    "severity": "error",
                }
            )
        else:
            seen_name_keys_without_number[name_key] = student

    excluded_student_keys.update(duplicate_keys)
    return issues, excluded_student_keys


def issue_counts_from_payload(issues: list[dict]) -> tuple[int, int]:
    errors = 0
    warnings = 0
    for issue in issues:
        if issue.get("severity") == "warning":
            warnings += 1
        else:
            errors += 1
    return errors, warnings


def subject_key(value: str | None) -> str:
    value = re.sub(r"[ㆍᆞ]+", "", str(value or "").lower())
    return re.sub(r"[\W_]+", "", value)


def excel_layout_subject_from_payload(payload: dict) -> str:
    metadata = payload.get("metadata") or {}
    excel_layout = metadata.get("excel_layout") or {}
    return str(excel_layout.get("subject") or "").strip()


def detected_subject_from_payload(payload: dict) -> str:
    subject = excel_layout_subject_from_payload(payload)
    if subject:
        return subject
    subjects = {
        str(column.get("subject") or "").strip()
        for column in payload.get("columns", [])
        if str(column.get("subject") or "").strip()
    }
    if len(subjects) == 1:
        return next(iter(subjects))
    return ""


def validate_expected_subject(expected_subject: str | None, payload: dict) -> None:
    expected = str(expected_subject or "").strip()
    if not expected:
        return

    detected = excel_layout_subject_from_payload(payload)
    if detected and subject_key(expected) != subject_key(detected):
        raise ValueError(
            f"{expected} 칸에는 {expected} 엑셀을 넣어야 합니다. "
            f"선택한 파일은 {detected} 엑셀로 인식되었습니다. "
            f"{detected} 과목 칸에 다시 넣어주세요."
        )
    if not detected:
        raise ValueError(
            f"{expected} 칸에 넣은 엑셀에서 과목명을 인식하지 못했습니다. "
            "NEIS에서 내려받은 과목별 성적 일람표 원본 파일인지 확인해 주세요."
        )


def columns_for_expected_subject(columns: list, expected_subject: str | None) -> list:
    expected = str(expected_subject or "").strip()
    if not expected:
        return columns
    filtered = [column for column in columns if subject_key(getattr(column, "subject", "")) == subject_key(expected)]
    if not filtered:
        raise ValueError(f"HWP 양식에서 {expected} 과목 평가 항목을 찾지 못했습니다. HWP 양식을 다시 확인해 주세요.")
    return filtered


def resolve_expected_subject(columns: list, requested_subject: str | None) -> str:
    requested = str(requested_subject or "").strip()
    if requested:
        columns_for_expected_subject(columns, requested)
        return requested

    subjects: list[str] = []
    seen: set[str] = set()
    for column in columns:
        subject = str(getattr(column, "subject", "") or "").strip()
        key = subject_key(subject)
        if not subject or key in seen:
            continue
        seen.add(key)
        subjects.append(subject)

    if len(subjects) == 1:
        return subjects[0]
    raise ValueError("과목별 엑셀 선택 칸에서 파일을 올려주세요. 어떤 과목 엑셀인지 알 수 없습니다.")


def imported_summary(payload: dict) -> dict:
    metadata = payload.get("metadata") or {}
    excel_layout = metadata.get("excel_layout") or {}
    return {
        "source_excel": payload.get("source_excel") or payload.get("paste_source"),
        "extracted_tsv": payload.get("extracted_tsv"),
        "source_format": payload.get("source_format"),
        "expected_subject": payload.get("expected_subject"),
        "subject": excel_layout.get("subject"),
        "title": excel_layout.get("title"),
        "column_labels": [column.get("column_label") for column in payload.get("columns", [])],
        "header_message": payload.get("header_message"),
        "created_at": payload.get("created_at"),
    }


def import_matches(import_item: dict, incoming_item: dict) -> bool:
    imported_columns = {label for label in import_item.get("column_labels", []) if label}
    incoming_columns = {label for label in incoming_item.get("column_labels", []) if label}
    if imported_columns and incoming_columns and imported_columns & incoming_columns:
        return True
    return bool(import_item.get("subject") and import_item.get("subject") == incoming_item.get("subject"))


def issue_matches_import(issue: dict, import_item: dict) -> bool:
    subject = str(import_item.get("subject") or "").strip()
    labels = {str(label) for label in import_item.get("column_labels", []) if str(label)}
    column = str(issue.get("column") or "")
    message = str(issue.get("message") or "")
    value = str(issue.get("value") or "")
    text = f"{column} {message} {value}"

    if column in labels:
        return True
    if any(label in text for label in labels):
        return True
    if subject and (f"{subject} 엑셀" in text or f"{subject} 교과" in text or f"{subject} /" in text):
        return True
    return False


def import_column_indexes(import_item: dict) -> set[int]:
    indexes: set[int] = set()
    for label in import_item.get("column_labels", []):
        match = re.match(r"\s*(\d+)\.", str(label or ""))
        if match:
            indexes.add(int(match.group(1)))
    return indexes


def column_matches_import(column: dict, import_item: dict) -> bool:
    labels = {str(label) for label in import_item.get("column_labels", []) if str(label)}
    indexes = import_column_indexes(import_item)
    column_label = str(column.get("column_label") or "")
    if column_label in labels:
        return True
    if column_index(column) in indexes:
        return True
    subject = str(import_item.get("subject") or "").strip()
    return bool(subject and subject_key(column.get("subject")) == subject_key(subject))


def header_check_matches_import(check: dict, import_item: dict) -> bool:
    labels = {str(label) for label in import_item.get("column_labels", []) if str(label)}
    expected_column = str(check.get("expected_column") or "")
    if expected_column in labels:
        return True
    indexes = import_column_indexes(import_item)
    match = re.match(r"\s*(\d+)\.", expected_column)
    return bool(match and int(match.group(1)) in indexes)


def strip_existing_import(existing_payload: dict | None, incoming_summary: dict) -> dict | None:
    if not existing_payload:
        return None
    stripped = dict(existing_payload)
    stripped["columns"] = [
        column
        for column in existing_payload.get("columns", [])
        if not column_matches_import(column, incoming_summary)
    ]
    stripped_students: list[dict] = []
    for student in existing_payload.get("students", []):
        stripped_student = dict(student)
        stripped_student["assessments"] = [
            assessment
            for assessment in student.get("assessments", [])
            if not column_matches_import(
                {
                    "index": int(assessment.get("block_index", 0)),
                    "subject": assessment.get("subject"),
                    "column_label": assessment.get("column_label"),
                },
                incoming_summary,
            )
        ]
        stripped_students.append(stripped_student)
    stripped["students"] = stripped_students
    return stripped


def dedupe_issues(issues: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    result: list[dict] = []
    for issue in issues:
        key = (
            issue.get("column"),
            issue.get("message"),
            issue.get("value"),
            issue.get("severity"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def dedupe_header_checks(checks: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    result: list[dict] = []
    for check in checks:
        key = (
            check.get("expected_column"),
            check.get("header_value"),
            check.get("status"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(check)
    return result


def global_issues_for_payload(payload: dict) -> list[dict]:
    imported_labels = {column.get("column_label") for column in payload.get("columns", [])}
    metadata = payload.get("metadata") or {}
    excel_layout = metadata.get("excel_layout") or {}
    first_data_row = int(excel_layout.get("data_start_index", 0)) + 1 if excel_layout else 0
    result: list[dict] = []
    for issue in payload.get("issues", []):
        column = issue.get("column")
        source_row = int(issue.get("source_row") or 0)
        if column not in imported_labels or (first_data_row and source_row < first_data_row):
            result.append(issue)
    return result


def merge_phase2_payload(
    phase1_path: Path,
    all_columns: list,
    existing_payload: dict | None,
    incoming_payload: dict,
    baseline_roster: list[dict] | None = None,
) -> dict:
    existing_can_merge = bool(
        existing_payload
        and existing_payload.get("source_phase1_json") == str(phase1_path)
        and existing_payload.get("source_format") in {"subject_excel_aggregate", "neis_excel_fixed"}
    )
    existing = existing_payload if existing_can_merge else None
    incoming_summary = imported_summary(incoming_payload)
    existing = strip_existing_import(existing, incoming_summary)

    columns_by_index: dict[int, dict] = {}
    for payload in (existing, incoming_payload):
        if not payload:
            continue
        for column in payload.get("columns", []):
            columns_by_index[column_index(column)] = column
    merged_columns = [columns_by_index[index] for index in sorted(columns_by_index)]

    roster_issues, excluded_student_keys = validate_payload_roster_uniqueness(incoming_payload, incoming_summary)
    metadata = existing.get("metadata", {}) if existing else {}
    configured_roster = normalize_student_roster(baseline_roster)
    baseline_roster = configured_roster or list(metadata.get("roster") or [])
    if not baseline_roster:
        if existing:
            baseline_roster = roster_from_students(existing.get("students", []))
        else:
            baseline_roster = roster_from_students(
                [
                    student
                    for student in incoming_payload.get("students", [])
                    if student_identity(student)[2] not in excluded_student_keys
                ]
            )
    baseline_roster = normalize_student_roster(baseline_roster)
    if baseline_roster and (existing or configured_roster):
        incoming_roster_issues, incoming_excluded_student_keys = validate_incoming_roster(
            baseline_roster,
            incoming_payload,
            incoming_summary,
        )
        roster_issues.extend(incoming_roster_issues)
        excluded_student_keys.update(incoming_excluded_student_keys)

    students_by_number: dict[str, dict] = {}
    merge_issues: list[dict] = []
    for roster_student in baseline_roster:
        number, name, key = student_identity(roster_student)
        if not key:
            continue
        students_by_number[key] = {
            "source_row": roster_student.get("source_row"),
            "number": number,
            "name": name,
            "assessments": [],
        }
    for payload in (existing, incoming_payload):
        if not payload:
            continue
        is_incoming = payload is incoming_payload
        for student in payload.get("students", []):
            number, name, key = student_identity(student)
            if is_incoming and key in excluded_student_keys:
                continue
            if key not in students_by_number:
                students_by_number[key] = {
                    "source_row": student.get("source_row"),
                    "number": number,
                    "name": name,
                    "assessments": [],
                }
            merged_student = students_by_number[key]
            if name and merged_student.get("name") and name != merged_student["name"]:
                merge_issues.append(
                    {
                        "source_row": student.get("source_row") or 0,
                        "column": "이름",
                        "message": f"{number}번 학생 이름이 파일마다 다릅니다. 처음 이름 {merged_student['name']}을 사용합니다.",
                        "value": name,
                        "severity": "error",
                    }
                )
            elif name and not merged_student.get("name"):
                merged_student["name"] = name

            assessments_by_index = {
                int(assessment.get("block_index", 0)): assessment
                for assessment in merged_student.get("assessments", [])
            }
            for assessment in student.get("assessments", []):
                assessments_by_index[int(assessment.get("block_index", 0))] = assessment
            merged_student["assessments"] = [
                assessments_by_index[index] for index in sorted(assessments_by_index)
            ]

    merged_students = sorted(students_by_number.values(), key=student_sort_key)

    imports = list(metadata.get("imports", []))
    if existing and not imports:
        imports.append(imported_summary(existing))
    imports = [item for item in imports if not import_matches(item, incoming_summary)]
    imports.append(incoming_summary)

    global_issues = list(metadata.get("global_issues", []))
    if existing and not global_issues:
        global_issues.extend(global_issues_for_payload(existing))
    global_issues = [issue for issue in global_issues if not issue_matches_import(issue, incoming_summary)]
    global_issues.extend(global_issues_for_payload(incoming_payload))
    global_issues = dedupe_issues(global_issues)

    assessment_issues: list[dict] = []
    for student in merged_students:
        for assessment in student.get("assessments", []):
            raw_value = str(assessment.get("raw_value") or "")
            level = assessment.get("level")
            if raw_value and level is None:
                assessment_issues.append(
                    {
                        "source_row": student.get("source_row") or 0,
                        "column": assessment.get("column_label", "평가"),
                        "message": "도달/부분도달/노력중으로 해석할 수 없는 값입니다.",
                        "value": raw_value,
                        "severity": "error",
                    }
                )

    issues = [*global_issues, *roster_issues, *merge_issues, *assessment_issues]
    error_count, warning_count = issue_counts_from_payload(issues)
    expected_count = len(all_columns)
    imported_count = len(merged_columns)
    if error_count:
        validation_mode = "subject_excel_error"
    elif imported_count < expected_count:
        validation_mode = "subject_excel_partial"
    else:
        validation_mode = "subject_excel_verified"

    imported_labels = ", ".join(column.get("column_label", "") for column in merged_columns) or "없음"
    header_message = f"HWP 양식 기준으로 과목별 엑셀을 누적했습니다. 입력된 평가 항목 {imported_count}/{expected_count}: {imported_labels}"
    header_checks = []
    if existing:
        header_checks.extend(
            check
            for check in existing.get("header_checks", [])
            if not header_check_matches_import(check, incoming_summary)
        )
    header_checks.extend(incoming_payload.get("header_checks", []))
    header_checks = dedupe_header_checks(header_checks)

    return {
        "source_phase1_json": str(phase1_path),
        "paste_source": "subject-excel-aggregate",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "block_count": imported_count,
        "student_count": len(merged_students),
        "has_errors": error_count > 0,
        "has_warnings": warning_count > 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "validation_mode": validation_mode,
        "header_exists": True,
        "header_message": header_message,
        "header_checks": header_checks,
        "source_format": "subject_excel_aggregate",
        "metadata": {
            "expected_column_count": expected_count,
            "imported_column_count": imported_count,
            "imports": imports,
            "roster": baseline_roster,
            "roster_source": "basic_info" if configured_roster else "imported_scores",
            "global_issues": global_issues,
        },
        "columns": merged_columns,
        "students": merged_students,
        "issues": issues,
    }


def assessment_column_payload(column) -> dict:
    return {
        "index": int(getattr(column, "index", 0)),
        "subject": str(getattr(column, "subject", "") or ""),
        "area": str(getattr(column, "area", "") or ""),
        "evaluation_element": str(getattr(column, "evaluation_element", "") or ""),
        "column_label": str(getattr(column, "column_label", "") or ""),
        "header_candidates": list(getattr(column, "header_candidates", []) or []),
    }


def grid_rows_by_student(rows: object) -> dict[str, dict]:
    result: dict[str, dict] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        number, name, key = student_identity(row)
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        if key:
            result[key] = {"number": number, "name": name, "values": values}
    return result


def score_grid_to_payload(
    phase1_path: Path,
    subject: str,
    columns: list,
    roster: list[dict],
    rows: object,
) -> dict:
    row_lookup = grid_rows_by_student(rows)
    column_payloads = [assessment_column_payload(column) for column in columns]
    students: list[dict] = []

    for row_index, roster_student in enumerate(roster, start=1):
        number, name, key = student_identity(roster_student)
        grid_row = row_lookup.get(key, {})
        values = grid_row.get("values") if isinstance(grid_row.get("values"), dict) else {}
        assessments: list[dict] = []
        for column, column_payload in zip(columns, column_payloads):
            block_index = int(getattr(column, "index", 0))
            raw_value = str(values.get(str(block_index)) or values.get(block_index) or "").strip()
            assessments.append(
                {
                    "block_index": block_index,
                    "column_label": column_payload["column_label"],
                    "subject": column_payload["subject"],
                    "area": column_payload["area"],
                    "evaluation_element": column_payload["evaluation_element"],
                    "level": normalize_level(raw_value) if raw_value else None,
                    "raw_value": raw_value,
                }
            )
        students.append(
            {
                "source_row": row_index,
                "number": number,
                "name": name,
                "assessments": assessments,
            }
        )

    return {
        "source_phase1_json": str(phase1_path),
        "paste_source": f"manual-grid:{subject}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "block_count": len(column_payloads),
        "student_count": len(students),
        "has_errors": False,
        "has_warnings": False,
        "error_count": 0,
        "warning_count": 0,
        "validation_mode": "manual_score_grid",
        "header_exists": True,
        "header_message": f"{subject} 입력 그리드에서 {len(column_payloads)}개 평가 항목을 저장했습니다.",
        "header_checks": [],
        "source_format": "manual_score_grid",
        "expected_subject": subject,
        "metadata": {
            "excel_layout": {
                "subject": subject,
                "title": f"{subject} 직접 입력",
                "data_start_index": 0,
            },
            "manual_grid": True,
        },
        "columns": column_payloads,
        "students": students,
        "issues": [],
    }


def save_score_grid(payload: dict) -> dict:
    state = read_state()
    phase1_path = state_path(state, "current_phase1_json")
    if phase1_path is None or not phase1_path.exists():
        raise ValueError("먼저 HWP 양식을 인식해야 합니다.")

    roster = validate_student_roster(state.get("student_roster") or [])
    all_columns = load_assessment_columns(phase1_path)
    subject = resolve_expected_subject(all_columns, payload.get("subject"))
    subject_columns = columns_for_expected_subject(all_columns, subject)
    single_payload = score_grid_to_payload(
        phase1_path,
        subject,
        subject_columns,
        roster,
        payload.get("rows") or [],
    )

    PHASE2_DIR.mkdir(parents=True, exist_ok=True)
    aggregate_output_path = default_phase2_output_path(phase1_path, PHASE2_DIR)
    existing_payload = read_json_file(state_path(state, "current_phase2_json"))
    aggregate_payload = merge_phase2_payload(
        phase1_path,
        all_columns,
        existing_payload,
        single_payload,
        roster,
    )
    aggregate_payload["latest_manual_subject"] = subject
    write_json_file(aggregate_output_path, aggregate_payload)

    state["current_phase2_json"] = str(aggregate_output_path)
    state["current_phase3_json"] = None
    write_state(state)
    return get_results()


def extract_excel_tsv(excel_path: Path, tsv_path: Path) -> None:
    script_path = PROJECT_ROOT / "scripts" / "extract_excel_tsv.ps1"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-InputPath",
        str(excel_path),
        "-OutputPath",
        str(tsv_path),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Excel 파일 추출에 실패했습니다.\n{detail}")


def import_excel(upload_name: str, content: bytes, expected_subject: str = "") -> dict:
    validate_upload_file(upload_name, content, {".xls", ".xlsx"}, "엑셀 성적표")
    state = read_state()
    phase1_path = state_path(state, "current_phase1_json")
    if phase1_path is None or not phase1_path.exists():
        raise ValueError("먼저 HWP 양식을 인식해야 합니다.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    PHASE2_DIR.mkdir(parents=True, exist_ok=True)

    cleanup_on_error: list[Path] = []
    upload_path = unique_upload_path(UPLOAD_DIR, upload_name)
    cleanup_on_error.append(upload_path)
    try:
        upload_path.write_bytes(content)

        tsv_path = EXTRACT_DIR / f"{upload_path.stem}.tsv"
        cleanup_on_error.append(tsv_path)
        extract_excel_tsv(upload_path, tsv_path)
        tsv_text = tsv_path.read_text(encoding="utf-8-sig")

        all_columns = load_assessment_columns(phase1_path)
        expected_subject = resolve_expected_subject(all_columns, expected_subject)
        parse_columns = columns_for_expected_subject(all_columns, expected_subject)
        parsed_columns, result = parse_neis_excel_tsv(tsv_text, parse_columns)

        single_output_path = PHASE2_DIR / f"{upload_path.stem}.phase2.json"
        cleanup_on_error.append(single_output_path)
        single_payload = result_to_payload(phase1_path, str(upload_path), parsed_columns, result)
        single_payload["source_excel"] = str(upload_path)
        single_payload["extracted_tsv"] = str(tsv_path)
        single_payload["expected_subject"] = str(expected_subject or "").strip()
        validate_expected_subject(expected_subject, single_payload)
        write_json_file(single_output_path, single_payload)

        aggregate_output_path = default_phase2_output_path(phase1_path, PHASE2_DIR)
        existing_payload = read_json_file(state_path(state, "current_phase2_json"))
        aggregate_payload = merge_phase2_payload(
            phase1_path,
            all_columns,
            existing_payload,
            single_payload,
            state.get("student_roster"),
        )
        aggregate_payload["latest_source_excel"] = str(upload_path)
        aggregate_payload["latest_extracted_tsv"] = str(tsv_path)
        write_json_file(aggregate_output_path, aggregate_payload)

        state["current_phase2_json"] = str(aggregate_output_path)
        state["current_phase3_json"] = None
        write_state(state)
        return get_results()
    except Exception:
        remove_files(cleanup_on_error)
        raise


def prepare_reports() -> dict:
    state = read_state()
    phase1_path = state_path(state, "current_phase1_json")
    phase2_path = state_path(state, "current_phase2_json")
    if phase1_path is None or not phase1_path.exists():
        raise ValueError("먼저 HWP 양식을 인식해야 합니다.")

    phase1_payload = read_json_file(phase1_path)
    phase2_payload = read_json_file(phase2_path)
    school_info = normalize_school_info(state.get("school_info"))
    output_path = default_phase3_output_path(phase2_path or phase1_path, PHASE3_DIR)
    phase3_payload = build_phase3_payload(
        phase1_path,
        phase1_payload,
        phase2_path,
        phase2_payload,
        PHASE3_DIR,
        school_info,
    )
    write_json_file(output_path, phase3_payload)
    state["current_phase3_json"] = str(output_path)
    write_state(state)
    return get_results()


def reset_scores() -> dict:
    state = read_state()
    phase1_path = state_path(state, "current_phase1_json")
    if phase1_path is None or not phase1_path.exists():
        raise ValueError("먼저 HWP 양식을 인식해야 합니다.")

    state["current_phase2_json"] = None
    state["current_phase3_json"] = None
    write_state(state)
    return get_results()


def save_school_info(payload: dict) -> dict:
    state = read_state()
    previous_roster = normalize_student_roster(state.get("student_roster"))
    has_roster_payload = any(key in payload for key in ("student_roster", "roster", "students"))
    next_roster = previous_roster
    if has_roster_payload:
        next_roster = validate_student_roster(
            payload.get("student_roster")
            or payload.get("roster")
            or payload.get("students")
            or []
        )
    state["school_info"] = normalize_school_info(payload)
    state["student_roster"] = next_roster
    if not rosters_equal(previous_roster, next_roster):
        state["current_phase2_json"] = None
    state["current_phase3_json"] = None
    write_state(state)
    return get_results()


def student_output_base_name(student: dict) -> str:
    number = str(student.get("number") or "").strip()
    name = str(student.get("name") or "").strip()
    try:
        number_part = f"{int(number):02d}"
    except (TypeError, ValueError):
        number_part = number
    return safe_name(f"{number_part}_{name}")


def unique_output_path(output_dir: Path, base_name: str, used_names: set[str]) -> Path:
    counter = 1
    while True:
        suffix = "" if counter == 1 else f"_{counter}"
        file_name = f"{base_name}{suffix}.hwp"
        key = file_name.casefold()
        candidate = output_dir / file_name
        if key not in used_names and not candidate.exists():
            used_names.add(key)
            return candidate
        counter += 1


def report_checkbox_ordinals(student: dict) -> list[int]:
    ordinals: list[int] = []
    for assessment in student.get("assessments", []):
        if not assessment.get("should_mark") or not assessment.get("checkbox_ordinal"):
            continue
        ordinals.append(int(assessment.get("checkbox_ordinal")))
    return ordinals


def validate_direct_generated_hwp(
    output_path: Path,
    manifest_payload: dict,
    student: dict,
    placeholders: list[dict],
    checkbox_ordinals: list[int],
) -> None:
    expected_total = int(manifest_payload.get("expected_checkbox_count") or 0)
    if expected_total:
        states = count_hwp_checkbox_states(output_path)
        if states["total"] != expected_total:
            raise ValueError(
                f"생성 HWP 체크박스 전체 수가 맞지 않습니다. 기대 {expected_total}개, 실제 {states['total']}개: {output_path.name}"
            )
        expected_filled = len(set(checkbox_ordinals))
        if states["filled"] != expected_filled:
            raise ValueError(
                f"생성 HWP 체크박스 표시 수가 맞지 않습니다. 기대 {expected_filled}개, 실제 {states['filled']}개: {output_path.name}"
            )

    replacement_hits = 0
    placeholder_hits = 0
    for placeholder in placeholders:
        replacement = student_placeholder_replacement(placeholder, student.get("number"), student.get("name"))
        replacement_hits += count_hwp_text_occurrences(output_path, replacement)
        placeholder_hits += count_hwp_text_occurrences(output_path, str(placeholder.get("find") or ""))
    if replacement_hits <= 0:
        raise ValueError(f"생성 HWP에서 학생 이름 치환 결과를 확인하지 못했습니다: {output_path.name}")
    if placeholder_hits:
        raise ValueError(f"생성 HWP에 학생 이름 자리표시자가 남아 있습니다: {output_path.name}")


def run_hwp_report_generation_direct(manifest_payload: dict, output_dir: Path, limit: int = 0) -> list[str]:
    source_hwp = Path(str(manifest_payload.get("source_hwp") or ""))
    if source_hwp.suffix.lower() != ".hwp":
        raise ValueError("직접 HWP 패치는 .hwp 원본에서만 사용할 수 있습니다.")
    if not source_hwp.exists():
        raise ValueError("원본 HWP 파일을 찾지 못했습니다.")

    placeholders = list(manifest_payload.get("student_placeholders") or [])
    if not placeholders:
        raise ValueError("학생 이름 자리표시자 정보가 없어 직접 HWP 패치를 사용할 수 없습니다.")
    school_info = normalize_school_info(manifest_payload.get("school_info"))
    school_placeholders = list(manifest_payload.get("school_info_placeholders") or [])

    output_dir.mkdir(parents=True, exist_ok=True)
    students = list(manifest_payload.get("students") or [])
    if limit:
        students = students[:limit]

    created_files: list[str] = []
    attempted_files: list[str] = []
    used_names: set[str] = set()
    try:
        for student in students:
            output_path = unique_output_path(output_dir, student_output_base_name(student), used_names)
            attempted_files.append(str(output_path))
            shutil.copy2(source_hwp, output_path)
            replacements = patch_hwp_student_placeholders(
                output_path,
                placeholders,
                student.get("number"),
                student.get("name"),
            )
            if replacements <= 0:
                raise ValueError(f"HWP 학생 이름 자리표시자를 직접 패치하지 못했습니다: {output_path.name}")
            if school_placeholders:
                school_replacements = patch_hwp_school_info_placeholders(output_path, school_placeholders, school_info)
                if school_replacements <= 0:
                    raise ValueError(f"HWP 기본 정보 자리표시자를 직접 패치하지 못했습니다: {output_path.name}")
            checkbox_ordinals = report_checkbox_ordinals(student)
            patch_hwp_checkboxes(output_path, checkbox_ordinals)
            validate_direct_generated_hwp(output_path, manifest_payload, student, placeholders, checkbox_ordinals)
            created_files.append(str(output_path))
    except Exception:
        for path in attempted_files:
            try:
                Path(path).unlink()
            except OSError:
                pass
        raise

    validate_created_files(created_files, len(students))
    return created_files


def run_hwp_report_generation(manifest_path: Path, output_dir: Path, limit: int = 0) -> tuple[list[str], dict]:
    manifest_payload = read_json_file(manifest_path) or {}
    try:
        created_files = run_hwp_report_generation_direct(manifest_payload, output_dir, limit)
        return created_files, {"method": "direct_hwp_patch"}
    except Exception as direct_exc:
        clear_report_output_files(output_dir)
        fallback_reason = str(direct_exc)

    script_path = PROJECT_ROOT / "scripts" / "generate_hwp_reports.ps1"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-ManifestPath",
        str(manifest_path),
        "-OutputDir",
        str(output_dir),
    ]
    if limit:
        command.extend(["-Limit", str(limit)])
    completed = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        clear_report_output_files(output_dir)
        raise RuntimeError(f"HWP 출력 생성에 실패했습니다.\n{detail}")
    created_files = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    students = list(manifest_payload.get("students") or [])
    if limit:
        students = students[:limit]

    try:
        validate_created_files(created_files, len(students))
        placeholders = list(manifest_payload.get("student_placeholders") or [])
        for output_path, student in zip(created_files, students):
            checkbox_ordinals = report_checkbox_ordinals(student)
            output_hwp = Path(output_path)
            patch_hwp_checkboxes(output_hwp, checkbox_ordinals)
            if placeholders:
                validate_direct_generated_hwp(output_hwp, manifest_payload, student, placeholders, checkbox_ordinals)
    except Exception:
        clear_report_output_files(output_dir)
        raise
    return created_files, {
        "method": "hwp_com_fallback",
        "fallback_reason": fallback_reason,
    }


def validate_created_files(created_files: list[str], expected_count: int) -> None:
    if len(created_files) != expected_count:
        raise RuntimeError("HWP 출력 생성 결과 개수가 학생 수와 맞지 않습니다.")

    seen: set[str] = set()
    duplicates: list[str] = []
    for path in created_files:
        key = str(Path(path).resolve()).casefold()
        if key in seen:
            duplicates.append(path)
        seen.add(key)
    if duplicates:
        sample = ", ".join(Path(path).name for path in duplicates[:3])
        raise RuntimeError(f"HWP 출력 파일명이 중복되었습니다. 학생 번호와 이름을 확인하세요: {sample}")


def clear_report_output_files(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    removed: list[Path] = []
    for path in output_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".hwp", ".zip"}:
            continue
        path.unlink()
        removed.append(path)
    return removed


def create_reports_zip(created_files: list[str], output_dir: Path) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = [Path(path) for path in created_files if Path(path).exists()]
    if not files:
        return None

    zip_path = output_dir / "hwp_reports.zip"
    used_names: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, file_path in enumerate(files, start=1):
            archive_name = file_path.name
            if archive_name in used_names:
                archive_name = f"{index:03d}_{archive_name}"
            used_names.add(archive_name)
            archive.write(file_path, archive_name)
    return zip_path


def combined_report_base_name(school_info: dict | None) -> str:
    info = normalize_school_info(school_info)
    grade = info.get("grade")
    class_name = info.get("class_name")
    if grade and class_name:
        return safe_name(f"{grade}학년 {class_name}반 전체")
    if grade:
        return safe_name(f"{grade}학년 전체")
    return safe_name("전체 학생")


def create_combined_hwp_report(created_files: list[str], output_dir: Path, school_info: dict | None) -> Path | None:
    files = [Path(path) for path in created_files if Path(path).exists()]
    if not files:
        return None

    output_path = output_dir / f"{combined_report_base_name(school_info)}.hwp"
    combine_hwp_files_as_sections(files, output_path)

    expected = {"empty": 0, "filled": 0, "total": 0}
    for file_path in files:
        states = count_hwp_checkbox_states(file_path)
        for key in expected:
            expected[key] += states[key]
    actual = count_hwp_checkbox_states(output_path)
    if actual != expected:
        output_path.unlink(missing_ok=True)
        raise ValueError(
            f"전체 학생 HWP 체크박스 수가 맞지 않습니다. 기대 {expected['total']}개/"
            f"표시 {expected['filled']}개, 실제 {actual['total']}개/표시 {actual['filled']}개"
        )
    return output_path


def report_output_dir(limit: int = 0) -> Path:
    return PHASE3_DIR / ("hwp_reports_sample" if limit > 0 else "hwp_reports")


def clear_phase3_generation_metadata(phase3_payload: dict) -> dict:
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
        phase3_payload.pop(key, None)
    return phase3_payload


def phase3_needs_refresh(phase3_payload: dict) -> bool:
    if "expected_checkbox_count" not in phase3_payload:
        return True
    if not phase3_payload.get("student_placeholders"):
        return True
    if "school_info" not in phase3_payload or "school_info_placeholders" not in phase3_payload:
        return True
    for student in phase3_payload.get("students", []):
        for assessment in student.get("assessments", []):
            if assessment.get("should_mark") and not assessment.get("checkbox_ordinal"):
                return True
    return False


def generate_reports(limit: int = 0) -> dict:
    state = read_state()
    phase3_path = state_path(state, "current_phase3_json")
    if phase3_path is None or not phase3_path.exists():
        prepare_reports()
        state = read_state()
        phase3_path = state_path(state, "current_phase3_json")
    if phase3_path is None or not phase3_path.exists():
        raise ValueError("출력 준비 데이터를 먼저 만들어야 합니다.")

    phase3_payload = read_json_file(phase3_path)
    if phase3_payload and phase3_payload.get("ready") and phase3_needs_refresh(phase3_payload):
        prepare_reports()
        state = read_state()
        phase3_path = state_path(state, "current_phase3_json")
        phase3_payload = read_json_file(phase3_path)

    if not phase3_payload or not phase3_payload.get("ready"):
        issues = phase3_payload.get("blocking_issues", []) if phase3_payload else []
        detail = "\n".join(f"- {issue.get('message', '')}" for issue in issues)
        raise ValueError(f"출력 전 확인이 필요합니다.\n{detail}".strip())

    output_dir = report_output_dir(limit)
    clear_report_output_files(output_dir)
    clear_phase3_generation_metadata(phase3_payload)
    write_json_file(phase3_path, phase3_payload)
    try:
        created_files, generation_info = run_hwp_report_generation(phase3_path, output_dir, limit)
        combined_hwp_path = None
        if limit == 0:
            combined_hwp_path = create_combined_hwp_report(
                created_files,
                output_dir,
                phase3_payload.get("school_info"),
            )
        zip_files = created_files + ([str(combined_hwp_path)] if combined_hwp_path else [])
        zip_path = create_reports_zip(zip_files, output_dir)
    except Exception:
        clear_report_output_files(output_dir)
        raise

    phase3_payload["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    phase3_payload["generated_mode"] = "sample" if limit > 0 else "all"
    phase3_payload["generated_limit"] = limit
    phase3_payload["generation_method"] = generation_info.get("method")
    if generation_info.get("fallback_reason"):
        phase3_payload["generation_fallback_reason"] = generation_info.get("fallback_reason")
    else:
        phase3_payload.pop("generation_fallback_reason", None)
    phase3_payload["generated_files"] = created_files
    phase3_payload["generated_combined_hwp"] = str(combined_hwp_path) if combined_hwp_path else None
    phase3_payload["generated_zip"] = str(zip_path) if zip_path else None
    write_json_file(phase3_path, phase3_payload)
    return get_results()


def coerce_paste_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return value["value"]
    return str(value or "")


def relative_url(path: Path | None) -> str | None:
    if not path:
        return None
    try:
        relative = path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return None
    return "/" + "/".join(quote(part) for part in relative.parts)


def existing_relative_url(path: Path | None) -> str | None:
    return relative_url(path) if path and path.exists() else None


def phase2_matches_student_roster(phase2_payload: dict | None, roster: list[dict]) -> bool:
    normalized_roster = normalize_student_roster(roster)
    if not normalized_roster:
        return True
    metadata_roster = normalize_student_roster((phase2_payload or {}).get("metadata", {}).get("roster"))
    return metadata_roster == normalized_roster


def phase3_for_response(phase3_path: Path | None) -> dict | None:
    payload = read_json_file(phase3_path)
    if not payload:
        return None

    generated_files = [Path(path) for path in payload.get("generated_files", [])]
    generated_links = []
    for path in generated_files:
        url = relative_url(path)
        if not url or not path.exists():
            continue
        generated_links.append(
            {
                "name": path.name,
                "path": str(path),
                "url": url,
            }
        )
    payload["generated_file_links"] = generated_links

    generated_combined_hwp = Path(payload["generated_combined_hwp"]) if payload.get("generated_combined_hwp") else None
    generated_combined_url = relative_url(generated_combined_hwp) if generated_combined_hwp and generated_combined_hwp.exists() else None
    payload["generated_combined_hwp_link"] = (
        {
            "name": generated_combined_hwp.name,
            "path": str(generated_combined_hwp),
            "url": generated_combined_url,
        }
        if generated_combined_url
        else None
    )

    generated_output_dir = Path(generated_links[0]["path"]).parent if generated_links else None
    if not generated_output_dir and generated_combined_hwp and generated_combined_hwp.exists():
        generated_output_dir = generated_combined_hwp.parent
    output_dir_url = relative_url(generated_output_dir) if generated_output_dir else None
    payload["generated_output_dir"] = str(generated_output_dir) if generated_output_dir else None
    payload["generated_output_dir_url"] = output_dir_url if generated_output_dir and generated_output_dir.exists() else None
    generated_zip = Path(payload["generated_zip"]) if payload.get("generated_zip") else None
    generated_zip_url = relative_url(generated_zip) if generated_zip and generated_zip.exists() else None
    payload["generated_zip_url"] = generated_zip_url
    payload["generated_zip_name"] = generated_zip.name if generated_zip_url else None
    return payload


def get_results() -> dict:
    state = read_state()
    phase1_path = state_path(state, "current_phase1_json")
    phase2_path = state_path(state, "current_phase2_json")
    phase3_path = state_path(state, "current_phase3_json")
    phase1_payload = read_json_file(phase1_path)
    if not phase1_payload:
        phase1_path = None
    student_roster = normalize_student_roster(state.get("student_roster"))

    phase2_payload = read_json_file(phase2_path)
    if not (
        phase1_path
        and phase2_payload
        and payload_source_matches(phase2_payload, "source_phase1_json", phase1_path)
        and phase2_matches_student_roster(phase2_payload, student_roster)
    ):
        phase2_path = None
        phase2_payload = None

    phase3_payload = phase3_for_response(phase3_path)
    if not (
        phase1_path
        and phase2_path
        and phase3_payload
        and payload_source_matches(phase3_payload, "source_phase1_json", phase1_path)
        and payload_source_matches(phase3_payload, "source_phase2_json", phase2_path)
        and not phase3_needs_refresh(phase3_payload)
        and normalize_school_info(phase3_payload.get("school_info")) == normalize_school_info(state.get("school_info"))
    ):
        phase3_path = None
        phase3_payload = None

    return {
        "phase1": phase1_payload,
        "phase2": phase2_payload,
        "phase3": phase3_payload,
        "school_info": normalize_school_info(state.get("school_info")),
        "student_roster": student_roster,
        "paths": {
            "phase1_json": existing_relative_url(phase1_path),
            "phase2_json": existing_relative_url(phase2_path),
            "phase3_json": existing_relative_url(phase3_path),
        },
    }


class LocalHandler(SimpleHTTPRequestHandler):
    server_version = "HwpAlimiLocal/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_error_json(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if path == "/api/results":
            self.send_json({"ok": True, **get_results()})
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            if path == "/api/recognize-template":
                content_type = self.headers.get("Content-Type", "")
                filename, content = parse_multipart_file(content_type, body, "template")
                self.send_json({"ok": True, **recognize_hwp(filename, content)})
                return
            if path == "/api/parse-neis":
                payload = json.loads(body.decode("utf-8"))
                self.send_json({"ok": True, **parse_neis_text(coerce_paste_text(payload.get("text", "")))})
                return
            if path == "/api/import-excel":
                content_type = self.headers.get("Content-Type", "")
                filename, content = parse_multipart_file(content_type, body, "excel")
                expected_subject = (parse_qs(parsed_url.query).get("subject") or [""])[0]
                self.send_json({"ok": True, **import_excel(filename, content, expected_subject)})
                return
            if path == "/api/school-info":
                payload = json.loads(body.decode("utf-8") or "{}")
                self.send_json({"ok": True, **save_school_info(payload)})
                return
            if path == "/api/save-score-grid":
                payload = json.loads(body.decode("utf-8") or "{}")
                self.send_json({"ok": True, **save_score_grid(payload)})
                return
            if path == "/api/reset-scores":
                self.send_json({"ok": True, **reset_scores()})
                return
            if path == "/api/prepare-reports":
                self.send_json({"ok": True, **prepare_reports()})
                return
            if path == "/api/generate-reports":
                limit = 0
                if body:
                    try:
                        payload = json.loads(body.decode("utf-8") or "{}")
                        limit = max(0, int(payload.get("limit") or 0))
                    except (json.JSONDecodeError, TypeError, ValueError):
                        limit = 0
                self.send_json({"ok": True, **generate_reports(limit)})
                return
            self.send_error_json("알 수 없는 API입니다.", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            status, message = api_error_response(exc)
            if status == HTTPStatus.INTERNAL_SERVER_ERROR:
                traceback.print_exc()
            self.send_error_json(message, status)

    def guess_type(self, path: str) -> str:
        if path.endswith(".hwp"):
            return "application/octet-stream"
        return mimetypes.guess_type(unquote(path))[0] or "application/octet-stream"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="배움성장알리미 로컬 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    for directory in (UPLOAD_DIR, PHASE1_DIR, PHASE2_DIR, PHASE3_DIR, EXTRACT_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), LocalHandler)
    print(f"Serving http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
