# 模块 Agent 对话工作台

## 使用流程

1. 启动本地服务并打开 V2 页面。
2. 在左侧先选择公司，再选择项目。公司和项目是两个独立的上下文。
3. 在页面顶部的“和模块 Agent 一起工作”区域选择模块：调研、追问、本体、数据接入、诊断、因果、决策、行动、学习、报告或批评。
4. 点击“新建对话”，在当前模块的独立会话中输入任务。可以先点击“添加材料”上传 CSV、Excel、Word、Markdown、JSON 或文本文件。
5. 点击“发送”。系统会保存用户消息，将附件绑定到这条消息，生成当前项目的上下文快照，然后运行对应模块 Agent 并保存回复。
6. 查看回复后，再到本体、决策或行动模块的正式操作区确认写入。聊天本身不会自动修改已确认的企业投影。
7. 不需要的对话点击“删除”会进入回收站；可以恢复，也可以在回收站中永久删除。消息同样支持单条删除。

## 上下文边界

每个对话只属于一个项目和一个 `agent_kind`。执行桥会显式记录：项目、模块、对话、触发消息、当前调研答案数、对象数、关系数和发现数。附件保存在本地 V2 数据库中，不写入 Git 工作树。

## 接口约定

- `GET /api/v2/projects/{project_id}/agent-threads`：列出当前模块的对话。
- `POST /api/v2/projects/{project_id}/agent-threads`：创建对话。
- `GET /api/v2/projects/{project_id}/agent-threads/{thread_id}/context`：查看可审计上下文包。
- `POST /api/v2/projects/{project_id}/agent-threads/{thread_id}/run`：执行当前模块 Agent 并保存回复。
- `POST /api/v2/projects/{project_id}/agent-threads/{thread_id}/attachments`：上传本地材料，单文件上限 20 MB。
- `DELETE /api/v2/projects/{project_id}/agent-threads/{thread_id}`：软删除到回收站。
- `DELETE /api/v2/projects/{project_id}/agent-threads/{thread_id}/permanent`：永久删除对话及其消息、附件。

当前执行桥复用 V2 的可审计 Agent Run 记录；模型网关、流式输出和结构化提案确认将在后续阶段接入，不改变上述对话数据边界。
