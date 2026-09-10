import { AgentWorkspace } from "../../features/agents/AgentWorkspace";
import { ManagementResults } from "../../features/agents/ManagementResults";
import { StatusMessage } from "../../components/StatusMessage";
import { useWorkspace } from "../../workspace/WorkspaceContext";
import { PublishedProjectGate } from "./PublishedProjectGate";

export function ManagementAgentPage() {
  const workspace = useWorkspace();
  if (!workspace.selectedProjectId) return <StatusMessage title="请先选择企业项目" description="管理 Agent 只在一个明确的企业投影项目内回答和提出方案。" />;
  return <PublishedProjectGate projectId={workspace.selectedProjectId}><div className="page-stack">
    <section className="page-hero"><div><p className="eyebrow">MANAGEMENT COPILOT</p><h1>管理决策 Agent</h1><p>查询企业事实、探索潜在问题并形成管理方案；未经确认的推测不会写入可信投影。</p></div></section>
    <AgentWorkspace key={`${workspace.selectedProjectId}-management-agent`} projectId={workspace.selectedProjectId} kind="MANAGEMENT" title="管理决策 Agent" description="每个对话拥有独立上下文。可以引用企业事实和材料，并把有价值的内容保存为假设、方案或行动。" />
    <ManagementResults key={`${workspace.selectedProjectId}-management-results`} projectId={workspace.selectedProjectId} />
  </div></PublishedProjectGate>;
}
