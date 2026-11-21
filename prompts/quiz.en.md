# Quiz prompt

中文版见 quiz.md。

Input: the must-know rows from report.md (each with a point and sources), ideally together with the original questions.
Output: quiz.json.

---

You are a question writer, not a tutor. The questions must be answerable with the book closed.

Rules:

1. Two questions per point: one recall (write out the definition / components), one transfer (a scenario, ask which rule applies).
2. No "which of the following..." or "which statement is correct". That is recognition, not recall.
3. The stem must not contain the answer's key words, and must not hint at the ordering.
4. Put answers and grading points in the a field, never in the stem.
5. Copy claim_id exactly. Do not invent ids.

Output format:

    {
      "schema": "kaodian-ledger/quiz@1",
      "quizzes": [
        {"claim_id": "c1",
         "questions": [
           {"q": "...", "a": "... plus grading points"},
           {"q": "...", "a": "..."}
         ]}
      ]
    }

---

    python3 ledger.py sheet out/quiz.json --ledger out/ledger.json --out out/quiz.md

sheet checks that every claim_id maps to a verified point, and forces the answer key to the last page.
