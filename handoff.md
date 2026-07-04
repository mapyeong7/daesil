# hwp-alimi Handoff

Last updated: 2026-07-04

## Project Summary

`hwp-alimi` is a local Windows web tool for creating student HWP report cards from a school HWP template and NEIS Excel score exports.

The intended teacher workflow is:

1. Enter grade, class, and teacher name.
2. Upload and recognize the HWP template.
3. Upload one NEIS Excel file per subject detected from the HWP template.
4. Review roster, matching, missing values, warnings, and errors.
5. Generate per-student HWP report cards.

The project folder is:

```powershell
C:\Users\대실초\Documents\hwp-alimi
```

The local server URL is:

```text
http://127.0.0.1:8765/
```

Run it with:

```powershell
cd C:\Users\대실초\Documents\hwp-alimi
python .\run_server.py
```

## Main Files

- `index.html`: single-page local UI.
- `run_server.py`: starts the local server.
- `src/hwp_alimi/local_server.py`: HTTP API, state, upload, merge, and HWP generation orchestration.
- `src/hwp_alimi/phase1.py`: HWP text parsing and assessment block extraction.
- `src/hwp_alimi/phase2.py`: NEIS paste/Excel parsing and subject/assessment matching.
- `src/hwp_alimi/phase3.py`: output manifest creation and validation before HWP generation.
- `src/hwp_alimi/hwp5_patch.py`: direct HWP5/OLE stream patching for names, checkboxes, and school info.
- `scripts/generate_hwp_reports.ps1`: HWP COM fallback generator.
- `tests/`: unittest coverage.

## Current Implemented State

### Frontend workflow

- Reworked the UI into a teacher-facing 5-step sidebar.
- Removed the old topbar layout.
- The main content changes according to the selected step.
- Step states are visually marked:
  - green/ok when ready or completed,
  - red background when missing, blocked, or problematic.
- Errors and important warnings are shown as popups because inline-only warnings were easy to miss.
- HWP-detected subjects create separate Excel upload cards automatically.
  - Example: if HWP has `국어`, the UI shows a `국어` Excel selector.
  - Example: if HWP has `즐거운 생활`, the UI shows a `즐거운 생활` Excel selector.

### School info support

- Added grade, class, and teacher-name input in step 1.
- Added `/api/school-info` to save school info into `server_state.json`.
- Saving school info clears the old Phase 3 manifest so output is regenerated with the new info.
- Phase 3 now validates that grade/class/teacher values exist before output.
- Phase 3 now detects HWP placeholders for:
  - grade/class, such as `0학년 0반`,
  - teacher labels, such as `담임: 000`, `담임교사: OOO`, or `담임 0 0 0`.
- Direct HWP generation patches these values into every generated student HWP.
- The PowerShell COM fallback also patches school info placeholders.

### Student and score handling

- Students are matched by number/name from NEIS data.
- Duplicate student numbers in Excel are blocked and excluded.
- Student roster mismatches are surfaced as issues.
- Blank score values do not mark any `상/중/하` checkbox.
- Invalid or unmatched values are reported rather than silently colored.
- Subject-specific Excel imports are merged into one Phase 2 payload.

### HWP direct generation

- Direct HWP generation is implemented for per-student HWP files.
- Student name/number placeholders are patched directly inside HWP5 body streams.
- Checkbox states are reset first, then only matched score ordinals are filled.
- If a score is blank, no checkbox is filled for that assessment.
- Generated files are validated by counting expected checkbox/text occurrences.
- Generated files are zipped into `hwp_reports.zip`.

### HWP5/OLE patch improvements

`src/hwp_alimi/hwp5_patch.py` now supports more direct OLE manipulation:

- FAT sector expansion for regular streams.
- Mini stream read/write helpers.
- Directory entry creation and child tree rebuild helpers.
- Safe stream writing when compressed HWP body streams grow.
- Byte-length-preserving UTF-16 replacement so HWP record sizes remain valid.
- Compact student placeholder replacements for tight placeholders, such as `10번 이름:박서은`.

