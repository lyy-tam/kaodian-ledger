# Contributing

Thanks for looking. This project is small and opinionated; the fastest way to help is a precise bug report. 中文提问也可以。

## Run it

    git clone https://github.com/lyy-tam/kaodian-ledger
    cd kaodian-ledger
    python3 -m unittest discover -s tests

No third-party dependencies; Python 3.9+.

## Reporting a parsing bug

Attach the smallest text that reproduces it, including the metadata lines (@paper, @year)
and the section headers. Say what you expected (question count, numbering, scores) and what
you got. If the paper is copyrighted, retype a few questions instead of uploading the file.

## What will be merged

- Deterministic improvements: parsing, matching, reporting, docs, tests.
- New input formats that can be tested without shipping real papers.
- Bug fixes that come with a failing test.

## What will not be merged

- Vector search or embeddings replacing exact substring matching. See ROADMAP "Not doing".
- Letting the model count occurrences, frequency or scores.
- Cloud upload of question banks, accounts, telemetry.
- Anything that requires shipping real past papers.

If your idea is in that list, the answer is no — not because it is hard, but because it
makes the ledger less trustworthy.

## Style

- Python standard library only; no new dependencies.
- Tests are plain unittest and must run offline.
- Commit messages: short, prefixed by area (parse:, check:, docs:, serve:).
- By contributing you agree to license your work under the MIT license.
