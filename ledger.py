#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考点账本 kaodian-ledger：让 AI 划的每一条重点都带可机器校验的出处。

命令:
  parse       真题文本 -> corpus.json
  import-csv  旧录入稿(CSV) -> corpus.json + claims.json
  check       结论 + 题库 -> ledger.json(逐条对账,分出存疑区)
  rank        已核实结论 -> report.md(Top N,三档)
  sample      从 Top N 随机抽 3-5 条,生成人工核对表
  calibrate   人工核对结果 -> 命中率;低于阈值整体降级为"仅参考"
  sheet       自测题 -> 闭卷题面 + 独立答案页

只依赖 Python 标准库。
"""

import argparse
import csv
import json
import pathlib
import random
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from statistics import median

VERSION = "0.7.2"

CORPUS_SCHEMA = "kaodian-ledger/corpus@1"
LEDGER_SCHEMA = "kaodian-ledger/ledger@1"
SAMPLE_SCHEMA = "kaodian-ledger/samples@1"
CALIB_SCHEMA = "kaodian-ledger/calibration@1"
COVERAGE_SCHEMA = "kaodian-ledger/coverage@1"
MISTAKES_SCHEMA = "kaodian-ledger/mistakes@1"
QUIZ_SCHEMA = "kaodian-ledger/quiz@1"

TIER_MUST = "必背"
TIER_UNDER = "理解"
TIER_DROP = "可放弃"

Q_START = re.compile(r"^\s*(?:[\[【]\s*(\d{1,3})\s*[\]】]|(\d{1,3})\s*[.、．)）])\s*(.*)$")
OPTION_LINE = re.compile(r"^\s*\d{1,3}\s*[.、．]\s*[A-D][.、．]")
AUTO_MARK = chr(1)
DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
META_LINE = re.compile(r"^\s*@([A-Za-z_]+)\s*[:：]\s*(.*?)\s*$")
SCORE_RE = re.compile(r"[（(\[【]\s*(\d+(?:\.\d+)?)\s*分\s*[)）\]】]")
SECTION_LINE = re.compile(r"^\s*([一二三四五六七八九十]+)\s*[、.．]\s*(.*)$")
PER_Q_SCORE = re.compile(r"每题\s*(\d+(?:\.\d+)?)\s*分")
TOTAL_SCORE = re.compile(r"共\s*(\d+(?:\.\d+)?)\s*分")
YEAR_RE = re.compile(r"(?:19|20)\d{2}")

REASON_TEXT = {
    "paper_not_found": "题库里找不到这份卷子(卷号或年份对不上)",
    "question_not_found": "该卷里找不到这个题号",
    "empty_quote": "引文为空",
    "quote_not_found": "引文在该题题干里找不到",
    "missing_ref": "缺卷号/年份或题号",
}

WARN_TEXT = {
    "quote_too_long": "引文超过 10 字",
    "weak_quote": "同一引文在题库里出现多次,区分度弱",
    "score_mismatch": "分值与会题库不一致",
    "score_unverified": "题库未标分值,沿用结论里的分值",
    "ambiguous_ref": "同一年份有多卷,按第一份核对",
}


# ---------------------------------------------------------------- 基础工具

def die(message):
    print("[错误] " + str(message), file=sys.stderr)
    raise SystemExit(1)


def now():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def load_json(path):
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        die("读不到 %s: %s" % (path, exc))
    try:
        return json.loads(text)
    except ValueError as exc:
        die("%s 不是合法 JSON: %s" % (path, exc))


def write_text(path, text):
    target = pathlib.Path(path)
    if str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def write_json(path, obj):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def norm(value):
    """匹配用归一化:NFKC + 大小写折叠 + 只保留字母数字(去掉空格与全部标点)。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def to_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("分", "").replace(",", "").replace("，", "")
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    match = YEAR_RE.search(text)
    if match:
        return int(match.group(0))
    try:
        return int(float(text))
    except ValueError:
        return None


def _num(value):
    number = float(value)
    return int(number) if number.is_integer() else number


def fmt_num(value):
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return ("%g" % number)


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _same_no(a, b):
    if str(a).strip() == str(b).strip():
        return True
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def _ref_text(claim):
    matched = claim.get("matched") or {}
    paper = matched.get("paper") or claim.get("paper") or claim.get("year") or "?"
    no = matched.get("no") or claim.get("no") or "?"
    return "%s#%s" % (paper, no)


def load_aliases(path):
    """合并表: {规范名: [同义写法, ...]}。不给文件就返回空表。"""
    if not path:
        return {"keys": {}, "labels": {}}
    target = pathlib.Path(path)
    if not target.is_file():
        die("找不到合并表: " + str(target))
    document = load_json(target)
    if not isinstance(document, dict):
        die("aliases.json 必须是 {规范名: [同义写法...]} 的对象")
    keys = {}
    labels = {}
    for canonical, variants in document.items():
        canonical_key_text = norm(canonical)
        if not canonical_key_text:
            continue
        keys[canonical_key_text] = canonical_key_text
        labels[canonical_key_text] = str(canonical)
        if isinstance(variants, str):
            variants = [variants]
        for variant in variants or []:
            variant_key = norm(variant)
            if variant_key:
                keys[variant_key] = canonical_key_text
    return {"keys": keys, "labels": labels}


def canonical_key(point, aliases):
    key = norm(point)
    return (aliases or {}).get("keys", {}).get(key, key)


def canonical_label(point, aliases):
    key = canonical_key(point, aliases)
    labels = (aliases or {}).get("labels", {})
    return labels.get(key, point or "")


# ---------------------------------------------------------------- 入库

