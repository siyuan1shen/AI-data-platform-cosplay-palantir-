import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { ActionDefinition, ActionInvocation, ActionInvocationStatus, ActionObservationCreate, AgentRun } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

const statusLabels: Record<ActionInvocationStatus, string> = {
  DRAFT: "待预演", DRY_RUN_COMPLETED: "预演完成", WAITING_APPROVAL: "待审批", APPROVED: "已审批", RUNNING: "执行中", SUCCEEDED: "已成功", FAILED: "失败", CANCELLED: "已取消", OBSERVING: "观察中", EFFECTIVE: "已验证有效", INEFFECTIVE: "已验证无效", ROLLED_BACK: "已回滚",
};
const statusClass = (status: ActionInvocationStatus) => `status-${status.toLowerCase().replaceAll("_", "-")}`;
const pollingStatuses = new Set<ActionInvocationStatus>(["RUNNING"]);

export function shouldPollActionInvocations(items: ActionInvocation[]) {
  return items.some((item) => pollingStatuses.has(item.status));
}

interface ActionCenterProps {
  projectId: string;
  invocationIds?: string[];
  allowCreate?: boolean;
  title?: string;
  description?: string;
  agentRun?: AgentRun;
}

type Command = "dryRun" | "approve" | "execute" | "cancel" | "retry" | "rollback";
type ObservationDraft = { kind: ActionObservationCreate["observation_kind"]; metricKey: string; periodKey: string; value: string; outcome: ActionObservationCreate["outcome"]; note: string };
const emptyObservation: ObservationDraft = { kind: "QUALITATIVE", metricKey: "", periodKey: "", value: "", outcome: "UNKNOWN", note: "" };

