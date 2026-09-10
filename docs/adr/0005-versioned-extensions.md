# ADR-0005：不可变版本与扩展清单

- 状态：Accepted
- 日期：2026-08-23

## 决议

QuestionPack、Ontology、Mapping、Program、AgentRecipe、Playbook、EvalSuite 和 DatasetRelease 发布后不可变。修改必须产生新版本。

所有扩展使用 Manifest 声明：

- namespace、stable id、version；
- 输入和输出契约；
- 依赖和兼容范围；
- 权限和资源限制；
- 内容哈希和签名；
- 测试与迁移说明。

安装生成 lockfile。依赖无法解析时必须明确失败，不能静默选择版本。

