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
