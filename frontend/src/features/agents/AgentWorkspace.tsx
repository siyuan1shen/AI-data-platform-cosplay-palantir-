import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api";
import type { AgentKind, AgentRun, AgentThread } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";
import { ActionCenter } from "../actions/ActionCenter";

const agentLabels: Record<AgentKind, string> = { PROJECTION: "企业投影 Agent", MANAGEMENT_INPUT: "管理信息输入", MANAGEMENT: "管理决策 Agent", SYSTEM_ONTOLOGY: "系统本体 Agent" };
const runningStatuses = new Set(["QUEUED", "RETRIEVING", "PLANNING", "RUNNING_TOOLS", "PRODUCING_PROPOSAL", "VALIDATING"]);
const runStatusLabels: Record<AgentRun["status"], string> = {
  QUEUED: "等待处理",
  RETRIEVING: "检索项目上下文",
  PLANNING: "规划任务",
  RUNNING_TOOLS: "调用工具",
  PRODUCING_PROPOSAL: "生成提案",
  VALIDATING: "校验结果",
  WAITING_REVIEW: "等待人工审核",
  BUDGET_EXHAUSTED: "本轮预算用尽，可继续",
  COMPLETED: "运行完成",
  FAILED: "运行失败",
  CANCELLED: "已取消",
};

export const agentRunStages = [
  { status: "QUEUED", label: "排队" },
  { status: "RETRIEVING", label: "取上下文" },
  { status: "PLANNING", label: "规划" },
  { status: "RUNNING_TOOLS", label: "工具" },
  { status: "PRODUCING_PROPOSAL", label: "生成" },
  { status: "VALIDATING", label: "校验" },
  { status: "WAITING_REVIEW", label: "待审核" },
] as const;

export function getAgentRunStageState(status: AgentRun["status"], stageStatus: typeof agentRunStages[number]["status"]) {
  if (status === "COMPLETED") return "complete" as const;
  if (status === "FAILED" || status === "CANCELLED" || status === "BUDGET_EXHAUSTED") return "pending" as const;
  const current = agentRunStages.findIndex((stage) => stage.status === status);
  const stage = agentRunStages.findIndex((item) => item.status === stageStatus);
  if (stage < current) return "complete" as const;
  if (stage === current) return "current" as const;
  return "pending" as const;
}
const managementPrompts = [
  { key: "run_management_analysis", label: "分析管理信号", prompt: "请运行管理分析，结合当前企业投影、管理指标和近期记录，生成需要我确认的管理洞察与后续动作提案。" },
  { key: "save_information_request", label: "要求补充信息", prompt: "请识别当前判断缺少的关键信息，并提出一条可执行的信息请求供我审核。" },
  { key: "record_design_tradeoff", label: "记录设计取舍", prompt: "请把我接下来描述的组织设计选择整理为收益、代价和监控指标明确的设计取舍动作。" },
  { key: "record_meeting_observation", label: "记录会议观察", prompt: "请把我接下来提供的会议内容整理为议题、决定、升级事项和行动项，并提出记录动作。" },
  { key: "save_causal_hypothesis", label: "保存因果假设", prompt: "请根据当前证据提出一个可证伪的因果假设，写清机制、预测、替代解释和验证办法，并生成保存动作供我审核。" },
] as const;

