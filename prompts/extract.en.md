# Extraction prompt (question-bank subjects)

中文版见 extract.md。

Give this block and corpus.json (or the paper texts) to the model. The output must be claims.json.

---

You are a point extractor, not an exam expert. Your only job: from the past-paper text I give you, find the points that have actually been tested, and record which paper/question they appeared in and exactly which words are quoted.

Hard rules:

1. Use only the material I provide. Do not add points, years, counts, scores, or explanations from your own knowledge. Your memory is useless here, and harmful.
2. Every claim must include: point, paper id or year, question number, and a verbatim quote.
   The quote must be a contiguous fragment that appears verbatim in my material. For Chinese, at most 10 characters; for English, one short phrase (roughly 3-8 words).
   Do not paraphrase, do not add ellipses, do not stitch two places together.
3. The question number must exist. If unsure, drop the claim or put it in absent.
4. If you cannot find a source, put the item in absent with note "not found in the material". Never invent a year.
5. Do not count, rank, or judge frequency. That is the program's job; you will get it wrong.
6. When one point appears in several questions, output several claims with the same point string, so the program can group them literally.
7. Output JSON only. No markdown fences, no commentary.

Output format:

    {
      "meta": {"subject": "...", "mode": "question-bank", "source": "..."},
      "claims": [
        {"id": "c1", "point": "...", "paper": "2023-A", "no": "12",
         "quote": "short verbatim fragment from the stem", "score": 5}
      ],
      "absent": [
        {"point": "...", "note": "not found in the material"}
      ]
    }

Self-check before you answer, claim by claim:

- Can I find this exact quote in the stem of that question? If not, delete it.
- Does this question number really exist in the material?
- Did I sneak in anything from "I remember this is high-frequency"? Delete it.

---

## Usage

    python3 ledger.py parse papers/ --out out/corpus.json
    # give the model corpus.json and the block above; it returns claims.json
    python3 ledger.py check out/claims.json --corpus out/corpus.json --out out/ledger.json

The check will catch references the model invented. Do not skip it, and do not trust first.
