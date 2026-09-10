import { AgentWorkspace } from "../../features/agents/AgentWorkspace";
import { ImportWorkbench } from "../../features/imports/ImportWorkbench";
import { useWorkspace } from "../../workspace/WorkspaceContext";
import { StatusMessage } from "../../components/StatusMessage";

export function BuildWorkbenchPage() {
  const workspace = useWorkspace();
  if (!workspace.selectedProjectId) return <StatusMessage title="请先选择项目" description="在“公司与项目”中选择或建立企业投影项目，再导入材料并开始建模。" />;
  return <div className="page-stack">
    <section className="page-hero"><div><p className="eyebrow">DISCOVER & MODEL</p><h1>材料与 Agent 建模</h1><p>材料形成可引用证据，企业投影 Agent 根据证据提出建模草稿。</p></div></section>
    <ImportWorkbench key={`${workspace.selectedProjectId}-imports`} projectId={workspace.selectedProjectId} />
    <AgentWorkspace key={`${workspace.selectedProjectId}-projection-agent`} projectId={workspace.selectedProjectId} kind="PROJECTION" title="企业投影 Agent" description="把问卷、访谈、报告和建模目标交给 Agent；它会基于证据生成可审核的对象、关系或变更集。" />
  </div>;
}
