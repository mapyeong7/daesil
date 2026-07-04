from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from hwp_alimi.hwp5_patch import count_hwp_checkboxes
from hwp_alimi.io_utils import atomic_write_json
from hwp_alimi.phase1 import read_text_file


STUDENT_NAME_LABELS = ("이름", "성명", "학생명")
STUDENT_PLACEHOLDER_TOKEN = r"[0O○〇]{2,3}"
STUDENT_PLACEHOLDER_RE = re.compile(
    rf"(?P<prefix>\d+\s*번\s*)?(?P<label>{'|'.join(re.escape(label) for label in STUDENT_NAME_LABELS)})\s*:\s*(?P<token>{STUDENT_PLACEHOLDER_TOKEN})",
    re.IGNORECASE,
)
GRADE_CLASS_PLACEHOLDER_RE = re.compile(r"(?P<grade>[0O○〇])\s*학년\s*(?P<class_name>[0O○〇])\s*반")
TEACHER_LABELS = ("담임교사", "담임 교사", "담임명", "교사명", "담임", "교사")
TEACHER_PLACEHOLDER_TOKEN = r"(?:[0O○〇](?:\s*[0O○〇]){1,3}|[_＿](?:\s*[_＿]){1,}|\.{3,})"
TEACHER_PLACEHOLDER_RE = re.compile(
    rf"(?P<label>{'|'.join(re.escape(label) for label in TEACHER_LABELS)})\s*(?P<separator>[:：]?\s*)(?P<token>{TEACHER_PLACEHOLDER_TOKEN})",
    re.IGNORECASE,
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


def default_output_path(phase2_json_path: Path, output_dir: Path) -> Path:
    stem = phase2_json_path.stem
    if stem.endswith(".phase2"):
        stem = stem[: -len(".phase2")]
    if stem.endswith(".phase1"):
        stem = stem[: -len(".phase1")]
    return output_dir / f"{stem}.phase3.json"


def phase1_blocks_by_index(phase1_payload: dict) -> dict[int, dict]:
    return {int(block.get("index", 0)): block for block in phase1_payload.get("blocks", [])}


def checkbox_ordinals_by_block(phase1_payload: dict) -> dict[int, dict[str, int]]:
    ordinals: dict[int, dict[str, int]] = {}
    ordinal = 1
    for block in phase1_payload.get("blocks", []):
        try:
            block_index = int(block.get("index", 0))
        except (TypeError, ValueError):
            block_index = 0
        if block_index <= 0:
            continue

        level_ordinals: dict[str, int] = {}
        for item in block.get("levels", []):
            label = str(item.get("label") or "").strip()
            if label:
                level_ordinals[label] = ordinal
            ordinal += 1
        ordinals[block_index] = level_ordinals
    return ordinals


def expected_checkbox_count(phase1_payload: dict) -> int:
    count = 0
    for block in phase1_payload.get("blocks", []):
        count += len([item for item in block.get("levels", []) if str(item.get("label") or "").strip()])
    return count


def student_placeholder_patterns() -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for label in STUDENT_NAME_LABELS:
        label_pattern = re.escape(label)
        patterns.append(
            re.compile(rf"\d+\s*번\s*{label_pattern}\s*:\s*{STUDENT_PLACEHOLDER_TOKEN}", re.IGNORECASE)
        )
        patterns.append(
            re.compile(rf"{label_pattern}\s*:\s*{STUDENT_PLACEHOLDER_TOKEN}", re.IGNORECASE)
        )
    return patterns


def has_student_placeholder(text: str) -> bool:
    return any(pattern.search(text) for pattern in student_placeholder_patterns())


def find_student_placeholders(text: str) -> list[dict]:
    placeholders: list[dict] = []
    seen: set[tuple[str, str, bool]] = set()
    for match in STUDENT_PLACEHOLDER_RE.finditer(text):
        find = match.group(0)
        label = match.group("label")
        includes_number = bool(match.group("prefix"))
        key = (find, label, includes_number)
        if key in seen:
            continue
        seen.add(key)
        placeholders.append(
            {
                "find": find,
                "label": label,
                "includes_number": includes_number,
            }
        )
    return placeholders


def normalize_school_info(school_info: dict | None) -> dict:
    school_info = school_info or {}
    return {
        "grade": str(school_info.get("grade") or "").strip(),
        "class_name": str(school_info.get("class_name") or school_info.get("class") or "").strip(),
        "teacher_name": str(school_info.get("teacher_name") or school_info.get("teacher") or "").strip(),
    }


def find_school_info_placeholders(text: str) -> list[dict]:
    placeholders: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for match in GRADE_CLASS_PLACEHOLDER_RE.finditer(text):
        find = match.group(0)
        key = ("grade_class", find)
        if key in seen:
            continue
        seen.add(key)
        placeholders.append({"kind": "grade_class", "find": find})

    for match in TEACHER_PLACEHOLDER_RE.finditer(text):
        find = match.group(0)
        label = " ".join(match.group("label").split())
        separator = match.group("separator")
        key = ("teacher", find)
        if key in seen:
            continue
        seen.add(key)
        placeholders.append(
            {
                "kind": "teacher",
                "find": find,
                "label": label,
                "separator": separator,
            }
        )

    return placeholders


def student_placeholders_from_phase1(phase1_payload: dict | None) -> list[dict]:
    extracted_text = str((phase1_payload or {}).get("extracted_text") or "").strip()
    if not extracted_text:
        return []
    text_path = Path(extracted_text)
    if not text_path.exists():
        return []
    try:
        return find_student_placeholders(read_text_file(text_path))
    except Exception:
        return []


def school_info_placeholders_from_phase1(phase1_payload: dict | None) -> list[dict]:
    extracted_text = str((phase1_payload or {}).get("extracted_text") or "").strip()
    if not extracted_text:
        return []
    text_path = Path(extracted_text)
    if not text_path.exists():
        return []
    try:
        return find_school_info_placeholders(read_text_file(text_path))
    except Exception:
        return []


def validate_student_placeholder(phase1_payload: dict) -> dict | None:
    extracted_text = str(phase1_payload.get("extracted_text") or "").strip()
    if not extracted_text:
        return {
            "severity": "error",
            "message": "HWP 텍스트 추출본 경로가 없어 학생 이름 자리표시자를 확인하지 못했습니다. HWP 양식을 다시 인식하세요.",
        }

    text_path = Path(extracted_text)
    if not text_path.exists():
        return {
            "severity": "error",
            "message": "HWP 텍스트 추출본 파일이 없어 학생 이름 자리표시자를 확인하지 못했습니다. HWP 양식을 다시 인식하세요.",
            "value": str(text_path),
        }

    try:
        text = read_text_file(text_path)
    except Exception as exc:
        return {
            "severity": "error",
            "message": f"HWP 텍스트 추출본을 읽지 못해 학생 이름 자리표시자를 확인하지 못했습니다. HWP 양식을 다시 인식하세요. ({exc})",
            "value": str(text_path),
        }

    if not find_student_placeholders(text):
        return {
            "severity": "error",
            "message": "HWP 양식에서 학생 이름 자리표시자 예: '0번 이름: 000', '이름: 000', '성명: OOO'를 찾지 못했습니다. HWP 양식을 다시 확인하세요.",
            "value": str(text_path),
        }
    return None


def validate_school_info(school_info: dict | None) -> dict | None:
    info = normalize_school_info(school_info)
    missing: list[str] = []
    if not info["grade"]:
        missing.append("학년")
    if not info["class_name"]:
        missing.append("반")
    if not info["teacher_name"]:
        missing.append("교사 이름")
    if not missing:
        return None
    return {
        "severity": "error",
        "message": "HWP에 넣을 기본 정보를 먼저 입력하고 저장하세요.",
        "value": ", ".join(missing),
    }


def validate_school_info_placeholders(phase1_payload: dict) -> dict | None:
    placeholders = school_info_placeholders_from_phase1(phase1_payload)
    kinds = {placeholder.get("kind") for placeholder in placeholders}
    missing: list[str] = []
    if "grade_class" not in kinds:
        missing.append("'0학년 0반'")
    if "teacher" not in kinds:
        missing.append("'담임: 000' 또는 '교사명: OOO'")
    if not missing:
        return None
    return {
        "severity": "error",
        "message": "HWP 양식에서 학년/반/교사 이름 자리표시자를 찾지 못했습니다. 양식에 자리표시자를 넣은 뒤 다시 인식하세요.",
        "value": ", ".join(missing),
    }


def level_text_for_block(block: dict, level: str | None) -> str:
    if not level:
        return ""
    for item in block.get("levels", []):
        if item.get("label") == level:
            return str(item.get("text") or "")
    return ""


def level_index_for_block(block: dict, level: str | None) -> int | None:
    if not level:
        return None
    for index, item in enumerate(block.get("levels", [])):
        if item.get("label") == level:
            return index
    return None


def phase2_issue_count(phase2_payload: dict, severity: str | None = None) -> int:
    issues = phase2_payload.get("issues", [])
    if severity == "warning":
        return len([issue for issue in issues if issue.get("severity") == "warning"])
    if severity == "error":
        return len([issue for issue in issues if issue.get("severity") != "warning"])
    return len(issues)


def shorten_text(value: object, max_length: int = 90) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1]}…"


