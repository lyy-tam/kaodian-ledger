# Spec · v0.2

[中文](SPEC.md) | [English](SPEC.en.md)

Change this file first when you change a data format.

## 0 In one sentence

The model only finds sentences in the material and labels a point. Counting, matching,
tiering and downgrading are all done by code. Any step that needs the model to
"remember what was tested in past years" is a bug.

## 1 Data flow

    paper text --parse-------> corpus.json --\
    old CSV    --import-csv-->             >--check--> ledger.json --rank--> report.md
    AI output  --extract-----> claims.json /

                                              sample --> samples.json --calibrate--> calibration.json

    mock-exam topics + ledger.json --coverage--> coverage.json / coverage.md   (⑦)
    results.json --grade--> mistakes.json --review--> review.md                (after ⑥)

## 2 Paper text conventions

One paper per .txt or .md. Metadata lines start with @ and are only honoured before the first question:

    @paper: 2023-A
    @year: 2023
    @subject: Barista certification (fictional)

Questions start with a marker at the beginning of a line; two forms are accepted:

    [1] stem...(2 points)
    【2】stem...

Every line between one question number and the next belongs to the stem (multi-line
questions are supported). Scores are extracted from (2 points) /（2 分）/【2 分】; if
none is present the score is null — never guessed.

### Sectioned papers

Chinese papers often split into sections, and each section restarts numbering at 1:

    一、Single choice (2 points each, 20 total)
    1. stem...
    二、Multiple choice (2 points each, 10 total)
    1. stem...

- A line starting with 一、/二、 is a section header. When question numbers repeat across
  sections they get a section prefix: 一.1, 二.1. Continuous numbering is left as is.
- "X points per question" in the section header becomes the default score for that
  section; a per-question (X points) overrides it.
- An essay prompt with no number and a single paragraph becomes question 1 of its
  section, with the total score in parentheses.

### Word documents

.docx files can be placed in the directory directly:

- Questions that rely on Word auto-numbering (no numbers visible in the text, common
  for teacher-made papers) are numbered 1, 2, 3... per section.
- Legacy .doc is not supported; re-save as .docx first.
- A mis-numbered option line (such as "2.A. bone membrane B. bone substance") is folded
  back into the previous question instead of creating a phantom question.

Traps: a list line starting with "1." inside a stem is mistaken for the next question;
after OCR, rewrite those as [n]. Another trap: a true/false stem is itself a possibly
false statement; without an answer key it only proves that the sentence is on the paper,
not that it is correct — see section 11.

## 3 corpus.json

    {
      "schema": "kaodian-ledger/corpus@1",
      "papers": [
        {"paper_id": "2023-A", "year": 2023, "subject": "...",
         "questions": [{"no": "1", "text": "verbatim stem", "score": 2}]}
      ]
    }

## 4 claims.json (model output)

Strict JSON, no markdown code fences. Each claim:

| Field | Required | Meaning |
|---|---|---|
| id | yes | unique, short |
| point | yes | the knowledge point, one sentence |
| paper | one of two | paper id, e.g. 2023-A |
| year | one of two | year; usable instead of paper when that year has a single paper |
| no | yes | question number |
| quote | yes | verbatim fragment from the stem (≤10 Chinese chars, short phrase in English) |
| score | no | score; compared against the corpus when given |

An optional absent array holds inferences not found in the material:
{"point": "...", "note": "..."}. It never enters ranking and appears in the doubt list verbatim.

## 5 Judgement rules

Doubt list (status=suspect, excluded from ranking):

| Situation | reason |
|---|---|
| paper id / year not in the corpus | paper_not_found |
| that question number does not exist | question_not_found |
| empty quote | empty_quote |
| quote not found in that stem | quote_not_found |
| missing paper/year or question number | missing_ref |

Warnings only, still verified:

