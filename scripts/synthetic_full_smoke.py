from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from hwp_alimi.local_server import (  # noqa: E402
    clear_report_output_files,
    create_reports_zip,
    read_json_file,
    run_hwp_report_generation,
    write_json_file,
)
from hwp_alimi.phase3 import build_phase3_payload  # noqa: E402


def phase2_column_from_block(block: dict) -> dict:
    return {
        "index": block.get("index"),
        "subject": block.get("subject"),
        "area": block.get("area"),
        "evaluation_element": block.get("evaluation_element"),
        "column_label": f"{block.get('index')}. {block.get('subject')} / {block.get('area')}",
    }


def synthetic_assessment(block: dict, offset: int) -> dict:
    levels = [item for item in block.get("levels", []) if str(item.get("label") or "").strip()]
    level = levels[offset % len(levels)]["label"] if levels else None
    return {
        "block_index": block.get("index"),
        "column_label": f"{block.get('index')}. {block.get('subject')} / {block.get('area')}",
        "subject": block.get("subject"),
        "area": block.get("area"),
        "evaluation_element": block.get("evaluation_element"),
        "level": level,
        "raw_value": level or "",
    }


def build_synthetic_phase2(phase1_path: Path, phase1_payload: dict) -> dict:
    blocks = list(phase1_payload.get("blocks", []))
    assessments = [synthetic_assessment(block, index) for index, block in enumerate(blocks)]
    return {
        "source_phase1_json": str(phase1_path),
        "paste_source": "synthetic-full-smoke",
        "block_count": len(blocks),
        "student_count": 1,
        "has_errors": False,
        "has_warnings": False,
        "error_count": 0,
        "warning_count": 0,
        "validation_mode": "synthetic_full_verified",
        "header_exists": True,
        "header_message": "합성 전체 입력 smoke 데이터입니다.",
        "header_checks": [],
        "source_format": "synthetic_full_smoke",
        "metadata": {
            "expected_column_count": len(blocks),
            "imported_column_count": len(blocks),
            "imports": [],
            "roster": [{"number": "99", "name": "합성검증"}],
            "global_issues": [],
        },
        "columns": [phase2_column_from_block(block) for block in blocks],
        "students": [
            {
                "source_row": 1,
                "number": "99",
                "name": "합성검증",
                "assessments": assessments,
            }
        ],
        "issues": [],
    }


def load_default_phase1_path() -> Path:
    state_path = PROJECT_ROOT / "server_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    phase1_path = state.get("current_phase1_json")
    if not phase1_path:
        raise ValueError("server_state.json에 current_phase1_json이 없습니다.")
    return Path(phase1_path)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="합성 전체 입력으로 HWP 생성 파이프라인을 점검합니다.")
    parser.add_argument("--phase1-json", type=Path, help="Phase 1 JSON 경로. 생략하면 server_state.json의 현재 값을 사용합니다.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "phase3_output" / "synthetic_full_smoke")
    parser.add_argument("--generate", action="store_true", help="실제 HWP 1명 생성까지 실행합니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    phase1_path = (args.phase1_json or load_default_phase1_path()).resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    phase1_payload = read_json_file(phase1_path)
    if not phase1_payload:
        raise ValueError(f"Phase 1 JSON을 읽지 못했습니다: {phase1_path}")

    phase2_payload = build_synthetic_phase2(phase1_path, phase1_payload)
    phase2_path = output_dir / "synthetic_full.phase2.json"
    phase3_path = output_dir / "synthetic_full.phase3.json"
    write_json_file(phase2_path, phase2_payload)

    phase3_payload = build_phase3_payload(phase1_path, phase1_payload, phase2_path, phase2_payload, output_dir)
    write_json_file(phase3_path, phase3_payload)

    print(f"phase2: {phase2_path}")
    print(f"phase3: {phase3_path}")
    print(f"ready: {phase3_payload.get('ready')}")
    print(f"students: {phase3_payload.get('student_count')}")
    print(f"blocks: {phase3_payload.get('block_count')}")
    if phase3_payload.get("blocking_issues"):
        for issue in phase3_payload["blocking_issues"]:
            print(f"- {issue.get('message')}")

    if args.generate:
        if not phase3_payload.get("ready"):
            raise ValueError("합성 Phase 3 manifest가 ready 상태가 아니라 HWP 생성을 실행하지 않습니다.")
        clear_report_output_files(output_dir)
        created_files, generation_info = run_hwp_report_generation(phase3_path, output_dir, limit=1)
        zip_path = create_reports_zip(created_files, output_dir)
        phase3_payload["generation_method"] = generation_info.get("method")
        if generation_info.get("fallback_reason"):
            phase3_payload["generation_fallback_reason"] = generation_info.get("fallback_reason")
        phase3_payload["generated_files"] = created_files
        phase3_payload["generated_zip"] = str(zip_path) if zip_path else None
        write_json_file(phase3_path, phase3_payload)
        print(f"generated: {len(created_files)}")
        print(f"generation_method: {phase3_payload.get('generation_method')}")
        for path in created_files:
            print(path)
        if zip_path:
            print(f"zip: {zip_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
