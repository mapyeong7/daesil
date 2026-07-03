import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hwp_alimi.phase2 import AssessmentColumn, header_key, normalize_level, parse_neis_excel_tsv, parse_neis_paste


def columns(count: int = 3) -> list[AssessmentColumn]:
    return [
        AssessmentColumn(i, subject, "영역", f"{subject} 평가", f"{i}. {subject} / 영역")
        for i, subject in enumerate(("국어", "수학", "영어")[:count], start=1)
    ]


class Phase2ParserTest(unittest.TestCase):
    def test_parse_without_header(self):
        text = "1\t김대실\t도달\t부분도달\t노력중\n2\t이대실\t부분 도달\t도달\t도달"
        result = parse_neis_paste(text, columns())

        self.assertEqual(result.student_count, 2)
        self.assertEqual(result.validation_mode, "order_assumed")
        self.assertEqual(len([issue for issue in result.issues if issue.severity != "warning"]), 0)
        self.assertEqual(len([issue for issue in result.issues if issue.severity == "warning"]), 1)
        self.assertEqual(result.students[0].name, "김대실")
        self.assertEqual(result.students[0].assessments[1].level, "부분도달")
        self.assertEqual(result.students[1].assessments[0].level, "부분도달")

    def test_parse_with_header_and_aliases(self):
        text = "번호\t이름\t국어\t수학\t영어\n1\t김대실\t상\t중\t하"
        result = parse_neis_paste(text, columns())

        self.assertEqual(result.student_count, 1)
        self.assertEqual(result.validation_mode, "header_verified")
        self.assertFalse(result.issues)
        self.assertEqual([a.level for a in result.students[0].assessments], ["도달", "부분도달", "노력중"])

    def test_invalid_level_reports_issue(self):
        text = "번호\t이름\t국어\t수학\t영어\n1\t김대실\t도달\t확인\t노력중"
        result = parse_neis_paste(text, columns())

        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].severity, "error")
        self.assertEqual(result.issues[0].column, "2. 수학 / 영역")
        self.assertIsNone(result.students[0].assessments[1].level)

    def test_blank_assessment_value_is_uncolored_missing_input_not_error(self):
        text = "번호\t이름\t국어\t수학\t영어\n1\t김대실\t상\t\t하"
        result = parse_neis_paste(text, columns())

        self.assertFalse(result.issues)
        self.assertEqual(result.students[0].assessments[0].level, "도달")
        self.assertIsNone(result.students[0].assessments[1].level)
        self.assertEqual(result.students[0].assessments[1].raw_value, "")
        self.assertEqual(result.students[0].assessments[2].level, "노력중")

    def test_header_order_mismatch_reports_error(self):
        text = "번호\t이름\t수학\t국어\t영어\n1\t김대실\t도달\t도달\t도달"
        result = parse_neis_paste(text, columns())

        self.assertEqual(result.validation_mode, "header_error")
        errors = [issue for issue in result.issues if issue.severity != "warning"]
        self.assertTrue(errors)
        self.assertIn("헤더 순서", errors[0].message)

    def test_duplicate_area_header_is_warning(self):
        duplicate_columns = [
            AssessmentColumn(1, "국어", "쓰기", "국어 쓰기 평가", "1. 국어 / 쓰기"),
            AssessmentColumn(2, "영어", "쓰기", "영어 쓰기 평가", "2. 영어 / 쓰기"),
        ]
        text = "번호\t이름\t쓰기\t쓰기\n1\t김대실\t도달\t부분도달"
        result = parse_neis_paste(text, duplicate_columns)

        self.assertEqual(result.validation_mode, "header_warning")
        self.assertTrue(any(issue.severity == "warning" for issue in result.issues))

    def test_neis_excel_two_row_header_with_preamble(self):
        excel_columns = [
            AssessmentColumn(1, "국어", "문학", "문학 평가", "1. 국어 / 문학"),
            AssessmentColumn(2, "국어", "문법", "문법 평가", "2. 국어 / 문법"),
            AssessmentColumn(3, "국어", "쓰기", "쓰기 평가", "3. 국어 / 쓰기"),
        ]
        text = "\n".join(
            [
                "\t\t국어 교과 성적 일람표",
                "\t2026학년도   1학기   3학년   3반",
                "\t학년-반/번호\t\t성명\t1:코드\t2:코드\t3:코드",
                "\t\t\t\t문학\t문법\t쓰기",
                "\t1\t\t김대실\t상\t중\t하",
            ]
        )

        result = parse_neis_paste(text, excel_columns)

        self.assertEqual(result.validation_mode, "header_verified")
        self.assertEqual(result.student_count, 1)
        self.assertEqual(result.students[0].source_row, 5)
        self.assertEqual(result.students[0].number, "1")
        self.assertEqual(result.students[0].name, "김대실")
        self.assertEqual([a.level for a in result.students[0].assessments], ["도달", "부분도달", "노력중"])

    def test_neis_fixed_excel_layout_maps_subject_area_column(self):
        hwp_columns = [
            AssessmentColumn(1, "국어", "읽기", "국어 읽기 평가", "1. 국어 / 읽기"),
            AssessmentColumn(2, "사회", "정치·문화사", "사회 평가", "2. 사회 / 정치·문화사"),
        ]
        text = "\n".join(
            [
                "\t\t\t\t\t\t",
                "\t\t\t\t\t\t",
                "\t\t국어 교과 성적 일람표\t\t\t\t",
                "\t\t\t\t\t\t",
                "\t2026학년도   1학기   3학년   3반\t\t\t\t",
                "\t\t\t\t\t\t",
                "\t학년-반/번호\t\t성명\t1:코드\t2:코드\t3:코드\t4:코드\t\t\t5:코드\t6:코드",
                "\t\t\t\t문학\t문법\t쓰기\t매체\t\t\t듣기말하기\t읽기",
                "\t1\t\t김대실\t상\t\t\t\t\t\t\t하",
            ]
        )

        parsed_columns, result = parse_neis_excel_tsv(text, hwp_columns)

        self.assertEqual([column.column_label for column in parsed_columns], ["1. 국어 / 읽기"])
        self.assertEqual(result.source_format, "neis_excel_fixed")
        self.assertEqual(result.validation_mode, "excel_fixed_verified")
        self.assertEqual(result.student_count, 1)
        self.assertEqual(result.students[0].source_row, 5)
        self.assertEqual(result.students[0].name, "김대실")
        self.assertEqual(result.students[0].assessments[0].raw_value, "하")
        self.assertEqual(result.students[0].assessments[0].level, "노력중")

    def test_header_key_ignores_middle_dot_variants(self):
        self.assertEqual(header_key("듣기 ․ 말하기"), "듣기말하기")
        self.assertEqual(header_key("듣기 · 말하기"), "듣기말하기")
        self.assertEqual(header_key("듣기ㆍ말하기"), "듣기말하기")

    def test_neis_fixed_excel_layout_matches_area_without_middle_dot(self):
        hwp_columns = [
            AssessmentColumn(5, "국어", "듣기 ․ 말하기", "듣기 평가", "5. 국어 / 듣기 ․ 말하기"),
        ]
        text = "\n".join(
            [
                "\t\t국어 교과 성적 일람표",
                "\t2026학년도   1학기   3학년   3반",
                "\t학년-반/번호\t\t성명\t5:코드",
                "\t\t\t\t듣기말하기",
                "\t1\t\t김대실\t중",
            ]
        )

        parsed_columns, result = parse_neis_excel_tsv(text, hwp_columns)

        self.assertEqual([column.index for column in parsed_columns], [5])
        self.assertFalse([issue for issue in result.issues if issue.severity != "warning"])
        self.assertEqual(result.students[0].assessments[0].level, "부분도달")

    def test_neis_fixed_excel_layout_resolves_spaced_subject_from_hwp_columns(self):
        hwp_columns = [
            AssessmentColumn(1, "즐거운 생활", "놀이", "놀이 평가", "1. 즐거운 생활 / 놀이"),
            AssessmentColumn(2, "국어", "읽기", "읽기 평가", "2. 국어 / 읽기"),
        ]
        text = "\n".join(
            [
                "\t\t즐거운 생활 교과 성적 일람표",
                "\t2026학년도   1학기   1학년   1반",
                "\t학년-반/번호\t\t성명\t1:코드",
                "\t\t\t\t놀이",
                "\t1\t\t김대실\t상",
            ]
        )

        parsed_columns, result = parse_neis_excel_tsv(text, hwp_columns)

        self.assertEqual([column.column_label for column in parsed_columns], ["1. 즐거운 생활 / 놀이"])
        self.assertEqual(result.metadata["excel_layout"]["subject"], "즐거운 생활")
        self.assertEqual(result.validation_mode, "excel_fixed_verified")
        self.assertEqual(result.students[0].assessments[0].level, "도달")

    def test_neis_fixed_excel_layout_maps_duplicate_area_by_order(self):
        hwp_columns = [
            AssessmentColumn(1, "사회", "역사 일반", "연표 만들기", "1. 사회 / 역사 일반"),
            AssessmentColumn(2, "사회", "역사 일반", "과거 모습 살펴보기", "2. 사회 / 역사 일반"),
        ]
        text = "\n".join(
            [
                "\t\t사회 교과 성적 일람표",
                "\t2026학년도   1학기   3학년   3반",
                "\t학년-반/번호\t\t성명\t1:코드\t2:코드",
                "\t\t\t\t역사 일반\t역사 일반",
                "\t1\t\t김대실\t상\t하",
            ]
        )

        parsed_columns, result = parse_neis_excel_tsv(text, hwp_columns)

        self.assertEqual([column.index for column in parsed_columns], [1, 2])
        self.assertEqual([a.level for a in result.students[0].assessments], ["도달", "노력중"])
        self.assertFalse([issue for issue in result.issues if issue.severity != "warning"])

    def test_neis_fixed_excel_layout_reports_missing_expected_area(self):
        hwp_columns = [
            AssessmentColumn(1, "국어", "문학", "문학 평가", "1. 국어 / 문학"),
            AssessmentColumn(2, "국어", "문법", "문법 평가", "2. 국어 / 문법"),
        ]
        text = "\n".join(
            [
                "\t\t국어 교과 성적 일람표",
                "\t2026학년도   1학기   3학년   3반",
                "\t학년-반/번호\t\t성명\t1:코드",
                "\t\t\t\t문학",
                "\t1\t\t김대실\t상",
            ]
        )

        parsed_columns, result = parse_neis_excel_tsv(text, hwp_columns)

        self.assertEqual([column.index for column in parsed_columns], [1])
        self.assertEqual(result.validation_mode, "excel_fixed_error")
        self.assertTrue(any("문법" in issue.message for issue in result.issues if issue.severity != "warning"))

    def test_normalize_level_variants(self):
        self.assertEqual(normalize_level("부분 도달"), "부분도달")
        self.assertEqual(normalize_level("노력 중"), "노력중")
        self.assertEqual(normalize_level("◎"), "도달")


if __name__ == "__main__":
    unittest.main()
