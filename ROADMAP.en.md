# Roadmap

[中文](ROADMAP.md) | [English](ROADMAP.en.md)

One principle: verification must be deterministic. Any change that lets a model "remember"
or replaces exact matching with semantic similarity is not made.

## Now (v0.7)

Ten commands plus a local web UI, covering the seven-step method and one full revision loop:

- parse / import-csv: ingest (.docx / .txt / .md / old CSV)
- check: citation check against your own corpus
- rank: Top N in three tiers
- sample / calibrate: manual calibration
- sheet: closed-book quiz with the answer key on the last page
- grade / review: mistake write-back and a re-test list
- coverage: mock-exam coverage
- serve.py: four-tab local UI, data never leaves the machine (v0.6)
- Quiz tab: paste quiz.json, mark right/wrong, get a re-test list (v0.7)

v0.4 delivered section numbering and section scores; v0.5 delivered .docx ingest with
Word auto-numbering restored.

## v0.8 Content completeness

- Answer / explanation fields: a true/false stem may itself be a false statement. Without
  an answer key the tool must say "this row has a source but no truth value" instead of
  letting people memorise it as a point.
- Text-type PDF ingest: optional dependency on pdftotext; scanned PDFs fail loudly instead
  of pretending to OCR.
- Mis-split warnings: point at suspicious lines during parse instead of silently cutting
  the paper wrong.

## v0.9 Review scheduling

- Schedule "what to re-test today" by a forgetting curve, not as a one-off list.
- Track the latest right/wrong per point and adjust intervals accordingly.

## Want, but not promised

- Obsidian plugin: corpus/ and ledger/ live in a vault; the plugin only handles input and
  display, verification still calls the CLI.
- Phone camera ingest: OCR quality decides success; this repository will not solve it,
  at most provide an integration point.

## Not doing

- Vector search / RAG. The question here is "is this sentence in that question", not
  "is it semantically similar". Embeddings would score 萃取时间 and 萃取温度 highly and
  let fake sources into the table; substring matching is exact, reproducible and free.
  A claim of "100% accuracy" does not hold — vector recall is probabilistic.
- Letting the model count occurrences, frequency or scores. Once a model counts, it drifts;
  statistics belong to code.
- Uploading the question bank to the cloud. A user's past papers are copyrighted assets;
  local processing is the default.
