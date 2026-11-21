import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import serve  # noqa: E402

PAPER = """@paper: P1
@year: 2024
[1] 题一的题干(2分)
[2] 题二的题干(3分)
"""

CLAIMS = """{"meta": {"subject": "测试"}, "claims": [
  {"id": "c1", "point": "甲", "paper": "P1", "no": "1", "quote": "题一"},
  {"id": "c2", "point": "乙", "paper": "P1", "no": "2", "quote": "题二"},
  {"id": "c3", "point": "丙", "paper": "P1", "no": "9", "quote": "不存在"}
]}
"""


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = serve.Store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_upload_and_state(self):
        state = self.store.upload("P1.txt", PAPER.encode("utf-8"))
        self.assertEqual(state["questions"], 2)
        self.assertEqual(state["papers"][0]["paper_id"], "P1")
        self.assertEqual(state["papers"][0]["questions"][0]["score"], 2)

    def test_reject_unknown_suffix(self):
        with self.assertRaises(ValueError):
            self.store.upload("a.pdf", b"x")

    def test_check_and_report(self):
        self.store.upload("P1.txt", PAPER.encode("utf-8"))
        result = self.store.check(CLAIMS)
        self.assertEqual(result["summary"]["claims_verified"], 2)
        self.assertEqual(result["summary"]["claims_suspect"], 1)
        self.assertEqual(len(result["claims"]), 3)
        report = self.store.report(20)
        self.assertEqual(len(report["rows"]), 2)
        self.assertIn("考点账本", report["markdown"])
        self.assertEqual(report["rows"][0]["tier"], "必背")

    def test_check_without_corpus(self):
        with self.assertRaises(ValueError):
            self.store.check(CLAIMS)

    def test_clear(self):
        self.store.upload("P1.txt", PAPER.encode("utf-8"))
        state = self.store.clear()
        self.assertEqual(state["questions"], 0)


QUIZ = """{"quizzes": [{"claim_id": "c1", "questions": [
  {"q": "问题一", "a": "答案一"},
  {"q": "问题二", "a": "答案二"}
]}]}"""


class QuizTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = serve.Store(self.temp.name)
        self.store.upload("P1.txt", PAPER.encode("utf-8"))
        self.store.check(CLAIMS)

    def tearDown(self):
        self.temp.cleanup()

    def test_without_ledger_returns_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            store = serve.Store(folder)
            view = store.quiz_view()
            self.assertEqual(view["quizzes"], [])
            self.assertTrue(view["need_ledger"])
            self.assertTrue(store.review_view()["need_ledger"])

    def test_set_quiz_and_grade(self):
        view = self.store.set_quiz(QUIZ)
        self.assertEqual(len(view["quizzes"]), 1)
        self.assertEqual(view["quizzes"][0]["point"], "甲")
        data = self.store.grade({"results": [
            {"claim_id": "c1", "q": 1, "ok": False},
            {"claim_id": "c1", "q": 2, "ok": True},
        ]})
        self.assertEqual(data["mistakes"]["items"][0]["wrong"], 1)
        self.assertEqual(data["review"]["rows"][0]["point"], "甲")

    def test_unverified_claim_rejected(self):
        bad = '{"quizzes": [{"claim_id": "c3", "questions": [{"q": "q", "a": "a"}]}]}'
        with self.assertRaises(ValueError):
            self.store.set_quiz(bad)

    def test_grade_accumulates(self):
        self.store.set_quiz(QUIZ)
        self.store.grade({"results": [{"claim_id": "c1", "q": 1, "ok": False}]})
        data = self.store.grade({"results": [{"claim_id": "c1", "q": 1, "ok": False}]})
        self.assertEqual(data["mistakes"]["items"][0]["wrong"], 2)


if __name__ == "__main__":
    unittest.main()