def phase2_error_summary(phase2_payload: dict, max_items: int = 2) -> str:
    errors = [issue for issue in phase2_payload.get("issues", []) if issue.get("severity") != "warning"]
    samples: list[str] = []
    for issue in errors[:max_items]:
        column = str(issue.get("column") or "").strip()
        message = str(issue.get("message") or "").strip()
        value = str(issue.get("value") or "").strip()
        if column and message:
            samples.append(shorten_text(f"{column}: {message}"))
        elif message:
            samples.append(shorten_text(message))
        elif column:
            samples.append(shorten_text(column))
        elif value:
            samples.append(shorten_text(value))
    if not samples:
        return ""
    suffix = f" 외 {len(errors) - max_items}건" if len(errors) > max_items else ""
    return f" 예: {'; '.join(samples)}{suffix}"


def block_label(block: dict) -> str:
    index = str(block.get("index") or "").strip()
    subject = str(block.get("subject") or "").strip()
    area = str(block.get("area") or "").strip()
    element = str(block.get("evaluation_element") or "").strip()
    if subject and area:
        label = f"{subject} / {area}"
    else:
        label = subject or area or element or "이름 없는 평가항목"
    return f"{index}. {label}" if index else label


def imported_assessment_indexes(phase2_payload: dict) -> set[int]:
    indexes: set[int] = set()
    for column in phase2_payload.get("columns", []):
        raw_index = column.get("block_index", column.get("index"))
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if index > 0:
            indexes.add(index)
    return indexes


