# 规范 · v0.2

改数据格式先改这个文件。

## 0 一句话

模型只负责「从材料里找句子，给它标一个知识点」；计数、匹配、分档、降级全部由代码做。
任何一步需要模型「回忆往年考了什么」，都是 bug。

## 1 数据流

    真题文本 --parse-------> corpus.json --\
    旧 CSV   --import-csv-->             >--check--> ledger.json --rank--> report.md
    AI 输出  --extract-----> claims.json /                    \
                                           sample --> samples.json --calibrate--> calibration.json

    模考实际考点 + ledger.json --coverage--> coverage.json / coverage.md   (⑦)

## 2 真题文本约定

一卷一个 .txt 或 .md。元信息行以 @ 开头，只在前面的第一题之前有效：

    @paper: 2023-A
    @year: 2023
    @subject: 咖啡师认证（虚构）

题目从行首标记开始，两种写法都认：

    [1] 题干……(2分)
    【2】题干……

从题号到下一个题号之间的所有行都算题干（支持多行题）。分值从 (2分) /（2 分）/【2分】
里提取，没有就记 null ——不猜。

陷阱：题干里以「1.」开头的列表会被误判成下一题。OCR 后建议统一改成 [n]。

## 3 corpus.json

    {
      "schema": "kaodian-ledger/corpus@1",
      "papers": [
        {"paper_id": "2023-A", "year": 2023, "subject": "...",
         "questions": [{"no": "1", "text": "题干原文", "score": 2}]}
      ]
    }

## 4 claims.json（模型输出）

严格 JSON，不带 markdown 代码块。claims 每项：

| 字段 | 必填 | 说明 |
|---|---|---|
| id | 是 | 唯一，短 |
| point | 是 | 知识点，一句话 |
| paper | 二选一 | 卷号，如 2023-A |
| year | 二选一 | 年份；该年只有一卷时可以用它代替 paper |
| no | 是 | 题号 |
| quote | 是 | 原题摘录，建议 ≤10 字，必须逐字来自题干 |
| score | 否 | 分值；给了会和题库比对 |

另外可以给一个 absent 数组，放「材料中未见」的推断：
{"point": "...", "note": "..."}。它不参与排序，只会原样出现在存疑区。

## 5 判定规则

进存疑区（status=suspect，不参与排序）：

| 情况 | reason |
|---|---|
| 卷号 / 年份对不上 | paper_not_found |
| 该卷没有这个题号 | question_not_found |
| 引文为空 | empty_quote |
| 引文在这题题干里找不到 | quote_not_found |
| 缺卷号 / 年份或题号 | missing_ref |

只警告，仍然算可核实：

| 情况 | warning |
|---|---|
| 引文 > 10 字 | quote_too_long |
| 同一引文在题库出现 ≥3 次 | weak_quote |
| 结论分值和题库不一致 | score_mismatch |
| 题库没标分值，沿用结论里的 | score_unverified |
| 同一年份有多卷，按第一份核对 | ambiguous_ref |

判定顺序：先找卷 → 再找题号 → 再匹配引文。前一步失败就停，不瞎猜。
匹配前做归一化：NFKC、大小写折叠、只保留字母数字（去掉空格和全部标点），
所以 OCR 把「，」认成「,」不影响结论。归一化也会让极短引文更容易误命中，
这是为什么引文要求必须逐字来自题干，且建议 ≤10 字。

## 6 排序与分档

- 只在 status=verified 的结论上排序。
- 权重 = 该知识点命中题目的分值之和；同一题被两条结论引用只算一次。
  这是「次数 × 分值」的可算版本：分值相等时两者等价，不等时不会重复计分。
- 分值缺失：用同卷已知分值的中位数估，标 est。
- 排序：权重降序 → 次数降序 → 点名字典序。
- 分档按 Top N 的行数切：必背 30%，理解 40%，其余可放弃（可调）。
  可放弃档仍要留 20–30% 时间兜底，专门应对反押题。
- 归并：先按归一化后的字面合并；同义写法写进 aliases.json（{"规范名": ["写法", ...]}），
  用 --aliases 传入，rank / sample / coverage 都用同一张表。没写进去的不会自动合并。

## 7 ⑤ 校准

sample 用固定 seed 随机抽 Top N 里的 3–5 条，连每条的原题全文一起给你，不用翻文件。
人核只回答一个问题：这条知识点，真的考在列出的这些题里吗？

命中率 = hit / (hit + miss)。低于阈值（默认 0.7）时 calibration.status=reference_only，
report 顶部出横幅：排序仅供参考，不能用来筛掉任何结论。

## 8 ⑥ 自测

quiz.json 里每道题必须挂 claim_id，且该 claim 必须已核实。这是同源约束：
重点表里有 20 条、自测题考了别的，这种错配由脚本拦掉。
答案放在独立字段，sheet 渲染时强制排到最后。

## 9 ⑦ 模考覆盖率

模考后，把「实际考了什么」记成 actual.json：

    {"exam": "2025 模拟卷", "points": [
      {"point": "牛奶发泡温度", "ref": "2025-mock#3"}
    ]}

    python3 ledger.py coverage out/ledger.json --actual actual.json --md out/coverage.md --out out/coverage.json

- 预测集 = report 的 Top N（学生真正看到的那张表），只含 status=verified 的结论。
- 匹配用字面归一 + aliases.json。
- 覆盖率 = 命中 / 实际考点数。低于阈值（默认 0.6）判为偏低，回第 3 步重划。
- 未覆盖的点如果当初在存疑区（给不出出处），报告里会直接标出来——那正是反押题。
- 阈值是人的判断，不是定律，可以调。

## 10 已知边界

- 出处真实 ≠ 结论正确。标注是否贴切，只有人核能判断（⑤ 就是干这个的）。
- OCR 切题错误会让整卷错位，且很难自动发现。parse 后先看题数。
- 引文在选项里而不是题干里时，当前算可核实（选项属于题干块）；但选项内容不单独成题，
  所以「这题考的是选项里的点」这种判断仍然要人做。
- 分值估算会带来排序噪声，表里标了「估」。
