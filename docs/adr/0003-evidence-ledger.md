# ADR-0003：证据账本是真值起点

- 状态：Accepted
- 日期：2026-08-23

## 决议

问卷回答、附件、外部系统记录首先进入不可变 Evidence Ledger。Fact、Claim、Finding、Report 和 Ontology Patch 必须保留到 Evidence 的引用和生产血缘。

## 后果

- 回答不天然等于事实；
- OCR/LLM 抽取文本不能覆盖原件；
- 正式结论必须引用不可变证据位置；
- 向量、全文和图索引均可重建，不能成为真值源。

