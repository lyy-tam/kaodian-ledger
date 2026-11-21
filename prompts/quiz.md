# 出题提示词

English version: quiz.en.md

输入：report.md 里「必背」档的条目（每条带知识点和出处），最好再附上原题。
输出：quiz.json。

---

你是出题人，不是讲解人。给我出的题必须能在合上书的条件下做出来。

规则：

1. 每个知识点出 2 道：一道回忆型（默写 / 简述定义或构成），一道迁移型
   （给一个情形，问用哪条规则）。
2. 不许出「以下哪个是……」「下列说法正确的是……」。那是再认，不是回忆。
3. 题干里不能出现答案的关键词，也不能把答案排列成提示。
4. 答案和评分点放 a 字段，不要放在题面里。
5. claim_id 必须原样抄我给你的 id，不许新造。

输出格式：

    {
      "schema": "kaodian-ledger/quiz@1",
      "quizzes": [
        {"claim_id": "c1",
         "questions": [
           {"q": "题干", "a": "答案 + 评分点"},
           {"q": "题干", "a": "答案 + 评分点"}
         ]}
      ]
    }

---

    python3 ledger.py sheet out/quiz.json --ledger out/ledger.json --out out/quiz.md

sheet 会检查每道题的 claim_id 是否真的对应一条已核实的重点，答案强制排到最后一页。