export function ActionCenter({
  projectId,
  invocationIds,
  allowCreate = true,
  title = "方案与行动",
  description = "管理方案先形成动作调用，再按“预演 → 审批 → 执行 → 观察”闭环推进。",
  agentRun,
}: ActionCenterProps) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [inputText, setInputText] = useState("{}");
  const [draftDefinitionId, setDraftDefinitionId] = useState("");
  const [observation, setObservation] = useState<ObservationDraft>(emptyObservation);

  const definitionsQuery = useQuery({ queryKey: ["action-definitions", projectId], queryFn: () => api.listActionDefinitions(projectId) });
  const invocationsQuery = useQuery({
    queryKey: ["action-invocations", projectId],
    queryFn: () => api.listActionInvocations(projectId),
    refetchInterval: (query) => shouldPollActionInvocations(query.state.data?.items ?? []) ? 2000 : false,
  });
  const invocationFilter = useMemo(() => invocationIds ? new Set(invocationIds) : null, [invocationIds]);
  const invocations = useMemo(
    () => (invocationsQuery.data?.items ?? []).filter((item) => !invocationFilter || invocationFilter.has(item.id)),
    [invocationFilter, invocationsQuery.data],
  );
  const selected = invocations.find((item) => item.id === selectedId) ?? invocations[0];
  const selectedDefinition = definitionsQuery.data?.items.find((item) => item.id === selected?.action_definition_id);
  const draftDefinition = definitionsQuery.data?.items.find((item) => item.id === draftDefinitionId) ?? definitionsQuery.data?.items[0];
  const logsQuery = useQuery({ queryKey: ["action-logs", projectId, selected?.id], queryFn: () => api.listActionLogs(projectId, selected?.id), enabled: Boolean(selected?.id) });
  const observationsQuery = useQuery({ queryKey: ["action-observations", projectId, selected?.id], queryFn: () => api.listActionObservations(projectId, selected!.id), enabled: Boolean(selected?.id) });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ predicate: (query) => query.queryKey.includes(projectId) }),
      queryClient.invalidateQueries({ queryKey: ["projects"] }),
    ]);
  };

  const createMutation = useMutation({
    mutationFn: ({ definitionId, input }: { definitionId: string; input: Record<string, unknown> }) => api.createActionInvocation(projectId, { action_definition_id: definitionId, input, requested_by: "management", target_entity_ids: [] }),
    onSuccess: async (invocation) => { setSelectedId(invocation.id); setNotice(`已创建“${invocation.action_name}”调用，下一步先做预演。`); await refresh(); },
    onError: (value) => setError(value),
  });
  const commandMutation = useMutation({
    mutationFn: async ({ invocation, command }: { invocation: ActionInvocation; command: Command }) => {
      if (command === "dryRun") return api.dryRunAction(projectId, invocation.id);
      if (command === "approve") return api.approveAction(projectId, invocation.id, { approved_by: "management" });
      if (command === "execute") return api.executeAction(projectId, invocation.id);
      if (command === "cancel") return api.cancelAction(projectId, invocation.id);
      if (command === "retry") return api.retryAction(projectId, invocation.id, { requested_by: "management", idempotency_key: null });
      return api.rollbackAction(projectId, invocation.id);
    },
    onSuccess: async (invocation, variables) => {
      setSelectedId(invocation.id);
      setNotice(invocation.status === "FAILED" ? `${commandLabel(variables.command)}失败，请查看错误详情。` : `${commandLabel(variables.command)}已完成。`);
      await refresh();
    },
    onError: (value) => setError(value),
  });
  const observationMutation = useMutation({
    mutationFn: ({ invocationId, input }: { invocationId: string; input: ActionObservationCreate }) => api.addActionObservation(projectId, invocationId, input),
    onSuccess: async () => { setObservation(emptyObservation); setNotice("已记录执行后的观察结果。"); await refresh(); if (selected?.id) await queryClient.invalidateQueries({ queryKey: ["action-observations", projectId, selected.id] }); },
    onError: (value) => setError(value),
  });
  const continueAgentMutation = useMutation({
    mutationFn: () => api.continueAgentRun(projectId, agentRun!.id),
    onSuccess: async () => { setNotice("已继续 Agent 任务，正在读取新的工具结果。"); await refresh(); },
    onError: (value) => setError(value),
  });

  const pendingCount = useMemo(() => invocations.filter((item) => ["DRAFT", "DRY_RUN_COMPLETED", "WAITING_APPROVAL", "APPROVED", "RUNNING", "OBSERVING"].includes(item.status)).length, [invocations]);
  const canResumeAgent = canResumeAgentRun(agentRun, invocations);
  useEffect(() => {
    if (!draftDefinition) return;
    setDraftDefinitionId((current) => current || draftDefinition.id);
    setInputText(JSON.stringify(inputTemplate(draftDefinition), null, 2));
  }, [draftDefinition?.id]);
  const createInvocation = (definitionId: string) => {
    try {
      const parsed = JSON.parse(inputText) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("动作输入必须是 JSON 对象，例如 {}。");
      createMutation.mutate({ definitionId, input: parsed as Record<string, unknown> });
    } catch (value) {
      setError(value instanceof Error ? value : new Error("动作输入 JSON 无法解析。"));
    }
  };
  const addObservation = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected?.id || !observation.metricKey.trim() || !observation.value.trim()) return;
    let parsed: unknown = observation.value;
    try { parsed = JSON.parse(observation.value); } catch { /* ordinary text is also a valid observed value */ }
    observationMutation.mutate({ invocationId: selected.id, input: { observation_kind: observation.kind, metric_key: observation.metricKey.trim(), period_key: observation.periodKey.trim() || null, dimensions: {}, observed_value: parsed, outcome: observation.outcome, note: observation.note.trim() || null } });
  };

  if (definitionsQuery.isLoading || invocationsQuery.isLoading) return <section id="actions" className="panel action-center"><p className="panel-loading">正在读取可执行动作……</p></section>;
  if (definitionsQuery.error || invocationsQuery.error) return <StatusMessage tone="danger" title="行动区暂不可用" description={((definitionsQuery.error ?? invocationsQuery.error) as Error).message} action={{ label: "重试", onClick: () => void refresh() }} />;

  return (
    <section id="actions" className="panel action-center">
      <div className="panel-heading action-center-heading"><div><p className="eyebrow">ACTION ENGINE</p><h2>{title}</h2><p>{description}</p></div><div className="button-row"><button className="button secondary" onClick={() => void refresh()}>刷新</button>{canResumeAgent && <button className="button primary" disabled={continueAgentMutation.isPending} onClick={() => continueAgentMutation.mutate()}>{continueAgentMutation.isPending ? "正在继续……" : "继续 Agent"}</button>}</div></div>
      {notice && <div className="inline-notice">{notice}</div>}
      {error && <StatusMessage tone="danger" title="行动没有完成" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
      {allowCreate && <div className="summary-grid compact action-summary">
        <article><span>可执行动作</span><strong>{definitionsQuery.data?.total ?? 0}</strong><small>由项目动作定义提供</small></article>
        <article><span>历史调用</span><strong>{invocationsQuery.data?.total ?? 0}</strong><small>保留完整状态变化</small></article>
        <article><span>进行中</span><strong>{pendingCount}</strong><small>需要继续推进或观察</small></article>
      </div>}
      <div className={`action-layout ${allowCreate ? "" : "review-only"}`}>
        {allowCreate && <div className="action-catalog">
          <div className="subsection-heading"><h3>可执行动作</h3><span>共 {definitionsQuery.data?.total ?? 0} 个</span></div>
          <label className="action-input-label">本次调用输入（JSON）<span className="input-context">当前动作：{draftDefinition?.name || "未选择"} · 按动作定义提供必填字段</span><textarea value={inputText} onChange={(event) => setInputText(event.target.value)} rows={5} spellCheck={false} /></label>
          <div className="action-card-list">
            {(definitionsQuery.data?.items ?? []).filter((definition) => definition.enabled && definition.status !== "RETIRED").map((definition) => (
              <article className={`action-card ${draftDefinition?.id === definition.id ? "active" : ""}`} key={definition.id}>
                <div><strong>{definition.name}</strong><code>{definition.key}</code><button className="button text-button" onClick={() => { setDraftDefinitionId(definition.id); setError(null); }}>选择</button></div>
                <p>{definition.description || "没有填写说明。"}</p>
                <div className="tag-line"><span className="tag">{definition.risk_level}</span><span className="tag">{definition.require_approval ? "需审批" : "低风险直执"}</span></div>
                {draftDefinition?.id === definition.id && <button className="button primary full-button" disabled={createMutation.isPending} onClick={() => createInvocation(definition.id)}>创建调用</button>}
              </article>
            ))}
          </div>
        </div>}
        <div className="invocation-panel">
          <div className="subsection-heading"><h3>调用记录</h3><span>{invocations.length ? "选择一条查看" : "尚无调用"}</span></div>
          <div className="invocation-list">
            {invocations.map((invocation) => <button key={invocation.id} className={`invocation-item ${selected?.id === invocation.id ? "selected" : ""}`} onClick={() => setSelectedId(invocation.id)}><span><strong>{invocation.action_name}</strong><small>{formatDate(invocation.created_at)}</small></span><span className={`status-pill ${statusClass(invocation.status)}`}>{statusLabels[invocation.status]}</span></button>)}
            {!invocations.length && <p className="empty-copy">{allowCreate ? "创建第一个动作调用后，执行轨迹会显示在这里。" : "Agent 本次没有提出需要执行的动作。"}</p>}
          </div>
          {selected && <InvocationDetail invocation={selected} definitionName={selectedDefinition?.name} commandMutation={commandMutation} logs={logsQuery.data?.items ?? []} observations={observationsQuery.data?.items ?? []} observation={observation} setObservation={setObservation} onObservationSubmit={addObservation} observationPending={observationMutation.isPending} />}
        </div>
      </div>
    </section>
  );
}