def docx_to_text(path):
    """.docx 转文本。Word 自动编号不在正文里，用 \x01 标出来交给 parser 合成题号。"""
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("读不了 .docx: %s" % exc)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("文档 XML 坏了: %s" % exc)
    lines = []
    for paragraph in root.iter(DOCX_NS + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(DOCX_NS + "t"))
        numbered = False
        props = paragraph.find(DOCX_NS + "pPr")
        if props is not None:
            num_props = props.find(DOCX_NS + "numPr")
            if num_props is not None:
                num_id = num_props.find(DOCX_NS + "numId")
                if num_id is not None and num_id.get(DOCX_NS + "val") not in (None, "0"):
                    numbered = True
        lines.append((AUTO_MARK if numbered else "") + text)
    return "\n".join(lines)


def _next_number(section):
    highest = 0
    for question in section["questions"]:
        try:
            highest = max(highest, int(question["no"]))
        except (TypeError, ValueError):
            continue
    return highest + 1


def parse_paper_text(text, fallback_id=None, year_hint=None):
    meta = {}
    sections = []
    current_section = None
    current_question = None
    seen_question = False

    def open_section(label, header):
        section = {
            "label": label,
            "header": header,
            "per_score": None,
            "total_score": None,
            "questions": [],
            "intro": [],
        }
        match = PER_Q_SCORE.search(header)
        if match is not None:
            section["per_score"] = _num(match.group(1))
        match = TOTAL_SCORE.search(header)
        if match is not None:
            section["total_score"] = _num(match.group(1))
        else:
            match = SCORE_RE.search(header)
            if match is not None:
                section["total_score"] = _num(match.group(1))
        sections.append(section)
        return section

    for line in text.splitlines():
        auto = line.startswith(AUTO_MARK)
        if auto:
            line = line[len(AUTO_MARK):]
        match = META_LINE.match(line)
        if match is not None and not sections and not seen_question:
            meta[match.group(1).lower()] = match.group(2).strip()
            continue
        match = SECTION_LINE.match(line)
        if match is not None:
            current_question = None
            current_section = open_section(match.group(1), match.group(2).strip())
            continue
        # 选项行被误编号(如 "2.A. 骨膜 B. 骨质")时,归到上一题,不另起一题
        if not auto and OPTION_LINE.match(line) and current_question is not None:
            current_question["text"] = (current_question["text"] + "\n" + line).strip()
            continue
        match = Q_START.match(line)
        if match is not None or auto:
            if current_section is None:
                current_section = open_section(None, "")
            if match is not None:
                raw_no = match.group(1) if match.group(1) is not None else match.group(2)
                no = str(int(raw_no))
                body = (match.group(3) or "").strip()
            else:
                no = str(_next_number(current_section))
                body = line.strip()
            current_question = {"no": no, "text": body}
            current_section["questions"].append(current_question)
            seen_question = True
            continue
        if current_question is not None:
            current_question["text"] = (current_question["text"] + "\n" + line).strip()
        elif current_section is not None and line.strip():
            current_section["intro"].append(line.strip())

    # 有些大题(简答/论述)整段没有题号,收成一道题
    for section in sections:
        if section["questions"] or not section["label"] or not section["intro"]:
            continue
        block = "\n".join(section["intro"]).strip()
        if block:
            section["questions"].append({"no": "1", "text": block})

    questions = []
    for section in sections:
        for question in section["questions"]:
            match = SCORE_RE.search(question["text"])
            if match is not None:
                question["score"] = _num(match.group(1))
            elif section["per_score"] is not None:
                question["score"] = section["per_score"]
            elif len(section["questions"]) == 1 and section["total_score"] is not None:
                question["score"] = section["total_score"]
            else:
                question["score"] = None
            question["section_label"] = section["label"]
            questions.append(question)

    if not questions:
        raise ValueError("没有识别到题目(题号要写在行首,如 [1] 或 1.)")

    # 分节卷里每节题号都从 1 开始,加节号前缀避免撞号
    if len({question["no"] for question in questions}) != len(questions):
        for question in questions:
            label = question.get("section_label")
            if label:
                question["no"] = "%s.%s" % (label, question["no"])

    seen = set()
    for question in questions:
        if question["no"] in seen:
            raise ValueError("题号重复: 同一卷里有两个相同的题号 " + question["no"])
        seen.add(question["no"])
        if not question["text"]:
            raise ValueError("第 %s 题题干为空" % question["no"])

    paper_id = meta.get("paper") or fallback_id
    if not paper_id:
        raise ValueError("缺少卷号:写 @paper 或用文件名")
    year = meta.get("year") or year_hint
    return {
        "paper_id": str(paper_id),
        "year": to_int(year),
        "subject": meta.get("subject"),
        "questions": [
            {"no": question["no"], "text": question["text"], "score": question["score"]}
            for question in questions
        ],
    }


def parse_paper_file(path):
    path = pathlib.Path(path)
    if path.suffix.lower() == ".docx":
        try:
            text = docx_to_text(path)
        except ValueError as exc:
            raise ValueError("%s: %s" % (path.name, exc))
    else:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ValueError("读不到文件: %s" % exc)
    match = YEAR_RE.search(path.stem)
    year_hint = match.group(0) if match else None
    try:
        return parse_paper_text(text, fallback_id=path.stem, year_hint=year_hint)
    except ValueError as exc:
        raise ValueError("%s: %s" % (path.name, exc))