These changes were needed because longer patched values could corrupt HWP files if stream sizes or record lengths were not handled carefully.

## Current Output Behavior

When Phase 3 is ready and HWP generation is executed:

- individual HWP files are generated under `phase3_output/hwp_reports` or `phase3_output/hwp_reports_sample`,
- a ZIP file is generated for download,
- response metadata includes generated file links and ZIP links,
- the UI shows generated HWP links in step 5.

## Verified Locally

Known verification from the current development session:

- `python -m unittest discover -s tests -v` previously passed with 95 tests before the unfinished combined-HWP experiment.
- Generated individual HWP files opened successfully in Hancom HWP 2022.
- Sample files checked visually included `01_김경우.hwp` and `10_박서은.hwp`.
- A two-student multi-section experimental HWP opened in HWP and paginated as 10 pages.

Run the test suite before continuing or releasing:

```powershell
cd C:\Users\대실초\Documents\hwp-alimi
python -m unittest discover -s tests -v
```

## In-Progress / Not Finished

### 전체 학생 HWP file

The user requested an additional `전체 학생` HWP file for duplex printing, where each student must occupy an even number of pages.

Current findings:

- Simple binary concatenation of `BodyText/Section0` streams is not enough.
  - The file may open, but HWP pagination/display does not correctly show the second student.
- A multi-section HWP approach is promising.
  - Copy the first student's HWP.
  - Add later students as `BodyText/Section1`, `Section2`, etc.
  - Patch `DocInfo` section count.
  - Rebuild the `BodyText` storage child tree.
  - A 2-student prototype opened in HWP and showed 10 pages.
- Duplex padding is not completed.
  - If one student is 5 pages, the next student can begin on the back side during duplex printing.
  - Need either a reliable odd-page/blank-page control insertion or another verified padding strategy.
- An experiment file named `combined_odd_control_test.hwp` was created to test HWP `PGCT` odd-page control, but Windows Computer Use was stopped by the user with Escape before visual verification finished.

Do not claim the `전체 학생` HWP feature is complete yet.

## Current Git Notes

- `handoff.md` is ignored by `.gitignore` under "Private handoff/progress notes".
- If the user wants this handoff committed, stage it explicitly with `git add -f handoff.md`.
- `.tmp_pyhwp_download/` was a temporary pyhwp reference download used only to inspect HWP internals. Do not commit it.

## Known Risks / Gaps

- HWP templates vary by grade. The current parser extracts many template details, but fully automatic recognition of arbitrary grade-specific layout differences is still limited by what is present in the HWP text extraction.
- HWP placeholder replacement is length-sensitive. Longer replacements must either fit existing placeholder byte lengths or be handled with deeper record rewriting.
- Full combined-HWP duplex-safe generation is pending.
- Real HWP and Excel COM behavior must be checked on Windows with installed Hancom HWP and Excel.
- PowerShell 5 may display Korean text as mojibake even when UTF-8 files are valid.

## Suggested Next Steps

1. Finish `전체 학생` HWP generation.
2. Verify an odd/even page control or blank-page insertion strategy in Hancom HWP.
3. Add production code to create `전체_학생.hwp` after individual files are generated.
4. Add the combined HWP link to the Phase 3 response and step 5 UI.
5. Include the combined HWP in the ZIP or expose it as a separate primary download.
6. Add tests for combined-HWP metadata and ZIP inclusion.
7. Run `python -m unittest discover -s tests -v`.
8. Open the generated `전체_학생.hwp` in HWP and confirm duplex page boundaries manually.

## Useful Commands

Run server:

```powershell
python .\run_server.py
```

Run tests:

```powershell
python -m unittest discover -s tests -v
```

Check Git state:

```powershell
git status -sb
```

Commit ignored handoff when explicitly requested:

```powershell
git add -f handoff.md
```