function InvocationDetail({ invocation, definitionName, commandMutation, logs, observations, observation, setObservation, onObservationSubmit, observationPending }: { invocation: ActionInvocation; definitionName?: string; commandMutation: ReturnType<typeof useMutation<ActionInvocation, Error, { invocation: ActionInvocation; command: Command }>>; logs: Array<{ id: string; event_type: string; actor: string; from_status: ActionInvocationStatus | null; to_status: ActionInvocationStatus | null; created_at: string }>; observations: Array<{ id: string; observation_kind: string; metric_key: string; metric_observation_id?: string | null; period_key?: string | null; observed_value: unknown; outcome: string; note?: string | null; observed_at: string }>; observation: ObservationDraft; setObservation: (value: ObservationDraft) => void; onObservationSubmit: (event: FormEvent<HTMLFormElement>) => void; observationPending: boolean }) {
  const canDryRun = ["DRAFT", "FAILED"].includes(invocation.status);
  const canApprove = invocation.status === "WAITING_APPROVAL";
  const canExecute = ["DRY_RUN_COMPLETED", "APPROVED"].includes(invocation.status) && (!invocation.require_approval || invocation.status === "APPROVED");
  // Keep these transitions identical to the backend state machine.  Showing an
  // impossible command is particularly confusing here because the user only
  // discovers the mismatch after an HTTP 409 response.
  const canCancel = ["DRAFT", "DRY_RUN_COMPLETED", "WAITING_APPROVAL", "APPROVED", "OBSERVING"].includes(invocation.status);
  const canRetry = invocation.status === "FAILED";
  const canRollback = ["SUCCEEDED", "INEFFECTIVE"].includes(invocation.status)
    && ["create_entity", "create_relation"].includes(invocation.action_key);
  const canObserve = ["SUCCEEDED", "OBSERVING", "EFFECTIVE", "INEFFECTIVE"].includes(invocation.status);
  const run = (command: Command) => commandMutation.mutate({ invocation, command });
  return <article className="invocation-detail">
    <div className="detail-title"><div><span className="eyebrow">SELECTED INVOCATION</span><h3>{definitionName || invocation.action_name}</h3><code>{invocation.id}</code></div><span className={`status-pill ${statusClass(invocation.status)}`}>{statusLabels[invocation.status]}</span></div>
    <dl className="invocation-meta"><div><dt>发起人</dt><dd>{invocation.requested_by}</dd></div><div><dt>风险</dt><dd>{invocation.risk_level}</dd></div><div><dt>目标对象</dt><dd>{invocation.target_entity_ids.length || "未指定"}</dd></div><div><dt>幂等键</dt><dd>{invocation.idempotency_key}</dd></div></dl>
    <div className="command-toolbar"><button className="button secondary" disabled={!canDryRun || commandMutation.isPending} onClick={() => run("dryRun")}>预演</button><button className="button secondary" disabled={!canApprove || commandMutation.isPending} onClick={() => run("approve")}>审批</button><button className="button primary" disabled={!canExecute || commandMutation.isPending} onClick={() => run("execute")}>执行</button><button className="button text-button" disabled={!canCancel || commandMutation.isPending} onClick={() => run("cancel")}>取消</button><button className="button text-button" disabled={!canRetry || commandMutation.isPending} onClick={() => run("retry")}>重试</button><button className="button text-button danger-text" disabled={!canRollback || commandMutation.isPending} onClick={() => run("rollback")}>回滚</button></div>
    {invocation.preflight && <ResultPayload title="预演结果" payload={invocation.preflight} />}
    {invocation.result && <ResultPayload title="执行结果" payload={invocation.result} />}
    {invocation.error && <div className="result-box error-box"><strong>错误</strong><pre>{JSON.stringify(invocation.error, null, 2)}</pre></div>}
    <div className="detail-subsection"><div className="subsection-heading"><h4>状态日志</h4><span>{logs.length} 条</span></div>{logs.length ? <ol className="timeline">{logs.map((log) => <li key={log.id}><span>{log.event_type}</span><small>{log.from_status ? `${statusLabels[log.from_status]} → ` : ""}{log.to_status ? statusLabels[log.to_status] : "—"} · {formatDate(log.created_at)}</small></li>)}</ol> : <p className="muted">暂无状态日志。</p>}</div>
    <div className="detail-subsection"><div className="subsection-heading"><h4>结果观察</h4><span>{observations.length} 条</span></div>{observations.length ? <div className="observation-list">{observations.map((item) => <div key={item.id}><strong>{item.metric_key}</strong><span>{String(item.observed_value)}</span><small>{item.observation_kind}{item.period_key ? ` · ${item.period_key}` : ""} · {item.outcome} · {formatDate(item.observed_at)}{item.metric_observation_id ? " · 已写入统一 KPI" : ""}</small></div>)}</div> : <p className="muted">执行后可以记录指标、反馈或人工观察。</p>}{canObserve ? <form className="observation-form" onSubmit={onObservationSubmit}><select value={observation.kind} onChange={(event) => setObservation({ ...observation, kind: event.target.value as ActionObservationCreate["observation_kind"] })}><option value="QUALITATIVE">定性观察</option><option value="METRIC">统一 KPI 结果</option></select><input value={observation.metricKey} onChange={(event) => setObservation({ ...observation, metricKey: event.target.value })} placeholder={observation.kind === "METRIC" ? "必须填写已存在的 KPI 键" : "观察主题"} /><input value={observation.periodKey} onChange={(event) => setObservation({ ...observation, periodKey: event.target.value })} placeholder="周期（KPI 可选，默认当天）" /><input value={observation.value} onChange={(event) => setObservation({ ...observation, value: event.target.value })} placeholder="观察值，文本或 JSON" /><select value={observation.outcome} onChange={(event) => setObservation({ ...observation, outcome: event.target.value as ActionObservationCreate["outcome"] })}><option value="UNKNOWN">未知</option><option value="EFFECTIVE">有效</option><option value="INEFFECTIVE">无效</option></select><input value={observation.note} onChange={(event) => setObservation({ ...observation, note: event.target.value })} placeholder="备注（可选）" /><button className="button secondary" disabled={observationPending}>记录观察</button></form> : <p className="muted">请先完成预演、审批（如需要）和执行，再记录现实结果。</p>}</div>
  </article>;
}

