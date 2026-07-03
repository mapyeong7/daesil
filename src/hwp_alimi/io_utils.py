from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding=encoding)
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    atomic_write_text(path, text, encoding="utf-8")
