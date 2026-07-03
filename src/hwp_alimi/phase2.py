from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from hwp_alimi.io_utils import atomic_write_json


LEVELS = ("도달", "부분도달", "노력중")

LEVEL_ALIASES = {
    "도달": "도달",
    "상": "도달",
    "잘함": "도달",
    "◎": "도달",
    "부분도달": "부분도달",
    "부분": "부분도달",
    "중": "부분도달",
    "보통": "부분도달",
    "○": "부분도달",
    "노력중": "노력중",
    "노력": "노력중",
    "하": "노력중",
    "노력요함": "노력중",
    "△": "노력중",
}

NUMBER_HEADERS = {"번호", "번", "순번", "학번", "학년-반/번호", "학년반번호"}
NAME_HEADERS = {"이름", "성명", "학생명", "성명(이름)"}
BASIC_HEADERS = NUMBER_HEADERS | NAME_HEADERS | {"학년", "반", "학반", "학년반"}


@dataclass
class AssessmentColumn:
    index: int
    subject: str
    area: str
    evaluation_element: str
    column_label: str
    header_candidates: list[str] = field(default_factory=list)


@dataclass
class StudentAssessment:
    block_index: int
    column_label: str
    subject: str
    area: str
    evaluation_element: str
    level: str | None
    raw_value: str


@dataclass
class StudentRecord:
    source_row: int
    number: str
    name: str
    assessments: list[StudentAssessment]


@dataclass
class ValidationIssue:
    source_row: int
    column: str
    message: str
    value: str
    severity: str = "error"


@dataclass
class HeaderCheck:
    expected_column: str
    header_value: str
    status: str
    message: str


@dataclass
class HeaderLayout:
    header_exists: bool
    header_row_index: int | None
    label_row_index: int | None
    data_start_index: int
    number_col: int
    name_col: int
    assessment_cols: list[int]
    effective_header: list[str]
    issues: list[ValidationIssue]


@dataclass
class NeisExcelFixedLayout:
    subject: str
    title: str
    header_row_index: int
    label_row_index: int
    data_start_index: int
    number_col: int
    name_col: int
    assessment_cols: list[int]
    effective_header: list[str]


@dataclass
class Phase2Result:
    block_count: int
    student_count: int
    students: list[StudentRecord]
    issues: list[ValidationIssue]
    validation_mode: str = "unknown"
    header_exists: bool = False
    header_message: str = ""
    header_checks: list[HeaderCheck] = field(default_factory=list)
    source_format: str = "generic"
    metadata: dict = field(default_factory=dict)


def clean_cell(value: str) -> str:
    value = html.unescape(value).replace("\u2027", "·")
    value = value.replace("\ufeff", "")
    return re.sub(r"\s+", " ", value).strip()


def compact_key(value: str) -> str:
    return re.sub(r"\s+", "", clean_cell(value))


def header_key(value: str) -> str:
    value = clean_cell(value).lower()
    value = re.sub(r"^[0-9]+[.)]?\s*", "", value)
    value = re.sub(r"[ㆍᆞ]+", "", value)
    value = re.sub(r"[\W_]+", "", value)
    return value