def cmd_parse(args):
    target = pathlib.Path(args.corpus_dir)
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted([
            item for item in target.iterdir()
            if item.suffix.lower() in (".txt", ".md", ".docx")
        ])
        legacy = sorted(item for item in target.iterdir() if item.suffix.lower() == ".doc")
        if legacy:
            print("[注意] 跳过 %d 个老式 .doc(读不了,请在 WPS/Word 里另存为 .docx):" % len(legacy))
            for item in legacy[:3]:
                print("  " + item.name)
    else:
        die("找不到这个路径: " + str(target))
    if not files:
        die("没有可读的文件(.txt / .md / .docx): " + str(target))
    papers = []
    ids = set()
    for path in files:
        try:
            paper = parse_paper_file(path)
        except ValueError as exc:
            die(exc)
        if paper["paper_id"] in ids:
            die("卷号重复: %s(检查 @paper 或文件名)" % paper["paper_id"])
        ids.add(paper["paper_id"])
        papers.append(paper)
    corpus = {"schema": CORPUS_SCHEMA, "papers": papers}
    write_json(args.out, corpus)
    total = sum(len(p["questions"]) for p in papers)
    print("入库: %d 卷 / %d 题 -> %s" % (len(papers), total, args.out))
    for paper in papers:
        known = sum(1 for q in paper["questions"] if q["score"] is not None)
        print("  %-14s %3d 题  已知分值 %d 题  年份 %s" % (
            paper["paper_id"], len(paper["questions"]), known, paper["year"] or "?"))
    short = [(p["paper_id"], q) for p in papers for q in p["questions"] if len(q["text"]) < 4]
    if short:
        print("[注意] 有 %d 道题题干过短,可能切错了题:" % len(short))
        for paper_id, question in short[:5]:
            print("  %s#%s: %r" % (paper_id, question["no"], question["text"]))


def _pick(row, *names):
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name]).strip()
    return ""


def cmd_import_csv(args):
    path = pathlib.Path(args.csv_path)
    if not path.is_file():
        die("找不到 CSV: " + str(path))
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        die("CSV 是空的")
    papers = {}
    claims = []
    for index, row in enumerate(rows, 2):
        year_raw = _pick(row, "年份", "year")
        no = _pick(row, "题号", "no")
        stem = _pick(row, "题干原文", "题干", "stem")
        score_raw = _pick(row, "分值", "score")
        point = _pick(row, "知识点", "point")
        quote = _pick(row, "原文摘录", "摘录", "quote")
        if not no:
            die("第 %d 行缺题号" % index)
        if not stem:
            die("第 %d 行缺题干原文(没题干就没法对账)" % index)
        if not year_raw:
            die("第 %d 行缺年份" % index)
        paper = papers.get(year_raw)
        if paper is None:
            paper = {"paper_id": year_raw, "year": to_int(year_raw),
                     "subject": args.subject, "questions": []}
            papers[year_raw] = paper
        if any(q["no"] == no for q in paper["questions"]):
            die("第 %d 行: %s 里题号 %s 重复" % (index, year_raw, no))
        paper["questions"].append({"no": no, "text": stem, "score": to_number(score_raw)})
        if point:
            claims.append({
                "id": "c%d" % (len(claims) + 1),
                "point": point,
                "paper": year_raw,
                "no": no,
                "quote": quote,
                "score": to_number(score_raw),
            })
    corpus = {"schema": CORPUS_SCHEMA, "papers": list(papers.values())}
    write_json(args.out, corpus)
    claims_doc = {"meta": {"subject": args.subject or "", "mode": "题库型",
                           "source": path.name}, "claims": claims, "absent": []}
    write_json(args.claims_out, claims_doc)
    total = sum(len(p["questions"]) for p in papers.values())
    print("导入: %d 卷 / %d 题 / %d 条带知识点的结论" % (len(papers), total, len(claims)))
    print("  题库 -> %s" % args.out)
    print("  结论 -> %s" % args.claims_out)
    print("注意: 原文摘录为空的行会进存疑区,先补摘录再对账。")


# ---------------------------------------------------------------- 对账

def build_index(corpus):
    papers = corpus.get("papers") or []
    if not isinstance(papers, list):
        die("corpus.json 格式不对: 缺 papers 数组")
    by_id = {}
    flat = []
    for paper in papers:
        by_id[paper["paper_id"]] = paper
        for question in paper.get("questions") or []:
            flat.append((paper, question, norm(question.get("text"))))
    return {"papers": papers, "by_id": by_id, "flat": flat}


def resolve_papers(index, claim):
    paper_id = claim.get("paper")
    if paper_id:
        paper = index["by_id"].get(str(paper_id))
        if paper is not None:
            return [paper]
    year = to_int(claim.get("year"))
    if year is not None:
        return [p for p in index["papers"] if to_int(p.get("year")) == year]
    return []


def check_claim(claim, index):
    result = {"status": "suspect", "reasons": [], "warnings": [],
              "matched": None, "score_used": None, "score_source": None}
    quote_raw = claim.get("quote")
    quote = "" if quote_raw is None else str(quote_raw).strip()
    quote_norm = norm(quote)
    if not quote_norm:
        result["reasons"].append("empty_quote")
        return result
    has_cjk = any(chr(0x4E00) <= ch <= chr(0x9FFF) for ch in quote)
    limit = 10 if has_cjk else 40
    if len(quote) > limit:
        result["warnings"].append({"code": "quote_too_long", "detail": len(quote)})

    papers = resolve_papers(index, claim)
    if not papers:
        result["reasons"].append("paper_not_found")
        return result

    no = claim.get("no")
    if no is None or str(no).strip() == "":
        result["reasons"].append("missing_ref")
        return result
    no_text = str(no).strip()

    candidates = []
    for paper in papers:
        for question in paper.get("questions") or []:
            if _same_no(question.get("no"), no_text):
                candidates.append((paper, question))
    if not candidates:
        result["reasons"].append("question_not_found")
        return result
    involved = {paper["paper_id"] for paper, _ in candidates}
    if len(involved) > 1:
        result["warnings"].append({"code": "ambiguous_ref", "detail": len(involved)})

    hit = None
    for paper, question in candidates:
        if quote_norm in norm(question.get("text")):
            hit = (paper, question)
            break
    if hit is None:
        result["reasons"].append("quote_not_found")
        return result

    paper, question = hit
    result["matched"] = {
        "paper": paper["paper_id"],
        "year": paper.get("year"),
        "no": question.get("no"),
        "text": question.get("text") or "",
        "score": question.get("score"),
    }
    result["status"] = "verified"

    occurrences = sum(1 for _, _, text in index["flat"] if quote_norm in text)
    if occurrences >= 3:
        result["warnings"].append({"code": "weak_quote", "detail": occurrences})

    claim_score = to_number(claim.get("score"))
    question_score = to_number(question.get("score"))
    if claim_score is not None and question_score is not None:
        if abs(claim_score - question_score) > 1e-9:
            result["warnings"].append({
                "code": "score_mismatch",
                "detail": "结论 %s / 题库 %s" % (fmt_num(claim_score), fmt_num(question_score)),
            })
        result["score_used"] = question_score
        result["score_source"] = "question"
    elif claim_score is not None:
        result["warnings"].append({"code": "score_unverified", "detail": fmt_num(claim_score)})
        result["score_used"] = claim_score
        result["score_source"] = "claim"
    elif question_score is not None:
        result["score_used"] = question_score
        result["score_source"] = "question"
    return result


