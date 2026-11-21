---
name: Bug report
about: A paper parsed wrong, a check behaved unexpectedly, or the web UI failed
title: "[bug] "
labels: bug
---

**What I fed it**

File type (.docx / .txt / CSV), language, and a minimal excerpt. Do not upload copyrighted papers.

**What I expected**

For example: 26 questions, 一.1 .. 一.10, 二.1 .. 二.5, scores 2 / 2 / 1 / 10.

**What I got**

For example: 24 questions, 三.1 missing, every score null.

**Version**

    python3 ledger.py --version