export function AgentWorkspace({ projectId, kind, title, description }: { projectId: string; kind: AgentKind; title: string; description: string }) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [showTrash, setShowTrash] = useState(false);
  const [content, setContent] = useState("");
  const [selectedDocuments, setSelectedDocuments] = useState<string[]>([]);
  const [selectedCases, setSelectedCases] = useState<string[]>([]);
  const [modelProfileId, setModelProfileId] = useState("");
  const [allowExternalModel, setAllowExternalModel] = useState(false);
  const [shareProjectContext, setShareProjectContext] = useState(false);
  const [includeUnconfirmedMaterial, setIncludeUnconfirmedMaterial] = useState(true);
  const [latestRunId, setLatestRunId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const threadsQuery = useQuery({ queryKey: ["agent-threads", projectId, showTrash], queryFn: () => api.listAgentThreads(projectId, showTrash) });
  const threads = useMemo(() => (threadsQuery.data?.items ?? []).filter((thread) => thread.agent_kind === kind && (showTrash ? thread.trashed : !thread.trashed)), [kind, showTrash, threadsQuery.data]);
  const selected = threads.find((thread) => thread.id === selectedId) ?? threads[0];
  const messagesQuery = useQuery({ queryKey: ["agent-messages", projectId, selected?.id], queryFn: () => api.listAgentMessages(projectId, selected!.id), enabled: Boolean(selected?.id), refetchInterval: 2500 });
  const runsQuery = useQuery({ queryKey: ["agent-runs", projectId], queryFn: () => api.listAgentRuns(projectId), refetchInterval: (query) => (query.state.data?.items.some((run) => runningStatuses.has(run.status)) ? 2500 : false) });
  const documentsQuery = useQuery({ queryKey: ["documents", projectId], queryFn: () => api.listDocuments(projectId) });
  const profilesQuery = useQuery({ queryKey: ["model-profiles"], queryFn: () => api.listModelProfiles() });
  const projectCasesQuery = useQuery({ queryKey: ["learning-cases", projectId], queryFn: () => api.listLearningCases(projectId) });
  const catalogQuery = useQuery({ queryKey: ["learning-case-catalog"], queryFn: () => api.listLearningCaseCatalog() });
  const threadRuns = useMemo(() => (runsQuery.data?.items ?? []).filter((run) => run.thread_id === selected?.id).sort((a, b) => b.updated_at.localeCompare(a.updated_at)), [runsQuery.data, selected?.id]);
  const readError = [threadsQuery.error, messagesQuery.error, runsQuery.error, documentsQuery.error, profilesQuery.error, projectCasesQuery.error, catalogQuery.error].find(Boolean);
  const currentRun = threadRuns.find((run) => run.id === latestRunId) ?? threadRuns[0];
  const currentActionInvocationIds = currentRun?.action_invocation_ids ?? [];
  const enabledProfiles = useMemo(() => (profilesQuery.data?.items ?? []).filter((profile) => profile.enabled), [profilesQuery.data]);
  const caseOptions = useMemo(() => {
    const local = (projectCasesQuery.data?.items ?? []).filter((item) => item.status !== "RETIRED").map((item) => ({ id: item.id, label: `本项目 · ${item.title}` }));
    const catalog = (catalogQuery.data?.items ?? []).map((item) => ({ id: item.id, label: `同行案例 · ${[item.industry, item.organization_scale].filter(Boolean).join(" / ") || "匿名"} · ${item.reusable_summary}` }));
    return [...new Map([...local, ...catalog].map((item) => [item.id, item])).values()];
  }, [catalogQuery.data, projectCasesQuery.data]);

  useEffect(() => { if (selected && selected.id !== selectedId) setSelectedId(selected.id); }, [selected?.id, selectedId]);
  useEffect(() => {
    if (!currentRun || runningStatuses.has(currentRun.status)) return;
    void Promise.all([
      messagesQuery.refetch(),
      queryClient.invalidateQueries({ predicate: (query) => query.queryKey.includes(projectId) }),
      queryClient.invalidateQueries({ queryKey: ["projects"] }),
    ]);
  }, [currentRun?.status, currentRun?.id, projectId, queryClient]);
  useEffect(() => {
    setLatestRunId("");
  }, [selected?.id]);
  useEffect(() => {
    if (!enabledProfiles.some((profile) => profile.id === modelProfileId)) setModelProfileId("");
  }, [enabledProfiles, modelProfileId]);

  const refreshThreads = async () => queryClient.invalidateQueries({ queryKey: ["agent-threads", projectId] });
  const createMutation = useMutation({ mutationFn: () => api.createAgentThread(projectId, { agent_kind: kind, title: `${agentLabels[kind]} 对话` }), onSuccess: async (thread) => { await refreshThreads(); setSelectedId(thread.id); setShowTrash(false); }, onError: (value) => setError(value) });
  const updateMutation = useMutation({ mutationFn: ({ thread, input }: { thread: AgentThread; input: { title?: string; trashed?: boolean } }) => api.updateAgentThread(projectId, thread.id, input), onSuccess: async (thread) => { await refreshThreads(); setSelectedId(thread.trashed ? "" : thread.id); }, onError: (value) => setError(value) });
  const trashMutation = useMutation({ mutationFn: (thread: AgentThread) => api.trashAgentThread(projectId, thread.id), onSuccess: async () => { setSelectedId(""); await refreshThreads(); }, onError: (value) => setError(value) });
  const cancelMutation = useMutation({
    mutationFn: (runId: string) => api.cancelAgentRun(projectId, runId),
    onSuccess: async (run) => { setLatestRunId(run.id); setNotice("Agent 运行已取消；已产生的草稿和动作不会被自动写入正式投影。 "); await queryClient.invalidateQueries({ queryKey: ["agent-runs", projectId] }); },
    onError: (value) => setError(value),
  });
  const sendMutation = useMutation({
    mutationFn: ({ threadId, message }: { threadId: string; message: string }) => api.sendAgentMessage(projectId, threadId, {
      content: message,
      attachment_ids: selectedDocuments,
      include_unconfirmed_material: includeUnconfirmedMaterial,
      model_profile_id: modelProfileId || null,
      allow_external_model: allowExternalModel,
      share_project_context_with_model: shareProjectContext,
      reference_case_ids: selectedCases,
    }),
    onSuccess: async (accepted) => {
      setContent("");
      setSelectedDocuments([]);
      setLatestRunId(accepted.run.id);
      setNotice(`消息已入队，开始运行 ${agentLabels[kind]}。`);
      await queryClient.invalidateQueries({ queryKey: ["agent-messages", projectId, accepted.message.thread_id] });
      await queryClient.invalidateQueries({ queryKey: ["agent-runs", projectId] });
    },
    onError: (value) => setError(value),
  });

  const send = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (selected && content.trim()) sendMutation.mutate({ threadId: selected.id, message: content.trim() }); };
  const rename = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (!selected) return; const form = new FormData(event.currentTarget); const value = String(form.get("title") ?? "").trim(); if (value && value !== selected.title) updateMutation.mutate({ thread: selected, input: { title: value } }); };
  const toggleDocument = (id: string) => setSelectedDocuments((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const toggleCase = (id: string) => setSelectedCases((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);

  return <><section className="panel agent-workspace">
    <div className="panel-heading agent-heading"><div><p className="eyebrow">AGENT WORKSPACE</p><h2>{title}</h2><p>{description}</p></div><button className="button primary" onClick={() => createMutation.mutate()} disabled={createMutation.isPending}>新建对话</button></div>
    {notice && <div className="inline-notice">{notice}</div>}
    {error && <StatusMessage tone="danger" title="Agent 操作失败" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    {readError && <StatusMessage tone="danger" title="Agent 上下文读取失败" description={(readError as Error).message} action={{ label: "重试", onClick: () => void queryClient.invalidateQueries({ predicate: (query) => query.queryKey.includes(projectId) }) }} />}
    <div className="chat-layout">
      <aside className="thread-sidebar">
        <div className="thread-toolbar"><strong>{showTrash ? "回收站" : "对话"}</strong><button className="button text-button" onClick={() => { setShowTrash(!showTrash); setSelectedId(""); }}>{showTrash ? "返回对话" : "回收站"}</button></div>
        <div className="thread-list">{threads.map((thread) => <button key={thread.id} className={thread.id === selected?.id ? "selected" : ""} onClick={() => setSelectedId(thread.id)}><strong>{thread.title}</strong><small>{formatDate(thread.updated_at)}</small></button>)}{!threads.length && <p className="empty-copy">{showTrash ? "回收站为空。" : "新建一个独立对话开始工作。"}</p>}</div>
      </aside>
      <div className="chat-main">
        {selected ? <>
          <div className="chat-topbar"><form onSubmit={rename}><input name="title" defaultValue={selected.title} aria-label="对话名称" key={selected.id} /><button className="button text-button">重命名</button></form><div>{showTrash ? <button className="button text-button" onClick={() => updateMutation.mutate({ thread: selected, input: { trashed: false } })}>恢复</button> : <button className="button text-button danger-text" onClick={() => trashMutation.mutate(selected)}>移入回收站</button>}</div></div>
          <div className="message-list">{(messagesQuery.data?.items ?? []).map((message) => <article key={message.id} className={`message ${message.role.toLowerCase()}`}><div className="message-role">{message.role === "USER" ? "你" : message.role === "ASSISTANT" ? agentLabels[kind] : message.role}</div><p>{message.content}</p>{(message.citations?.length ?? 0) > 0 && <div className="citation-list">{message.citations?.map((citation, index) => <span key={`${citation.source_document_id}-${citation.fragment_id}-${index}`}>证据 {citation.note || citation.fragment_id || index + 1}</span>)}</div>}</article>)}{!messagesQuery.data?.items.length && <div className="chat-empty"><strong>把材料和建模要求交给 {agentLabels[kind]}</strong><p>Agent 的草稿和提案会留在这个独立对话里，正式写入仍需开发者确认。</p></div>}</div>
          {threadRuns.length > 0 && <RunHistory runs={threadRuns} selectedRunId={currentRun?.id ?? ""} onSelect={setLatestRunId} onCancel={(runId) => cancelMutation.mutate(runId)} cancelPending={cancelMutation.isPending} />}
          {!showTrash && <form className="message-composer" onSubmit={send}>
            {!profilesQuery.isLoading && !profilesQuery.error && enabledProfiles.length === 0 && <div className="model-setup-guide" role="status"><div><strong>还没有可用的模型配置</strong><span>可以先发送并使用本地规则能力；要让 Agent 调用大模型，请先保存并测试一个模型配置。</span></div><Link className="button secondary" to="/developer/advanced">配置模型</Link></div>}
            <textarea value={content} onChange={(event) => setContent(event.target.value)} rows={4} placeholder={`告诉 ${agentLabels[kind]} 你要完成什么，可同时引用已导入材料。`} />
            {kind === "MANAGEMENT" && <div className="agent-quick-actions"><span>常用能力</span>{managementPrompts.map((item) => <button type="button" key={item.key} title={item.key} onClick={() => setContent(item.prompt)}>{item.label}</button>)}</div>}
            <details className="agent-context-options">
              <summary>本次使用的材料、模型与参考案例</summary>
              <div className="agent-options-grid">
                <label>模型配置<select value={modelProfileId} onChange={(event) => setModelProfileId(event.target.value)} disabled={!enabledProfiles.length}><option value="">{enabledProfiles.length ? "项目默认模型" : "尚未配置"}</option>{enabledProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>)}</select></label>
                <label className="checkbox-label compact-check"><input type="checkbox" checked={includeUnconfirmedMaterial} onChange={(event) => setIncludeUnconfirmedMaterial(event.target.checked)} />包含未确认材料</label>
                <label className="checkbox-label compact-check"><input type="checkbox" checked={allowExternalModel} disabled={!enabledProfiles.length} onChange={(event) => setAllowExternalModel(event.target.checked)} />允许调用所选外部模型</label>
                <label className="checkbox-label compact-check"><input type="checkbox" checked={shareProjectContext} onChange={(event) => setShareProjectContext(event.target.checked)} />向模型提供项目上下文</label>
              </div>
              <div className="context-option-group"><strong>材料附件</strong><div className="attachment-strip">{(documentsQuery.data?.items ?? []).map((document) => <label key={document.id}><input type="checkbox" checked={selectedDocuments.includes(document.id)} onChange={() => toggleDocument(document.id)} />{document.file_name}</label>)}{!documentsQuery.data?.items.length && <span>尚无可引用材料</span>}</div></div>
              <div className="context-option-group"><strong>参考案例</strong><div className="case-option-list">{caseOptions.map((item) => <label key={item.id}><input type="checkbox" checked={selectedCases.includes(item.id)} onChange={() => toggleCase(item.id)} /><span>{item.label}</span></label>)}{!caseOptions.length && <span className="muted">暂无本项目案例或可复用匿名案例。</span>}</div></div>
            </details>
            <button className="button primary" disabled={!content.trim() || sendMutation.isPending}>{sendMutation.isPending ? "正在提交……" : "发送给 Agent"}</button>
          </form>}
        </> : <div className="chat-empty full"><strong>{showTrash ? "回收站为空" : "还没有对话"}</strong><p>{showTrash ? "删除的对话会出现在这里，并可恢复。" : "点击“新建对话”开始。"}</p></div>}
      </div>
    </div>
  </section>{currentActionInvocationIds.length > 0 && <ActionCenter key={currentRun?.id} projectId={projectId} invocationIds={currentActionInvocationIds} agentRun={currentRun} allowCreate={false} title={`本次运行的动作（${currentActionInvocationIds.length}）`} description="动作必须按“预演 → 审批（如需要）→ 执行”推进；执行完成后，项目上下文与当前页面会自动刷新。" />}</>;
}

function RunHistory({ runs, selectedRunId, onSelect, onCancel, cancelPending }: { runs: AgentRun[]; selectedRunId: string; onSelect: (runId: string) => void; onCancel: (runId: string) => void; cancelPending: boolean }) {
  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? runs[0];
  const materialCoverage = getMaterialCoverage(selectedRun);
  return <section className="agent-run-console" aria-label="Agent 运行记录">
    <div className="run-history-strip">
      <strong>运行记录</strong>
      <div>{runs.map((run, index) => <button type="button" key={run.id} className={run.id === selectedRun.id ? "selected" : ""} onClick={() => onSelect(run.id)}><span>第 {runs.length - index} 次</span><small>{runStatusLabels[run.status]} · {formatDate(run.updated_at)}</small></button>)}</div>
    </div>
    <div className={`run-detail-state ${runningStatuses.has(selectedRun.status) ? "running" : ""} ${selectedRun.status === "FAILED" ? "failed" : ""}`}>
      <div className="run-detail-heading"><div><strong>{runStatusLabels[selectedRun.status]}</strong><small>尝试 {selectedRun.attempt_count} 次 · 更新于 {formatDate(selectedRun.updated_at)}</small></div><div className="button-row"><span>{selectedRun.action_invocation_ids?.length ?? 0} 个动作</span>{runningStatuses.has(selectedRun.status) && <button type="button" className="button text-button danger-text" disabled={cancelPending} onClick={() => onCancel(selectedRun.id)}>{cancelPending ? "取消中……" : "取消运行"}</button>}</div></div>
      <ol className="run-stage-list">{agentRunStages.map((stage) => { const state = getAgentRunStageState(selectedRun.status, stage.status); return <li key={stage.status} className={state}><i aria-hidden="true" /><span>{stage.label}</span></li>; })}</ol>
      <div className="run-artifacts">
        {selectedRun.change_set_id && <span>变更集：{selectedRun.change_set_id}</span>}
        {(selectedRun.action_invocation_ids?.length ?? 0) > 0 && <span>下方可逐项预演、审批和执行</span>}
        {selectedRun.status === "WAITING_REVIEW" && !selectedRun.action_invocation_ids?.length && <span>本次结果需要人工查看，但没有动作提案</span>}
        {materialCoverage.length > 0 && <details className="material-coverage"><summary>材料覆盖：已读 {materialCoverage.reduce((sum, item) => sum + item.included_fragments, 0)} / {materialCoverage.reduce((sum, item) => sum + item.available_fragments, 0)} 片段</summary><ul>{materialCoverage.map((item) => <li key={item.source_document_id}>材料 {item.source_document_id.slice(0, 8)}…：{item.included_fragments} / {item.available_fragments}{item.included_fragments < item.available_fragments ? "（部分读取）" : "（已覆盖）"}</li>)}</ul></details>}
        {selectedRun.error && <details><summary>查看运行错误</summary><pre>{JSON.stringify(selectedRun.error, null, 2)}</pre></details>}
      </div>
    </div>
  </section>;
}

type MaterialCoverage = { source_document_id: string; available_fragments: number; included_fragments: number };

export function getMaterialCoverage(run: AgentRun): MaterialCoverage[] {
  const value = run.context_manifest?.material_coverage;
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is MaterialCoverage => Boolean(item) && typeof item === "object" && typeof item.source_document_id === "string" && Number.isFinite(item.available_fragments) && Number.isFinite(item.included_fragments));
}

function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
