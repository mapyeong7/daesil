import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class IndexHtmlTest(unittest.TestCase):
    def test_subject_cards_match_spacing_variants(self):
        if not shutil.which("node"):
            self.skipTest("node is not available")

        script = r"""
            const fs = require("fs");
            const vm = require("vm");

            const html = fs.readFileSync("index.html", "utf8");
            const match = html.match(/<script>([\s\S]*)<\/script>/);
            if (!match) throw new Error("script tag not found");
            const appScript = match[1].replace(/\n\s*loadAll\(\);\s*$/, "\n");

            const elements = new Map();
            function element(id) {
              if (!elements.has(id)) {
                elements.set(id, {
                  id,
                  hidden: false,
                  disabled: false,
                  href: "",
                  innerHTML: "",
                  textContent: "",
                  files: [],
                  dataset: {},
                  focus() {},
                  addEventListener() {},
                  closest() { return null; },
                  querySelector() { return element(`${id}-child`); },
                });
              }
              return elements.get(id);
            }

            const context = {
              console,
              window: {},
              document: {
                getElementById: element,
                addEventListener() {},
              },
              FormData: function FormData() {},
              fetch: async () => { throw new Error("fetch disabled in test"); },
              confirm: () => true,
            };
            context.globalThis = context;
            vm.createContext(context);
            vm.runInContext(appScript, context);

            const hooks = context.window.__hwpAlimiTestHooks;
            if (!hooks) throw new Error("test hooks not registered");

            function assert(condition, message) {
              if (!condition) throw new Error(message);
            }

            assert(hooks.subjectKey("즐거운 생활") === hooks.subjectKey("즐거운생활"), "spacing should be ignored");
            assert(hooks.subjectKey("바른·생활") === hooks.subjectKey("바른생활"), "middle dots should be ignored");
            const pastedRoster = hooks.parseRosterPaste("2\\t이대실\\n1\\t김대실");
            const normalizedRoster = hooks.normalizeStudentRoster(pastedRoster);
            assert(normalizedRoster[0].number === "1" && normalizedRoster[0].name === "김대실", "roster paste should parse and sort by number");
            const pastedNumbers = hooks.parseRosterPaste("1\\n2");
            assert(pastedNumbers[0].number === "1" && pastedNumbers[0].name === "", "numeric-only paste should fill number column");
            const pastedScores = hooks.parseScorePaste("상\\t도달\\n부분도달\\t노력중\\n\\t하");
            assert(pastedScores[0][0] === "상" && pastedScores[0][1] === "상", "score paste should normalize reached values");
            assert(pastedScores[1][0] === "중" && pastedScores[1][1] === "하", "score paste should normalize level labels");
            assert(pastedScores[2][0] === "" && pastedScores[2][1] === "하", "score paste should preserve blank cells");
            assert(hooks.normalizeScoreCellValue("부분 도달") === "중", "score cell should normalize whitespace variants");
            assert(hooks.subjectValueMatches("즐거운 생활", "즐거운생활"), "subject value should match spacing variants");
            assert(
              hooks.issueMatchesSubject(
                { column: "학생명단", message: "즐거운생활 엑셀에 기준 명단 학생이 없습니다.", value: "2 이대실" },
                "즐거운 생활"
              ),
              "subject issue should match spacing variants"
            );

            const subjects = hooks.subjectsFromPhase1({
              blocks: [
                { subject: "즐거운 생활" },
                { subject: "즐거운생활" },
                { subject: "국어" },
              ],
            });
            assert(subjects.length === 2, `expected deduped subjects, got ${subjects.join(",")}`);

            const phase1 = {
              blocks: [
                { index: 1, subject: "즐거운 생활", area: "말하기", evaluation_element: "문장 말하기" },
                { index: 2, subject: "즐거운 생활", area: "듣기", evaluation_element: "이야기 듣기" },
              ],
            };
            const phase2 = {
              columns: [{ index: 1, subject: "즐거운생활" }],
              issues: [
                {
                  severity: "error",
                  column: "학생명단",
                  message: "즐거운생활 엑셀에 기준 명단 학생이 없습니다.",
                  value: "2 이대실",
                },
              ],
              metadata: {
                imports: [
                  {
                    subject: "즐거운생활",
                    title: "즐거운생활 교과 성적 일람표",
                    source_excel: "C:/tmp/즐거운생활.xls",
                  },
                ],
              },
            };
            const state = hooks.subjectCardState("즐거운 생활", phase1, phase2);
            assert(state.importedCount === 1, `expected imported count 1, got ${state.importedCount}`);
            assert(state.cardClass === "error", `expected error card, got ${state.cardClass}`);
            assert(state.details.some((item) => item.includes("즐거운생활.xls")), "expected imported file detail");
            assert(state.details.some((item) => item.includes("누락")), "expected missing block detail");
        """

        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        if result.returncode != 0:
            self.fail(f"node index.html check failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")

    def test_roster_grid_is_spreadsheet_style(self):
        html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="add-roster-batch-button"', html)
        self.assertIn('class="panel basic-info-card"', html)
        self.assertIn('class="basic-info-card-title"', html)
        self.assertIn('class="basic-info-section"', html)
        self.assertIn("기본정보 통합 저장", html)
        self.assertIn('class="basic-save-strip status-only"', html)
        self.assertIn("기본정보 전체 저장", html)
        self.assertLess(html.index('id="save-school-info-button"'), html.index('class="basic-info-section"'))
        self.assertIn("학급정보와 학생명단을 한 번에 저장합니다.", html)
        self.assertIn("DEFAULT_ROSTER_GRID_ROWS = 30", html)
        self.assertIn('class="row-index">#', html)
        self.assertIn('class="delete-col">삭제', html)
        self.assertIn('data-action="delete-roster-row"', html)
        self.assertIn("deleteRosterRow(button)", html)
        self.assertIn("markRosterChanged", html)
        self.assertIn("syncSubjectGridWithRoster", html)
        self.assertIn("기본정보 명단 변경사항이 과목별 표에 반영되었습니다.", html)
        self.assertIn("학생 명단에서 삭제했습니다", html)
        self.assertIn('addEventListener("paste", applyRosterPasteToGrid)', html)
        self.assertIn("let rosterDirty = false", html)
        self.assertIn("payloadIncludesRoster", html)
        self.assertIn("keepLocalRoster", html)
        self.assertIn("payload.student_roster = nextRoster", html)
        self.assertIn("저장 필요", html)
        self.assertIn("scoreGridSaveDisabled", html)
        self.assertNotIn('id="roster-paste-input"', html)
        self.assertNotIn('id="paste-roster-button"', html)

    def test_score_grid_is_spreadsheet_style(self):
        html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn("const scoreGridDrafts = new Map()", html)
        self.assertIn("과목별 성적입력", html)
        self.assertNotIn('<span class="step-label">과목별 엑셀</span>', html)
        self.assertNotIn("<h1>과목별 엑셀</h1>", html)
        self.assertIn('class="score-entry-shell"', html)
        self.assertIn('class="score-help"', html)
        self.assertIn('class="scroll score-grid score-grid-shell"', html)
        self.assertIn('class="score-entry-title"', html)
        self.assertIn('class="score-save-strip status-only"', html)
        self.assertIn('class="subject-upload-section"', html)
        self.assertIn("<strong>성적 입력</strong>", html)
        self.assertNotIn("엑셀형 성적 입력", html)
        self.assertIn("엑셀 파일로 불러오기", html)
        self.assertIn("필요할 때만 하단에서 NEIS 엑셀 파일을 선택합니다.", html)
        self.assertIn("현재 과목 성적 저장", html)
        self.assertLess(html.index('id="save-score-grid-button"'), html.index('id="score-grid"'))
        self.assertIn("첫 번째 성적 칸에 바로 붙여넣으세요", html)
        self.assertLess(html.index('class="score-entry-shell"'), html.index('id="subject-excel-list"'))
        self.assertIn("border-collapse: separate", html)
        self.assertIn("border-spacing: 0", html)
        self.assertIn("border-right: 1px solid #e3e9f0", html)
        self.assertIn("border-bottom: 1px solid #e3e9f0", html)
        self.assertIn(".score-grid input::placeholder", html)
        self.assertIn("color: transparent", html)
        self.assertIn("box-shadow: inset 0 0 0 1px #2f80ed", html)
        self.assertIn("parseScorePaste", html)
        self.assertIn("applyScorePasteToGrid", html)
        self.assertIn('addEventListener("paste", applyScorePasteToGrid)', html)
        self.assertIn('class="score-cell', html)
        self.assertIn('class="row-index">${index + 1}</td>', html)
        self.assertIn('class="row-index">#', html)
        self.assertIn('if (step === "excel")', html)
        self.assertIn("syncSubjectGridWithRoster();", html)
        self.assertNotIn('data-action="delete-score-student"', html)
        self.assertNotIn("deleteStudentFromScoreGrid", html)
        self.assertIn('id="score-entry-summary"', html)
        self.assertIn('data-score-col-index', html)
        self.assertIn('paste-start', html)
        self.assertIn("성적 저장 필요", html)
        self.assertNotIn("select[data-score-block]", html)

    def test_output_buttons_are_numbered(self):
        html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn('class="form-row output-action-row"', html)
        self.assertIn('class="output-primary-actions"', html)
        self.assertIn('class="output-secondary-actions"', html)
        self.assertIn('id="prepare-output-button"', html)
        self.assertIn("1.학생별 출력 데이터 만들기", html)
        self.assertNotIn(">학생별 출력 데이터 만들기</button>", html)
        self.assertIn('class="primary" id="generate-output-button"', html)
        self.assertIn("2.HWP 파일 생성", html)
        self.assertNotIn(">HWP 파일 생성</button>", html)
        self.assertLess(html.index('id="generate-output-button"'), html.index('id="generate-sample-output-button"'))

    def test_local_launcher_package_is_exe_first(self):
        html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
        start_script = (PROJECT_ROOT / "scripts" / "start_local.ps1").read_text(encoding="utf-8")
        package_script = (PROJECT_ROOT / "scripts" / "package_local.ps1").read_text(encoding="utf-8")
        launcher_source = (PROJECT_ROOT / "launcher" / "HwpAlimiLauncher.cs").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("HwpAlimi.exe", readme)
        self.assertIn("`index.html`을 직접 열면", readme)
        self.assertIn("HwpAlimi.exe", package_script)
        self.assertIn("HwpAlimiLauncher.cs", package_script)
        self.assertIn("/target:exe", package_script)
        self.assertIn("Get-Command python.exe", start_script)
        self.assertLess(start_script.index("Get-Command python.exe"), start_script.index("Get-Command py.exe"))
        self.assertIn(".StartsWith($env:WINDIR", start_script)
        self.assertIn("Quote-Argument", start_script)
        self.assertIn("$serverArgumentLine", start_script)
        self.assertIn("Find-LaunchTarget", start_script)
        self.assertIn("Get-ExistingAppRoot", start_script)
        self.assertIn("/api/app-info", start_script)
        self.assertIn("Test-SamePath", start_script)
        self.assertIn("기본 포트", start_script)
        self.assertIn("Wait-ServerReady", start_script)
        self.assertIn("System.Net.Sockets.TcpClient", start_script)
        self.assertIn("BeginConnect", start_script)
        self.assertIn("$launchTarget.UseExisting", start_script)
        self.assertIn("if (-not (Wait-ServerReady", start_script)
        self.assertLess(start_script.index("if (-not (Wait-ServerReady"), start_script.rindex("Start-Process $serverUrl"))
        self.assertIn("scripts", launcher_source)
        self.assertIn("start_local.ps1", launcher_source)
        self.assertIn("powershell.exe", launcher_source)
        self.assertIn("HwpAlimi.exe 실행파일로 시작", html)
        self.assertIn("로컬 서버에 연결하지 못했습니다", html)

    def test_local_server_exposes_app_root_for_launcher(self):
        server_source = (PROJECT_ROOT / "src" / "hwp_alimi" / "local_server.py").read_text(encoding="utf-8")

        self.assertIn('if path == "/api/app-info":', server_source)
        self.assertIn('"app_root": str(PROJECT_ROOT)', server_source)
