import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hwp_alimi.io_utils import atomic_write_json


class IoUtilsTest(unittest.TestCase):
    def test_atomic_write_json_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "sample.json"
            atomic_write_json(path, {"value": "old"})

            atomic_write_json(path, {"value": "새 값"})

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": "새 값"})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_atomic_write_json_keeps_existing_file_when_payload_cannot_serialize(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.json"
            atomic_write_json(path, {"value": "old"})

            with self.assertRaises(TypeError):
                atomic_write_json(path, {"bad": object()})

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": "old"})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