def unique_non_empty(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean_cell(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def normalize_level(value: str) -> str | None:
    key = compact_key(value)
    return LEVEL_ALIASES.get(key)


def split_pasted_table(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw_line.strip():
            continue
        if "\t" in raw_line:
            cells = next(csv.reader([raw_line], delimiter="\t"))
        elif "," in raw_line:
            cells = next(csv.reader([raw_line]))
        else:
            cells = re.split(r"\s{2,}", raw_line.strip())
            if len(cells) == 1:
                cells = raw_line.strip().split()
        cleaned = [clean_cell(cell) for cell in cells]
        if any(cleaned):
            rows.append(cleaned)
    return rows


def load_assessment_columns(phase1_json_path: Path) -> list[AssessmentColumn]:
    data = json.loads(phase1_json_path.read_text(encoding="utf-8"))
    blocks = data.get("blocks", [])
    columns: list[AssessmentColumn] = []
    for block in blocks:
        index = int(block["index"])
        subject = clean_cell(str(block["subject"]))
        area = clean_cell(str(block["area"]))
        evaluation_element = clean_cell(str(block["evaluation_element"]))
        header_candidates = block.get("expected_neis_headers")
        if not header_candidates:
            header_candidates = [
                f"{subject} / {area}",
                f"{subject} {area}",
                area,
                subject,
                evaluation_element,
            ]
        columns.append(
            AssessmentColumn(
                index=index,
                subject=subject,
                area=area,
                evaluation_element=evaluation_element,
                column_label=f"{index}. {subject} / {area}",
                header_candidates=unique_non_empty(str(value) for value in header_candidates),
            )
        )
    if not columns:
        raise ValueError("Phase 1 JSON에서 평가 항목을 찾지 못했습니다.")
    return columns


def looks_like_header(row: list[str]) -> bool:
    keys = {compact_key(cell) for cell in row}
    header_keys = {header_key(cell) for cell in row}
    has_name = bool(keys & NAME_HEADERS) or bool(header_keys & {header_key(value) for value in NAME_HEADERS})
    has_number = bool(keys & NUMBER_HEADERS) or bool(header_keys & {header_key(value) for value in NUMBER_HEADERS})
    has_level_word = any(key in LEVELS for key in keys)
    return has_name and (has_number or not has_level_word)


def find_header_index(row: list[str], candidates: set[str]) -> int | None:
    candidate_keys = {compact_key(candidate) for candidate in candidates} | {header_key(candidate) for candidate in candidates}
    for i, cell in enumerate(row):
        if compact_key(cell) in candidate_keys or header_key(cell) in candidate_keys:
            return i
    return None


def find_table_header_row(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows):
        if looks_like_header(row):
            return index
    return None


def has_label_row(row: list[str], number_col: int, name_col: int) -> bool:
    if cell_at(row, number_col) or cell_at(row, name_col):
        return False
    non_empty_after_name = [
        cell
        for index, cell in enumerate(row)
        if index not in {number_col, name_col} and index > min(number_col, name_col) and clean_cell(cell)
    ]
    return bool(non_empty_after_name)


def effective_header_from_rows(
    header: list[str],
    label_row: list[str] | None,
    width: int,
    number_col: int,
    name_col: int,
) -> list[str]:
    result: list[str] = []
    for index in range(width):
        if index in {number_col, name_col}:
            result.append(cell_at(header, index))
        elif label_row is not None and cell_at(label_row, index):
            result.append(cell_at(label_row, index))
        else:
            result.append(cell_at(header, index))
    return result


def choose_assessment_cols_from_header(
    header: list[str],
    effective_header: list[str],
    number_col: int,
    name_col: int,
) -> list[int]:
    result: list[int] = []
    for index, value in enumerate(effective_header):
        if index in {number_col, name_col}:
            continue
        key = compact_key(value)
        if not key or key in BASIC_HEADERS:
            continue
        result.append(index)
    if result:
        return result

    start = max(number_col, name_col) + 1
    return [index for index in range(start, len(header)) if clean_cell(cell_at(header, index))]


def make_issue(source_row: int, column: str, message: str, value: str = "", severity: str = "error") -> ValidationIssue:
    return ValidationIssue(source_row, column, message, value, severity)


def issue_counts(issues: Iterable[ValidationIssue]) -> tuple[int, int]:
    error_count = 0
    warning_count = 0
    for issue in issues:
        if issue.severity == "warning":
            warning_count += 1
        else:
            error_count += 1
    return error_count, warning_count


def has_errors(issues: Iterable[ValidationIssue]) -> bool:
    return any(issue.severity != "warning" for issue in issues)


def duplicate_counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = header_key(value)
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def header_keys_for_column(column: AssessmentColumn, columns: list[AssessmentColumn]) -> set[str]:
    subject_counts = duplicate_counts(item.subject for item in columns)
    area_counts = duplicate_counts(item.area for item in columns)

    values = [
        column.column_label,
        f"{column.subject} / {column.area}",
        f"{column.subject} {column.area}",
        f"{column.subject}-{column.area}",
        column.evaluation_element,
        *column.header_candidates,
    ]
    if subject_counts.get(header_key(column.subject), 0) == 1:
        values.append(column.subject)
    if area_counts.get(header_key(column.area), 0) == 1:
        values.append(column.area)
    return {header_key(value) for value in values if header_key(value)}


def matching_column_indexes(header_value: str, columns: list[AssessmentColumn]) -> list[int]:
    key = header_key(header_value)
    if not key:
        return []

    direct_matches = [
        index
        for index, column in enumerate(columns)
        if key in header_keys_for_column(column, columns)
    ]
    if direct_matches:
        return direct_matches

    fuzzy_matches: list[int] = []
    if len(key) >= 4:
        for index, column in enumerate(columns):
            candidates = header_keys_for_column(column, columns)
            if any(len(candidate) >= 4 and (key in candidate or candidate in key) for candidate in candidates):
                fuzzy_matches.append(index)
    return fuzzy_matches


def validate_header_cells(
    header: list[str],
    assessment_cols: list[int],
    columns: list[AssessmentColumn],
    source_row: int = 1,
) -> tuple[list[ValidationIssue], list[HeaderCheck], str, str]:
    issues: list[ValidationIssue] = []
    checks: list[HeaderCheck] = []
    status_counts = {"matched": 0, "mismatch": 0, "ambiguous": 0, "unmatched": 0, "missing": 0}

    for expected_index, (column, header_col) in enumerate(zip(columns, assessment_cols)):
        header_value = cell_at(header, header_col)
        if not header_value:
            status = "missing"
            message = "헤더 셀이 비어 있어 평가 순서를 검증할 수 없습니다."
            issues.append(make_issue(source_row, column.column_label, message, "", "warning"))
        else:
            matches = matching_column_indexes(header_value, columns)
            if expected_index in matches and len(matches) == 1:
                status = "matched"
                message = "일치"
            elif expected_index in matches and len(matches) > 1:
                status = "ambiguous"
                message = "헤더가 여러 평가 항목과 맞아 수동 확인이 필요합니다."
                issues.append(make_issue(source_row, column.column_label, message, header_value, "warning"))
            elif matches:
                status = "mismatch"
                matched_labels = ", ".join(columns[index].column_label for index in matches)
                message = f"헤더 순서가 HWP 양식과 다릅니다. 이 헤더는 {matched_labels} 항목으로 보입니다."
                issues.append(make_issue(source_row, column.column_label, message, header_value))
            else:
                status = "unmatched"
                message = "HWP 양식 평가 목록과 맞는 헤더를 찾지 못했습니다."
                issues.append(make_issue(source_row, column.column_label, message, header_value, "warning"))

        status_counts[status] += 1
        checks.append(HeaderCheck(column.column_label, header_value, status, message))

    if status_counts["mismatch"]:
        return issues, checks, "header_error", "헤더가 있지만 HWP 양식 순서와 다른 열이 있습니다."
    if status_counts["ambiguous"] or status_counts["unmatched"] or status_counts["missing"]:
        return issues, checks, "header_warning", "헤더가 있지만 일부 열은 자동 검증이 애매합니다."
    return issues, checks, "header_verified", "헤더가 HWP 양식 평가 목록과 일치합니다."


def detect_header_layout(
    rows: list[list[str]],
    columns: list[AssessmentColumn],
) -> HeaderLayout:
    issues: list[ValidationIssue] = []
    block_count = len(columns)
    header_row_index = find_table_header_row(rows)
    header_exists = header_row_index is not None
    header = rows[header_row_index] if header_exists else []

    if header_exists:
        number_col = find_header_index(header, NUMBER_HEADERS)
        name_col = find_header_index(header, NAME_HEADERS)
        if number_col is None:
            number_col = 0
            issues.append(make_issue(1, "번호", "번호 열을 찾지 못해 첫 번째 열을 번호로 사용했습니다.", "", "warning"))
        if name_col is None:
            name_col = 1 if len(header) > 1 else 0
            issues.append(make_issue(1, "이름", "이름 열을 찾지 못해 두 번째 열을 이름으로 사용했습니다.", "", "warning"))

        label_row_index: int | None = None
        if header_row_index is not None and header_row_index + 1 < len(rows):
            next_row = rows[header_row_index + 1]
            if has_label_row(next_row, number_col, name_col):
                label_row_index = header_row_index + 1

        width = max(len(row) for row in rows)
        label_row = rows[label_row_index] if label_row_index is not None else None
        effective_header = effective_header_from_rows(header, label_row, width, number_col, name_col)
        candidates = choose_assessment_cols_from_header(header, effective_header, number_col, name_col)
        if len(candidates) != block_count:
            issues.append(
                make_issue(
                    (header_row_index or 0) + 1,
                    "평가열",
                    f"HWP 평가 항목은 {block_count}개인데 가져온 NEIS 표의 평가 열은 {len(candidates)}개입니다.",
                    "",
                )
            )
        if len(candidates) < block_count:
            start = max(number_col, name_col) + 1
            candidates = [i for i in range(start, start + block_count)]
        assessment_cols = candidates[:block_count]
        data_start_index = (label_row_index + 1) if label_row_index is not None else ((header_row_index or 0) + 1)
    else:
        number_col = 0
        name_col = 1
        assessment_cols = list(range(2, 2 + block_count))
        label_row_index = None
        data_start_index = 0
        effective_header = []
        issues.append(
            make_issue(
                1,
                "헤더",
                "가져온 NEIS 표에 평가 항목 제목이 없어 순서 검증을 할 수 없습니다. HWP 양식 순서대로 처리합니다.",
                "",
                "warning",
            )
        )

    if len(assessment_cols) < block_count:
        issues.append(
            make_issue(
                (header_row_index or 0) + 1,
                "평가열",
                f"평가 열이 {block_count}개 필요하지만 {len(assessment_cols)}개만 찾았습니다.",
                "",
            )
        )

    widest_row = max(len(row) for row in rows)
    needed_columns = max([number_col, name_col, *assessment_cols], default=0) + 1
    if widest_row > needed_columns:
        issues.append(
            make_issue(
                (header_row_index or 0) + 1,
                "추가열",
                f"필요한 열 이후의 {widest_row - needed_columns}개 열은 Phase 2에서 사용하지 않습니다.",
                "",
                "warning",
            )
        )
    return HeaderLayout(
        header_exists,
        header_row_index,
        label_row_index,
        data_start_index,
        number_col,
        name_col,
        assessment_cols,
        effective_header,
        issues,
    )


def title_subject_from_row(row: list[str]) -> tuple[str, str] | None:
    for cell in row:
        title = clean_cell(cell)
        match = re.search(
            r"(?P<subject>[가-힣A-Za-z0-9]+(?:\s+[가-힣A-Za-z0-9]+)*)\s*교과\s*성적\s*일람표",
            title,
        )
        if match:
            return clean_cell(match.group("subject")), title
    return None


def subject_from_title_candidates(title: str, columns: list[AssessmentColumn]) -> str:
    title_key = header_key(title)
    if not title_key:
        return ""
    subjects = unique_non_empty(column.subject for column in columns)
    matches = [subject for subject in subjects if header_key(subject) and header_key(subject) in title_key]
    if not matches:
        return ""
    return max(matches, key=lambda subject: len(header_key(subject)))


def detect_neis_excel_fixed_layout(rows: list[list[str]]) -> NeisExcelFixedLayout | None:
    if not rows:
        return None

    title_row_index: int | None = None
    subject = ""
    title = ""
    for index, row in enumerate(rows[:12]):
        parsed_title = title_subject_from_row(row)
        if parsed_title:
            subject, title = parsed_title
            title_row_index = index
            break

    header_row_index: int | None = None
    for index, row in enumerate(rows[:15]):
        if find_header_index(row, NUMBER_HEADERS) is not None and find_header_index(row, NAME_HEADERS) is not None:
            header_row_index = index
            break

    # NEIS subject Excel exports have been stable at these coordinates:
    # title row 3, header row 7, label row 8, number col B, name col D.
    if header_row_index is None and len(rows) > 7:
        fixed_header = rows[6]
        if cell_at(fixed_header, 1) and cell_at(fixed_header, 3):
            header_row_index = 6

    if header_row_index is None:
        return None

    header = rows[header_row_index]
    number_col = find_header_index(header, NUMBER_HEADERS)
    name_col = find_header_index(header, NAME_HEADERS)
    if number_col is None:
        number_col = 1
    if name_col is None:
        name_col = 3

    label_row_index = header_row_index + 1
    if label_row_index >= len(rows):
        return None

    label_row = rows[label_row_index]
    width = max(len(row) for row in rows)
    effective_header = effective_header_from_rows(header, label_row, width, number_col, name_col)
    assessment_start_col = max(number_col, name_col) + 1
    assessment_cols = [
        index
        for index in range(assessment_start_col, width)
        if clean_cell(cell_at(label_row, index))
    ]
    if not assessment_cols:
        assessment_cols = [
            index
            for index in range(assessment_start_col, width)
            if clean_cell(cell_at(header, index))
        ]
    if not assessment_cols:
        return None

    data_start_index = label_row_index + 1
    while data_start_index < len(rows):
        row = rows[data_start_index]
        if cell_at(row, number_col) or cell_at(row, name_col):
            break
        data_start_index += 1

    if title_row_index is None and len(rows) > 2:
        title = clean_cell(" ".join(cell for cell in rows[2] if clean_cell(cell)))

    return NeisExcelFixedLayout(
        subject=subject,
        title=title,
        header_row_index=header_row_index,
        label_row_index=label_row_index,
        data_start_index=data_start_index,
        number_col=number_col,
        name_col=name_col,
        assessment_cols=assessment_cols,
        effective_header=effective_header,
    )


def subject_matches_excel(column: AssessmentColumn, subject: str) -> bool:
    return not subject or header_key(column.subject) == header_key(subject)


def parse_neis_excel_fixed(
    text: str,
    columns: list[AssessmentColumn],
) -> tuple[list[AssessmentColumn], Phase2Result] | None:
    rows = split_pasted_table(text)
    layout = detect_neis_excel_fixed_layout(rows)
    if layout is None:
        return None

    resolved_subject = subject_from_title_candidates(layout.title, columns)
    if resolved_subject and header_key(resolved_subject) != header_key(layout.subject):
        layout = replace(layout, subject=resolved_subject)

    candidate_columns = [column for column in columns if subject_matches_excel(column, layout.subject)]
    if layout.subject and not candidate_columns:
        result = Phase2Result(
            0,
            0,
            [],
            [
                make_issue(
                    layout.header_row_index + 1,
                    "과목",
                    f"HWP 양식에 {layout.subject} 과목이 없어 이 엑셀은 반영할 수 없습니다.",
                    layout.subject,
                )
            ],
            "excel_fixed_error",
            True,
            f"NEIS {layout.subject} 엑셀을 인식했지만 HWP 양식에 같은 과목이 없습니다.",
            [],
            "neis_excel_fixed",
            {"excel_layout": asdict(layout)},
        )
        return [], result
    if not candidate_columns:
        candidate_columns = columns

    issues: list[ValidationIssue] = []
    matched_pairs: list[tuple[AssessmentColumn, int, str]] = []
    used_column_indexes: set[int] = set()
    header_checks: list[HeaderCheck] = []
    header_source_row = layout.label_row_index + 1

    for excel_col in layout.assessment_cols:
        header_value = cell_at(layout.effective_header, excel_col)
        matches = matching_column_indexes(header_value, candidate_columns)
        if len(matches) == 1:
            column = candidate_columns[matches[0]]
            if column.index in used_column_indexes:
                issues.append(
                    make_issue(
                        header_source_row,
                        column.column_label,
                        "같은 HWP 평가 항목에 매칭되는 엑셀 열이 여러 개입니다.",
                        header_value,
                        "warning",
                    )
                )
                continue
            used_column_indexes.add(column.index)
            matched_pairs.append((column, excel_col, header_value))
            header_checks.append(HeaderCheck(column.column_label, header_value, "matched", "NEIS 엑셀 고정 양식에서 매칭했습니다."))
        elif len(matches) > 1:
            unused_columns = [candidate_columns[index] for index in matches if candidate_columns[index].index not in used_column_indexes]
            if unused_columns:
                column = unused_columns[0]
                used_column_indexes.add(column.index)
                matched_pairs.append((column, excel_col, header_value))
                header_checks.append(
                    HeaderCheck(
                        column.column_label,
                        header_value,
                        "matched_by_order",
                        "같은 이름의 평가열이 여러 개라 HWP 양식 순서대로 매칭했습니다.",
                    )
                )
            else:
                labels = ", ".join(candidate_columns[index].column_label for index in matches)
                issues.append(
                    make_issue(
                        header_source_row,
                        header_value or "평가열",
                        f"엑셀 평가열이 여러 HWP 항목과 맞지만 이미 매칭된 항목이라 사용하지 않았습니다: {labels}",
                        header_value,
                        "warning",
                    )
                )
        else:
            issues.append(
                make_issue(
                    header_source_row,
                    header_value or "평가열",
                    f"{layout.subject or '과목'} 엑셀의 '{header_value or '빈 제목'}' 열은 HWP 양식 평가 목록에 없어 사용하지 않았습니다.",
                    header_value,
                    "warning",
                )
            )

    matched_pairs.sort(key=lambda item: item[0].index)
    selected_columns = [column for column, _, _ in matched_pairs]
    matched_column_indexes = {column.index for column in selected_columns}
    if selected_columns:
        for column in candidate_columns:
            if column.index in matched_column_indexes:
                continue
            issues.append(
                make_issue(
                    header_source_row,
                    column.column_label,
                    f"{layout.subject or column.subject} 엑셀에서 HWP 평가 항목 '{column.area}' 열을 찾지 못했습니다.",
                    column.area,
                )
            )
    if not selected_columns:
        result = Phase2Result(
            0,
            0,
            [],
            issues
            + [
                make_issue(
                    header_source_row,
                    "평가열",
                    "NEIS 엑셀 고정 양식은 인식했지만 HWP 양식과 맞는 평가열을 찾지 못했습니다.",
                )
            ],
            "excel_fixed_error",
            True,
            "NEIS 엑셀 고정 양식은 인식했지만 HWP 양식과 맞는 평가열을 찾지 못했습니다.",
            header_checks,
            "neis_excel_fixed",
            {"excel_layout": asdict(layout)},
        )
        return [], result

    students: list[StudentRecord] = []
    for source_index, row in enumerate(rows[layout.data_start_index :], start=layout.data_start_index + 1):
        number = cell_at(row, layout.number_col)
        name = cell_at(row, layout.name_col)
        if not number and not name:
            continue
        if not number:
            issues.append(make_issue(source_index, "번호", "번호가 비어 있습니다."))
        if not name:
            issues.append(make_issue(source_index, "이름", "이름이 비어 있습니다."))

        assessments: list[StudentAssessment] = []
        for column, excel_col, _ in matched_pairs:
            raw_value = cell_at(row, excel_col)
            level = normalize_level(raw_value)
            if raw_value and level is None:
                issues.append(
                    make_issue(
                        source_index,
                        column.column_label,
                        "도달/부분도달/노력중으로 해석할 수 없는 값입니다.",
                        raw_value,
                    )
                )
            assessments.append(
                StudentAssessment(
                    block_index=column.index,
                    column_label=column.column_label,
                    subject=column.subject,
                    area=column.area,
                    evaluation_element=column.evaluation_element,
                    level=level,
                    raw_value=raw_value,
                )
            )
        students.append(StudentRecord(source_index, number, name, assessments))

    matched_labels = ", ".join(column.column_label for column in selected_columns)
    header_message = (
        f"NEIS {layout.subject or '과목'} 엑셀 고정 양식으로 읽었습니다. "
        f"HWP와 맞는 평가열 {len(selected_columns)}개를 사용했습니다: {matched_labels}"
    )
    result = Phase2Result(
        len(selected_columns),
        len(students),
        students,
        issues,
        "excel_fixed_verified" if not has_errors(issues) else "excel_fixed_error",
        True,
        header_message,
        header_checks,
        "neis_excel_fixed",
        {"excel_layout": asdict(layout)},
    )
    return selected_columns, result


def parse_neis_excel_tsv(
    text: str,
    columns: list[AssessmentColumn],
) -> tuple[list[AssessmentColumn], Phase2Result]:
    fixed_result = parse_neis_excel_fixed(text, columns)
    if fixed_result is not None:
        return fixed_result
    return columns, parse_neis_paste(text, columns)


def cell_at(row: list[str], index: int) -> str:
    return row[index] if 0 <= index < len(row) else ""


def parse_neis_paste(text: str, columns: list[AssessmentColumn]) -> Phase2Result:
    rows = split_pasted_table(text)
    issues: list[ValidationIssue] = []
    if not rows:
        return Phase2Result(
            len(columns),
            0,
            [],
            [make_issue(0, "전체", "가져온 NEIS 표가 비어 있습니다.")],
            "empty",
            False,
            "가져온 NEIS 표가 비어 있습니다.",
            [],
        )

    layout = detect_header_layout(rows, columns)
    header_exists = layout.header_exists
    number_col = layout.number_col
    name_col = layout.name_col
    assessment_cols = layout.assessment_cols
    issues.extend(layout.issues)
    header_checks: list[HeaderCheck] = []
    if header_exists:
        header_source_row = (layout.label_row_index if layout.label_row_index is not None else layout.header_row_index or 0) + 1
        header_issues, header_checks, validation_mode, header_message = validate_header_cells(
            layout.effective_header,
            assessment_cols,
            columns,
            header_source_row,
        )
        issues.extend(header_issues)
    else:
        validation_mode = "order_assumed"
        header_message = "헤더가 없어 평가 순서를 자동 검증하지 못했습니다."
    data_rows = rows[layout.data_start_index :]

    students: list[StudentRecord] = []
    for filtered_row_index, row in enumerate(data_rows, start=layout.data_start_index + 1):
        if not cell_at(row, number_col) and not cell_at(row, name_col):
            continue
        expected_width = max([number_col, name_col, *assessment_cols], default=0) + 1
        if len(row) < expected_width:
            issues.append(
                make_issue(
                    filtered_row_index,
                    "열 개수",
                    f"이 행은 {expected_width}개 열이 필요하지만 {len(row)}개만 있습니다.",
                    "",
                )
            )
        elif not header_exists and len(row) > expected_width:
            issues.append(
                make_issue(
                    filtered_row_index,
                    "열 개수",
                    f"이 행은 HWP 평가 항목 기준 {expected_width}개 열이어야 하지만 {len(row)}개입니다.",
                    "",
                )
            )

        number = cell_at(row, number_col)
        name = cell_at(row, name_col)
        if not number:
            issues.append(make_issue(filtered_row_index, "번호", "번호가 비어 있습니다."))
        if not name:
            issues.append(make_issue(filtered_row_index, "이름", "이름이 비어 있습니다."))

        assessments: list[StudentAssessment] = []
        for column, cell_index in zip(columns, assessment_cols):
            raw_value = cell_at(row, cell_index)
            level = normalize_level(raw_value)
            if raw_value and level is None:
                issues.append(
                    make_issue(
                        filtered_row_index,
                        column.column_label,
                        "도달/부분도달/노력중으로 해석할 수 없는 값입니다.",
                        raw_value,
                    )
                )
            assessments.append(
                StudentAssessment(
                    block_index=column.index,
                    column_label=column.column_label,
                    subject=column.subject,
                    area=column.area,
                    evaluation_element=column.evaluation_element,
                    level=level,
                    raw_value=raw_value,
                )
            )

        students.append(StudentRecord(filtered_row_index, number, name, assessments))

    return Phase2Result(
        len(columns),
        len(students),
        students,
        issues,
        validation_mode,
        header_exists,
        header_message,
        header_checks,
    )


def result_to_payload(
    phase1_json_path: Path,
    paste_source: str,
    columns: list[AssessmentColumn],
    result: Phase2Result,
) -> dict:
    error_count, warning_count = issue_counts(result.issues)
    return {
        "source_phase1_json": str(phase1_json_path),
        "paste_source": paste_source,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "block_count": result.block_count,
        "student_count": result.student_count,
        "has_errors": error_count > 0,
        "has_warnings": warning_count > 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "validation_mode": result.validation_mode,
        "header_exists": result.header_exists,
        "header_message": result.header_message,
        "header_checks": [asdict(check) for check in result.header_checks],
        "source_format": result.source_format,
        "metadata": result.metadata,
        "columns": [asdict(column) for column in columns],
        "students": [asdict(student) for student in result.students],
        "issues": [asdict(issue) for issue in result.issues],
    }


def write_phase2_json(
    output_path: Path,
    phase1_json_path: Path,
    paste_source: str,
    columns: list[AssessmentColumn],
    result: Phase2Result,
) -> None:
    payload = result_to_payload(phase1_json_path, paste_source, columns, result)
    atomic_write_json(output_path, payload)


def default_output_path(phase1_json_path: Path, output_dir: Path) -> Path:
    stem = phase1_json_path.name
    if stem.endswith(".phase1.json"):
        stem = stem[: -len(".phase1.json")]
    else:
        stem = phase1_json_path.stem
    return output_dir / f"{stem}.phase2.json"


def print_result_summary(result: Phase2Result, output_path: Path | None = None) -> None:
    error_count, warning_count = issue_counts(result.issues)
    print(f"평가 항목: {result.block_count}개")
    print(f"학생: {result.student_count}명")
    print(f"헤더 검증: {result.header_message}")
    if result.issues:
        print(f"확인 필요: 오류 {error_count}건, 경고 {warning_count}건")
        for issue in result.issues[:20]:
            value = f" 값={issue.value!r}" if issue.value else ""
            label = "경고" if issue.severity == "warning" else "오류"
            print(f" - [{label}] {issue.source_row}행 {issue.column}: {issue.message}{value}")
        if len(result.issues) > 20:
            print(f" - 나머지 {len(result.issues) - 20}건은 JSON에서 확인하세요.")
    else:
        print("확인 필요 항목이 없습니다.")
    if output_path:
        print(f"분석 JSON: {output_path}")


def read_stdin_text() -> str:
    print("NEIS 표 텍스트를 입력하고 Ctrl+Z, Enter를 누르세요.", file=sys.stderr)
    return sys.stdin.read()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 2 NEIS 표 검증기")
    parser.add_argument("phase1_json", type=Path, help="Phase 1 분석 JSON 경로")
    parser.add_argument("paste_file", type=Path, nargs="?", help="NEIS 표 텍스트/TSV 파일 경로")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("phase2_output"),
        help="Phase 2 JSON 저장 폴더",
    )
    parser.add_argument("--json-path", type=Path, help="Phase 2 JSON 저장 경로")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    phase1_json_path = args.phase1_json.expanduser().resolve()
    if not phase1_json_path.exists():
        parser.error(f"Phase 1 JSON을 찾을 수 없습니다: {phase1_json_path}")

    columns = load_assessment_columns(phase1_json_path)
    if args.paste_file:
        paste_path = args.paste_file.expanduser().resolve()
        if not paste_path.exists():
            parser.error(f"NEIS 표 파일을 찾을 수 없습니다: {paste_path}")
        paste_text = paste_path.read_text(encoding="utf-8-sig")
        paste_source = str(paste_path)
    else:
        paste_text = read_stdin_text()
        paste_source = "stdin"

    result = parse_neis_paste(paste_text, columns)
    output_dir = args.output_dir.expanduser().resolve()
    output_path = args.json_path.expanduser().resolve() if args.json_path else default_output_path(phase1_json_path, output_dir)
    write_phase2_json(output_path, phase1_json_path, paste_source, columns, result)
    print_result_summary(result, output_path)
    return 1 if has_errors(result.issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
