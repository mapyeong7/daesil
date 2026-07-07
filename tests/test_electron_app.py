import json
import shutil
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ElectronAppTest(unittest.TestCase):
    def test_package_json_declares_electron_entrypoints(self):
        package = json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(package["main"], "electron/main.js")
        self.assertEqual(package["scripts"]["electron"], "electron .")
        self.assertIn("package:electron", package["scripts"])
        self.assertIn("electron", package["devDependencies"])
        self.assertIn("@electron/packager", package["devDependencies"])

    def test_electron_main_starts_python_server_and_loads_local_url(self):
        main_js = (PROJECT_ROOT / "electron" / "main.js").read_text(encoding="utf-8")

        self.assertIn("run_server.py", main_js)
        self.assertIn("--port", main_js)
        self.assertIn("api/app-info", main_js)
        self.assertIn("userData", main_js)
        self.assertIn("where.exe", main_js)
        self.assertIn(".asar.unpacked", main_js)
        self.assertIn("BrowserWindow", main_js)
        self.assertIn("loadURL", main_js)
        self.assertIn("before-quit", main_js)
        self.assertIn("serverProcess.kill", main_js)

    def test_electron_main_js_syntax(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")

        result = subprocess.run(
            ["node", "--check", "electron/main.js"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        if result.returncode != 0:
            self.fail(f"electron/main.js syntax check failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")

    def test_electron_package_script_exists(self):
        script = (PROJECT_ROOT / "scripts" / "package_electron.ps1").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("electron-packager", script)
        self.assertIn("--no-asar", script)
        self.assertIn("HwpAlimi.exe", script)
        self.assertIn("npm run electron", readme)
        self.assertIn("npm run package:electron", readme)
