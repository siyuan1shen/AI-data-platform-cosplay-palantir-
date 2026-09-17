import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import type { ChangeOperation, ChangeSet } from "../../api/types";
import { ActionDefinitionsPanel } from "../../features/actions/ActionDefinitionsPanel";
import { AgentWorkspace } from "../../features/agents/AgentWorkspace";
import { LearningCasesPanel } from "../../features/agents/LearningCasesPanel";
import { ModelProfilesPanel } from "../../features/agents/ModelProfilesPanel";
import { EvaluationPanel } from "../../features/agents/EvaluationPanel";
import { ExportCenter } from "../../features/imports/ExportCenter";
import { RestoreCenter } from "../../features/imports/RestoreCenter";
import { SourceDataPanel } from "../../features/imports/SourceDataPanel";
import { StatusMessage } from "../../components/StatusMessage";
import { useWorkspace } from "../../workspace/WorkspaceContext";

export function AdvancedToolsPage() {
  const workspace = useWorkspace();
  const navigate = useNavigate();
  return <div className="page-stack">
    <section className="page-hero"><div><p className="eyebrow">SYSTEM CONNECTIONS</p><h1>信息系统接入</h1><p>在当前公司的统一企业投影下接入系统、校验语义、审核发布并导出；低频开发设置收在下方折叠区域。</p></div>{workspace.selectedProjectId && <button className="button primary" onClick={() => navigate("/executive?preview=true")}>预览当前投影</button>}</section>
    <ModelProfilesPanel />
    <RestoreCenter onRestored={workspace.refresh} />
    {workspace.selectedProjectId && workspace.selectedProject ? <AdvancedTools key={workspace.selectedProjectId} projectId={workspace.selectedProjectId} projectRevision={workspace.selectedProject.revision} refreshWorkspace={workspace.refresh} /> : <StatusMessage title="尚未选择企业" description="模型可以先配置；系统接入、发布和导出需要先在“企业”中选择公司。" />}
  </div>;
}

function AdvancedTools({ projectId, projectRevision, refreshWorkspace }: { projectId: string; projectRevision: number; refreshWorkspace: () => Promise<void> }) {
  const navigate = useNavigate();
  return <>
    <details className="advanced-section" open><summary><span><strong>建设 Agent · 系统接入模式</strong><small>同一个建设 Agent 的另一种受控工作模式：起草映射，再由人预览、校验、批准并执行同步</small></span><i>展开 / 收起</i></summary><div className="advanced-section-body advanced-stack"><AgentWorkspace projectId={projectId} kind="SYSTEM_ONTOLOGY" title="建设 Agent · 系统接入" description="说明系统、表、字段含义与目标企业对象；Agent 可以提出语义映射动作或只读字段候选，人工复核后保存为草稿，再校验、批准并执行。" /><SourceDataPanel projectId={projectId} /></div></details>
    <details id="release" className="advanced-section" open><summary><span><strong>变更校验与正式发布</strong><small>审核企业投影 Agent 提案并发布公司版本</small></span><i>展开 / 收起</i></summary><div className="advanced-section-body"><ReleasePanel projectId={projectId} projectRevision={projectRevision} refreshWorkspace={refreshWorkspace} /></div></details>
    <details className="advanced-section"><summary><span><strong>导出与学习案例</strong><small>导出企业投影，维护本项目案例和匿名参考目录</small></span><i>展开 / 收起</i></summary><div className="advanced-section-body advanced-stack"><ExportCenter projectId={projectId} /><LearningCasesPanel projectId={projectId} /></div></details>
    <details className="advanced-section"><summary><span><strong>评测与回归</strong><small>用固定案例实际运行 Agent，核对引用、动作和回答边界</small></span><i>展开 / 收起</i></summary><div className="advanced-section-body"><EvaluationPanel projectId={projectId} /></div></details>
    <details className="advanced-section"><summary><span><strong>动作目录与扩展</strong><small>低频开发功能：检查、启停或扩展 Agent 可调用的受控动作</small></span><i>展开 / 收起</i></summary><div className="advanced-section-body"><ActionDefinitionsPanel projectId={projectId} onOpenExecution={() => navigate("/executive/agent#actions")} /></div></details>
  </>;
}