def cmd_check(args):
    corpus = load_json(args.corpus)
    document = load_json(args.claims)
    if not isinstance(document, dict) or not isinstance(document.get("claims"), list):
        die("claims.json 必须是 {claims: [...]} 形式的对象")
    index = build_index(corpus)
    checked = []
    ids = set()
    for raw in document.get("claims") or []:
        cid = str(raw.get("id") or "").strip()
        if not cid:
            die("有一条结论没有 id(对账需要对得上号)")
        if cid in ids:
            die("结论 id 重复: " + cid)
        ids.add(cid)
        if not str(raw.get("point") or "").strip():
            die("结论 %s 缺少 point" % cid)
        item = {key: raw.get(key) for key in ("id", "point", "paper", "year", "no", "quote", "score")}
        item["id"] = cid
        item.update(check_claim(raw, index))
        checked.append(item)

    absent_items = []
    for position, raw in enumerate(document.get("absent") or [], 1):
        absent_items.append({
            "id": str(raw.get("id") or ("absent-%d" % position)),
            "point": str(raw.get("point") or ""),
            "note": str(raw.get("note") or "材料中未见"),
        })

    verified = [c for c in checked if c["status"] == "verified"]
    warnings = sum(len(c["warnings"]) for c in checked)
    ledger = {
        "schema": LEDGER_SCHEMA,
        "generated_at": now(),
        "meta": document.get("meta") or {},
        "corpus": {"papers": len(index["papers"]), "questions": len(index["flat"])},
        "summary": {
            "claims": len(checked),
            "claims_verified": len(verified),
            "claims_suspect": len(checked) - len(verified),
            "absent": len(absent_items),
            "warnings": warnings,
        },
        "claims": checked,
        "absent": absent_items,
    }
    write_json(args.out, ledger)
    print("对账完成: %d 条 -> 可核实 %d / 存疑 %d / 材料中未见 %d" % (
        len(checked), len(verified), len(checked) - len(verified), len(absent_items)))
    suspect = [c for c in checked if c["status"] != "verified"]
    if suspect:
        print("存疑区:")
        for claim in suspect:
            reasons = "; ".join(REASON_TEXT.get(r, r) for r in claim["reasons"])
            quote = claim.get("quote") or ""
            print("  - %s [%s] %s | %s -> %s" % (
                claim["id"], claim["point"], _ref_text(claim), quote, reasons))
    if warnings:
        print("警告 %d 条(不影响核实,细节见 ledger.json)" % warnings)
    print("-> " + str(args.out))


# ---------------------------------------------------------------- 排序

def build_rank(claims, top=20, must_pct=30, under_pct=40, aliases=None):
    verified = [c for c in claims if c.get("status") == "verified"]
    known = {}
    for claim in verified:
        score = claim.get("score_used")
        if score is not None:
            paper = claim["matched"]["paper"]
            known.setdefault(paper, []).append(float(score))
    all_known = [s for values in known.values() for s in values]

    groups = {}
    for claim in verified:
        key = canonical_key(claim.get("point"), aliases)
        if not key:
            continue
        group = groups.setdefault(key, {"point": canonical_label(claim.get("point"), aliases), "refs": []})
        ref = {
            "paper": claim["matched"]["paper"],
            "no": claim["matched"]["no"],
            "quote": claim.get("quote") or "",
            "score": claim.get("score_used"),
        }
        if not any(r["paper"] == ref["paper"] and r["no"] == ref["no"] for r in group["refs"]):
            group["refs"].append(ref)

    rows = []
    for group in groups.values():
        estimated = False
        scores = []
        for ref in group["refs"]:
            score = ref["score"]
            if score is None:
                pool = known.get(ref["paper"]) or all_known
                score = median(pool) if pool else 1.0
                estimated = True
            scores.append(float(score))
        rows.append({
            "point": group["point"],
            "refs": group["refs"],
            "scores": scores,
            "count": len(group["refs"]),
            "weight": sum(scores),
            "est": estimated,
        })

    rows.sort(key=lambda r: (-r["weight"], -r["count"], r["point"]))
    rows = rows[: max(0, int(top))]
    count = len(rows)
    must = max(1, int(count * must_pct / 100.0 + 0.5)) if count else 0
    must = min(must, count)
    under = int(count * under_pct / 100.0 + 0.5)
    under = max(0, min(under, count - must))
    for position, row in enumerate(rows):
        row["rank"] = position + 1
        if position < must:
            row["tier"] = TIER_MUST
        elif position < must + under:
            row["tier"] = TIER_UNDER
        else:
            row["tier"] = TIER_DROP
    return {"rows": rows, "must": must, "under": under, "points": len(groups)}


def _pct(value):
    if value is None:
        return "?"
    return "%d%%" % round(float(value) * 100)


