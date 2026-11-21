#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考点账本 本地网页版。

    python3 serve.py
    浏览器打开 http://127.0.0.1:8000

数据只写在本机 data/ 目录，不联网、不上传。
"""

import argparse
import json
import pathlib
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import ledger

ROOT = pathlib.Path(__file__).resolve().parent
WEB_FILE = ROOT / "web" / "app.html"
MAX_BODY = 16 * 1024 * 1024


class Store:
    def __init__(self, data_dir):
        self.root = pathlib.Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.corpus_path = self.root / "corpus.json"
        self.ledger_path = self.root / "ledger.json"
        self.quiz_path = self.root / "quiz.json"
        self.mistakes_path = self.root / "mistakes.json"
        self.lock = threading.Lock()
        self.corpus = self._load(self.corpus_path) or {"schema": ledger.CORPUS_SCHEMA, "papers": []}
        self.ledger = self._load(self.ledger_path)
        self.quiz = self._load(self.quiz_path)
        self.mistakes = self._load(self.mistakes_path)

    @staticmethod
    def _load(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _save(path, document):
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    def _reset_derived(self):
        self.ledger = None
        self.quiz = None
        self.mistakes = None
        self.ledger_path.unlink(missing_ok=True)
        self.quiz_path.unlink(missing_ok=True)
        self.mistakes_path.unlink(missing_ok=True)

    def state(self):
        papers = []
        for paper in self.corpus.get("papers") or []:
            papers.append({
                "paper_id": paper["paper_id"],
                "year": paper.get("year"),
                "questions": [
                    {"no": question["no"], "score": question["score"], "text": question["text"]}
                    for question in paper.get("questions") or []
                ],
            })
        return {
            "papers": papers,
            "questions": sum(len(paper["questions"]) for paper in papers),
            "has_ledger": bool(self.ledger),
            "has_quiz": bool(self.quiz),
            "summary": (self.ledger or {}).get("summary"),
            "version": ledger.VERSION,
        }

    def clear(self):
        with self.lock:
            self.corpus = {"schema": ledger.CORPUS_SCHEMA, "papers": []}
            self._save(self.corpus_path, self.corpus)
            self._reset_derived()
        return self.state()

    def upload(self, name, data):
        filename = pathlib.PurePath(name).name
        suffix = pathlib.Path(filename).suffix.lower()
        if suffix not in (".txt", ".md", ".docx"):
            raise ValueError("只支持 .txt / .md / .docx")
        if not data:
            raise ValueError("文件是空的")
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / filename
            target.write_bytes(data)
            paper = ledger.parse_paper_file(target)
        with self.lock:
            papers = [item for item in self.corpus.get("papers") or [] if item["paper_id"] != paper["paper_id"]]
            papers.append(paper)
            papers.sort(key=lambda item: str(item["paper_id"]))
            self.corpus = {"schema": ledger.CORPUS_SCHEMA, "papers": papers}
            self._save(self.corpus_path, self.corpus)
            self._reset_derived()
        return self.state()

    def demo(self):
        examples = ROOT / "examples"
        papers = [ledger.parse_paper_file(path) for path in sorted((examples / "corpus").glob("*.txt"))]
        if not papers:
            raise ValueError("找不到示例题库")
        claims_text = (examples / "claims.json").read_text(encoding="utf-8")
        with self.lock:
            self.corpus = {"schema": ledger.CORPUS_SCHEMA, "papers": papers}
            self._save(self.corpus_path, self.corpus)
            self._reset_derived()
        return {"state": self.state(), "claims": claims_text}

    def check(self, text):
        if not (self.corpus.get("papers") or []):
            raise ValueError("先上传题库，再对账")
        try:
            document = json.loads(text)
        except ValueError as exc:
            raise ValueError("claims.json 不是合法 JSON: %s" % exc)
        if not isinstance(document, dict) or not isinstance(document.get("claims"), list):
            raise ValueError("claims.json 必须是 {claims: [...]} 形式的对象")
        index = ledger.build_index(self.corpus)
        checked = []
        ids = set()
        for raw in document["claims"]:
            cid = str(raw.get("id") or "").strip()
            if not cid:
                raise ValueError("有一条结论没有 id")
            if cid in ids:
                raise ValueError("结论 id 重复: " + cid)
            ids.add(cid)
            if not str(raw.get("point") or "").strip():
                raise ValueError("结论 %s 缺少 point" % cid)
            item = {key: raw.get(key) for key in ("id", "point", "paper", "year", "no", "quote", "score")}
            item["id"] = cid
            item.update(ledger.check_claim(raw, index))
            checked.append(item)
        absent = []
        for position, raw in enumerate(document.get("absent") or [], 1):
            absent.append({
                "id": str(raw.get("id") or ("absent-%d" % position)),
                "point": str(raw.get("point") or ""),
                "note": str(raw.get("note") or "材料中未见"),
            })
        verified = [claim for claim in checked if claim["status"] == "verified"]
        document_out = {
            "schema": ledger.LEDGER_SCHEMA,
            "generated_at": ledger.now(),
            "meta": document.get("meta") or {},
            "corpus": {"papers": len(index["papers"]), "questions": len(index["flat"])},
            "summary": {
                "claims": len(checked),
                "claims_verified": len(verified),
                "claims_suspect": len(checked) - len(verified),
                "absent": len(absent),
                "warnings": sum(len(claim["warnings"]) for claim in checked),
            },
            "claims": checked,
            "absent": absent,
        }
        with self.lock:
            self.ledger = document_out
            self._save(self.ledger_path, document_out)
        return self._check_view()

    def _check_view(self):
        document = self.ledger or {}
        return {
            "summary": document.get("summary") or {},
            "claims": [
                {
                    "id": claim["id"],
                    "point": claim["point"],
                    "quote": claim.get("quote") or "",
                    "ref": ledger._ref_text(claim),
                    "status": claim["status"],
                    "reasons": [ledger.REASON_TEXT.get(reason, reason) for reason in claim["reasons"]],
                    "warnings": [warning["code"] for warning in claim["warnings"]],
                }
                for claim in document.get("claims") or []
            ],
            "absent": document.get("absent") or [],
        }

    def last_check(self):
        if not self.ledger:
            return {"need_ledger": True, "summary": {}, "claims": [], "absent": []}
        return self._check_view()

    def report(self, top):
        if not self.ledger:
            raise ValueError("先对账，再出表")
        rank = ledger.build_rank(self.ledger.get("claims") or [], top=top, must_pct=30, under_pct=40)
        rows = []
        for row in rank["rows"]:
            scores = "+".join(ledger.fmt_num(score) for score in row["scores"])
            total = ledger.fmt_num(row["weight"])
            score_text = ("%s=%s" % (scores, total)) if len(row["scores"]) > 1 else total
            if row["est"]:
                score_text += "(估)"
            rows.append({
                "rank": row["rank"],
                "tier": row["tier"],
                "point": row["point"],
                "refs": "; ".join("%s#%s" % (ref["paper"], ref["no"]) for ref in row["refs"]),
                "quotes": " / ".join(ref["quote"] for ref in row["refs"] if ref["quote"]),
                "score": score_text,
            })
        return {
            "rows": rows,
            "must": rank["must"],
            "under": rank["under"],
            "markdown": ledger.render_report(rank, self.ledger),
            "summary": self.ledger.get("summary"),
        }


    def _verified(self):
        return {
            claim["id"]: claim
            for claim in (self.ledger or {}).get("claims") or []
            if claim.get("status") == "verified"
        }

    def review_view(self):
        if not self.ledger:
            return {
                "rows": [],
                "untested": [],
                "wrong_points": 0,
                "measured_points": 0,
                "markdown": "",
                "need_ledger": True,
            }
        rank = ledger.build_rank(self.ledger.get("claims") or [], top=20, must_pct=30, under_pct=40)
        plan = ledger.review_plan(rank["rows"], (self.mistakes or {}).get("items") or [])
        rows = []
        for row in plan["rows"]:
            rows.append({
                "point": row["point"],
                "tier": row["tier"],
                "refs": "; ".join("%s#%s" % (ref["paper"], ref["no"]) for ref in row["refs"]),
                "wrong": row["wrong"],
                "right": row["right"],
                "priority": ledger.fmt_num(round(row["priority"], 2)),
            })
        untested = [
            {
                "point": row["point"],
                "refs": "; ".join("%s#%s" % (ref["paper"], ref["no"]) for ref in row["refs"]),
            }
            for row in plan["untested"]
        ]
        return {
            "rows": rows,
            "untested": untested,
            "wrong_points": plan["wrong_points"],
            "measured_points": plan["measured_points"],
            "markdown": ledger.render_review(plan),
        }

    def quiz_view(self):
        if not self.ledger:
            return {"quizzes": [], "review": self.review_view(), "markdown": "", "need_ledger": True}
        document = self.quiz
        if not document:
            return {"quizzes": [], "review": self.review_view(), "markdown": ""}
        points = {cid: claim["point"] for cid, claim in self._verified().items()}
        markdown = ledger.render_sheet(document, points)
        quizzes = []
        for item in document.get("quizzes") or []:
            quizzes.append({
                "claim_id": item["claim_id"],
                "point": points.get(item["claim_id"], ""),
                "questions": [
                    {"q": question["q"], "a": question["a"]}
                    for question in item.get("questions") or []
                ],
            })
        return {"quizzes": quizzes, "review": self.review_view(), "markdown": markdown}

    def set_quiz(self, text):
        if not self.ledger:
            raise ValueError("先对账，才能出自测题")
        try:
            document = json.loads(text)
        except ValueError as exc:
            raise ValueError("quiz.json 不是合法 JSON: %s" % exc)
        if not isinstance(document, dict) or not isinstance(document.get("quizzes"), list):
            raise ValueError("quiz.json 必须有 quizzes 数组")
        points = {cid: claim["point"] for cid, claim in self._verified().items()}
        ledger.render_sheet(document, points)
        with self.lock:
            self.quiz = document
            self._save(self.quiz_path, document)
        return self.quiz_view()

    def grade(self, payload):
        if not self.ledger:
            raise ValueError("先对账，才能记成绩")
        results = payload.get("results") or []
        if not results:
            raise ValueError("没有提交任何结果")
        previous = self.mistakes or {"items": []}
        items = ledger.grade_results(self._verified(), results, previous)
        ordered = sorted(items.values(), key=lambda item: (-item["wrong"], -item["right"], item["point"]))
        document = {"schema": ledger.MISTAKES_SCHEMA, "generated_at": ledger.now(), "items": ordered}
        with self.lock:
            self.mistakes = document
            self._save(self.mistakes_path, document)
        return {"mistakes": document, "review": self.review_view()}


class Handler(BaseHTTPRequestHandler):
    store = None

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send_file(WEB_FILE, "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send_json(self.store.state())
            elif path == "/api/quiz":
                self._send_json(self.store.quiz_view())
            elif path == "/api/check":
                self._send_json(self.store.last_check())
            elif path == "/api/review":
                self._send_json(self.store.review_view())
            elif path == "/api/quiz-example":
                self._send_text(ROOT / "examples" / "quiz.json")
            else:
                self._send_json({"error": "抓到一条不存在的请求"}, 404)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self._send_json({"error": "服务器错误: %s" % exc}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/upload":
                name = parse_qs(parsed.query).get("name", ["上传.txt"])[0]
                self._send_json(self.store.upload(name, self._read_body()))
            elif path == "/api/check":
                self._send_json(self.store.check(self._read_body().decode("utf-8", "ignore")))
            elif path == "/api/report":
                body = self._read_body()
                options = json.loads(body) if body else {}
                self._send_json(self.store.report(int(options.get("top") or 20)))
            elif path == "/api/quiz":
                self._send_json(self.store.set_quiz(self._read_body().decode("utf-8", "ignore")))
            elif path == "/api/grade":
                body = self._read_body()
                self._send_json(self.store.grade(json.loads(body) if body else {}))
            elif path == "/api/review":
                self._send_json(self.store.review_view())
            elif path == "/api/clear":
                self._send_json(self.store.clear())
            elif path == "/api/demo":
                self._send_json(self.store.demo())
            else:
                self._send_json({"error": "抓到一条不存在的请求"}, 404)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self._send_json({"error": "服务器错误: %s" % exc}, 500)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("文件太大，上限 16MB")
        return self.rfile.read(length) if length else b""

    def _send_json(self, document, status=200):
        payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_text(self, path):
        try:
            payload = path.read_text(encoding="utf-8").encode("utf-8")
        except OSError:
            self._send_json({"error": "找不到示例题"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path, content_type):
        try:
            payload = path.read_bytes()
        except OSError as exc:
            self._send_json({"error": "读不到页面文件: %s" % exc}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return


def main(argv=None):
    parser = argparse.ArgumentParser(prog="serve.py", description="考点账本 本地网页版")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data", default=str(ROOT / "data"))
    args = parser.parse_args(argv)
    Handler.store = Store(args.data)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("考点账本 %s -> http://%s:%d" % (ledger.VERSION, args.host, args.port))
    if args.host not in ("127.0.0.1", "localhost"):
        print("注意: 绑定到 %s，同网段的设备都能访问，确定吗" % args.host)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(chr(10) + "已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