def missing_assessment_labels(phase1_payload: dict, phase2_payload: dict, max_items: int = 10) -> list[str]:
    imported_indexes = imported_assessment_indexes(phase2_payload)
    missing_blocks: list[dict] = []
    for block in phase1_payload.get("blocks", []):
        try:
            index = int(block.get("index", 0))
        except (TypeError, ValueError):
            continue
        if index > 0 and index not in imported_indexes:
            missing_blocks.append(block)
    labels = [block_label(block) for block in missing_blocks[:max_items]]
    if len(missing_blocks) > max_items:
        labels.append(f"외 {len(missing_blocks) - max_items}개")
    return labels


def student_missing_assessment_summaries(
    phase1_payload: dict,
    phase2_payload: dict,
    max_students: int = 5,
    max_items: int = 3,
) -> list[str]:
    blocks = phase1_blocks_by_index(phase1_payload)
    expected_indexes = set(blocks)
    summaries: list[str] = []
    for student in phase2_payload.get("students", []):
        student_indexes: set[int] = set()
        for assessment in student.get("assessments", []):
            try:
                index = int(assessment.get("block_index", 0))
            except (TypeError, ValueError):
                continue
            if index > 0:
                student_indexes.add(index)
        missing_indexes = sorted(expected_indexes - student_indexes)
        if not missing_indexes:
            continue
        name = str(student.get("name") or "").strip()
        number = str(student.get("number") or "").strip()
        label = f"{number}번 {name}".strip() or "이름 없는 학생"
        missing_labels = [block_label(blocks[index]) for index in missing_indexes[:max_items]]
        if len(missing_indexes) > max_items:
            missing_labels.append(f"외 {len(missing_indexes) - max_items}개")
        summaries.append(f"{label}: {', '.join(missing_labels)}")
        if len(summaries) >= max_students:
            break
    return summaries


