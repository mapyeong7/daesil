from __future__ import annotations

import re
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path


END_OF_CHAIN = -2
FREE_SECTOR = -1
MINI_STREAM_CUTOFF = 4096


@dataclass
class DirectoryEntry:
    entry_no: int
    name: str
    entry_type: int
    start_sector: int
    size: int
    file_offset: int


class OleCompoundFile:
    def __init__(self, path: Path):
        self.path = path
        self.data = bytearray(path.read_bytes())
        if self.data[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
            raise ValueError(f"HWP OLE 파일이 아닙니다: {path}")

        self.sector_size = 1 << struct.unpack_from("<H", self.data, 0x1E)[0]
        self.first_dir_sector = struct.unpack_from("<i", self.data, 0x30)[0]
        self.num_fat_sectors = struct.unpack_from("<I", self.data, 0x2C)[0]
        self.fat = self._read_fat()
        self.dir_chain = self._sector_chain(self.first_dir_sector)
        self.entries = self._read_directory_entries()

    def _sector_offset(self, sector_id: int) -> int:
        return (sector_id + 1) * self.sector_size

    def _read_fat(self) -> list[int]:
        difat = [
            sector_id
            for sector_id in struct.unpack_from("<109i", self.data, 0x4C)
            if sector_id >= 0
        ]
        fat: list[int] = []
        for sector_id in difat[: self.num_fat_sectors]:
            offset = self._sector_offset(sector_id)
            fat.extend(struct.unpack_from(f"<{self.sector_size // 4}i", self.data, offset))
        return fat

    def _sector_chain(self, start_sector: int) -> list[int]:
        chain: list[int] = []
        sector_id = start_sector
        seen: set[int] = set()
        while sector_id >= 0 and sector_id not in seen:
            chain.append(sector_id)
            seen.add(sector_id)
            sector_id = self.fat[sector_id]
        return chain

    def _read_directory_entries(self) -> list[DirectoryEntry]:
        entries: list[DirectoryEntry] = []
        dir_stream_size = len(self.dir_chain) * self.sector_size
        for entry_no in range(dir_stream_size // 128):
            stream_offset = entry_no * 128
            sector_index = stream_offset // self.sector_size
            within_sector = stream_offset % self.sector_size
            file_offset = self._sector_offset(self.dir_chain[sector_index]) + within_sector
            entry = self.data[file_offset : file_offset + 128]
            name_length = struct.unpack_from("<H", entry, 64)[0]
            if name_length < 2:
                continue
            name = entry[: name_length - 2].decode("utf-16le", errors="replace")
            entry_type = entry[66]
            start_sector = struct.unpack_from("<i", entry, 116)[0]
            size = struct.unpack_from("<Q", entry, 120)[0]
            entries.append(DirectoryEntry(entry_no, name, entry_type, start_sector, size, file_offset))
        return entries

    def read_regular_stream(self, entry: DirectoryEntry) -> bytes:
        if entry.size < MINI_STREAM_CUTOFF:
            raise ValueError(f"작은 OLE 스트림은 아직 직접 패치할 수 없습니다: {entry.name}")
        content = bytearray()
        for sector_id in self._sector_chain(entry.start_sector):
            offset = self._sector_offset(sector_id)
            content.extend(self.data[offset : offset + self.sector_size])
        return bytes(content[: entry.size])

    def write_regular_stream(self, entry: DirectoryEntry, content: bytes) -> None:
        chain = self._sector_chain(entry.start_sector)
        capacity = len(chain) * self.sector_size
        if len(content) > capacity:
            raise ValueError(f"{entry.name} 스트림 공간이 부족합니다.")

        stored_size = len(content)
        if stored_size < MINI_STREAM_CUTOFF:
            # Keep the stream in the regular FAT chain. OLE readers switch to the
            # mini stream when the directory size is below 4096 bytes.
            stored_size = MINI_STREAM_CUTOFF
        struct.pack_into("<Q", self.data, entry.file_offset + 120, stored_size)

        padded = content + (b"\x00" * (stored_size - len(content)))
        cursor = 0
        for sector_id in chain:
            offset = self._sector_offset(sector_id)
            chunk = padded[cursor : cursor + self.sector_size]
            if not chunk:
                break
            self.data[offset : offset + len(chunk)] = chunk
            cursor += len(chunk)

    def write_back(self) -> None:
        write_bytes_with_retry(self.path, self.data)


def write_bytes_with_retry(path: Path, data: bytes | bytearray, attempts: int = 10, delay_seconds: float = 0.08) -> None:
    last_error: PermissionError | None = None
    for attempt in range(attempts):
        try:
            path.write_bytes(data)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(delay_seconds * (attempt + 1))
    if last_error:
        raise last_error


def decode_section_stream(raw: bytes) -> tuple[bytearray, bool]:
    try:
        return bytearray(zlib.decompress(raw, -15)), True
    except zlib.error:
        return bytearray(raw), False


def encode_section_stream(decoded: bytes, compressed: bool) -> bytes:
    if not compressed:
        return bytes(decoded)
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(bytes(decoded)) + compressor.flush()


def section_number(name: str) -> int:
    match = re.fullmatch(r"Section(\d+)", name)
    return int(match.group(1)) if match else -1


def count_decoded_checkboxes(decoded: bytes | bytearray) -> int:
    empty_box = "□".encode("utf-16le")
    filled_box = "■".encode("utf-16le")
    return bytes(decoded).count(empty_box) + bytes(decoded).count(filled_box)


def count_decoded_checkbox_states(decoded: bytes | bytearray) -> dict[str, int]:
    empty_box = "□".encode("utf-16le")
    filled_box = "■".encode("utf-16le")
    empty = bytes(decoded).count(empty_box)
    filled = bytes(decoded).count(filled_box)
    return {"empty": empty, "filled": filled, "total": empty + filled}


def count_decoded_text_occurrences(decoded: bytes | bytearray, text: str) -> int:
    if not text:
        return 0
    return bytes(decoded).count(text.encode("utf-16le"))


def body_text_sections(ole: OleCompoundFile) -> list[DirectoryEntry]:
    sections = sorted(
        [entry for entry in ole.entries if section_number(entry.name) >= 0],
        key=lambda entry: section_number(entry.name),
    )
    if not sections:
        raise ValueError("HWP 본문 Section 스트림을 찾지 못했습니다.")
    return sections


def count_hwp_checkboxes(hwp_path: Path) -> int:
    ole = OleCompoundFile(hwp_path)
    count = 0
    for entry in body_text_sections(ole):
        raw = ole.read_regular_stream(entry)
        decoded, _ = decode_section_stream(raw)
        count += count_decoded_checkboxes(decoded)
    return count


def count_hwp_checkbox_states(hwp_path: Path) -> dict[str, int]:
    ole = OleCompoundFile(hwp_path)
    result = {"empty": 0, "filled": 0, "total": 0}
    for entry in body_text_sections(ole):
        raw = ole.read_regular_stream(entry)
        decoded, _ = decode_section_stream(raw)
        states = count_decoded_checkbox_states(decoded)
        for key in result:
            result[key] += states[key]
    return result


def count_hwp_text_occurrences(hwp_path: Path, text: str) -> int:
    ole = OleCompoundFile(hwp_path)
    count = 0
    for entry in body_text_sections(ole):
        raw = ole.read_regular_stream(entry)
        decoded, _ = decode_section_stream(raw)
        count += count_decoded_text_occurrences(decoded, text)
    return count


def student_placeholder_replacement(placeholder: dict, number: object, name: object) -> str:
    label = str(placeholder.get("label") or "이름").strip() or "이름"
    number_text = str(number or "").strip()
    name_text = str(name or "").strip()
    if placeholder.get("includes_number"):
        return f"{number_text}번 {label}: {name_text}"
    return f"{label}: {name_text}"


def replace_decoded_text(decoded: bytearray, find: str, replace: str) -> int:
    find_bytes = str(find or "").encode("utf-16le")
    if not find_bytes:
        return 0
    replace_bytes = str(replace or "").encode("utf-16le")
    count = bytes(decoded).count(find_bytes)
    if count:
        decoded[:] = decoded.replace(find_bytes, replace_bytes)
    return count


def patch_hwp_student_placeholders(
    hwp_path: Path,
    placeholders: list[dict],
    number: object,
    name: object,
) -> int:
    if not placeholders:
        return 0

    ole = OleCompoundFile(hwp_path)
    sections = body_text_sections(ole)
    decoded_sections: list[tuple[bytearray, bool]] = []
    replacement_count = 0

    for entry in sections:
        raw = ole.read_regular_stream(entry)
        decoded, compressed = decode_section_stream(raw)
        for placeholder in placeholders:
            find = str(placeholder.get("find") or "")
            replace = student_placeholder_replacement(placeholder, number, name)
            replacement_count += replace_decoded_text(decoded, find, replace)
        decoded_sections.append((decoded, compressed))

    if not replacement_count:
        return 0

    for section_index, entry in enumerate(sections):
        decoded, compressed = decoded_sections[section_index]
        raw = encode_section_stream(decoded, compressed)
        ole.write_regular_stream(entry, raw)
    ole.write_back()
    return replacement_count


def patch_hwp_checkboxes(hwp_path: Path, checkbox_ordinals: list[int]) -> None:
    target_set: set[int] = set()
    for value in checkbox_ordinals:
        try:
            ordinal = int(value)
        except (TypeError, ValueError):
            continue
        if ordinal > 0:
            target_set.add(ordinal)
    targets = sorted(target_set)
    ole = OleCompoundFile(hwp_path)
    sections = body_text_sections(ole)

    empty_box = "□".encode("utf-16le")
    filled_box = "■".encode("utf-16le")
    decoded_sections: list[tuple[bytearray, bool]] = []
    checkbox_positions: list[tuple[int, int]] = []

    for section_index, entry in enumerate(sections):
        raw = ole.read_regular_stream(entry)
        decoded, compressed = decode_section_stream(raw)
        decoded[:] = decoded.replace(filled_box, empty_box)
        cursor = 0
        while True:
            cursor = decoded.find(empty_box, cursor)
            if cursor < 0:
                break
            checkbox_positions.append((section_index, cursor))
            cursor += len(empty_box)
        decoded_sections.append((decoded, compressed))

    for ordinal in targets:
        if ordinal > len(checkbox_positions):
            raise ValueError(f"{ordinal}번째 체크박스를 HWP 본문에서 찾지 못했습니다.")
        section_index, cursor = checkbox_positions[ordinal - 1]
        decoded_sections[section_index][0][cursor : cursor + len(filled_box)] = filled_box

    for section_index, entry in enumerate(sections):
        decoded, compressed = decoded_sections[section_index]
        raw = encode_section_stream(decoded, compressed)
        ole.write_regular_stream(entry, raw)

    ole.write_back()
