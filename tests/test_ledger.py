import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ledger", ROOT / "ledger.py")
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)

PAPER_2023 = """@paper: 2023-A
@year: 2023
@subject: 测试科目

[1] 意式浓缩的萃取时间一般控制在多少秒?(2分)
A. 5-10 秒
B. 20-30 秒

[2] 牛奶发泡温度过高会让蛋白质变性吗?(3分)
A. 会
B. 不会
"""


class NormTest(unittest.TestCase):
    def test_punctuation_and_space_ignored(self):
        self.assertEqual(ledger.norm("粉水比 1:16"), ledger.norm("粉水比１：１６"))

    def test_none_and_empty(self):
        self.assertEqual(ledger.norm(None), "")
        self.assertEqual(ledger.norm("  ，。 "), "")


class ParseTest(unittest.TestCase):
    def test_parse_fields(self):
        paper = ledger.parse_paper_text(PAPER_2023, "2023-A")
        self.assertEqual(paper["paper_id"], "2023-A")
        self.assertEqual(paper["year"], 2023)
        self.assertEqual(paper["subject"], "测试科目")
        self.assertEqual(len(paper["questions"]), 2)
        self.assertEqual(paper["questions"][0]["no"], "1")
        self.assertEqual(paper["questions"][0]["score"], 2)
        self.assertIn("20-30 秒", paper["questions"][0]["text"])

    def test_no_question_raises(self):
        with self.assertRaises(ValueError):
            ledger.parse_paper_text("没有题号的纯文本", "x")

    def test_duplicate_no_raises(self):
        with self.assertRaises(ValueError):
            ledger.parse_paper_text("@paper: p\n[1] 甲\n[1] 乙\n", "p")


class CheckTest(unittest.TestCase):
    def setUp(self):
        paper = ledger.parse_paper_text(PAPER_2023, "2023-A")
        self.index = ledger.build_index({"papers": [paper]})

    def test_verified(self):
        result = ledger.check_claim(
            {"id": "c1", "point": "萃取时间", "paper": "2023-A", "no": "1", "quote": "萃取时间"},
            self.index)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["score_used"], 2)
        self.assertEqual(result["score_source"], "question")

    def test_year_fallback(self):
        result = ledger.check_claim(
            {"id": "c1", "point": "牛奶发泡", "year": 2023, "no": "2", "quote": "蛋白质变性"},
            self.index)
        self.assertEqual(result["status"], "verified")

    def test_quote_not_found(self):
        result = ledger.check_claim(
            {"id": "c1", "point": "x", "paper": "2023-A", "no": "1", "quote": "不存在的句子"},
            self.index)
        self.assertEqual(result["status"], "suspect")
        self.assertIn("quote_not_found", result["reasons"])

    def test_question_not_found(self):
        result = ledger.check_claim(
            {"id": "c1", "point": "x", "paper": "2023-A", "no": "99", "quote": "萃取时间"},
            self.index)
        self.assertIn("question_not_found", result["reasons"])

    def test_paper_not_found(self):
        result = ledger.check_claim(
            {"id": "c1", "point": "x", "paper": "2099", "no": "1", "quote": "萃取时间"},
            self.index)
        self.assertIn("paper_not_found", result["reasons"])

    def test_long_quote_warns_but_verified(self):
        result = ledger.check_claim(
            {"id": "c1", "point": "x", "paper": "2023-A", "no": "1",
             "quote": "意式浓缩的萃取时间一般控制在多少秒"}, self.index)
        self.assertEqual(result["status"], "verified")
        self.assertIn("quote_too_long", [w["code"] for w in result["warnings"]])

    def test_score_mismatch_warns(self):
        result = ledger.check_claim(
            {"id": "c1", "point": "x", "paper": "2023-A", "no": "1",
             "quote": "萃取时间", "score": 5}, self.index)
        self.assertIn("score_mismatch", [w["code"] for w in result["warnings"]])
        self.assertEqual(result["score_used"], 2)