def validate_phase3_inputs(
    phase1_path: Path,
    phase1_payload: dict | None,
    phase2_path: Path | None,
    phase2_payload: dict | None,
    school_info: dict | None = None,
) -> list[dict]:
    issues: list[dict] = []
    if not phase1_payload:
        issues.append({"severity": "error", "message": "HWP 양식 인식 결과가 없습니다."})
        return issues

    school_info_issue = validate_school_info(school_info)
    if school_info_issue:
        issues.append(school_info_issue)

    placeholder_issue = validate_student_placeholder(phase1_payload)
    if placeholder_issue:
        issues.append(placeholder_issue)

    school_placeholder_issue = validate_school_info_placeholders(phase1_payload)
    if school_placeholder_issue:
        issues.append(school_placeholder_issue)

    source_hwp = Path(str(phase1_payload.get("source_hwp") or ""))
    if not source_hwp.exists() or source_hwp.suffix.lower() not in {".hwp", ".hwpx"}:
        issues.append(
            {
                "severity": "error",
                "message": "원본 HWP 양식 파일 경로가 없거나 올바르지 않습니다. HWP 양식을 다시 인식하세요.",
                "value": str(source_hwp) if str(source_hwp) else "",
            }
        )
    elif source_hwp.suffix.lower() == ".hwp":
        expected_checkboxes = expected_checkbox_count(phase1_payload)
        if expected_checkboxes:
            try:
                actual_checkboxes = count_hwp_checkboxes(source_hwp)
                if actual_checkboxes != expected_checkboxes:
                    issues.append(
                        {
                            "severity": "error",
                            "message": (
                                f"원본 HWP 체크박스 수가 HWP 인식 결과와 맞지 않습니다. "
                                f"인식한 평가 수준은 {expected_checkboxes}개인데 본문 체크박스는 {actual_checkboxes}개입니다. "
                                "HWP 양식을 다시 확인하세요."
                            ),
                            "value": str(source_hwp),
                        }
                    )
            except Exception as exc:
                issues.append(
                    {
                        "severity": "error",
                        "message": f"원본 HWP 체크박스 수를 확인하지 못했습니다. HWP 양식을 다시 인식하세요. ({exc})",
                        "value": str(source_hwp),
                    }
                )

    if not phase2_path or not phase2_payload:
        issues.append({"severity": "error", "message": "과목별 성적 엑셀을 먼저 입력하세요."})
        return issues

    if str(phase2_payload.get("source_phase1_json")) != str(phase1_path):
        issues.append({"severity": "error", "message": "성적 입력 결과가 현재 HWP 양식과 맞지 않습니다."})

    if phase2_issue_count(phase2_payload, "error"):
        error_count = phase2_issue_count(phase2_payload, "error")
        issues.append(
            {
                "severity": "error",
                "message": f"성적 입력 오류 {error_count}건을 먼저 해결해야 합니다.{phase2_error_summary(phase2_payload)}",
            }
        )

    expected_count = phase2_payload.get("metadata", {}).get("expected_column_count")
    imported_count = phase2_payload.get("metadata", {}).get("imported_column_count", phase2_payload.get("block_count"))
    if expected_count is not None and imported_count is not None and int(imported_count) < int(expected_count):
        labels = missing_assessment_labels(phase1_payload, phase2_payload)
        detail = f" 누락: {', '.join(labels)}" if labels else ""
        issues.append(
            {
                "severity": "error",
                "message": f"HWP 평가 항목 {expected_count}개 중 {imported_count}개만 입력되었습니다.{detail}",
            }
        )
    elif expected_count is not None and imported_count is not None and int(imported_count) >= int(expected_count):
        summaries = student_missing_assessment_summaries(phase1_payload, phase2_payload)
        if summaries:
            issues.append(
                {
                    "severity": "error",
                    "message": f"학생별 평가 결과가 일부 빠졌습니다. 예: {'; '.join(summaries)}",
                }
            )

    if not phase2_payload.get("students"):
        issues.append({"severity": "error", "message": "출력할 학생 성적 데이터가 없습니다."})

    return issues