def render_report(rank, ledger, calibration=None):
    summary = ledger.get("summary") or {}
    corpus = ledger.get("corpus") or {}
    meta = ledger.get("meta") or {}
    lines = ["# 考点账本 · Top %d" % len(rank["rows"]), ""]
    if meta.get("subject"):
        lines.append("- 科目: " + str(meta["subject"]))
    if meta.get("mode"):
        lines.append("- 模式: " + str(meta["mode"]))
    if corpus:
        lines.append("- 语料: %d 卷 / %d 题" % (int(corpus.get("papers", 0)), int(corpus.get("questions", 0))))
    lines.append("- 对账: %d 条 -> 可核实 %d / 存疑 %d / 材料中未见 %d" % (
        int(summary.get("claims", 0)),
        int(summary.get("claims_verified", 0)),
        int(summary.get("claims_suspect", 0)),
        int(summary.get("absent", 0)),
    ))
    lines.append("- 生成: " + str(ledger.get("generated_at") or ""))
    lines.append("")

    if calibration:
        if calibration.get("status") == "reference_only":
            lines.append("> [!] 命中率 %s(%d/%d),低于 %s 阈值:以下排序仅供参考,不能用来筛掉任何结论。" % (
                _pct(calibration.get("hit_rate")),
                int(calibration.get("hits", 0)),
                int(calibration.get("checked", 0)),
                _pct(calibration.get("threshold")),
            ))
            lines.append("")
        elif calibration.get("status") == "ok":
            lines.append("> 校准通过:人核命中率 %s(%d/%d),排序可用于筛选。" % (
                _pct(calibration.get("hit_rate")),
                int(calibration.get("hits", 0)),
                int(calibration.get("checked", 0)),
            ))
            lines.append("")

    lines.append("| # | 档 | 知识点 | 出处 | 原题摘录 | 分值 |")
    lines.append("|---:|---|---|---|---|---|")
    for row in rank["rows"]:
        refs = "; ".join("%s#%s" % (ref["paper"], ref["no"]) for ref in row["refs"])
        quotes = " / ".join(ref["quote"] for ref in row["refs"] if ref["quote"])
        scores = "+".join(fmt_num(score) for score in row["scores"])
        total = fmt_num(row["weight"])
        score_text = "%s=%s" % (scores, total) if len(row["scores"]) > 1 else total
        if row["est"]:
            score_text += "(估)"
        lines.append("| %d | %s | %s | %s | %s | %s |" % (
            row["rank"], row["tier"], cell(row["point"]), cell(refs), cell(quotes), score_text))
    lines.append("")

    drop = max(0, len(rank["rows"]) - rank["must"] - rank["under"])
    lines.append("分档: 必背 %d / 理解 %d / 可放弃 %d;共 %d 个知识点,只统计已核实的结论。" % (
        rank["must"], rank["under"], drop, rank["points"]))
    lines.append("")
    lines.append("可放弃档请留 20-30% 时间兜底,专门应对反押题。")
    lines.append("")

    doubt = [c for c in (ledger.get("claims") or []) if c.get("status") != "verified"]
    absent = ledger.get("absent") or []
    lines.append("## 存疑区(不进排序)")
    lines.append("")
    if not doubt and not absent:
        lines.append("无。")
    else:
        lines.append("| id | 知识点 | 出处 | 引文 | 原因 |")
        lines.append("|---|---|---|---|---|")
        for claim in doubt:
            reasons = "; ".join(REASON_TEXT.get(reason, reason) for reason in claim.get("reasons") or [])
            lines.append("| %s | %s | %s | %s | %s |" % (
                cell(claim.get("id")), cell(claim.get("point")), cell(_ref_text(claim)),
                cell(claim.get("quote") or ""), reasons))
        for item in absent:
            lines.append("| %s | %s | - | - | %s |" % (
                cell(item.get("id")), cell(item.get("point")), cell(item.get("note") or "材料中未见")))
    lines.append("")
    if not calibration:
        lines.append("> 尚未做人工校准(第 5 步):以上排序未经抽样人核。")
    return "\n".join(lines) + "\n"


def cmd_rank(args):
    ledger = load_json(args.ledger)
    calibration = load_json(args.calibration) if args.calibration else None
    aliases = load_aliases(getattr(args, "aliases", None))
    rank = build_rank(ledger.get("claims") or [], top=args.top,
                      must_pct=args.must_pct, under_pct=args.under_pct, aliases=aliases)
    write_text(args.out, render_report(rank, ledger, calibration))
    counts = {TIER_MUST: 0, TIER_UNDER: 0, TIER_DROP: 0}
    for row in rank["rows"]:
        counts[row["tier"]] += 1
    print("出表: Top %d(必背 %d / 理解 %d / 可放弃 %d)-> %s" % (
        len(rank["rows"]), counts[TIER_MUST], counts[TIER_UNDER], counts[TIER_DROP], args.out))


# ---------------------------------------------------------------- 校准

def calibrate_decision(items, threshold=0.7):
    hits = 0
    misses = 0
    for item in items:
        result = str(item.get("result") or "").strip().lower()
        if result == "hit":
            hits += 1
        elif result == "miss":
            misses += 1
    checked = hits + misses
    if checked == 0:
        raise ValueError("没有已填写 hit / miss 的条目")
    rate = hits / checked
    ok = rate + 1e-9 >= threshold
    return {
        "hits": hits,
        "misses": misses,
        "checked": checked,
        "hit_rate": round(rate, 4),
        "threshold": threshold,
        "status": "ok" if ok else "reference_only",
        "note": ("命中率达标,排序可用于筛选。" if ok else
                 "命中率低于阈值:所有概率性结论降级为参考,只用于排序,不用于筛选。"),
    }


