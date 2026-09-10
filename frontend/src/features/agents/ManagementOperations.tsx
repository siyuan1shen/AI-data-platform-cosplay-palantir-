import { type FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type {
  Hypothesis,
  HypothesisStatus,
  InformationRequest,
  InformationRequestUpdate,
  MeetingRecord,
  MeetingRecordCreate,
  MeetingRecordUpdate,
  MetricDefinition,
  MetricDefinitionCreate,
  MetricObservationCreate,
} from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

const hypothesisStatuses: HypothesisStatus[] = ["EXPLORING", "NEEDS_EVIDENCE", "CONFIRMED", "PARTIALLY_CONFIRMED", "REJECTED", "DEFERRED"];

export function ManagementOperations({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [requestAnswers, setRequestAnswers] = useState<Record<string, string>>({});
  const [hypothesisComments, setHypothesisComments] = useState<Record<string, string>>({});
  const [hypothesisStates, setHypothesisStates] = useState<Record<string, HypothesisStatus>>({});
  const [selectedMetricId, setSelectedMetricId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);

  const requestsQuery = useQuery({ queryKey: ["management-information-requests", projectId], queryFn: () => api.listInformationRequests(projectId) });
  const hypothesesQuery = useQuery({ queryKey: ["hypotheses", projectId], queryFn: () => api.listHypotheses(projectId) });
  const meetingsQuery = useQuery({ queryKey: ["management-meetings", projectId], queryFn: () => api.listManagementMeetings(projectId) });
  const metricsQuery = useQuery({ queryKey: ["management-metrics", projectId], queryFn: () => api.listManagementMetrics(projectId) });
  const selectedMetric = metricsQuery.data?.items.find((item) => item.id === selectedMetricId) ?? metricsQuery.data?.items[0];
  const metricObservationsQuery = useQuery({
    queryKey: ["management-metric-observations", projectId, selectedMetric?.id],
    queryFn: () => api.listManagementMetricObservations(projectId, selectedMetric!.id),
    enabled: Boolean(selectedMetric?.id),
  });

  useEffect(() => {
    if (selectedMetric && selectedMetric.id !== selectedMetricId) setSelectedMetricId(selectedMetric.id);
  }, [selectedMetric?.id, selectedMetricId]);

  const refresh = (...keys: string[]) => Promise.all(keys.map((key) => queryClient.invalidateQueries({ queryKey: [key, projectId] })));
  const requestMutation = useMutation({
    mutationFn: ({ item, status }: { item: InformationRequest; status: "ANSWERED" | "CANCELLED" }) => api.updateInformationRequest(projectId, item.id, informationRequestUpdate(item, status, requestAnswers[item.id] ?? "")),
    onSuccess: async (item) => { setNotice(item.status === "ANSWERED" ? `已回答“${item.title}”。` : `已取消“${item.title}”。`); await refresh("management-information-requests"); },
    onError: (value) => setError(value),
  });
  const hypothesisMutation = useMutation({
    mutationFn: (item: Hypothesis) => api.addHypothesisFeedback(projectId, item.id, { status: hypothesisStates[item.id] ?? item.status, comment: hypothesisComments[item.id]?.trim() || null, provided_by: "management" }),
    onSuccess: async () => { setNotice("管理层假设反馈已保存。"); await refresh("hypotheses"); },
    onError: (value) => setError(value),
  });
  const meetingMutation = useMutation({
    mutationFn: (input: MeetingRecordCreate) => api.createManagementMeeting(projectId, input),
    onSuccess: async (item) => { setNotice(`会议“${item.title}”已登记。`); await refresh("management-meetings"); },
    onError: (value) => setError(value),
  });
  const meetingUpdateMutation = useMutation({
    mutationFn: (item: MeetingRecord) => api.updateManagementMeeting(projectId, item.id, completeMeetingActions(item)),
    onSuccess: async (item) => { setNotice(`“${item.title}”的行动项已更新。`); await refresh("management-meetings"); },
    onError: (value) => setError(value),
  });
  const metricMutation = useMutation({
    mutationFn: (input: MetricDefinitionCreate) => api.createManagementMetric(projectId, input),
    onSuccess: async (item) => { setSelectedMetricId(item.id); setNotice(`指标“${item.name}”已建立。`); await refresh("management-metrics"); },
    onError: (value) => setError(value),
  });
  const metricUpdateMutation = useMutation({
    mutationFn: (item: MetricDefinition) => api.updateManagementMetric(projectId, item.id, { expected_revision: item.revision, active: !item.active }),
    onSuccess: async (item) => { setNotice(`指标“${item.name}”已${item.active ? "启用" : "停用"}。`); await refresh("management-metrics"); },
    onError: (value) => setError(value),
  });
  const metricObservationMutation = useMutation({
    mutationFn: ({ metricId, input }: { metricId: string; input: MetricObservationCreate }) => api.addManagementMetricObservation(projectId, metricId, input),
    onSuccess: async () => { setNotice("指标观测值已记录。"); await queryClient.invalidateQueries({ queryKey: ["management-metric-observations", projectId] }); },
    onError: (value) => setError(value),
  });

  const createMeeting = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const actionTitle = String(form.get("action_title") ?? "").trim();
    meetingMutation.mutate({
      title: String(form.get("title") ?? "").trim(),
      occurred_at: toIsoDate(String(form.get("occurred_at") ?? "")),
      topics: splitItems(String(form.get("topics") ?? "")),
      participant_entity_ids: [],
      related_entity_ids: [],
      decisions: [],
      escalations: [],
      evidence: [],
      action_items: actionTitle ? [{ title: actionTitle, status: "OPEN", expected_outcome: String(form.get("expected_outcome") ?? "").trim() || null }] : [],
    });
    event.currentTarget.reset();
  };
  const createMetric = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    metricMutation.mutate({
      key: String(form.get("key") ?? "").trim(),
      name: String(form.get("name") ?? "").trim(),
      description: String(form.get("description") ?? "").trim() || null,
      scope: String(form.get("scope")) as MetricDefinitionCreate["scope"],
      direction: String(form.get("direction")) as MetricDefinitionCreate["direction"],
      target_value: parseManagementValue(String(form.get("target_value") ?? "")),
      unit: String(form.get("unit") ?? "").trim() || null,
      properties: {},
    });
    event.currentTarget.reset();
  };
  const addMetricObservation = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const metricId = String(form.get("metric_id") ?? "");
    if (!metricId) return;
    metricObservationMutation.mutate({ metricId, input: { period_key: String(form.get("period_key") ?? "").trim(), value: parseManagementValue(String(form.get("value") ?? "")), status: String(form.get("status")) as MetricObservationCreate["status"], source: "MANUAL", evidence: [] } });
    setSelectedMetricId(metricId);
    event.currentTarget.reset();
  };

  return <section className="panel management-operations">
    <div className="panel-heading"><div><p className="eyebrow">MANAGEMENT FEEDBACK LOOP</p><h2>管理信息与现实结果</h2><p>把管理层确认、会议事实和指标结果反馈回项目，供下一次 Agent 分析使用。</p></div></div>
    {notice && <div className="inline-notice">{notice}</div>}
    {error && <StatusMessage tone="danger" title="管理操作没有保存" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    <div className="management-operation-grid">
      <details className="management-operation" open><summary><strong>回答信息请求</strong><span>{requestsQuery.data?.items.filter((item) => item.status === "OPEN").length ?? 0} 待回答</span></summary><div className="operation-list">{(requestsQuery.data?.items ?? []).map((item) => <article key={item.id}><div className="resource-title"><strong>{item.title}</strong><span className="status-pill">{item.priority} · {item.status}</span></div><p>{item.question}</p><small>原因：{item.reason}</small>{item.status === "OPEN" ? <><textarea rows={2} value={requestAnswers[item.id] ?? ""} onChange={(event) => setRequestAnswers((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="输入事实、数据位置或管理层判断" /><div className="command-toolbar"><button className="button primary" disabled={!requestAnswers[item.id]?.trim() || requestMutation.isPending} onClick={() => requestMutation.mutate({ item, status: "ANSWERED" })}>提交回答</button><button className="button text-button danger-text" disabled={requestMutation.isPending} onClick={() => requestMutation.mutate({ item, status: "CANCELLED" })}>取消请求</button></div></> : item.answer && <blockquote>{item.answer}</blockquote>}</article>)}{!requestsQuery.data?.items.length && <p className="empty-copy">暂无信息请求。</p>}</div></details>

      <details className="management-operation"><summary><strong>确认探索假设</strong><span>{hypothesesQuery.data?.total ?? 0} 条</span></summary><div className="operation-list">{(hypothesesQuery.data?.items ?? []).map((item) => <article key={item.id}><div className="resource-title"><strong>{item.title}</strong><span className="status-pill">{item.status}</span></div><p>{item.summary}</p>{item.validation_questions?.length ? <small>待验证：{item.validation_questions.join("；")}</small> : null}<div className="form-grid-two"><label>判断<select value={hypothesisStates[item.id] ?? item.status} onChange={(event) => setHypothesisStates((current) => ({ ...current, [item.id]: event.target.value as HypothesisStatus }))}>{hypothesisStatuses.map((status) => <option key={status} value={status}>{status}</option>)}</select></label><label>反馈<input value={hypothesisComments[item.id] ?? ""} onChange={(event) => setHypothesisComments((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="证据、反例或判断理由" /></label></div><button className="button secondary" disabled={hypothesisMutation.isPending} onClick={() => hypothesisMutation.mutate(item)}>保存反馈</button></article>)}{!hypothesesQuery.data?.items.length && <p className="empty-copy">暂无探索假设。</p>}</div></details>

      <details className="management-operation"><summary><strong>会议与行动结果</strong><span>{meetingsQuery.data?.total ?? 0} 场</span></summary><div className="operation-form-wrap"><form className="create-form" onSubmit={createMeeting}><div className="form-grid-two"><label>会议标题<input name="title" required /></label><label>发生时间<input name="occurred_at" type="datetime-local" defaultValue={localDateTimeNow()} required /></label></div><label>议题（用分号分隔）<input name="topics" /></label><div className="form-grid-two"><label>首个行动项<input name="action_title" /></label><label>预期结果<input name="expected_outcome" /></label></div><button className="button secondary" disabled={meetingMutation.isPending}>登记会议</button></form></div><div className="operation-list">{(meetingsQuery.data?.items ?? []).map((item) => { const openActions = item.action_items?.filter((action) => action.status === "OPEN") ?? []; return <article key={item.id}><div className="resource-title"><strong>{item.title}</strong><span>{formatDate(item.occurred_at)}</span></div><p>{item.topics?.join("；") || "未记录议题"}</p><small>决定 {item.decisions?.length ?? 0} · 行动项 {item.action_items?.length ?? 0} · 待完成 {openActions.length}</small>{openActions.length > 0 && <button className="button text-button" disabled={meetingUpdateMutation.isPending} onClick={() => meetingUpdateMutation.mutate(item)}>将行动项标记为完成</button>}</article>; })}{!meetingsQuery.data?.items.length && <p className="empty-copy">暂无会议记录。</p>}</div></details>

      <details className="management-operation"><summary><strong>指标与实际结果</strong><span>{metricsQuery.data?.total ?? 0} 个指标</span></summary><div className="operation-form-wrap"><form className="create-form" onSubmit={createMetric}><div className="form-grid-two"><label>指标键<input name="key" required placeholder="order_cycle_time" /></label><label>指标名称<input name="name" required /></label></div><label>说明<input name="description" /></label><div className="form-grid-two"><label>范围<select name="scope" defaultValue="LOCAL"><option value="LOCAL">局部</option><option value="ENTERPRISE_OUTCOME">企业结果</option></select></label><label>方向<select name="direction" defaultValue="HIGHER_IS_BETTER"><option value="HIGHER_IS_BETTER">越高越好</option><option value="LOWER_IS_BETTER">越低越好</option><option value="TARGET_RANGE">目标区间</option></select></label></div><div className="form-grid-two"><label>目标值<input name="target_value" placeholder="数值、文本或 JSON" /></label><label>单位<input name="unit" /></label></div><button className="button secondary" disabled={metricMutation.isPending}>建立指标</button></form>{(metricsQuery.data?.items?.length ?? 0) > 0 && <form className="create-form metric-observation-form" onSubmit={addMetricObservation}><h4>记录实际结果</h4><label>指标<select name="metric_id" value={selectedMetric?.id ?? ""} onChange={(event) => setSelectedMetricId(event.target.value)}>{(metricsQuery.data?.items ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><div className="form-grid-two"><label>期间<input name="period_key" required placeholder="2026-Q3 / 2026-09" /></label><label>观测值<input name="value" required /></label></div><label>结果状态<select name="status" defaultValue="UNKNOWN"><option value="UNKNOWN">待判断</option><option value="ON_TARGET">达标</option><option value="MISS">未达标</option></select></label><button className="button primary" disabled={metricObservationMutation.isPending}>记录观测值</button></form>}</div><div className="operation-list metric-list">{(metricsQuery.data?.items ?? []).map((item) => <article key={item.id} className={item.id === selectedMetric?.id ? "selected" : ""} onClick={() => setSelectedMetricId(item.id)}><div className="resource-title"><strong>{item.name}</strong><span className="status-pill">{item.active ? "启用" : "停用"}</span></div><p>{item.key} · {item.scope} · {item.direction}</p><small>目标：{item.target_value == null ? "未设定" : String(item.target_value)} {item.unit || ""}</small><button className="button text-button" disabled={metricUpdateMutation.isPending} onClick={(event) => { event.stopPropagation(); metricUpdateMutation.mutate(item); }}>{item.active ? "停用" : "启用"}</button></article>)}{!metricsQuery.data?.items.length && <p className="empty-copy">暂无指标。</p>}</div>{selectedMetric && <div className="metric-observation-history"><strong>{selectedMetric.name} 的观测历史</strong>{(metricObservationsQuery.data?.items ?? []).map((item) => <span key={item.id}>{item.period_key} · {String(item.value)} · {item.status}</span>)}{!metricObservationsQuery.data?.items.length && <small>暂无实际结果。</small>}</div>}</details>
    </div>
  </section>;
}

export function informationRequestUpdate(item: InformationRequest, status: "ANSWERED" | "CANCELLED", answer: string): InformationRequestUpdate {
  return { expected_revision: item.revision, status, ...(status === "ANSWERED" ? { answer: answer.trim() } : {}) };
}

export function completeMeetingActions(item: MeetingRecord): MeetingRecordUpdate {
  return { expected_revision: item.revision, action_items: (item.action_items ?? []).map((action) => ({ ...action, status: action.status === "OPEN" ? "DONE" : action.status })) };
}

export function parseManagementValue(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return null;
  try { return JSON.parse(trimmed) as unknown; } catch { return trimmed; }
}

function splitItems(value: string) { return value.split(/[；;\n]/).map((item) => item.trim()).filter(Boolean); }
function toIsoDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString(); }
function localDateTimeNow() { const date = new Date(Date.now() - new Date().getTimezoneOffset() * 60_000); return date.toISOString().slice(0, 16); }
function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
