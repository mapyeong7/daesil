from __future__ import annotations

import re
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path


END_OF_CHAIN = -2
FREE_SECTOR = -1
FAT_SECTOR = -3
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

    def _mini_sector_size(self) -> int:
        return 1 << struct.unpack_from("<H", self.data, 0x20)[0]

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

    def _fat_sector_ids(self) -> list[int]:
        return [
            sector_id
            for sector_id in struct.unpack_from("<109i", self.data, 0x4C)
            if sector_id >= 0
        ][: self.num_fat_sectors]

    def _sector_chain(self, start_sector: int) -> list[int]:
        chain: list[int] = []
        sector_id = start_sector
        seen: set[int] = set()
        while sector_id >= 0 and sector_id not in seen:
            chain.append(sector_id)
            seen.add(sector_id)
            sector_id = self.fat[sector_id]
        return chain

    def _mini_sector_chain(self, start_sector: int) -> list[int]:
        mini_fat = self._read_mini_fat()
        chain: list[int] = []
        sector_id = start_sector
        seen: set[int] = set()
        while sector_id >= 0 and sector_id not in seen:
            chain.append(sector_id)
            seen.add(sector_id)
            sector_id = mini_fat[sector_id]
        return chain

    def _read_mini_fat(self) -> list[int]:
        first_mini_fat_sector = struct.unpack_from("<i", self.data, 0x3C)[0]
        if first_mini_fat_sector < 0:
            return []
        entries_per_sector = self.sector_size // 4
        mini_fat: list[int] = []
        for sector_id in self._sector_chain(first_mini_fat_sector):
            offset = self._sector_offset(sector_id)
            mini_fat.extend(struct.unpack_from(f"<{entries_per_sector}i", self.data, offset))
        return mini_fat

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

    def read_stream(self, entry: DirectoryEntry) -> bytes:
        if entry.size < MINI_STREAM_CUTOFF:
            return self.read_mini_stream(entry)
        return self.read_regular_stream(entry)

    def read_mini_stream(self, entry: DirectoryEntry) -> bytes:
        root_entry = self.entry_by_name("Root Entry")
        root_stream = self.read_regular_stream(root_entry)
        mini_sector_size = self._mini_sector_size()
        content = bytearray()
        for mini_sector_id in self._mini_sector_chain(entry.start_sector):
            offset = mini_sector_id * mini_sector_size
            content.extend(root_stream[offset : offset + mini_sector_size])
        return bytes(content[: entry.size])

    def write_regular_stream(self, entry: DirectoryEntry, content: bytes) -> None:
        chain = self._sector_chain(entry.start_sector)
        stored_size = len(content)
        if stored_size < MINI_STREAM_CUTOFF:
            # Keep the stream in the regular FAT chain. OLE readers switch to the
            # mini stream when the directory size is below 4096 bytes.
            stored_size = MINI_STREAM_CUTOFF
        struct.pack_into("<Q", self.data, entry.file_offset + 120, stored_size)

        needed_sectors = max(1, (stored_size + self.sector_size - 1) // self.sector_size)
        if len(chain) < needed_sectors:
            chain.extend(self._append_free_sectors(needed_sectors - len(chain)))
        elif len(chain) > needed_sectors:
            for sector_id in chain[needed_sectors:]:
                self.fat[sector_id] = FREE_SECTOR
            chain = chain[:needed_sectors]

        if entry.start_sector != chain[0]:
            struct.pack_into("<i", self.data, entry.file_offset + 116, chain[0])
            entry.start_sector = chain[0]
        for index, sector_id in enumerate(chain):
            self.fat[sector_id] = chain[index + 1] if index + 1 < len(chain) else END_OF_CHAIN

        self._sync_fat()

        padded = content + (b"\x00" * (stored_size - len(content)))
        cursor = 0
        for sector_id in chain:
            offset = self._sector_offset(sector_id)
            chunk = padded[cursor : cursor + self.sector_size]
            if not chunk:
                break
            self.data[offset : offset + len(chunk)] = chunk
            cursor += len(chunk)

    def write_stream(self, entry: DirectoryEntry, content: bytes) -> None:
        if entry.size < MINI_STREAM_CUTOFF and len(content) < MINI_STREAM_CUTOFF:
            self.write_mini_stream(entry, content)
            return
        self.write_regular_stream(entry, content)

    def write_mini_stream(self, entry: DirectoryEntry, content: bytes) -> None:
        mini_sector_size = self._mini_sector_size()
        mini_chain = self._mini_sector_chain(entry.start_sector)
        capacity = len(mini_chain) * mini_sector_size
        if len(content) > capacity:
            raise ValueError(f"{entry.name} 미니 스트림 공간이 부족합니다.")
        root_entry = self.entry_by_name("Root Entry")
        root_stream = bytearray(self.read_regular_stream(root_entry))
        padded = content + (b"\x00" * (capacity - len(content)))
        cursor = 0
        for mini_sector_id in mini_chain:
            offset = mini_sector_id * mini_sector_size
            root_stream[offset : offset + mini_sector_size] = padded[cursor : cursor + mini_sector_size]
            cursor += mini_sector_size
        struct.pack_into("<Q", self.data, entry.file_offset + 120, len(content))
        entry.size = len(content)
        self.write_regular_stream(root_entry, bytes(root_stream))

    def _append_sector(self) -> int:
        sector_id = (len(self.data) // self.sector_size) - 1
        self.data.extend(b"\x00" * self.sector_size)
        self.fat.append(FREE_SECTOR)
        return sector_id

    def _append_free_sectors(self, count: int) -> list[int]:
        sector_ids = [self._append_sector() for _ in range(count)]
        self._ensure_fat_capacity()
        return sector_ids

    def _ensure_fat_capacity(self) -> None:
        fat_sector_ids = self._fat_sector_ids()
        entries_per_sector = self.sector_size // 4
        while len(fat_sector_ids) * entries_per_sector < len(self.fat):
            fat_sector_id = self._append_sector()
            fat_sector_ids.append(fat_sector_id)
            self.fat[fat_sector_id] = FAT_SECTOR
        if len(fat_sector_ids) > 109:
            raise ValueError("OLE DIFAT 확장은 아직 지원하지 않습니다.")
        self.num_fat_sectors = len(fat_sector_ids)
        struct.pack_into("<I", self.data, 0x2C, self.num_fat_sectors)
        difat = fat_sector_ids + [FREE_SECTOR] * (109 - len(fat_sector_ids))
        struct.pack_into("<109i", self.data, 0x4C, *difat)

    def _sync_fat(self) -> None:
        self._ensure_fat_capacity()
        entries_per_sector = self.sector_size // 4
        fat_sector_ids = self._fat_sector_ids()
        padded_fat = self.fat + [FREE_SECTOR] * (len(fat_sector_ids) * entries_per_sector - len(self.fat))
        for index, fat_sector_id in enumerate(fat_sector_ids):
            offset = self._sector_offset(fat_sector_id)
            chunk = padded_fat[index * entries_per_sector : (index + 1) * entries_per_sector]
            struct.pack_into(f"<{entries_per_sector}i", self.data, offset, *chunk)

    def entry_by_name(self, name: str) -> DirectoryEntry:
        for entry in self.entries:
            if entry.name == name:
                return entry
        raise ValueError(f"OLE 항목을 찾을 수 없습니다: {name}")

    def _directory_entry_offset(self, entry_no: int) -> int:
        while entry_no >= (len(self.dir_chain) * self.sector_size // 128):
            self._append_directory_sector()
        stream_offset = entry_no * 128
        sector_index = stream_offset // self.sector_size
        within_sector = stream_offset % self.sector_size
        return self._sector_offset(self.dir_chain[sector_index]) + within_sector

    def _append_directory_sector(self) -> None:
        new_sector = self._append_free_sectors(1)[0]
        self.fat[self.dir_chain[-1]] = new_sector
        self.fat[new_sector] = END_OF_CHAIN
        self.dir_chain.append(new_sector)
        offset = self._sector_offset(new_sector)
        self.data[offset : offset + self.sector_size] = b"\x00" * self.sector_size
        self._sync_fat()

    def _write_directory_entry(self, entry: DirectoryEntry, left: int = -1, right: int = -1, child: int = -1) -> None:
        offset = self._directory_entry_offset(entry.entry_no)
        raw = bytearray(128)
        name_bytes = entry.name.encode("utf-16le") + b"\x00\x00"
        if len(name_bytes) > 64:
            raise ValueError(f"OLE 항목 이름이 너무 깁니다: {entry.name}")
        raw[: len(name_bytes)] = name_bytes
        struct.pack_into("<H", raw, 64, len(name_bytes))
        raw[66] = entry.entry_type
        raw[67] = 1
        struct.pack_into("<iii", raw, 68, left, right, child)
        struct.pack_into("<i", raw, 116, entry.start_sector)
        struct.pack_into("<Q", raw, 120, entry.size)
        self.data[offset : offset + 128] = raw
        entry.file_offset = offset

    def add_regular_stream_entry(self, name: str, content: bytes) -> DirectoryEntry:
        capacity = len(self.dir_chain) * self.sector_size // 128
        used_entry_numbers = {entry.entry_no for entry in self.entries}
        entry_no = next((index for index in range(capacity) if index not in used_entry_numbers), capacity)
        offset = self._directory_entry_offset(entry_no)
        entry = DirectoryEntry(entry_no, name, 2, -1, 0, offset)
        self._write_directory_entry(entry)
        self.entries.append(entry)
        self.write_regular_stream(entry, content)
        return entry

    def set_directory_links(
        self,
        entry: DirectoryEntry,
        left: int | None = None,
        right: int | None = None,
        child: int | None = None,
    ) -> None:
        current_left, current_right, current_child = struct.unpack_from("<iii", self.data, entry.file_offset + 68)
        if left is None:
            left = current_left
        if right is None:
            right = current_right
        if child is None:
            child = current_child
        struct.pack_into("<iii", self.data, entry.file_offset + 68, left, right, child)

    def rebuild_storage_child_tree(self, storage_name: str, child_entries: list[DirectoryEntry]) -> None:
        storage = self.entry_by_name(storage_name)
        ordered = sorted(child_entries, key=lambda entry: entry.name.casefold())

        def attach(entries: list[DirectoryEntry]) -> int:
            if not entries:
                return -1
            middle = len(entries) // 2
            entry = entries[middle]
            left = attach(entries[:middle])
            right = attach(entries[middle + 1 :])
            self.set_directory_links(entry, left=left, right=right, child=-1)
            return entry.entry_no

        root = attach(ordered)
        self.set_directory_links(storage, child=root)

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
        default = f"{number_text}번 {label}: {name_text}"
        find = str(placeholder.get("find") or "")
        if find:
            max_bytes = len(find.encode("utf-16le"))
            candidates = [
                default,
                f"{number_text}번 {label}:{name_text}",
                f"{number_text}번 {name_text}",
                f"{number_text} {name_text}",
                name_text,
            ]
            for candidate in candidates:
                if len(candidate.encode("utf-16le")) <= max_bytes:
                    return candidate
        return default
    return f"{label}: {name_text}"


def school_info_placeholder_replacement(placeholder: dict, school_info: dict) -> str:
    kind = str(placeholder.get("kind") or "").strip()
    grade = str(school_info.get("grade") or "").strip()
    class_name = str(school_info.get("class_name") or school_info.get("class") or "").strip()
    teacher_name = str(school_info.get("teacher_name") or school_info.get("teacher") or "").strip()

    if kind == "grade_class":
        return f"{grade}학년 {class_name}반"

    if kind == "teacher":
        label = str(placeholder.get("label") or "담임").strip() or "담임"
        separator = str(placeholder.get("separator")) if placeholder.get("separator") is not None else " "
        if not separator.strip():
            separator = " "
        elif separator.strip() in {":", "："}:
            separator = separator.strip() + " "
        return f"{label}{separator}{teacher_name}"

    return ""


def same_size_utf16_replacement_bytes(find: str, replace: str) -> bytes:
    find_bytes = str(find or "").encode("utf-16le")
    replace_bytes = str(replace or "").encode("utf-16le")
    if len(replace_bytes) > len(find_bytes):
        raise ValueError(
            f"HWP 직접 패치는 자리표시자보다 긴 문구를 넣을 수 없습니다: {find!r} -> {replace!r}"
        )
    if len(replace_bytes) < len(find_bytes):
        replace_bytes += " ".encode("utf-16le") * ((len(find_bytes) - len(replace_bytes)) // 2)
    return replace_bytes


def replace_decoded_text(decoded: bytearray, find: str, replace: str) -> int:
    find_bytes = str(find or "").encode("utf-16le")
    if not find_bytes:
        return 0
    replace_bytes = same_size_utf16_replacement_bytes(find, replace)
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


def patch_hwp_school_info_placeholders(
    hwp_path: Path,
    placeholders: list[dict],
    school_info: dict,
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
            replace = school_info_placeholder_replacement(placeholder, school_info)
            if replace:
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
