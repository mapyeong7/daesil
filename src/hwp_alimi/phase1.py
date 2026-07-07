from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from hwp_alimi.hwp5_patch import find_hwp_teacher_story_slot
from hwp_alimi.io_utils import atomic_write_json
from typing import Iterable


STATUS_LABELS = ("도달", "부분도달", "노력중")

SUBJECT_NAMES = {
    "국어",
    "도덕",
    "사회",
    "수학",
    "과학",
    "실과",
    "체육",
    "음악",
    "미술",
    "영어",
    "바른 생활",
    "슬기로운 생활",
    "즐거운 생활",
    "통합교과",
    "창의적 체험활동",
    "창체",
}

SECTION_END_MARKERS = (
    "나의 성장을 위한 한 걸음",
    "스스로 나의 성장 성찰하기",
)

CHECKBOX_CHARS = set("□■☑☒✓✔")


@dataclass
class LevelText:
    label: str
    checked: bool
    mark: str | None
    text: str


@dataclass
class AssessmentBlock:
    index: int
    subject: str
    area: str
    evaluation_element: str
    levels: list[LevelText]


def read_text_file(path: Path) -> str:
    encodings = ("utf-8-sig", "cp949", "euc-kr", "utf-16", "utf-8")
    last_error: UnicodeDecodeError | None = None
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return path.read_text()


def normalize_lines(text: str) -> list[str]:
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for raw_line in raw_lines:
        line = html.unescape(raw_line).strip()
        line = line.replace("\u2027", "·")
        line = re.sub(r"\s+", " ", line)
        if line:
            lines.append(line)

    combined: list[str] = []
    i = 0
    while i < len(lines):
        current = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if current == "부분" and nxt == "도달":
            combined.append("부분도달")
            i += 2
        elif current == "노력" and nxt == "중":
            combined.append("노력중")
            i += 2
        else:
            combined.append(canonical_status_label(current) or current)
            i += 1
    return combined