def cmd_sample(args):
    ledger = load_json(args.ledger)
    aliases = load_aliases(getattr(args, "aliases", None))
    rank = build_rank(ledger.get("claims") or [], top=args.top,
                      must_pct=args.must_pct, under_pct=args.under_pct, aliases=aliases)
    rows = rank["rows"]
    if not rows:
        die("没有可核实的结论,先对账")
    count = min(int(args.n), len(rows))
    picked = random.Random(args.seed).sample(rows, count)
    text_by_ref = {}
    for claim in ledger.get("claims") or []:
        if claim.get("status") != "verified" or not claim.get("matched"):
            continue
        key = (claim["matched"]["paper"], claim["matched"]["no"])
        text_by_ref.setdefault(key, claim["matched"].get("text") or "")
    items = []
    for row in picked:
        items.append({
            "point": row["point"],
            "rank": row["rank"],
            "tier": row["tier"],
            "weight": row["weight"],
            "refs": [{
                "ref": "%s#%s" % (ref["paper"], ref["no"]),
                "quote": ref["quote"],
                "question": text_by_ref.get((ref["paper"], ref["no"]), ""),
            } for ref in row["refs"]],
            "result": None,
        })
    document = {"schema": SAMPLE_SCHEMA, "generated_at": now(),
                "seed": args.seed, "items": items}
    write_json(args.out, document)
    print("抽样 %d 条(seed=%s)-> %s" % (count, args.seed, args.out))
    for item in items:
        print("  #%d %s(%s)" % (item["rank"], item["point"], item["tier"]))
    print("核对方法:翻原卷,确认这条知识点确实考在列出的题目里。是改 hit,不是改 miss。")
    if count < 3:
        print("[注意] 少于 3 条,命中率的置信度很低。")


def cmd_calibrate(args):
    document = load_json(args.samples)
    try:
        decision = calibrate_decision(document.get("items") or [], threshold=args.threshold)
    except ValueError as exc:
        die(exc)
    decision["schema"] = CALIB_SCHEMA
    decision["generated_at"] = now()
    decision["seed"] = document.get("seed")
    write_json(args.out, decision)
    verdict = "通过" if decision["status"] == "ok" else "未达标,整体降级为仅参考"
    print("命中率: %d/%d = %s(阈值 %s)-> %s" % (
        decision["hits"], decision["checked"], _pct(decision["hit_rate"]),
        _pct(decision["threshold"]), verdict))
    print("-> " + str(args.out))


# ---------------------------------------------------------------- 自测

def render_sheet(quiz_doc, points_by_id):
    quizzes = quiz_doc.get("quizzes")
    if not isinstance(quizzes, list) or not quizzes:
        raise ValueError("quiz.json 里没有 quizzes")
    seen = set()
    for item in quizzes:
        cid = str(item.get("claim_id") or "").strip()
        if not cid:
            raise ValueError("有一条自测题没有 claim_id")
        if cid not in points_by_id:
            raise ValueError("claim_id %s 不是已核实的结论:自测题必须挂回一条已核实重点" % cid)
        if cid in seen:
            raise ValueError("claim_id 重复: " + cid)
        seen.add(cid)
        questions = item.get("questions") or []
        if not questions:
            raise ValueError("%s 没有题目" % cid)
        for position, question in enumerate(questions, 1):
            if not str(question.get("q") or "").strip():
                raise ValueError("%s 第 %d 题缺题干" % (cid, position))
            if not str(question.get("a") or "").strip():
                raise ValueError("%s 第 %d 题缺答案" % (cid, position))
    lines = ["# 闭卷自测", "", "合上书。做完再看最后一页。", ""]
    for index, item in enumerate(quizzes, 1):
        lines.append("## %d. %s" % (index, points_by_id[item["claim_id"]]))
        lines.append("")
        for position, question in enumerate(item["questions"], 1):
            lines.append("%d. %s" % (position, str(question["q"]).strip()))
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("# 答案")
    lines.append("")
    for index, item in enumerate(quizzes, 1):
        lines.append("## %d. %s" % (index, points_by_id[item["claim_id"]]))
        lines.append("")
        for position, question in enumerate(item["questions"], 1):
            lines.append("%d. %s" % (position, str(question["a"]).strip()))
        lines.append("")
    return "\n".join(lines) + "\n"


def cmd_sheet(args):
    quiz = load_json(args.quiz)
    ledger = load_json(args.ledger)
    points_by_id = {claim["id"]: claim.get("point") or ""
                    for claim in (ledger.get("claims") or [])
                    if claim.get("status") == "verified"}
    try:
        text = render_sheet(quiz, points_by_id)
    except ValueError as exc:
        die(exc)
    write_text(args.out, text)
    total = sum(len(item.get("questions") or []) for item in quiz["quizzes"])
    print("自测卷: %d 个知识点 / %d 道题,答案已排到最后 -> %s" % (
        len(quiz["quizzes"]), total, args.out))


# ---------------------------------------------------------------- 模考覆盖率

def coverage_analysis(rows, actual_points, threshold=0.6, aliases=None, doubt_keys=None):
    predicted = {}
    for row in rows:
        predicted[canonical_key(row.get("point"), aliases)] = row
    hits = []
    misses = []
    by_tier = {TIER_MUST: 0, TIER_UNDER: 0, TIER_DROP: 0}
    for item in actual_points:
        if isinstance(item, dict):
            point = str(item.get("point") or "")
            ref = str(item.get("ref") or "")
        else:
            point = str(item)
            ref = ""
        if not norm(point):
            continue
        key = canonical_key(point, aliases)
        row = predicted.get(key)
        if row is None:
            misses.append({
                "point": point,
                "ref": ref,
                "in_doubt": bool(doubt_keys and key in doubt_keys),
            })
            continue
        by_tier[row["tier"]] += 1
        hits.append({"point": point, "ref": ref, "tier": row["tier"], "matched": row["point"]})
    total = len(hits) + len(misses)
    covered = len(hits)
    rate = covered / total if total else 0.0
    return {
        "actual": total,
        "covered": covered,
        "coverage": round(rate, 4),
        "threshold": threshold,
        "status": "ok" if rate + 1e-9 >= threshold else "low",
        "by_tier": by_tier,
        "hits": hits,
        "misses": misses,
    }


