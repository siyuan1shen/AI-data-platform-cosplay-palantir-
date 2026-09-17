# Understand-Anything 代码展示

本项目将 [Egonex-AI/Understand-Anything](https://github.com/Egonex-AI/Understand-Anything) 作为可选的开发者代码理解和展示工具。

它展示的是：

- 前端、后端、脚本、契约和文档之间的代码结构；
- 文件、函数、类和依赖关系；
- 项目的架构层次和引导式阅读路径。

它不读取或展示企业运行数据库，也不参与企业投影、本体映射或管理 Agent 的生产流程。这样可以保持“开发端代码理解”和“企业端数据使用”的边界。

## 首次准备

在项目根目录执行：

```powershell
pnpm install --dir .tools/understand-anything
pnpm --dir .tools/understand-anything --filter @understand-anything/core build
pnpm --dir .tools/understand-anything --filter understand-anything-viewer build
```

本项目已经保留了一个本地 `.tools/understand-anything` 检出目录，但该目录被 `.gitignore` 忽略，不会被提交到项目仓库。

## 生成或刷新图谱

```powershell
node scripts/generate-understand-anything-graph.mjs
```

图谱输出到 `.ua/knowledge-graph.json`。它只包含代码结构、依赖和由文件路径生成的简短说明，不包含企业数据库内容。`.ua/intermediate/`、`.ua/tmp/` 和第三方工具目录不会提交。

## 打开查看器

```powershell
powershell -ExecutionPolicy Bypass -File scripts/open-understand-anything.ps1
```

默认地址是 `http://127.0.0.1:8022/`。查看器是本地只读页面，不需要模型密钥，也不会把图谱上传到云端。

## 当前图谱基线

最近一次生成结果：

- 314 个文件；
- 1,790 个节点；
- 2,670 条关系；
- 6 个架构层；
- 6 步引导阅读路径。

其中结构关系由 Understand-Anything 的确定性扫描和 Tree-sitter 解析结果生成。文件的自然语言说明目前是轻量的中文结构摘要；如果要进一步增加业务语义解释，可以在本地模型或已授权的模型环境中单独运行其语义分析流程，但不应把企业正式数据放进代码图谱。