def compact_label(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def canonical_status_label(value: str) -> str | None:
    compact = compact_label(value)
    for label in STATUS_LABELS:
        if compact == label:
            return label
    return None


def is_checkbox_mark(value: str) -> bool:
    compact = value.replace(" ", "")
    return bool(compact) and all(char in CHECKBOX_CHARS for char in compact)


def split_checkbox_prefix(value: str) -> tuple[str | None, str]:
    match = re.match(rf"^([{re.escape(''.join(CHECKBOX_CHARS))}]+)\s*(.*)$", value.strip())
    if not match:
        return None, value
    mark = match.group(1)
    rest = match.group(2).strip()
    if not is_checkbox_mark(mark):
        return None, value
    return mark, rest


def is_checked(mark: str | None) -> bool:
    return bool(mark and any(char in mark for char in ("■", "☑", "☒", "✓", "✔")))


def find_header_end(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if compact_label(line) not in {"교과", "과목"}:
            continue
        window = lines[i : i + 8]
        window_keys = [compact_label(item) for item in window]
        has_area = "영역" in window_keys
        has_element = "평가요소" in window_keys or "평가내용" in window_keys
        has_level = "성취수준" in window_keys
        if has_area and has_element and has_level:
            return i + window_keys.index("성취수준") + 1
    raise ValueError("평가 표 머리글(교과/영역/평가 요소/성취 수준)을 찾지 못했습니다.")


def find_section_end(lines: list[str], start: int) -> int:
    for i in range(start, len(lines)):
        if any(marker in lines[i] for marker in SECTION_END_MARKERS):
            return i
    return len(lines)


def find_next_label(lines: list[str], label: str, start: int, end: int) -> int:
    for i in range(start, end):
        if canonical_status_label(lines[i]) == label:
            return i
    raise ValueError(f"{label!r} 항목을 찾지 못했습니다.")


def split_mark_and_text(lines: list[str], start: int, end: int) -> tuple[str | None, str]:
    mark: str | None = None
    text_start = start
    if start < end and is_checkbox_mark(lines[start]):
        mark = lines[start]
        text_start = start + 1
        text = " ".join(lines[text_start:end]).strip()
        return mark, text
    if start < end:
        mark, first_text = split_checkbox_prefix(lines[start])
        if mark:
            text_parts = [first_text] if first_text else []
            text_parts.extend(lines[start + 1 : end])
            return mark, " ".join(text_parts).strip()
    text = " ".join(lines[text_start:end]).strip()
    return mark, text


def looks_like_area_line(value: str) -> bool:
    value = value.strip()
    return bool(value) and len(value) <= 24 and "수 있다" not in value


def find_block_start_before_reached(
    lines: list[str],
    reached_index: int,
    lower_bound: int,
    current_subject: str | None,
    current_area: str | None,
) -> int:
    subject_search_start = max(lower_bound, reached_index - 8)
    for index in range(subject_search_start, reached_index - 2):
        if lines[index] in SUBJECT_NAMES:
            return index
    if current_subject and reached_index - 2 >= lower_bound:
        if looks_like_area_line(lines[reached_index - 2]):
            return reached_index - 2
        if current_area and reached_index - 1 >= lower_bound:
            return reached_index - 1
    if reached_index - 3 >= lower_bound:
        return reached_index - 3
    return lower_bound


def find_next_block_start(
    lines: list[str],
    start: int,
    end: int,
    current_subject: str | None,
    current_area: str | None,
) -> int:
    try:
        next_reached = find_next_label(lines, "도달", start, end)
    except ValueError:
        return end
    return find_block_start_before_reached(lines, next_reached, start, current_subject, current_area)


def parse_preamble(
    preamble: Iterable[str],
    current_subject: str | None = None,
    current_area: str | None = None,
) -> tuple[str, str, str]:
    parts = [part.strip() for part in preamble if part.strip()]
    if not parts:
        raise ValueError(f"평가 블록 시작 부분이 너무 짧습니다: {parts}")
    if parts[0] in SUBJECT_NAMES:
        if len(parts) < 3:
            raise ValueError(f"평가 블록 시작 부분이 너무 짧습니다: {parts}")
        subject = parts[0]
        body = parts[1:]
    elif current_subject:
        subject = current_subject
        if len(parts) >= 2:
            body = parts
        elif current_area:
            return subject, current_area, parts[0]
        else:
            raise ValueError(f"평가 블록 시작 부분이 너무 짧습니다: {parts}")
    elif len(parts) >= 3:
        subject = parts[0]
        body = parts[1:]
    else:
        raise ValueError(f"평가 블록 시작 부분이 너무 짧습니다: {parts}")

    evaluation_element = body[-1]
    area = "".join(body[:-1]).strip()
    return subject, area, evaluation_element


def parse_assessment_blocks(text: str) -> list[AssessmentBlock]:
    lines = normalize_lines(text)
    start = find_header_end(lines)
    end = find_section_end(lines, start)

    blocks: list[AssessmentBlock] = []
    cursor = start
    current_subject: str | None = None
    current_area: str | None = None
    while cursor < end:
        while cursor < end and canonical_status_label(lines[cursor]) in STATUS_LABELS:
            cursor += 1
        if cursor >= end:
            break

        try:
            reached_index = find_next_label(lines, "도달", cursor, end)
        except ValueError:
            break

        preamble = lines[cursor:reached_index]
        subject, area, evaluation_element = parse_preamble(preamble, current_subject, current_area)
        current_subject = subject
        current_area = area

        partial_index = find_next_label(lines, "부분도달", reached_index + 1, end)
        effort_index = find_next_label(lines, "노력중", partial_index + 1, end)
        next_block_start = find_next_block_start(lines, effort_index + 1, end, current_subject, current_area)

        reached_mark, reached_text = split_mark_and_text(lines, reached_index + 1, partial_index)
        partial_mark, partial_text = split_mark_and_text(lines, partial_index + 1, effort_index)
        effort_mark, effort_text = split_mark_and_text(lines, effort_index + 1, next_block_start)

        blocks.append(
            AssessmentBlock(
                index=len(blocks) + 1,
                subject=subject,
                area=area,
                evaluation_element=evaluation_element,
                levels=[
                    LevelText("도달", is_checked(reached_mark), reached_mark, reached_text),
                    LevelText("부분도달", is_checked(partial_mark), partial_mark, partial_text),
                    LevelText("노력중", is_checked(effort_mark), effort_mark, effort_text),
                ],
            )
        )
        cursor = next_block_start

    if not blocks:
        raise ValueError("평가 블록을 찾지 못했습니다.")
    return blocks


def default_output_paths(hwp_path: Path, output_dir: Path) -> tuple[Path, Path]:
    safe_stem = re.sub(r'[<>:"/\\|?*]+', "_", hwp_path.stem).strip() or "hwp_template"
    text_path = output_dir / f"{safe_stem}.txt"
    json_path = output_dir / f"{safe_stem}.phase1.json"
    return text_path, json_path


def unique_non_empty(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def expected_neis_headers(block: AssessmentBlock) -> list[str]:
    return unique_non_empty(
        [
            f"{block.subject} / {block.area}",
            f"{block.subject} {block.area}",
            block.area,
            block.subject,
            block.evaluation_element,
        ]
    )


def block_to_json(block: AssessmentBlock) -> dict:
    data = asdict(block)
    data["expected_neis_headers"] = expected_neis_headers(block)
    return data


def extract_hwp_text(hwp_path: Path, text_path: Path) -> None:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "extract_hwp_text.ps1"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-InputPath",
        str(hwp_path),
        "-OutputPath",
        str(text_path),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"HWP 텍스트 추출에 실패했습니다.\n{detail}")


def write_json(path: Path, hwp_path: Path, text_path: Path, blocks: list[AssessmentBlock]) -> None:
    teacher_story_slot = None
    if hwp_path.suffix.lower() == ".hwp":
        try:
            teacher_story_slot = find_hwp_teacher_story_slot(hwp_path)
        except Exception:
            teacher_story_slot = None
    payload = {
        "source_hwp": str(hwp_path),
        "extracted_text": str(text_path),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "block_count": len(blocks),
        "blocks": [block_to_json(block) for block in blocks],
    }
    if teacher_story_slot:
        payload["teacher_story_slot"] = teacher_story_slot
    atomic_write_json(path, payload)


def print_summary(blocks: list[AssessmentBlock], json_path: Path, text_path: Path) -> None:
    print(f"평가 블록 {len(blocks)}개를 찾았습니다.")
    for block in blocks:
        print(f"{block.index}. {block.subject} / {block.area} / {block.evaluation_element}")
        for level in block.levels:
            mark = level.mark or "-"
            checked = "선택됨" if level.checked else "미선택"
            print(f"   - {level.label}: {mark} ({checked}) {level.text}")
    print()
    print(f"텍스트 추출본: {text_path}")
    print(f"분석 JSON: {json_path}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="학교 HWP 양식 Phase 1 평가 블록 추출기")
    parser.add_argument("hwp_path", type=Path, help="읽을 HWP 양식 경로")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("phase1_output"),
        help="텍스트/JSON 결과 저장 폴더",
    )
    parser.add_argument("--text-path", type=Path, help="텍스트 추출본 저장 경로")
    parser.add_argument("--json-path", type=Path, help="분석 JSON 저장 경로")
    parser.add_argument(
        "--from-text",
        action="store_true",
        help="HWP 추출 없이 hwp_path를 이미 추출된 텍스트 파일로 읽습니다.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    input_path = args.hwp_path.expanduser().resolve()
    if not input_path.exists():
        parser.error(f"파일을 찾을 수 없습니다: {input_path}")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.from_text:
        text_path = input_path
        json_path = args.json_path.expanduser().resolve() if args.json_path else output_dir / f"{input_path.stem}.phase1.json"
    else:
        text_path, json_path = default_output_paths(input_path, output_dir)
        if args.text_path:
            text_path = args.text_path.expanduser().resolve()
        if args.json_path:
            json_path = args.json_path.expanduser().resolve()
        text_path.parent.mkdir(parents=True, exist_ok=True)
        extract_hwp_text(input_path, text_path)

    text = read_text_file(text_path)
    blocks = parse_assessment_blocks(text)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(json_path, input_path, text_path, blocks)
    print_summary(blocks, json_path, text_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
