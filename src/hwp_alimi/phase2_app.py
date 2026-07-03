from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from hwp_alimi.io_utils import atomic_write_json
from hwp_alimi.phase2 import (
    AssessmentColumn,
    Phase2Result,
    default_output_path,
    load_assessment_columns,
    parse_neis_paste,
    result_to_payload,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHASE1_JSON = PROJECT_ROOT / "phase1_output" / "2. 2차 배움성장알리미 양식 (1).phase1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "phase2_output"


class Phase2App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("배움성장알리미 Phase 2 - 성적표 입력 확인")
        self.geometry("1180x760")
        self.minsize(980, 620)

        self.phase1_json_path = tk.StringVar(value=str(DEFAULT_PHASE1_JSON if DEFAULT_PHASE1_JSON.exists() else ""))
        self.status_text = tk.StringVar(value="Phase 1 JSON을 불러오세요.")
        self.columns: list[AssessmentColumn] = []
        self.result: Phase2Result | None = None

        self._build_ui()
        if self.phase1_json_path.get():
            self.load_phase1_json()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Phase 1 JSON").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(top, textvariable=self.phase1_json_path).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(top, text="찾기", command=self.choose_phase1_json).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(top, text="불러오기", command=self.load_phase1_json).grid(row=0, column=3)

        ttk.Label(self, textvariable=self.status_text, padding=(10, 0, 10, 6)).grid(row=1, column=0, sticky="ew")

        body = ttk.PanedWindow(self, orient=tk.VERTICAL)
        body.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))

        paste_frame = ttk.Frame(body)
        paste_frame.columnconfigure(0, weight=1)
        paste_frame.rowconfigure(1, weight=1)
        body.add(paste_frame, weight=1)

        paste_buttons = ttk.Frame(paste_frame)
        paste_buttons.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(paste_buttons, text="NEIS에서 복사한 표를 아래 칸에 붙여넣으세요.").pack(side=tk.LEFT)
        ttk.Button(paste_buttons, text="검증", command=self.validate_paste).pack(side=tk.RIGHT)
        ttk.Button(paste_buttons, text="비우기", command=self.clear_paste).pack(side=tk.RIGHT, padx=(0, 8))

        self.paste_text = tk.Text(paste_frame, height=10, wrap="none", undo=True)
        self.paste_text.grid(row=1, column=0, sticky="nsew")
        paste_y = ttk.Scrollbar(paste_frame, orient=tk.VERTICAL, command=self.paste_text.yview)
        paste_y.grid(row=1, column=1, sticky="ns")
        paste_x = ttk.Scrollbar(paste_frame, orient=tk.HORIZONTAL, command=self.paste_text.xview)
        paste_x.grid(row=2, column=0, sticky="ew")
        self.paste_text.configure(yscrollcommand=paste_y.set, xscrollcommand=paste_x.set)

        result_frame = ttk.Frame(body)
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        body.add(result_frame, weight=2)

        self.tree = ttk.Treeview(result_frame, show="headings")
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_y = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_y.grid(row=0, column=1, sticky="ns")
        tree_x = ttk.Scrollbar(result_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        tree_x.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)

        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        self.issue_text = tk.Text(bottom, height=5, wrap="word", state="disabled")
        self.issue_text.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(bottom, text="JSON 저장", command=self.save_json).grid(row=0, column=1, sticky="ns")

    def choose_phase1_json(self) -> None:
        path = filedialog.askopenfilename(
            title="Phase 1 JSON 선택",
            initialdir=str(PROJECT_ROOT),
            filetypes=[("Phase 1 JSON", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.phase1_json_path.set(path)

    def load_phase1_json(self) -> None:
        path = Path(self.phase1_json_path.get())
        try:
            self.columns = load_assessment_columns(path)
        except Exception as exc:
            messagebox.showerror("불러오기 실패", str(exc))
            return
        self.status_text.set(f"평가 항목 {len(self.columns)}개: " + ", ".join(column.column_label for column in self.columns))
        self.configure_tree_columns()

    def configure_tree_columns(self) -> None:
        ids = ["source_row", "number", "name"] + [f"block_{column.index}" for column in self.columns]
        self.tree.configure(columns=ids)
        headings = {"source_row": "행", "number": "번호", "name": "이름"}
        for column in self.columns:
            headings[f"block_{column.index}"] = column.column_label
        widths = {"source_row": 60, "number": 70, "name": 90}
        for column_id in ids:
            self.tree.heading(column_id, text=headings[column_id])
            self.tree.column(column_id, width=widths.get(column_id, 110), minwidth=60, stretch=True)
        for item in self.tree.get_children():
            self.tree.delete(item)

    def clear_paste(self) -> None:
        self.paste_text.delete("1.0", tk.END)
        self.result = None
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.set_issues("")

    def validate_paste(self) -> None:
        if not self.columns:
            self.load_phase1_json()
            if not self.columns:
                return
        text = self.paste_text.get("1.0", tk.END)
        self.result = parse_neis_paste(text, self.columns)
        self.render_result()

    def render_result(self) -> None:
        if self.result is None:
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        for student in self.result.students:
            values = [student.source_row, student.number, student.name]
            values.extend(assessment.level or assessment.raw_value for assessment in student.assessments)
            self.tree.insert("", tk.END, values=values)

        if self.result.issues:
            lines = [f"확인 필요 {len(self.result.issues)}건"]
            for issue in self.result.issues:
                label = "경고" if issue.severity == "warning" else "오류"
                value = f" / 값: {issue.value}" if issue.value else ""
                lines.append(f"[{label}] {issue.source_row}행 {issue.column}: {issue.message}{value}")
            self.set_issues("\n".join(lines))
        else:
            self.set_issues("확인 필요 항목이 없습니다.")

        self.status_text.set(
            f"평가 항목 {self.result.block_count}개, 학생 {self.result.student_count}명 / {self.result.header_message}"
        )

    def set_issues(self, text: str) -> None:
        self.issue_text.configure(state="normal")
        self.issue_text.delete("1.0", tk.END)
        self.issue_text.insert("1.0", text)
        self.issue_text.configure(state="disabled")

    def save_json(self) -> None:
        if self.result is None:
            self.validate_paste()
            if self.result is None:
                return

        phase1_path = Path(self.phase1_json_path.get()).resolve()
        default_path = default_output_path(phase1_path, DEFAULT_OUTPUT_DIR)
        path = filedialog.asksaveasfilename(
            title="Phase 2 JSON 저장",
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        payload = result_to_payload(phase1_path, "app-paste", self.columns, self.result)
        output_path = Path(path)
        atomic_write_json(output_path, payload)
        messagebox.showinfo("저장 완료", str(output_path))


def main() -> int:
    app = Phase2App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