class RankTest(unittest.TestCase):
    @staticmethod
    def claim(point, paper, no, score):
        return {"status": "verified", "point": point, "quote": "x", "score_used": score,
                "matched": {"paper": paper, "no": no}}

    def test_weight_and_dedupe(self):
        claims = [
            self.claim("甲", "P1", "1", 2),
            self.claim("甲", "P2", "1", 3),
            self.claim("甲", "P2", "1", 3),
            self.claim("乙", "P1", "2", 4),
        ]
        rank = ledger.build_rank(claims, top=10, must_pct=50, under_pct=50)
        self.assertEqual(rank["rows"][0]["point"], "甲")
        self.assertEqual(rank["rows"][0]["count"], 2)
        self.assertEqual(rank["rows"][0]["weight"], 5)
        self.assertEqual(rank["rows"][0]["tier"], "必背")
        self.assertEqual(rank["rows"][1]["tier"], "理解")

    def test_missing_score_estimated(self):
        claims = [
            self.claim("甲", "P1", "1", 4),
            {"status": "verified", "point": "乙", "quote": "x", "score_used": None,
             "matched": {"paper": "P1", "no": "2"}},
        ]
        rank = ledger.build_rank(claims, top=10)
        row = [r for r in rank["rows"] if r["point"] == "乙"][0]
        self.assertTrue(row["est"])
        self.assertEqual(row["weight"], 4)

    def test_suspect_excluded(self):
        claims = [dict(self.claim("甲", "P1", "1", 2), status="suspect")]
        self.assertEqual(ledger.build_rank(claims, top=10)["rows"], [])


class CalibrateTest(unittest.TestCase):
    def test_below_threshold(self):
        items = [{"result": "hit"}, {"result": "hit"}, {"result": "miss"}, {"result": None}]
        decision = ledger.calibrate_decision(items, threshold=0.7)
        self.assertEqual(decision["checked"], 3)
        self.assertEqual(decision["status"], "reference_only")
        self.assertAlmostEqual(decision["hit_rate"], 0.6667, places=4)

    def test_above_threshold(self):
        items = [{"result": "hit"}, {"result": "hit"}, {"result": "miss"}]
        self.assertEqual(ledger.calibrate_decision(items, threshold=0.6)["status"], "ok")

    def test_nothing_filled_raises(self):
        with self.assertRaises(ValueError):
            ledger.calibrate_decision([{"result": None}])


class SheetTest(unittest.TestCase):
    def test_answer_comes_last(self):
        quiz = {"quizzes": [{"claim_id": "c1", "questions": [
            {"q": "题干一", "a": "答案一"}, {"q": "题干二", "a": "答案二"}]}]}
        text = ledger.render_sheet(quiz, {"c1": "知识点一"})
        self.assertLess(text.index("题干一"), text.index("# 答案"))
        self.assertLess(text.index("题干二"), text.index("# 答案"))
        self.assertLess(text.index("# 答案"), text.index("答案一"))

    def test_unverified_claim_rejected(self):
        quiz = {"quizzes": [{"claim_id": "c9", "questions": [{"q": "q", "a": "a"}]}]}
        with self.assertRaises(ValueError):
            ledger.render_sheet(quiz, {"c1": "知识点一"})

    def test_missing_answer_rejected(self):
        quiz = {"quizzes": [{"claim_id": "c1", "questions": [{"q": "q", "a": " "}]}]}
        with self.assertRaises(ValueError):
            ledger.render_sheet(quiz, {"c1": "知识点一"})


class ReportTest(unittest.TestCase):
    def test_suspect_and_uncalibrated_notes(self):
        document = {
            "generated_at": "now",
            "meta": {"subject": "s"},
            "corpus": {"papers": 1, "questions": 1},
            "summary": {"claims": 1, "claims_verified": 0, "claims_suspect": 1, "absent": 0},
            "claims": [{"id": "c1", "point": "甲", "quote": "x", "status": "suspect",
                        "reasons": ["quote_not_found"], "paper": "P1", "no": "1"}],
            "absent": [],
        }
        text = ledger.render_report(ledger.build_rank([], top=20), document)
        self.assertIn("存疑区", text)
        self.assertIn("引文在该题题干里找不到", text)
        self.assertIn("尚未做人工校准", text)


if __name__ == "__main__":
    unittest.main()
