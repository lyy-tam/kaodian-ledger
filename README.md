# 考点账本 · kaodian-ledger

AI 划重点最大的风险不是漏，是编。没有出处的「高频考点」，你没法查，只能信。
这个工具给每条重点加一道机械校验：它引用的那句话，必须能在你给的那套真题的那道题里
被程序原样找到。找不到就进存疑区，而不是进你的复习清单。

它落实的是那套方法里最关键的两个检查点：

- ③ 有没有出处 → 对账脚本，机器判定，不靠 AI 自觉
- ⑥ 是背还是测 → 自测题必须挂回一条已核实的重点，答案强制分离

不碰 AI 的记忆，也不碰你的真题版权：仓库里没有任何真题，只有虚构的示例。

## 它做什么

| 命令 | 作用 |
|---|---|
| parse | 真题文本（OCR / 粘贴）切成「卷 → 题」，带年份、题号、分值 |
| import-csv | 已有的录入稿 CSV 直接转成题库 + 结论 |
| check | 拿着 AI 给的结论逐条回题库查出处，查不到的进存疑区 |
| rank | 只排在已核实的结论上，输出 Top N 和三档 |
| sample | 从 Top N 随机抽 3–5 条，生成人工核对表 |
| calibrate | 命中率低于阈值（默认 70%）→ 整体降级为「仅参考」 |
| sheet | 自测题渲染成闭卷题面，答案单独放最后一页 |

## 5 分钟上手

无第三方依赖，Python 3.9+ 就能跑，手机上也能跑。

    # 1. 入库（示例里是虚构题库，换成你自己的）
    python3 ledger.py parse examples/corpus --out out/corpus.json

    # 2. 抽取：把 prompts/extract.md 和 corpus.json 一起丢给模型
    #    它只会输出 claims.json

    # 3. 对账
    python3 ledger.py check examples/claims.json --corpus out/corpus.json --out out/ledger.json

    # 4. 出表
    python3 ledger.py rank out/ledger.json --out out/report.md

    # 5. 人核 3–5 条
    python3 ledger.py sample out/ledger.json --n 3 --seed 42 --out out/samples.json
    #    把 samples.json 里的 result 改成 hit 或 miss
    python3 ledger.py calibrate out/samples.json --out out/calibration.json

    # 6. 带上校准结果重新出表
    python3 ledger.py rank out/ledger.json --calibration out/calibration.json --out out/report.md

## 跑出来长什么样

用 examples/ 里的虚构咖啡师题库：

| # | 档 | 知识点 | 出处 | 原题摘录 | 分值 |
|---:|---|---|---|---|---|
| 1 | 必背 | 处理法风味 | 2023-A#4; 2024-A#4 | 发酵风味 / 发酵感与果香 | 3+3=6 |
| 2 | 必背 | 牛奶发泡温度 | 2024-A#3; 2023-A#2 | 55-65 摄氏度 / 蛋白质变性 | 2+3=5 |
| 3 | 理解 | 粉水比 | 2023-A#3; 2024-A#2 | 粉水比 / 粉水比 | 2+2=4 |
| 4 | 理解 | 日晒处理 | 2024-A#4 | 日晒处理法 | 3 |
| 5 | 可放弃 | 萃取时间 | 2023-A#1 | 萃取时间一般控制 | 2 |

存疑区（示例里是故意编的）：

    c7 水温对萃取的影响 | 2024-A#7 | "九十二度" → 该卷里找不到这个题号
    c8 研磨度与萃取   | 2023-A#2 | "研磨度过细" → 引文在该题题干里找不到

## 和旧录入稿的桥接

如果你手里已经有一张按「年份,题号,题干原文,分值,知识点,原文摘录」记的 CSV：

    python3 ledger.py import-csv 录入稿.csv --out out/corpus.json --claims-out out/claims.json
    python3 ledger.py check out/claims.json --corpus out/corpus.json --out out/ledger.json

「原文摘录」这一列第一次会被真正校验。以前它只是抄在那里，现在对不上题干的会全部落进存疑区。

## 边界（比功能重要）

- 只核「这句话在不在那道题里」，不核「这个知识点贴不贴」。语义靠 sample 人核。
- 归并按字面归一。「牛奶发泡温度」和「奶泡温度」不会自动合并，同义词要自己维护。
- OCR 切错题会污染整个账本。parse 完先看题数对不对。
- 分值缺失按同卷已知分值的中位数估，表里标「估」。
- 能力型科目（编程 / 写作 / 实验设计）概率没有意义，用 prompts/capability-mode.md 换成题型分析。
- 仓库不带任何真题，examples/ 里是虚构题目。

## 为什么不是 prompt 合集

prompt 谁都能复制。这个仓库真正带不走的是两个 JSON：corpus.json（你本地的题库）
和 ledger.json（每条结论的校验记录）。前者是版权边界，后者是可复算的账。

MIT License
