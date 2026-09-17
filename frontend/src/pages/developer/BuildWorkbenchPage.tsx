import { AgentWorkspace } from "../../features/agents/AgentWorkspace";
import { ImportWorkbench } from "../../features/imports/ImportWorkbench";
import { useWorkspace } from "../../workspace/WorkspaceContext";
import { StatusMessage } from "../../components/StatusMessage";
import { Link } from "react-router-dom";

export function BuildWorkbenchPage() {
  const workspace = useWorkspace();
  if (!workspace.selectedProjectId) return <StatusMessage title="请先选择公司" description="在“企业”中选择公司，系统会自动打开统一企业投影。" />;
  return <div className="page-stack">
    <section className="page-hero"><div><p className="eyebrow">DISCOVER & MODEL</p><h1>材料与 Agent 建模</h1><p>材料形成可引用证据，企业投影 Agent 根据证据提出建模草稿；所有修改最终都回到同一份企业投影。</p></div><div className="hero-actions"><Link className="button secondary" to="/developer/model">查看并编辑投影</Link><Link className="button primary" to="/developer/advanced#release">审核并发布</Link></div></section>
    <ImportWorkbench key={`${workspace.selectedProjectId}-imports`} projectId={workspace.selectedProjectId} />
    <AgentWorkspace key={`${workspace.selectedProjectId}-projection-agent`} projectId={workspace.selectedProjectId} kind="PROJECTION" title="企业投影 Agent" description="把问卷、访谈、报告和建模目标交给 Agent；它会基于证据生成可审核的对象、关系或变更集。" />
    <div className="inline-notice">下一步：打开投影编辑器检查对象和关系，再进入发布区冻结供管理层使用的版本。未发布内容不会冒充正式企业事实。</div>
  </div>;
}
