import { type FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { AgentKind, EvaluationCase, EvaluationSuite } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

const agentKinds: Array<{ value: AgentKind; label: string }> = [
  { value: "PROJECTION", label: "企业投影 Agent" },
  { value: "MANAGEMENT", label: "管理决策 Agent" },
  { value: "SYSTEM_ONTOLOGY", label: "系统本体 Agent" },
];

export function EvaluationPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [modelProfileId, setModelProfileId] = useState("");
  const [allowExternalModel, setAllowExternalModel] = useState(false);
  const [shareProjectContext, setShareProjectContext] = useState(false);
  const [executionId, setExecutionId] = useState("");
  const suitesQuery = useQuery({ queryKey: ["evaluation-suites", projectId], queryFn: () => api.listEvaluationSuites(projectId) });
  const profilesQuery = useQuery({ queryKey: ["model-profiles"], queryFn: api.listModelProfiles });
  const suites = suitesQuery.data?.items ?? [];
  const selected = suites.find((item) => item.id === selectedId) ?? suites[0];
  const casesQuery = useQuery({ queryKey: ["evaluation-cases", projectId, selected?.id], queryFn: () => api.listEvaluationCases(projectId, selected!.id), enabled: Boolean(selected?.id) });
  const runsQuery = useQuery({ queryKey: ["evaluation-runs", projectId, selected?.id], queryFn: () => api.listEvaluationRuns(projectId, selected!.id), enabled: Boolean(selected?.id) });
  const latestRun = runsQuery.data?.items[0];
  const resultsQuery = useQuery({ queryKey: ["evaluation-results", projectId, latestRun?.id], queryFn: () => api.listEvaluationResults(projectId, latestRun!.id), enabled: Boolean(latestRun?.id) });
  const refresh = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["evaluation-suites", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["evaluation-cases", projectId] }),
  ]);
  const createSuite = useMutation({
    mutationFn: (input: { name: string; agent_kind: AgentKind; description: string | null }) => api.createEvaluationSuite(projectId, input),
    onSuccess: async (item) => { setSelectedId(item.id); setNotice(`评测集“${item.name}”已创建。`); await refresh(); },
    onError: (value) => setError(asError(value)),
  });
  const updateSuite = useMutation({
    mutationFn: (item: EvaluationSuite) => api.updateEvaluationSuite(projectId, item.id, { status: "ACTIVE", expected_revision: item.revision }),
    onSuccess: async () => { setNotice("评测集已启用。现在可以执行真实轨迹评测。"); await refresh(); },
    onError: (value) => setError(asError(value)),
  });
  const createCase = useMutation({
    mutationFn: ({ suiteId, input }: { suiteId: string; input: { name: string; input: string; expected_action_keys: string[]; required_terms: string[]; forbidden_terms: string[]; minimum_citations: number } }) => api.createEvaluationCase(projectId, suiteId, input),
    onSuccess: async () => { setNotice("评测案例已保存。"); await refresh(); },
    onError: (value) => setError(asError(value)),
  });
  const schedule = useMutation({
    mutationFn: (suiteId: string) => api.scheduleEvaluationSuite(projectId, suiteId, { label: "开发端执行", model_profile_id: modelProfileId || null, allow_external_model: allowExternalModel, share_project_context_with_model: shareProjectContext }),
    onSuccess: (execution) => {
      setExecutionId(execution.id);
      setNotice(execution.status === "COMPLETED" ? "评测已完成。" : "评测已提交后台运行，将自动刷新结果。");
    },
    onError: (value) => setError(asError(value)),
  });
  const executionQuery = useQuery({
    queryKey: ["evaluation-execution", projectId, executionId],
    queryFn: () => api.getEvaluationExecution(projectId, executionId),
    enabled: Boolean(executionId),
    refetchInterval: executionId ? 1000 : false,
  });

  useEffect(() => {
    if (!selectedId && suites[0]) setSelectedId(suites[0].id);
    if (selectedId && !suites.some((item) => item.id === selectedId)) setSelectedId(suites[0]?.id ?? "");
  }, [selectedId, suites]);
  useEffect(() => {
    if (!modelProfileId) {
      const defaultProfile = profilesQuery.data?.items.find((item) => item.is_default) ?? profilesQuery.data?.items[0];
      if (defaultProfile) setModelProfileId(defaultProfile.id);
    }
  }, [modelProfileId, profilesQuery.data]);
  useEffect(() => {
    const execution = executionQuery.data;
    if (!execution) return;
    if (execution.status === "COMPLETED") {
      setNotice(`评测完成：结果已持久化（${execution.completed_cases}/${execution.total_cases} 个案例）。`);
      void queryClient.invalidateQueries({ queryKey: ["evaluation-runs", projectId, execution.suite_id] });
      setExecutionId("");
    } else if (execution.status === "FAILED") {
      const detail = execution.error?.message;
      setError(new Error(typeof detail === "string" ? detail : "评测执行失败。"));
      setExecutionId("");
    }
  }, [executionQuery.data]);

  const submitSuite = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    createSuite.mutate({ name: String(form.get("suite_name") ?? "").trim(), agent_kind: String(form.get("agent_kind") ?? "MANAGEMENT") as AgentKind, description: String(form.get("suite_description") ?? "").trim() || null });
    event.currentTarget.reset();
  };
  const submitCase = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    createCase.mutate({ suiteId: selected.id, input: {
      name: String(form.get("case_name") ?? "").trim(),
      input: String(form.get("case_input") ?? "").trim(),
      expected_action_keys: splitList(String(form.get("expected_actions") ?? "")),
      required_terms: splitList(String(form.get("required_terms") ?? "")),
      forbidden_terms: splitList(String(form.get("forbidden_terms") ?? "")),
      minimum_citations: Number(form.get("minimum_citations") ?? 0),
    }});
    event.currentTarget.reset();
  };

  return <section className="panel evaluation-panel">
    <div className="panel-heading"><div><p className="eyebrow">AGENT EVALUATION</p><h2>评测与回归</h2><p>每个案例都会实际创建并运行 Agent，评分读取持久化结果，不接受自报动作和引用数量。</p></div><span>{suites.length} 个评测集</span></div>
    {notice && <StatusMessage title="评测状态" description={notice} />}
    {error && <StatusMessage tone="danger" title="评测操作失败" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    <div className="evaluation-layout">
      <div className="evaluation-suites">
        <div className="subsection-heading"><h3>评测集</h3><span>{selected?.status ?? "未选择"}</span></div>
        {(suitesQuery.data?.items ?? []).map((item) => <button key={item.id} className={selected?.id === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span><strong>{item.name}</strong><small>{item.agent_kind} · {item.status}</small></span><span>v{item.revision}</span></button>)}
        {!suites.length && <p className="empty-copy">还没有评测集。先在右侧创建一个。</p>}
        <form className="create-form" onSubmit={submitSuite}><h4>新建评测集</h4><label>名称<input name="suite_name" required /></label><label>Agent<select name="agent_kind" defaultValue="MANAGEMENT">{agentKinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label>说明<textarea name="suite_description" rows={2} /></label><button className="button secondary" disabled={createSuite.isPending}>保存评测集</button></form>
      </div>
      <div className="evaluation-cases">
        {!selected ? <p className="empty-copy">选择或创建评测集后添加案例。</p> : <>
          <div className="subsection-heading"><div><h3>{selected.name} · 案例</h3><small>{casesQuery.data?.total ?? 0} 个有效案例{executionQuery.data && executionId ? ` · ${executionQuery.data.completed_cases}/${executionQuery.data.total_cases} 已完成` : ""}</small></div><div className="button-row">{selected.status === "DRAFT" && <button className="button secondary" onClick={() => updateSuite.mutate(selected)} disabled={updateSuite.isPending}>启用</button>}{selected.status === "ACTIVE" && <button className="button primary" onClick={() => schedule.mutate(selected.id)} disabled={schedule.isPending || Boolean(executionId) || !(casesQuery.data?.total)}>{schedule.isPending || executionId ? "运行中……" : "执行评测"}</button>}</div></div>
          {selected.status === "ACTIVE" && <div className="evaluation-run-options"><label>模型<select value={modelProfileId} onChange={(event) => setModelProfileId(event.target.value)}><option value="">项目默认模型</option>{(profilesQuery.data?.items ?? []).filter((item) => item.enabled).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model}</option>)}</select></label><label className="checkbox-row"><input type="checkbox" checked={allowExternalModel} onChange={(event) => setAllowExternalModel(event.target.checked)} />允许发送到外部模型</label><label className="checkbox-row"><input type="checkbox" checked={shareProjectContext} onChange={(event) => setShareProjectContext(event.target.checked)} disabled={!allowExternalModel} />允许共享项目上下文</label></div>}
          <div className="compact-list">{(casesQuery.data?.items ?? []).map((item) => <CaseRow key={item.id} item={item} />)}{!casesQuery.data?.items.length && <p className="empty-copy">还没有案例。</p>}</div>
          {latestRun && <div className="evaluation-results">
            <div className="subsection-heading"><div><h4>最近一次运行结果</h4><small>{latestRun.passed_cases}/{latestRun.total_cases} 通过 · 平均分 {Math.round(latestRun.average_score * 100)}%</small></div><span>{latestRun.status}</span></div>
            <div className="compact-list">{(resultsQuery.data?.items ?? []).map((item) => <article key={item.id}><div><strong>{item.passed ? "通过" : "未通过"} · {Math.round(item.score * 100)}%</strong><small>动作：{item.candidate_action_keys.join(", ") || "无"} · 可验证引用：{item.candidate_citation_count}</small><details><summary>查看 Agent 输出</summary><p>{item.candidate_content}</p></details></div></article>)}</div>
          </div>}
          <form className="create-form" onSubmit={submitCase}><h4>添加案例</h4><label>案例名称<input name="case_name" required /></label><label>输入内容<textarea name="case_input" rows={3} required /></label><div className="form-grid-two"><label>期望动作（逗号分隔）<input name="expected_actions" placeholder="save_information_request" /></label><label>最少引用数<input name="minimum_citations" type="number" min="0" defaultValue="0" /></label><label>必须包含（逗号分隔）<input name="required_terms" /></label><label>禁止包含（逗号分隔）<input name="forbidden_terms" /></label></div><button className="button secondary" disabled={createCase.isPending}>保存案例</button></form>
        </>}
      </div>
    </div>
  </section>;
}

function CaseRow({ item }: { item: EvaluationCase }) {
  return <article><div><strong>{item.name}</strong><small>{item.input}</small></div><span>{(item.expected_action_keys ?? []).join(", ") || "无动作"}</span></article>;
}

function splitList(value: string) { return value.split(",").map((item) => item.trim()).filter(Boolean); }
function asError(value: unknown) { return value instanceof Error ? value : new Error("评测操作失败。"); }