function ReleasePanel({ projectId, projectRevision, refreshWorkspace }: { projectId: string; projectRevision: number; refreshWorkspace: () => Promise<void> }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const changeSetsQuery = useQuery({ queryKey: ["change-sets", projectId], queryFn: () => api.listChangeSets(projectId) });
  const publicationsQuery = useQuery({ queryKey: ["publications", projectId], queryFn: () => api.listPublications(projectId) });
  const selected = changeSetsQuery.data?.items.find((item) => item.id === selectedId) ?? changeSetsQuery.data?.items[0];
  const isAppliedHistory = selected?.status === "APPLIED";
  const previewQuery = useQuery({ queryKey: ["change-preview", projectId, selected?.id], queryFn: () => api.previewChangeSet(projectId, selected!.id), enabled: Boolean(selected?.id) && !isAppliedHistory });
  const appliedCounts = countOperations(selected?.operations ?? []);
  const refresh = async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["change-sets", projectId] }), queryClient.invalidateQueries({ queryKey: ["publications", projectId] })]); await refreshWorkspace(); };
  const createMutation = useMutation({ mutationFn: (input: { title: string; description: string | null; operations: ChangeOperation[] }) => api.createChangeSet(projectId, { ...input, base_revision: projectRevision, created_by: "developer" }), onSuccess: async (item) => { setSelectedId(item.id); setNotice(`变更集“${item.title}”已创建。`); await refresh(); }, onError: (value) => setError(value) });
  const commandMutation = useMutation({ mutationFn: ({ item, command }: { item: ChangeSet; command: "validate" | "approve" | "apply" }) => command === "validate" ? api.validateChangeSet(projectId, item.id) : command === "approve" ? api.approveChangeSet(projectId, item.id) : api.applyChangeSet(projectId, item.id), onSuccess: async (item, variables) => { setNotice(`${commandLabel(variables.command)}完成，当前状态：${item.status}。`); await refresh(); await queryClient.invalidateQueries({ queryKey: ["change-preview", projectId, item.id] }); }, onError: (value) => setError(value) });
  const publishMutation = useMutation({ mutationFn: ({ label, notes }: { label: string; notes: string | null }) => api.publishProject(projectId, { label, notes, expected_project_revision: projectRevision }), onSuccess: async (item) => { setNotice(`正式版本 ${item.version} 已发布。`); await refresh(); }, onError: (value) => setError(value) });
  const create = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); try { const operations = JSON.parse(String(form.get("operations") || "[]")) as unknown; if (!Array.isArray(operations)) throw new Error("变更操作必须是 JSON 数组。"); createMutation.mutate({ title: String(form.get("title") ?? "").trim(), description: String(form.get("description") ?? "").trim() || null, operations: operations as ChangeOperation[] }); } catch (value) { setError(asError(value)); } };
  const publish = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); publishMutation.mutate({ label: String(form.get("label") ?? "").trim(), notes: String(form.get("notes") ?? "").trim() || null }); };
  const run = (command: "validate" | "approve" | "apply") => { if (selected) commandMutation.mutate({ item: selected, command }); };

  return <div className="advanced-stack">
    {notice && <StatusMessage title="审核结果" description={notice} />}
    {error && <StatusMessage tone="danger" title="变更没有完成" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    <section className="panel nested-panel"><div className="panel-heading"><div><h3>Agent 建模提案与变更集</h3><p>正式写入前必须预览、校验、审批、应用。</p></div><span>项目修订 {projectRevision}</span></div><div className="change-set-layout"><div className="change-set-list">{(changeSetsQuery.data?.items ?? []).map((item) => <button key={item.id} className={selected?.id === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span><strong>{item.title}</strong><small>{item.status} · 基于修订 {item.base_revision}</small></span><span>{item.operations?.length ?? 0} 项</span></button>)}{!changeSetsQuery.data?.items.length && <p className="empty-copy">暂无提案。企业投影 Agent 的建模结果会进入这里。</p>}</div><div className="change-preview">{selected && isAppliedHistory ? <><div className="subsection-heading"><h4>{selected.title}</h4><span>已应用</span></div><div className="preview-counts"><span>新建<strong>{appliedCounts.creates}</strong></span><span>修改<strong>{appliedCounts.updates}</strong></span><span>退役<strong>{appliedCounts.retires}</strong></span></div><div className="validation-box"><strong>历史变更已成功应用</strong><p>这是不可变执行记录；不会用当前投影重新校验并产生伪冲突。</p></div></> : selected && previewQuery.data ? <><div className="subsection-heading"><h4>{selected.title}</h4><span>{selected.status}</span></div><div className="preview-counts"><span>新建<strong>{previewQuery.data.creates}</strong></span><span>修改<strong>{previewQuery.data.updates}</strong></span><span>退役<strong>{previewQuery.data.retires}</strong></span></div><div className="validation-box"><strong>{previewQuery.data.validation.valid ? "校验通过" : "存在校验问题"}</strong>{previewQuery.data.validation.issues.map((issue, index) => <p key={index}>{issue.code}：{issue.message}</p>)}</div><div className="command-toolbar"><button className="button secondary" disabled={!['DRAFT','INVALID'].includes(selected.status) || commandMutation.isPending} onClick={() => run("validate")}>校验</button><button className="button secondary" disabled={selected.status !== "VALID" || commandMutation.isPending} onClick={() => run("approve")}>审批</button><button className="button primary" disabled={selected.status !== "APPROVED" || commandMutation.isPending} onClick={() => run("apply")}>应用</button></div></> : <p className="empty-copy">选择变更集查看校验结果。</p>}</div></div><details className="inline-advanced"><summary>手动建立变更集</summary><form className="create-form change-set-form" onSubmit={create}><div className="form-grid-two"><label>标题<input name="title" required /></label><label>说明<input name="description" /></label></div><label>操作 JSON 数组<textarea name="operations" rows={5} defaultValue="[]" spellCheck={false} /></label><button className="button secondary" disabled={createMutation.isPending}>创建变更集</button></form></details></section>
    <section className="panel nested-panel"><div className="panel-heading"><div><h3>正式发布</h3><p>一次冻结当前本体与企业投影；只有这个版本会出现在管理层使用端。</p></div><span>{publicationsQuery.data?.total ?? 0} 个版本</span></div><div className="publish-layout"><div className="publication-list">{(publicationsQuery.data?.items ?? []).map((item) => <article key={item.id}><strong>v{item.version} · {item.label}</strong><small>修订 {item.project_revision} · {item.entity_count} 对象 / {item.relation_count} 关系 · {formatDate(item.created_at)}</small></article>)}{!publicationsQuery.data?.items.length && <p className="empty-copy">尚无正式发布版本。</p>}</div><form className="create-form" onSubmit={publish}><label>版本名称<input name="label" required placeholder="例如：管理层基线版" /></label><label>发布说明<textarea name="notes" rows={2} /></label><button className="button primary" disabled={publishMutation.isPending}>正式发布</button></form></div></section>
  </div>;
}

function asError(value: unknown) { return value instanceof Error ? value : new Error("输入无法解析。"); }
function countOperations(operations: ChangeOperation[]) {
  return {
    creates: operations.filter((item) => item.kind.startsWith("CREATE_")).length,
    updates: operations.filter((item) => item.kind.startsWith("UPDATE_")).length,
    retires: operations.filter((item) => item.kind.startsWith("RETIRE_")).length,
  };
}
function commandLabel(command: "validate" | "approve" | "apply") { return ({ validate: "校验", approve: "审批", apply: "应用" })[command]; }
function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
