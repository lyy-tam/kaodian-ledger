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


if __name__ == "__main__":
    unittest.main()
