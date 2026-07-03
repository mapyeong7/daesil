# HWP Alimi

학교 배움성장알리미 HWP 양식을 읽고, 과목별 NEIS 엑셀 성적을 병합해 학생별 HWP 성적표와 ZIP 파일을 만드는 로컬 도구입니다.

## 주요 기능

- HWP 양식에서 교과, 영역, 평가 요소, 성취 수준 문구를 자동 추출합니다.
- 양식에서 추출된 과목별로 엑셀 업로드 칸을 따로 만듭니다.
- 과목 엑셀을 잘못된 칸에 넣으면 오류로 막습니다.
- 첫 과목 엑셀의 학생 명단을 기준으로 이후 과목의 누락, 추가, 이름 불일치를 검증합니다.
- 빈 성취도 값은 오류가 아니며 아무 체크박스도 표시하지 않습니다.
- 모든 평가 항목이 들어온 뒤 학생별 누락 평가가 있으면 출력 전에 막습니다.
- 학생별 HWP를 생성하고 ZIP으로 묶습니다.
- 일반 `.hwp` 양식은 한글 프로그램을 띄우지 않는 빠른 직접 패치 방식으로 생성합니다.
- 직접 패치가 어려운 특수 HWP는 기존 한글 COM 방식으로 fallback합니다.

## 실행

```powershell
python .\run_server.py
```

브라우저에서 아래 주소를 엽니다.

```text
http://127.0.0.1:8765/
```

## 화면 흐름

1. HWP 양식 파일을 선택하고 `양식 인식`을 누릅니다.
2. 화면에 자동으로 만들어진 과목별 엑셀 칸에 NEIS 엑셀을 넣습니다.
3. 학생 목록, 과목별 입력 상태, 오류/경고를 확인합니다.
4. `학생별 출력 데이터 만들기`를 눌러 전체 입력 상태를 검증합니다.
5. `1명 테스트 생성`으로 먼저 HWP 결과를 확인합니다.
6. 문제가 없으면 `HWP 파일 생성`으로 학생별 HWP와 ZIP을 만듭니다.

## 검증 정책

- 학생 명단은 첫 번째로 불러온 과목 엑셀을 기준으로 삼습니다.
- 같은 번호의 이름이 과목마다 다르면 해당 과목 성적은 병합하지 않습니다.
- 기준 명단에 없는 학생이 다른 과목 엑셀에 있으면 병합하지 않습니다.
- 기준 명단 학생이 특정 과목 엑셀에 없으면 오류로 표시합니다.
- 한 엑셀 안에 같은 학생 번호가 중복되면 오류로 막습니다.
- 엑셀의 성취도 값은 `상/중/하`, `도달/부분도달/노력중` 계열로 정규화합니다.
- 빈 값은 체크하지 않는 정상 미입력으로 처리합니다.

## 출력

- 테스트 출력: `phase3_output/hwp_reports_sample`
- 전체 출력: `phase3_output/hwp_reports`
- 생성 결과에는 HWP 파일과 `hwp_reports.zip`이 포함됩니다.
- 화면에는 실제 존재하는 생성 파일 링크만 표시됩니다.
- 생성 중 검증에 실패한 HWP 파일은 출력 폴더에 남기지 않습니다.
- Phase3 JSON의 `generation_method`로 생성 방식을 확인할 수 있습니다.
  - `direct_hwp_patch`: 빠른 직접 HWP 생성
  - `hwp_com_fallback`: 한글 프로그램 fallback 생성

## 전제 조건

- Windows
- Python 3.10 이상
- HWP 양식 텍스트 추출 및 fallback 생성을 위해 한글 2022 등 `HWPFrame.HwpObject` COM을 제공하는 한글 설치
- 일반 `.hwp` 출력 생성은 빠른 직접 패치 경로를 우선 사용합니다.
- 업로드 파일은 빈 파일과 지원하지 않는 확장자를 먼저 차단합니다.

## 개발자용 명령

HWP 양식만 분석:

```powershell
python .\run_phase1.py "C:\path\to\template.hwp"
```

Phase2 파서만 TSV로 검증:

```powershell
python .\run_phase2.py ".\phase1_output\sample.phase1.json" ".\examples\neis_sample.tsv"
```

합성 전체 데이터로 HWP 생성 smoke test:

```powershell
python -X utf8 .\scripts\synthetic_full_smoke.py --generate
```
