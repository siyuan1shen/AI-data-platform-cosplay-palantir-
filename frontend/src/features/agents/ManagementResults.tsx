import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { ManagementInsight, ManagementInsightClassification, ManagementInsightStatus, ManagementInsightUpdate } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";
import { ManagementOperations } from "./ManagementOperations";

const classificationLabels: Record<ManagementInsightClassification, string> = {
  CORROBORATED: "相互印证",
  STRUCTURAL_WARNING: "结构性预警",
  UNEXPLAINED_ANOMALY: "未解释异常",
  EVIDENCE_CONFLICT: "证据冲突",
  ACCEPTED_TRADEOFF: "已接受取舍",
};
const statusLabels: Record<ManagementInsightStatus, string> = {
  OPEN: "待判断",
  CONFIRMED: "已确认",
  DISMISSED: "已驳回",
  MONITORING: "监控中",
  RESOLVED: "已解决",
};

export function ManagementResults({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [selectedIssueId, setSelectedIssueId] = useState("");
  const [reopenReason, setReopenReason] = useState("");
  const insightsQuery = useQuery({ queryKey: ["management-insights", projectId], queryFn: () => api.listManagementInsights(projectId) });
  const issuesQuery = useQuery({ queryKey: ["management-issues", projectId], queryFn: () => api.listManagementIssues(projectId) });
  const selectedIssue = issuesQuery.data?.items.find((item) => item.id === selectedIssueId);
  const occurrencesQuery = useQuery({ queryKey: ["management-issue-occurrences", projectId, selectedIssueId], queryFn: () => api.listManagementIssueOccurrences(projectId, selectedIssueId), enabled: Boolean(selectedIssueId) });
  const issueFeedbackQuery = useQuery({ queryKey: ["management-issue-feedback", projectId, selectedIssueId], queryFn: () => api.listManagementIssueFeedback(projectId, selectedIssueId), enabled: Boolean(selectedIssueId) });
  const runsQuery = useQuery({ queryKey: ["management-analysis-runs", projectId], queryFn: () => api.listManagementAnalysisRuns(projectId) });
  const metricsQuery = useQuery({ queryKey: ["management-metrics", projectId], queryFn: () => api.listManagementMetrics(projectId) });
  const meetingsQuery = useQuery({ queryKey: ["management-meetings", projectId], queryFn: () => api.listManagementMeetings(projectId) });
  const tradeoffsQuery = useQuery({ queryKey: ["management-tradeoffs", projectId], queryFn: () => api.listDesignTradeoffs(projectId) });
  const requestsQuery = useQuery({ queryKey: ["management-information-requests", projectId], queryFn: () => api.listInformationRequests(projectId) });
  const causalQuery = useQuery({ queryKey: ["causal-hypotheses", projectId], queryFn: () => api.listCausalHypotheses(projectId) });
  const readError = [insightsQuery.error, issuesQuery.error, occurrencesQuery.error, issueFeedbackQuery.error, runsQuery.error, metricsQuery.error, meetingsQuery.error, tradeoffsQuery.error, requestsQuery.error, causalQuery.error].find(Boolean);

  const updateMutation = useMutation({
    mutationFn: ({ insight, status }: { insight: ManagementInsight; status: ManagementInsightStatus }) => api.updateManagementInsight(projectId, insight.id, managementInsightUpdate(insight, status, feedback)),
    onSuccess: async (item) => { setNotice(`“${item.title}”已更新为${statusLabels[item.status]}。`); await Promise.all([queryClient.invalidateQueries({ queryKey: ["management-insights", projectId] }), queryClient.invalidateQueries({ queryKey: ["management-issues", projectId] }), queryClient.invalidateQueries({ queryKey: ["management-issue-feedback", projectId] })]); },
    onError: (value) => setError(value),
  });
  const reopenMutation = useMutation({
    mutationFn: () => {
      if (!selectedIssue || !reopenReason.trim()) throw new Error("请填写重新检查原因。");
      return api.reopenManagementIssue(projectId, selectedIssue.id, { reason: reopenReason.trim(), provided_by: "management", expected_revision: selectedIssue.revision });
    },
    onSuccess: async () => { setReopenReason(""); setNotice("该问题已显式重新打开，后续分析会保留完整出现历史。"); await Promise.all([issuesQuery.refetch(), insightsQuery.refetch(), issueFeedbackQuery.refetch()]); },
    onError: (value) => setError(value),
  });

  return <div className="management-results-stack">
    <section className="panel management-insights">
      <div className="panel-heading"><div><p className="eyebrow">MANAGEMENT INSIGHTS</p><h2>管理洞察审核</h2><p>Agent 只提出证据化判断；管理层负责确认、驳回或持续监控。</p></div><span>{insightsQuery.data?.total ?? 0} 条</span></div>
      {notice && <div className="inline-notice">{notice}</div>}
      {error && <StatusMessage tone="danger" title="洞察反馈没有保存" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
      {readError && <StatusMessage tone="danger" title="管理结果读取失败" description={(readError as Error).message} action={{ label: "重试", onClick: () => void queryClient.invalidateQueries({ predicate: (query) => query.queryKey.includes(projectId) }) }} />}
      <div className="insight-card-grid">{(insightsQuery.data?.items ?? []).map((insight) => <article key={insight.id} className={`insight-card insight-${insight.classification.toLowerCase()}`}><div className="insight-title"><span className="status-pill">{classificationLabels[insight.classification]}</span><span className="status-pill">{statusLabels[insight.status]}</span>{insight.evidence_changed && <span className="status-pill">证据有变化</span>}</div><h3>{insight.title}</h3><p>{insight.summary}</p><dl><div><dt>严重度</dt><dd>{insight.severity}</dd></div><div><dt>置信度</dt><dd>{Math.round(insight.confidence * 100)}%</dd></div><div><dt>出现次数</dt><dd>{insight.occurrence_number}</dd></div></dl><details><summary>查看判断依据</summary><p>{insight.rationale}</p></details><label>管理层反馈<textarea rows={2} value={feedback[insight.id] ?? insight.management_feedback ?? ""} onChange={(event) => setFeedback((current) => ({ ...current, [insight.id]: event.target.value }))} placeholder="补充事实、判断理由或监控条件" /></label><div className="command-toolbar"><button className="button secondary" disabled={updateMutation.isPending} onClick={() => updateMutation.mutate({ insight, status: "CONFIRMED" })}>确认</button><button className="button text-button danger-text" disabled={updateMutation.isPending} onClick={() => updateMutation.mutate({ insight, status: "DISMISSED" })}>驳回</button><button className="button text-button" disabled={updateMutation.isPending} onClick={() => updateMutation.mutate({ insight, status: "MONITORING" })}>持续监控</button>{insight.issue_id && <button className="button text-button" onClick={() => setSelectedIssueId(insight.issue_id ?? "")}>出现历史</button>}</div></article>)}{!insightsQuery.data?.items.length && <p className="empty-copy">尚无管理洞察。可在上方告诉管理 Agent“分析当前企业的结构与结果信号”。</p>}</div>
      {selectedIssue && <div className="panel nested-panel"><div className="panel-heading"><div><h3>{selectedIssue.title} · 历史</h3><p>稳定问题标识 {selectedIssue.issue_key.slice(0, 12)}…，共出现 {selectedIssue.occurrence_count} 次。</p></div><button className="button text-button" onClick={() => setSelectedIssueId("")}>关闭</button></div><div className="data-release-grid"><div className="compact-list">{(occurrencesQuery.data?.items ?? []).map((item) => <article key={item.id}><div><strong>第 {item.occurrence_number} 次 · {classificationLabels[item.classification]}</strong><small>{formatDate(item.created_at)} · {item.severity} · {item.evidence_changed ? "证据已变化" : "证据相同"}</small><p>{item.summary}</p></div></article>)}</div><div><div className="compact-list">{(issueFeedbackQuery.data?.items ?? []).map((item) => <article key={item.id}><div><strong>{statusLabels[item.from_status]} → {statusLabels[item.to_status]}</strong><small>{item.provided_by} · {formatDate(item.created_at)}</small><p>{item.feedback || "未填写说明"}</p></div></article>)}</div>{selectedIssue.status !== "OPEN" && <form className="create-form" onSubmit={(event) => { event.preventDefault(); reopenMutation.mutate(); }}><label>重新检查原因<textarea value={reopenReason} onChange={(event) => setReopenReason(event.target.value)} required rows={2} /></label><button className="button secondary" disabled={reopenMutation.isPending}>显式重新打开</button></form>}</div></div></div>}
    </section>

    <ManagementOperations projectId={projectId} />

    <details className="advanced-section management-results-drawer">
      <summary><span><strong>Agent 结果与管理记录</strong><small>指标、会议、设计取舍、信息请求和因果假设</small></span><i>共 {(metricsQuery.data?.total ?? 0) + (meetingsQuery.data?.total ?? 0) + (tradeoffsQuery.data?.total ?? 0) + (requestsQuery.data?.total ?? 0) + (causalQuery.data?.total ?? 0)} 项</i></summary>
      <div className="advanced-section-body resource-drawer-grid">
        <ResourceGroup title="管理指标" count={metricsQuery.data?.total ?? 0}>{(metricsQuery.data?.items ?? []).map((item) => <article key={item.id}><strong>{item.name}</strong><small>{item.key} · {item.scope} · {item.direction}</small><p>目标：{item.target_value == null ? "未设定" : String(item.target_value)} {item.unit || ""}</p></article>)}</ResourceGroup>
        <ResourceGroup title="会议观察" count={meetingsQuery.data?.total ?? 0}>{(meetingsQuery.data?.items ?? []).map((item) => <article key={item.id}><strong>{item.title}</strong><small>{formatDate(item.occurred_at)} · {item.participant_entity_ids?.length ?? 0} 个参与对象</small><p>{item.topics?.join("；") || "未记录议题"}</p></article>)}</ResourceGroup>
        <ResourceGroup title="设计取舍" count={tradeoffsQuery.data?.total ?? 0}>{(tradeoffsQuery.data?.items ?? []).map((item) => <article key={item.id}><strong>{item.title}</strong><small>{item.status} · {item.issue_family}</small><p>收益：{item.benefit}<br />代价：{item.cost}</p></article>)}</ResourceGroup>
        <ResourceGroup title="信息请求" count={requestsQuery.data?.total ?? 0}>{(requestsQuery.data?.items ?? []).map((item) => <article key={item.id}><strong>{item.title}</strong><small>{item.priority} · {item.status}</small><p>{item.question}</p></article>)}</ResourceGroup>
        <ResourceGroup title="因果假设" count={causalQuery.data?.total ?? 0}>{(causalQuery.data?.items ?? []).map((item) => <article key={item.id}><strong>{item.title}</strong><small>{item.status} · {item.source}</small><p>{item.mechanism}</p></article>)}</ResourceGroup>
        <ResourceGroup title="分析批次" count={runsQuery.data?.total ?? 0}>{(runsQuery.data?.items ?? []).map((item) => <article key={item.id}><strong>{item.status}</strong><small>{formatDate(item.created_at)}</small><p>设计信号 {item.design_signal_count} · 结果信号 {item.outcome_signal_count} · 洞察 {item.insight_count}</p></article>)}</ResourceGroup>
      </div>
    </details>
  </div>;
}

export function managementInsightUpdate(
  insight: ManagementInsight,
  status: ManagementInsightStatus,
  feedback: Record<string, string>,
): ManagementInsightUpdate {
  const input: ManagementInsightUpdate = { expected_revision: insight.revision, status, provided_by: "management" };
  if (Object.prototype.hasOwnProperty.call(feedback, insight.id)) {
    input.management_feedback = feedback[insight.id].trim() || null;
  }
  return input;
}

function ResourceGroup({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return <section className="resource-group"><div className="subsection-heading"><h3>{title}</h3><span>{count} 项</span></div><div className="resource-list">{count ? children : <p className="empty-copy">暂无记录，管理 Agent 可通过动作提案创建。</p>}</div></section>;
}

function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