def build_student_output(student: dict, blocks: dict[int, dict], checkbox_ordinals: dict[int, dict[str, int]]) -> dict:
    assessments: list[dict[str, Any]] = []
    for assessment in student.get("assessments", []):
        block_index = int(assessment.get("block_index", 0))
        block = blocks.get(block_index, {})
        level = assessment.get("level")
        level_index = level_index_for_block(block, level)
        checkbox_ordinal = None
        if level:
            checkbox_ordinal = checkbox_ordinals.get(block_index, {}).get(str(level))
        assessments.append(
            {
                "block_index": block_index,
                "subject": assessment.get("subject") or block.get("subject"),
                "area": assessment.get("area") or block.get("area"),
                "evaluation_element": assessment.get("evaluation_element") or block.get("evaluation_element"),
                "level": level,
                "level_index": level_index,
                "checkbox_ordinal": checkbox_ordinal,
                "raw_value": assessment.get("raw_value") or "",
                "selected_text": level_text_for_block(block, level),
                "should_mark": bool(level),
            }
        )
    return {
        "source_row": student.get("source_row"),
        "number": str(student.get("number", "")).strip(),
        "name": str(student.get("name", "")).strip(),
        "assessments": sorted(assessments, key=lambda item: item["block_index"]),
    }


def build_phase3_payload(
    phase1_path: Path,
    phase1_payload: dict | None,
    phase2_path: Path | None,
    phase2_payload: dict | None,
    output_dir: Path,
    school_info: dict | None = None,
) -> dict:
    normalized_school_info = normalize_school_info(school_info)
    blocking_issues = validate_phase3_inputs(
        phase1_path,
        phase1_payload,
        phase2_path,
        phase2_payload,
        normalized_school_info,
    )
    blocks = phase1_blocks_by_index(phase1_payload or {})
    checkbox_ordinals = checkbox_ordinals_by_block(phase1_payload or {})
    students = [
        build_student_output(student, blocks, checkbox_ordinals)
        for student in (phase2_payload or {}).get("students", [])
    ]
    source_hwp = str((phase1_payload or {}).get("source_hwp") or "")
    return {
        "source_phase1_json": str(phase1_path),
        "source_phase2_json": str(phase2_path) if phase2_path else None,
        "source_hwp": source_hwp,
        "student_placeholders": student_placeholders_from_phase1(phase1_payload),
        "school_info": normalized_school_info,
        "school_info_placeholders": school_info_placeholders_from_phase1(phase1_payload),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ready": not blocking_issues,
        "blocking_issues": blocking_issues,
        "block_count": len(blocks),
        "expected_checkbox_count": expected_checkbox_count(phase1_payload or {}),
        "student_count": len(students),
        "output_dir": str(output_dir),
        "students": students,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="배움성장알리미 Phase 3 출력 준비 manifest 생성")
    parser.add_argument("phase1_json", type=Path)
    parser.add_argument("phase2_json", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("phase3_output"))
    parser.add_argument("--json-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    phase1_path = args.phase1_json.expanduser().resolve()
    phase2_path = args.phase2_json.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_path = args.json_path.expanduser().resolve() if args.json_path else default_output_path(phase2_path, output_dir)
    payload = build_phase3_payload(
        phase1_path,
        read_json(phase1_path),
        phase2_path,
        read_json(phase2_path),
        output_dir,
    )
    write_json(output_path, payload)
    print(f"출력 준비 JSON: {output_path}")
    if payload["ready"]:
        print(f"출력 가능: 학생 {payload['student_count']}명, 평가 항목 {payload['block_count']}개")
        return 0
    print("출력 전 해결 필요:")
    for issue in payload["blocking_issues"]:
        print(f" - {issue['message']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