function ResultPayload({ title, payload }: { title: string; payload: Record<string, unknown> }) {
  return <div className="result-box"><strong>{title}</strong><p>{summarizeActionPayload(payload)}</p><details><summary>查看技术详情</summary><pre>{JSON.stringify(payload, null, 2)}</pre></details></div>;
}

export function summarizeActionPayload(payload: Record<string, unknown>) {
  const changes = Array.isArray(payload.would_change) ? payload.would_change : [];
  if (changes.length) {
    const entities = changes.filter((item) => isChangeKind(item, "ENTITY")).length;
    const relations = changes.filter((item) => isChangeKind(item, "RELATION")).length;
    const events = changes.filter((item) => isChangeKind(item, "EVENT")).length;
    const parts = [entities && `${entities} 个对象`, relations && `${relations} 条关系`, events && `${events} 个事件`].filter(Boolean);
    return `${payload.valid === false ? "校验未通过" : "校验通过"}；预计变更 ${parts.join("、") || `${changes.length} 项资源`}。`;
  }
  if (typeof payload.operation_count === "number") return `已原子应用 ${payload.operation_count} 项变更，状态：${String(payload.status || "完成")}。`;
  if (typeof payload.resource === "string") return `${payload.resource} 已处理，状态：${String(payload.status || "完成")}。`;
  return "操作结果已返回；可展开查看技术详情。";
}

const terminalActionStatuses = new Set<ActionInvocationStatus>(["SUCCEEDED", "FAILED", "CANCELLED", "EFFECTIVE", "INEFFECTIVE", "ROLLED_BACK"]);

export function canResumeAgentRun(agentRun: AgentRun | undefined, invocations: ActionInvocation[]): boolean {
  return agentRun?.status === "WAITING_REVIEW" && invocations.length > 0 && invocations.every((item) => terminalActionStatuses.has(item.status));
}

function isChangeKind(value: unknown, resource: string) {
  if (!value || typeof value !== "object") return false;
  const kind = String((value as Record<string, unknown>).kind || "");
  return kind.includes(resource);
}

function commandLabel(command: Command) { return ({ dryRun: "预演", approve: "审批", execute: "执行", cancel: "取消", retry: "重试", rollback: "回滚" })[command]; }
function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
function inputTemplate(definition: ActionDefinition) {
  return Object.fromEntries((definition.parameters ?? []).map((parameter) => [parameter.key, parameter.value_type === "JSON" ? [] : ""]));
}
