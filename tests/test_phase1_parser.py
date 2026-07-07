import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hwp_alimi.phase1 import parse_assessment_blocks


SAMPLE_TEXT = """
교과
영역
평가 요소
성취 수준
국어
읽기
인물의 말과 행동을 바탕으로 인물이 추구하는 가치 찾기
도달
□
이야기 속 인물이 추구하는 가치를 설명할 수 있다.
부분
도달
■
인물이 추구하는 가치를 찾을 수 있다.
노력
중
□
친구들과의 협력학습을 통해 인물이 추구하는 가치를 알 수 있다.
사회
정치&#8231;
문화사
고려 시대 문화유산의 우수성과 관련된 이야기 쓰기
도달
■
고려 시대 문화유산의 우수성을 이해하고 이야기를 쓸 수 있다.
부분
도달
□
문화유산의 우수성을 알고 이야기를 쓸 수 있다.
노력
중
□
교사의 도움을 받아 이야기를 쓸 수 있다.
나의 성장을 위한 한 걸음
"""


class Phase1ParserTest(unittest.TestCase):
    def test_parse_blocks_with_split_status_labels_and_split_area(self):
        blocks = parse_assessment_blocks(SAMPLE_TEXT)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].subject, "국어")
        self.assertEqual(blocks[0].area, "읽기")
        self.assertEqual(blocks[0].levels[1].label, "부분도달")
        self.assertTrue(blocks[0].levels[1].checked)

        self.assertEqual(blocks[1].subject, "사회")
        self.assertEqual(blocks[1].area, "정치·문화사")
        self.assertEqual(blocks[1].levels[0].label, "도달")
        self.assertTrue(blocks[1].levels[0].checked)

    def test_parse_blocks_when_subject_cell_is_merged_and_omitted(self):
        text = """
교과
영역
평가 요소
성취 수준
국어
문학
감각적 표현의 느낌을 살려 시 낭송하기
도달
■
시를 낭송할 수 있다.
부분도달
□
일부 표현 방법을 활용하여 시를 낭송할 수 있다.
노력중
□
도움을 받아 시를 낭송할 수 있다.
문법
문장의 짜임에 맞게 글쓰기
도달
□
문장의 짜임에 맞게 글을 쓸 수 있다.
부분도달
□
문장의 짜임을 일부 활용할 수 있다.
노력중
□
도움을 받아 문장을 쓸 수 있다.
쓰기
우리 학교의 장소를 소개하는 글쓰기
도달
□
문단을 완성하여 쓸 수 있다.
부분도달
□
문단을 부분적으로 완성할 수 있다.
노력중
□
간단한 문단을 쓸 수 있다.
사회
지리 인식
주변 여러 장소의 경험과 느낌 표현하고 발표하기
도달
□
장소에 대한 느낌을 발표할 수 있다.
부분도달
□
장소에 대한 느낌을 일부 발표할 수 있다.
노력중
□
도움을 받아 장소에 대한 느낌을 말할 수 있다.
나의 성장을 위한 한 걸음
"""
        blocks = parse_assessment_blocks(text)

        self.assertEqual(len(blocks), 4)
        self.assertEqual([block.subject for block in blocks], ["국어", "국어", "국어", "사회"])
        self.assertEqual([block.area for block in blocks], ["문학", "문법", "쓰기", "지리 인식"])
        self.assertEqual(blocks[1].evaluation_element, "문장의 짜임에 맞게 글쓰기")

    def test_parse_blocks_when_area_cell_is_also_merged_and_omitted(self):
        text = """
교과
영역
평가 요소
성취 수준
영어
표현
알파벳 대소문자를 구분하여 쓰기
도달
□
알파벳을 쓸 수 있다.
부분도달
□
알파벳을 일부 쓸 수 있다.
노력중
□
도움을 받아 알파벳을 쓸 수 있다.
동물과 관련된 낱말의 의미를 알고 쓰기
도달
□
낱말을 쓸 수 있다.
부분도달
□
낱말을 일부 쓸 수 있다.
노력중
□
도움을 받아 낱말을 쓸 수 있다.
나의 성장을 위한 한 걸음
"""
        blocks = parse_assessment_blocks(text)

        self.assertEqual(len(blocks), 2)
        self.assertEqual([block.subject for block in blocks], ["영어", "영어"])
        self.assertEqual([block.area for block in blocks], ["표현", "표현"])
        self.assertEqual(blocks[1].evaluation_element, "동물과 관련된 낱말의 의미를 알고 쓰기")

    def test_parse_blocks_with_compact_header_spaced_status_and_inline_checkbox(self):
        text = """
과목
영역
평가요소
성취수준
수학
수와 연산
나눗셈의 계산 원리 이해하기
도달
■ 나눗셈을 정확하게 계산할 수 있다.
부분 도달
□ 나눗셈을 일부 계산할 수 있다.
노력 중
□ 도움을 받아 나눗셈을 계산할 수 있다.
나의 성장을 위한 한 걸음
"""
        blocks = parse_assessment_blocks(text)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].subject, "수학")
        self.assertEqual(blocks[0].area, "수와 연산")
        self.assertEqual(blocks[0].levels[0].text, "나눗셈을 정확하게 계산할 수 있다.")
        self.assertTrue(blocks[0].levels[0].checked)
        self.assertEqual(blocks[0].levels[1].label, "부분도달")
        self.assertEqual(blocks[0].levels[2].label, "노력중")


    def test_parse_second_grade_integrated_subjects_with_compact_names_and_repeated_headers(self):
        text = """
교과
영역
평가 요소
성취 수준
국어
읽기
글 읽기
도달
□
글을 읽을 수 있다.
부분도달
■
글을 일부 읽을 수 있다.
노력중
□
도움을 받아 글을 읽을 수 있다.
수학
수와 연산
수 비교하기
도달
□
수를 비교할 수 있다.
부분도달
■
수를 일부 비교할 수 있다.
노력중
□
도움을 받아 수를 비교할 수 있다.
바른생활
우리는 누구로 살아
갈까
자신의 감정을 이해하고 상황에 맞게 표현하기
도달
□
감정을 표현할 수 있다.
부분도달
■
감정을 일부 표현할 수 있다.
노력중
□
도움을 받아 감정을 표현할 수 있다.
자연을 보호하기 위한 약속을 정하고 실천하기
도달
□
약속을 실천할 수 있다.
부분도달
■
약속을 일부 실천할 수 있다.
노력중
□
도움을 받아 약속을 실천할 수 있다.
교과
영역
평가 요소
성취 수준
우리는 어디서 살아
갈까
마을의 공공장소에서 지킬 일과 올바른 이용 방법 알기
도달
□
공공장소 이용 방법을 알 수 있다.
부분도달
■
공공장소 이용 방법을 일부 알 수 있다.
노력중
□
도움을 받아 공공장소 이용 방법을 알 수 있다.
슬기로운생활
우리는 누구로 살아
갈까
내가 좋아하는 것을 소개하기
도달
□
나를 소개할 수 있다.
부분도달
■
나를 일부 소개할 수 있다.
노력중
□
도움을 받아 나를 소개할 수 있다.
교과
영역
평가 요소
성취 수준
우리는 누구로 살아
갈까
표정 알아맞히기 놀이하기
도달
□
놀이에 참여할 수 있다.
부분도달
■
놀이에 일부 참여할 수 있다.
노력중
□
도움을 받아 놀이에 참여할 수 있다.
자연 속에서 무늬를 찾아 나타내기
도달
□
무늬를 나타낼 수 있다.
부분도달
■
무늬를 일부 나타낼 수 있다.
노력중
□
도움을 받아 무늬를 나타낼 수 있다.
나의 성장을 위한 한 걸음
"""
        blocks = parse_assessment_blocks(text)

        self.assertEqual(len(blocks), 8)
        self.assertEqual(
            [block.subject for block in blocks],
            [
                "국어",
                "수학",
                "바른 생활",
                "바른 생활",
                "바른 생활",
                "슬기로운 생활",
                "즐거운 생활",
                "즐거운 생활",
            ],
        )
        self.assertEqual(blocks[2].area, "우리는 누구로 살아갈까")
        self.assertEqual(blocks[4].area, "우리는 어디서 살아갈까")
        self.assertEqual(blocks[6].area, "우리는 누구로 살아갈까")
        self.assertEqual(blocks[6].evaluation_element, "표정 알아맞히기 놀이하기")

if __name__ == "__main__":
    unittest.main()