| Situation | warning |
|---|---|
| quote exceeds the limit (10 CJK chars / 40 Latin chars) | quote_too_long |
| the same quote appears in ≥3 questions | weak_quote |
| claim score differs from the corpus | score_mismatch |
| corpus has no score, claim score is kept | score_unverified |
| several papers share the year; the first one is used | ambiguous_ref |

Order: find the paper → find the question number → match the quote. The first failure
stops the check; nothing is guessed. Matching normalises first: NFKC, case folding,
letters and digits only (all whitespace and punctuation removed), so OCR confusing "，"
with "," changes nothing. Normalisation also makes very short quotes easier to hit by
accident, which is why quotes must be verbatim from the stem.

## 6 Ranking and tiers

- Only status=verified claims are ranked.
- Weight = the sum of scores of the questions a point hit; a question cited twice counts once.
  This is a computable version of "count × score": identical when scores are equal, and
  it does not double count when they differ.
- Missing scores: estimated from the median of known scores in the same paper, marked est.
- Order: weight desc → count desc → point name.
- Tiers are cut by row count in the Top N: must-know 30%, understand 40%, the rest
  optional (adjustable). Keep 20–30% of your time for the optional tier as a hedge
  against anti-cramming.
- Merging: literal normalised keys first; synonyms go into aliases.json
  ({"canonical": ["variant", ...]}) and are passed with --aliases; rank / sample /
  coverage share the same table. Anything not listed is merged literally only.

## 7 ⑤ Calibration

sample draws 3–5 rows from the Top N with a fixed seed and shows each row's full source
question, so you do not have to open files. The human answers one question only: is this
point really tested in the questions listed?

Hit rate = hit / (hit + miss). Below the threshold (0.7 by default) the calibration file
is written with status=reference_only and the report gets a banner: the ranking is for
reference only and must not be used to filter claims out.

## 8 ⑥ Closed-book quiz

Every question in quiz.json must carry a claim_id, and that claim must be verified. This
is a same-source constraint: a report of 20 points and a quiz about something else is
rejected by the script. Answers live in a separate field, and sheet forces the answer key
to the last page.

## 9 ⑦ Mock-exam coverage

After a mock exam, record what was actually tested as actual.json:

    {"exam": "2025 mock", "points": [
      {"point": "Milk foam temperature", "ref": "2025-mock#3"}
    ]}

    python3 ledger.py coverage out/ledger.json --actual actual.json --md out/coverage.md --out out/coverage.json

- The prediction set is the Top N of the report (the table the student actually saw),
  verified claims only.
- Matching uses literal normalisation plus aliases.json.
- Coverage = hits / tested points. Below the threshold (0.6 by default) it is judged low
  and you go back to step 3 and redo the extraction.
- Misses that were once in the doubt list (no citable source) are flagged in the report —
  that is exactly the anti-cramming effect.
- The threshold is a human judgement, not a law; adjust it.

## 10 Mistake write-back and re-test

After a quiz, record each answer as results.json:

    {"results": [{"claim_id": "c5", "q": 1, "ok": false}]}

    python3 ledger.py grade --ledger out/ledger.json --results results.json --out out/mistakes.json
    python3 ledger.py review out/ledger.json --mistakes out/mistakes.json --out out/review.md

- claim_id must exist in the ledger, otherwise the command fails — a typo must not be
  swallowed silently.
- Re-running grade accumulates in the same file; --no-merge starts over.
- Re-test priority = point score × (1 + accumulated wrong count), verified claims only.
- Must-know items never tested are listed separately: silence is not mastery.

## 11 Known boundaries

- A real source is not a correct claim. Whether the label fits is a human judgement
  (that is what ⑤ is for).
- An OCR mis-split shifts a whole paper and is hard to spot automatically. Check the
  question count after parse.
- When the quote lives in an option rather than the stem, it currently counts as verified
  (options belong to the stem block). Options are not separate questions, so deciding
  "this question tests an option" is still a human job.
- Estimated scores add noise to the ranking; such rows are marked est.
- A true/false stem may be a false statement. Without an answer key, a passing citation
  check does not mean the statement is true.