def render_coverage(coverage, exam):
    lines = ["# 模考覆盖率", ""]
    lines.append("- 模考: " + (exam or "未命名"))
    lines.append("- 实际考点 %d 个,命中 %d,覆盖率 %s(阈值 %s)" % (
        coverage["actual"], coverage["covered"], _pct(coverage["coverage"]),
        _pct(coverage["threshold"])))
    if coverage["status"] == "ok":
        lines.append("- 结论: 达标。按原表继续,重点放在自测和补漏。")
    else:
        lines.append("- 结论: 偏低。回到第 3 步重划重点,不要在原表上加码背。")
    lines.append("- 分档: 必背 %d / 理解 %d / 可放弃 %d / 未预测 %d" % (
        coverage["by_tier"][TIER_MUST], coverage["by_tier"][TIER_UNDER],
        coverage["by_tier"][TIER_DROP], len(coverage["misses"])))
    lines.append("")
    lines.append("| 实际考点 | 出处 | 落在哪档 | 对应预测 |")
    lines.append("|---|---|---|---|")
    for hit in coverage["hits"]:
        lines.append("| %s | %s | %s | %s |" % (
            cell(hit["point"]), cell(hit["ref"]), hit["tier"], cell(hit["matched"])))
    lines.append("")
    lines.append("## 未覆盖")
    lines.append("")
    if not coverage["misses"]:
        lines.append("无。")
    else:
        for miss in coverage["misses"]:
            tag = " (当时在存疑区:给不出出处)" if miss.get("in_doubt") else ""
            lines.append("- %s | %s%s" % (miss["point"], miss["ref"], tag))
        lines.append("")
        lines.append("这些点要么补出处重划,要么就是反押题。别用「超纲」两个字糊过去。")
    return "\n".join(lines) + "\n"


def cmd_coverage(args):
    ledger = load_json(args.ledger)
    document = load_json(args.actual)
    aliases = load_aliases(getattr(args, "aliases", None))
    points = document.get("points") if isinstance(document, dict) else None
    if not isinstance(points, list) or not points:
        die("actual.json 里要有 points 数组")
    rank = build_rank(ledger.get("claims") or [], top=args.top,
                      must_pct=args.must_pct, under_pct=args.under_pct, aliases=aliases)
    doubt_keys = set()
    for claim in ledger.get("claims") or []:
        if claim.get("status") != "verified":
            doubt_keys.add(canonical_key(claim.get("point"), aliases))
    for item in ledger.get("absent") or []:
        doubt_keys.add(canonical_key(item.get("point"), aliases))
    result = coverage_analysis(rank["rows"], points, threshold=args.threshold,
                               aliases=aliases, doubt_keys=doubt_keys)
    result["schema"] = COVERAGE_SCHEMA
    result["generated_at"] = now()
    result["exam"] = str(document.get("exam") or "")
    write_json(args.out, result)
    if args.md:
        write_text(args.md, render_coverage(result, result["exam"]))
    print("覆盖率: %d/%d = %s(阈值 %s)-> %s" % (
        result["covered"], result["actual"], _pct(result["coverage"]),
        _pct(result["threshold"]),
        "达标" if result["status"] == "ok" else "偏低,回第 3 步重划"))
    print("-> " + str(args.out))


# ---------------------------------------------------------------- 错题与重测

def grade_results(claims_by_id, results, previous=None):
    items = {}
    for item in (previous or {}).get("items") or []:
        cid = str(item.get("claim_id") or "").strip()
        if not cid:
            continue
        items[cid] = {
            "claim_id": cid,
            "point": str(item.get("point") or ""),
            "wrong": int(item.get("wrong") or 0),
            "right": int(item.get("right") or 0),
            "last_result": str(item.get("last_result") or ""),
        }
    for result in results:
        cid = str(result.get("claim_id") or "").strip()
        if cid not in claims_by_id:
            raise ValueError("results.json 里的 claim_id %s 不在 ledger 里" % (cid or "(空)"))
        ok = result.get("ok")
        if not isinstance(ok, bool):
            raise ValueError("%s 的 ok 必须是 true / false(布尔值)" % cid)
        entry = items.setdefault(cid, {
            "claim_id": cid,
            "point": str(claims_by_id[cid].get("point") or ""),
            "wrong": 0,
            "right": 0,
            "last_result": "",
        })
        if ok:
            entry["right"] += 1
        else:
            entry["wrong"] += 1
        entry["last_result"] = "right" if ok else "wrong"
    return items


def cmd_grade(args):
    ledger = load_json(args.ledger)
    document = load_json(args.results)
    results = document.get("results") if isinstance(document, dict) else None
    if not isinstance(results, list) or not results:
        die("results.json 里要有 results 数组")
    claims_by_id = {claim["id"]: claim for claim in (ledger.get("claims") or [])}
    previous = None
    if args.merge and pathlib.Path(args.out).is_file():
        previous = load_json(args.out)
    try:
        items = grade_results(claims_by_id, results, previous)
    except ValueError as exc:
        die(exc)
    ordered = sorted(items.values(), key=lambda item: (-item["wrong"], -item["right"], item["point"]))
    write_json(args.out, {"schema": MISTAKES_SCHEMA, "generated_at": now(), "items": ordered})
    wrong = sum(item["wrong"] for item in ordered)
    right = sum(item["right"] for item in ordered)
    print("记录: %d 道题结果;累计错 %d / 对 %d,涉及 %d 个知识点" % (
        len(results), wrong, right, len(ordered)))
    print("-> " + str(args.out))


def review_plan(rows, items, aliases=None):
    by_point = {}
    for row in rows:
        by_point[canonical_key(row["point"], aliases)] = row
    measured = {}
    for item in items or []:
        key = canonical_key(item.get("point"), aliases)
        entry = measured.setdefault(key, {"point": item.get("point") or "", "wrong": 0, "right": 0})
        entry["wrong"] += int(item.get("wrong") or 0)
        entry["right"] += int(item.get("right") or 0)
    hits = []
    for key, entry in measured.items():
        row = by_point.get(key)
        if row is None:
            continue
        hits.append({
            "point": row["point"],
            "tier": row["tier"],
            "refs": row["refs"],
            "weight": row["weight"],
            "wrong": entry["wrong"],
            "right": entry["right"],
            "priority": row["weight"] * (1 + entry["wrong"]),
        })
    hits.sort(key=lambda hit: (-hit["priority"], -hit["wrong"], hit["point"]))
    tested = set(measured)
    untested = [row for key, row in by_point.items() if key not in tested and row["tier"] == TIER_MUST]
    untested.sort(key=lambda row: -row["weight"])
    return {
        "rows": hits,
        "untested": untested,
        "wrong_points": sum(1 for hit in hits if hit["wrong"] > 0),
        "measured_points": len(measured),
    }


def render_review(plan):
    lines = ["# 重测清单", ""]
    lines.append("- 已测 %d 个知识点,其中 %d 个错过" % (plan["measured_points"], plan["wrong_points"]))
    lines.append("- 排序: 分值 × (1 + 错次),错得越多越靠前")
    lines.append("")
    lines.append("| # | 知识点 | 档 | 出处 | 错/对 | 优先级 |")
    lines.append("|---:|---|---|---|---|---|")
    if plan["rows"]:
        for index, row in enumerate(plan["rows"], 1):
            refs = "; ".join("%s#%s" % (ref["paper"], ref["no"]) for ref in row["refs"])
            lines.append("| %d | %s | %s | %s | %d/%d | %s |" % (
                index, cell(row["point"]), row["tier"], cell(refs),
                row["wrong"], row["right"], fmt_num(round(row["priority"], 2))))
    else:
        lines.append("| - | 还没有记录任何自测结果 | - | - | - | - |")
    lines.append("")
    lines.append("## 还没测过的必背项")
    lines.append("")
    if not plan["untested"]:
        lines.append("无。")
    else:
        for row in plan["untested"]:
            refs = "; ".join("%s#%s" % (ref["paper"], ref["no"]) for ref in row["refs"])
            lines.append("- %s | %s" % (row["point"], refs))
    lines.append("")
    lines.append("先把错过的清掉,再上新题。重测仍然闭卷,别先看答案。")
    return "\n".join(lines) + "\n"


def cmd_review(args):
    ledger = load_json(args.ledger)
    mistakes_path = pathlib.Path(args.mistakes)
    if not mistakes_path.is_file():
        die("找不到错题记录: " + str(mistakes_path))
    mistakes = load_json(mistakes_path)
    aliases = load_aliases(args.aliases)
    rank = build_rank(ledger.get("claims") or [], top=args.top,
                      must_pct=args.must_pct, under_pct=args.under_pct, aliases=aliases)
    plan = review_plan(rank["rows"], mistakes.get("items") or [], aliases)
    write_text(args.out, render_review(plan))
    print("重测清单: %d 个错过的知识点,另有 %d 个必背项还没测 -> %s" % (
        plan["wrong_points"], len(plan["untested"]), args.out))


# ---------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ledger.py",
        description="考点账本:让 AI 划的每条重点都带可校验出处",
    )
    parser.add_argument("--version", action="version", version="kaodian-ledger " + VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="真题文本 -> corpus.json")
    p.add_argument("corpus_dir")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("import-csv", help="旧录入稿 CSV -> corpus.json + claims.json")
    p.add_argument("csv_path")
    p.add_argument("--out", required=True)
    p.add_argument("--claims-out", required=True)
    p.add_argument("--subject", default="")
    p.set_defaults(func=cmd_import_csv)

    p = sub.add_parser("check", help="结论 + 题库 -> ledger.json")
    p.add_argument("claims")
    p.add_argument("--corpus", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("rank", help="已核实结论 -> report.md")
    p.add_argument("ledger")
    p.add_argument("--out", required=True)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--must-pct", type=float, default=30)
    p.add_argument("--under-pct", type=float, default=40)
    p.add_argument("--calibration", default=None)
    p.add_argument("--aliases", default=None)
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("sample", help="随机抽 3-5 条生成人工核对表")
    p.add_argument("ledger")
    p.add_argument("--n", type=int, default=3)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--must-pct", type=float, default=30)
    p.add_argument("--under-pct", type=float, default=40)
    p.add_argument("--aliases", default=None)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("calibrate", help="人工核对结果 -> 命中率")
    p.add_argument("samples")
    p.add_argument("--threshold", type=float, default=0.7)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("sheet", help="自测题 -> 闭卷题面 + 答案页")
    p.add_argument("quiz")
    p.add_argument("--ledger", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_sheet)

    p = sub.add_parser("coverage", help="模考实际考点 -> 覆盖率")
    p.add_argument("ledger")
    p.add_argument("--actual", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--md", default=None)
    p.add_argument("--aliases", default=None)
    p.add_argument("--threshold", type=float, default=0.6)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--must-pct", type=float, default=30)
    p.add_argument("--under-pct", type=float, default=40)
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("grade", help="自测结果 -> mistakes.json")
    p.add_argument("--ledger", required=True)
    p.add_argument("--results", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--no-merge", dest="merge", action="store_false")
    p.set_defaults(func=cmd_grade)

    p = sub.add_parser("review", help="错题 + 账本 -> 重测清单")
    p.add_argument("ledger")
    p.add_argument("--mistakes", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--aliases", default=None)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--must-pct", type=float, default=30)
    p.add_argument("--under-pct", type=float, default=40)
    p.set_defaults(func=cmd_review)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
