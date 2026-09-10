import { lazy, Suspense, type ComponentProps, type FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type {
  Hypothesis,
  HypothesisCreate,
  HypothesisFeedbackCreate,
  Scenario,
  ScenarioComparison,
  ScenarioCreate,
} from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";
import { ActionCenter } from "../../features/actions/ActionCenter";
import { useWorkspace } from "../../workspace/WorkspaceContext";
import { PublishedProjectGate } from "./PublishedProjectGate";

const ProjectionGraphModule = lazy(async () => ({ default: (await import("../../features/projection/ProjectionGraph")).ProjectionGraph }));

function ProjectionGraph(props: ComponentProps<typeof ProjectionGraphModule>) {
  return <Suspense fallback={<div className="projection-canvas-wrap"><div className="graph-loading">正在加载关系图……</div></div>}><ProjectionGraphModule {...props} /></Suspense>;
}

export function DecisionLifecyclePage() {
  const workspace = useWorkspace();
  if (!workspace.selectedProjectId) {
    return <StatusMessage title="请先选择企业项目" description="选择项目后才能查看假设、方案与行动。" />;
  }
  return <PublishedProjectGate projectId={workspace.selectedProjectId}><DecisionWorkspace key={workspace.selectedProjectId} projectId={workspace.selectedProjectId} /></PublishedProjectGate>;
}

function DecisionWorkspace({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [selectedScenarioId, setSelectedScenarioId] = useState("");
  const [compareLeft, setCompareLeft] = useState("");
  const [compareRight, setCompareRight] = useState("");
  const [comparison, setComparison] = useState<ScenarioComparison | null>(null);

  const hypothesesQuery = useQuery({ queryKey: ["hypotheses", projectId], queryFn: () => api.listHypotheses(projectId) });
  const scenariosQuery = useQuery({ queryKey: ["scenarios", projectId], queryFn: () => api.listScenarios(projectId) });
  const selectedScenario = scenariosQuery.data?.items.find((item) => item.id === selectedScenarioId) ?? null;
  const diffQuery = useQuery({ queryKey: ["scenario-diff", projectId, selectedScenarioId], queryFn: () => api.getScenarioDiff(projectId, selectedScenarioId), enabled: Boolean(selectedScenarioId) });
  const graphQuery = useQuery({ queryKey: ["scenario-graph", projectId, selectedScenarioId], queryFn: () => api.queryGraph(projectId, { scenario_id: selectedScenarioId, include_observations: true, include_retired: false, include_unmodeled: false, depth: 1 }), enabled: Boolean(selectedScenarioId) });
  const readError = [hypothesesQuery.error, scenariosQuery.error, diffQuery.error, graphQuery.error].find(Boolean);

  useEffect(() => {
    const scenarios = scenariosQuery.data?.items ?? [];
    if (!scenarios.length) setSelectedScenarioId("");
    else if (!scenarios.some((item) => item.id === selectedScenarioId)) setSelectedScenarioId(scenarios[0].id);
  }, [scenariosQuery.data, selectedScenarioId]);

  const refreshScenarios = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["scenarios", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["scenario-diff", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["scenario-graph", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["executive-context", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["entities", projectId] }),
  ]);
  const hypothesisMutation = useMutation({
    mutationFn: (input: HypothesisCreate) => api.createHypothesis(projectId, input),
    onSuccess: async (item) => { setNotice(`假设“${item.title}”已保存，等待管理层判断。`); await queryClient.invalidateQueries({ queryKey: ["hypotheses", projectId] }); },
    onError: (value) => setError(asError(value)),
  });
  const feedbackMutation = useMutation({
    mutationFn: ({ item, input }: { item: Hypothesis; input: HypothesisFeedbackCreate }) => api.addHypothesisFeedback(projectId, item.id, input),
    onSuccess: async (saved, variables) => { setNotice(`假设“${variables.item.title}”已记录反馈：${saved.status}。`); await queryClient.invalidateQueries({ queryKey: ["hypotheses", projectId] }); },
    onError: (value) => setError(asError(value)),
  });
  const scenarioMutation = useMutation({
    mutationFn: (input: ScenarioCreate) => api.createScenario(projectId, input),
    onSuccess: async (item) => { setSelectedScenarioId(item.id); setNotice(`管理方案“${item.name}”已保存并通过结构校验。`); await refreshScenarios(); },
    onError: (value) => setError(asError(value)),
  });
  const statusMutation = useMutation({
    mutationFn: ({ item, status }: { item: Scenario; status: "DRAFT" | "UNDER_REVIEW" | "APPROVED" | "REJECTED" }) => api.updateScenario(projectId, item.id, { status, expected_revision: item.revision }),
    onSuccess: async (item) => { setNotice(`方案“${item.name}”已更新为 ${item.status}。`); await refreshScenarios(); },
    onError: (value) => setError(asError(value)),
  });
  const rebaseMutation = useMutation({
    mutationFn: (item: Scenario) => api.rebaseScenario(projectId, item.id, { expected_revision: item.revision, requested_by: "management" }),
    onSuccess: async (item) => { setNotice(`方案“${item.name}”已重基到当前正式投影。`); await refreshScenarios(); },
    onError: (value) => setError(asError(value)),
  });
  const applyMutation = useMutation({
    mutationFn: (item: Scenario) => api.applyScenario(projectId, item.id, { expected_revision: item.revision, requested_by: "management" }),
    onSuccess: async (item) => { setNotice(`方案“${item.name}”已作为原子变更集应用到正式投影。`); await refreshScenarios(); },
    onError: (value) => setError(asError(value)),
  });
  const compareMutation = useMutation({
    mutationFn: () => api.compareScenarios(projectId, { left_scenario_id: compareLeft, right_scenario_id: compareRight }),
    onSuccess: setComparison,
    onError: (value) => setError(asError(value)),
  });

  const createHypothesis = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    hypothesisMutation.mutate({ type_key: String(form.get("type_key")).trim(), title: String(form.get("title")).trim(), summary: String(form.get("summary")).trim(), participant_entity_ids: [], supporting_facts: [], counter_evidence: [], uncertainties: [], alternative_explanations: [], validation_questions: [], schema_version: 1, extension_payload: {} });
    event.currentTarget.reset();
  };
  const createScenario = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const operations = JSON.parse(String(form.get("operations") ?? "[]")) as unknown;
      if (!Array.isArray(operations)) throw new Error("方案变更必须是 JSON 数组。");
      scenarioMutation.mutate({ name: String(form.get("name")).trim(), goal: String(form.get("goal")).trim(), description: String(form.get("description") ?? "").trim() || null, assumptions: splitLines(String(form.get("assumptions") ?? "")), expected_benefits: splitLines(String(form.get("benefits") ?? "")), risks: splitLines(String(form.get("risks") ?? "")), validation_metrics: splitLines(String(form.get("metrics") ?? "")), overlay_operations: operations as ScenarioCreate["overlay_operations"] });
    } catch (value) { setError(asError(value)); }
  };
  const feedback = (item: Hypothesis, status: HypothesisFeedbackCreate["status"]) => feedbackMutation.mutate({ item, input: { status, provided_by: "management", comment: null } });

  return <div className="page-stack">
    <section className="page-hero"><div><p className="eyebrow">DECISION LOOP</p><h1>假设、方案与行动</h1><p>潜在问题先作为可质疑的假设；确认后在隔离方案图中比较，最后以原子变更应用并观察结果。</p></div></section>
    {notice && <StatusMessage title="操作已完成" description={notice} />}
    {error && <StatusMessage tone="danger" title="操作失败" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    {readError && <StatusMessage tone="danger" title="决策数据读取失败" description={(readError as Error).message} action={{ label: "重试", onClick: () => void queryClient.invalidateQueries({ predicate: (query) => query.queryKey.includes(projectId) }) }} />}
    <div className="decision-grid">
      <HypothesisPanel items={hypothesesQuery.data?.items ?? []} pending={hypothesisMutation.isPending || feedbackMutation.isPending} onFeedback={feedback} onCreate={createHypothesis} />
      <ScenarioPanel items={scenariosQuery.data?.items ?? []} selectedId={selectedScenarioId} pending={scenarioMutation.isPending || statusMutation.isPending || applyMutation.isPending} onSelect={setSelectedScenarioId} onStatus={(item, status) => statusMutation.mutate({ item, status })} onApply={(item) => applyMutation.mutate(item)} onCreate={createScenario} />
    </div>
    {selectedScenario && <section className="panel"><div className="panel-heading"><div><h2>方案预览：{selectedScenario.name}</h2><p>基准修订 {selectedScenario.base_revision}；应用前与正式投影完全隔离。</p></div>{diffQuery.data?.rebase_required && selectedScenario.status !== "ACTIVE" ? <button className="button secondary" disabled={rebaseMutation.isPending} onClick={() => rebaseMutation.mutate(selectedScenario)}>无冲突时重基</button> : <span className="status-pill">{selectedScenario.status}</span>}</div>{diffQuery.data && <div className="summary-strip"><article><span>新建</span><strong>{diffQuery.data.creates.length}</strong></article><article><span>修改</span><strong>{diffQuery.data.updates.length}</strong></article><article><span>退役</span><strong>{diffQuery.data.retires.length}</strong></article><article><span>冲突</span><strong>{diffQuery.data.conflicts.length}</strong></article></div>}{diffQuery.data?.conflicts.length ? <pre className="error-box">{JSON.stringify(diffQuery.data.conflicts, null, 2)}</pre> : null}{graphQuery.data ? <ProjectionGraph graph={graphQuery.data} search="" onSelect={() => undefined} /> : <p className="empty-copy">正在生成方案图……</p>}</section>}
    <ScenarioComparePanel scenarios={scenariosQuery.data?.items ?? []} left={compareLeft} right={compareRight} comparison={comparison} pending={compareMutation.isPending} onLeft={setCompareLeft} onRight={setCompareRight} onCompare={() => compareMutation.mutate()} />
    <ActionCenter projectId={projectId} />
  </div>;
}

function HypothesisPanel({ items, pending, onFeedback, onCreate }: { items: Hypothesis[]; pending: boolean; onFeedback: (item: Hypothesis, status: HypothesisFeedbackCreate["status"]) => void; onCreate: (event: FormEvent<HTMLFormElement>) => void }) {
  return <section className="panel"><div className="panel-heading"><div><h2>管理假设</h2><p>Agent 的潜在关系和问题必须由管理层确认。</p></div><span>{items.length} 条</span></div><div className="hypothesis-list">{items.map((item) => <article key={item.id}><div className="subsection-heading"><div><strong>{item.title}</strong><small>{item.type_key}</small></div><span className="status-pill">{item.status}</span></div><p>{item.summary}</p><div className="command-toolbar"><button className="button text-button" disabled={pending} onClick={() => onFeedback(item, "NEEDS_EVIDENCE")}>需要证据</button><button className="button text-button" disabled={pending} onClick={() => onFeedback(item, "PARTIALLY_CONFIRMED")}>部分确认</button><button className="button secondary" disabled={pending} onClick={() => onFeedback(item, "CONFIRMED")}>确认存在</button><button className="button text-button danger-text" disabled={pending} onClick={() => onFeedback(item, "REJECTED")}>否定</button></div></article>)}{!items.length && <p className="empty-copy">还没有管理假设。可由管理 Agent 发现，也可手动记录。</p>}</div><form className="create-form" onSubmit={onCreate}><h3>记录新假设</h3><div className="form-grid-two"><label>假设类型<input name="type_key" required placeholder="例如：agency_risk" /></label><label>标题<input name="title" required /></label></div><label>摘要<textarea name="summary" required rows={3} /></label><button className="button secondary" disabled={pending}>保存为待确认假设</button></form></section>;
}

function ScenarioPanel({ items, selectedId, pending, onSelect, onStatus, onApply, onCreate }: { items: Scenario[]; selectedId: string; pending: boolean; onSelect: (id: string) => void; onStatus: (item: Scenario, status: "DRAFT" | "UNDER_REVIEW" | "APPROVED" | "REJECTED") => void; onApply: (item: Scenario) => void; onCreate: (event: FormEvent<HTMLFormElement>) => void }) {
  return <section className="panel"><div className="panel-heading"><div><h2>管理方案</h2><p>Agent 生成结构化覆盖操作；管理层预览、比较并批准。</p></div><span>{items.length} 个</span></div><div className="scenario-list">{items.map((item) => <article key={item.id} className={item.id === selectedId ? "active" : ""}><div className="subsection-heading"><strong>{item.name}</strong><span className="status-pill">{item.status}</span></div><p>{item.goal}</p><div className="scenario-meta"><span>操作 {item.overlay_operations?.length ?? 0}</span><span>收益 {item.expected_benefits?.length ?? 0}</span><span>风险 {item.risks?.length ?? 0}</span></div><div className="row-actions"><button className="button text-button" onClick={() => onSelect(item.id)}>预览</button>{item.status === "DRAFT" && <button className="button secondary" disabled={pending} onClick={() => onStatus(item, "UNDER_REVIEW")}>提交复核</button>}{item.status === "UNDER_REVIEW" && <><button className="button secondary" disabled={pending} onClick={() => onStatus(item, "APPROVED")}>批准</button><button className="button text-button danger-text" disabled={pending} onClick={() => onStatus(item, "REJECTED")}>否定</button></>}{item.status === "APPROVED" && <button className="button primary" disabled={pending} onClick={() => onApply(item)}>应用到正式投影</button>}</div></article>)}{!items.length && <p className="empty-copy">还没有管理方案。</p>}</div><details className="inline-advanced"><summary>手动建立方案（通常由管理 Agent 生成）</summary><form className="create-form" onSubmit={onCreate}><label>方案名称<input name="name" required /></label><label>核心目标<input name="goal" required /></label><label>方案说明<textarea name="description" rows={2} /></label><div className="form-grid-two"><label>假设（每行一项）<textarea name="assumptions" rows={3} /></label><label>验证指标（每行一项）<textarea name="metrics" rows={3} /></label></div><div className="form-grid-two"><label>预期收益（每行一项）<textarea name="benefits" rows={3} /></label><label>主要风险（每行一项）<textarea name="risks" rows={3} /></label></div><label>覆盖操作 JSON<textarea name="operations" rows={8} spellCheck={false} defaultValue="[]" /><small>与企业投影变更集使用同一 CREATE/UPDATE/RETIRE 结构；先在隔离图中校验。</small></label><button className="button secondary" disabled={pending}>保存并校验方案</button></form></details></section>;
}

function ScenarioComparePanel({ scenarios, left, right, comparison, pending, onLeft, onRight, onCompare }: { scenarios: Scenario[]; left: string; right: string; comparison: ScenarioComparison | null; pending: boolean; onLeft: (id: string) => void; onRight: (id: string) => void; onCompare: () => void }) {
  return <section className="panel"><div className="panel-heading"><div><h2>双方案比较</h2><p>显示共有操作、各自独有操作和对同一目标的冲突修改。</p></div></div><div className="form-grid-two"><label>方案 A<select value={left} onChange={(event) => onLeft(event.target.value)}><option value="">请选择</option>{scenarios.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>方案 B<select value={right} onChange={(event) => onRight(event.target.value)}><option value="">请选择</option>{scenarios.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div><button className="button secondary" disabled={!left || !right || left === right || pending} onClick={onCompare}>比较方案</button>{comparison && <div className="summary-strip"><article><span>共有</span><strong>{comparison.shared_operations.length}</strong></article><article><span>仅 A</span><strong>{comparison.only_left.length}</strong></article><article><span>仅 B</span><strong>{comparison.only_right.length}</strong></article><article><span>目标冲突</span><strong>{comparison.conflicting_targets.length}</strong></article></div>}{comparison?.conflicting_targets.length ? <pre>{JSON.stringify(comparison.conflicting_targets, null, 2)}</pre> : null}</section>;
}

function splitLines(value: string) { return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean); }
function asError(value: unknown) { return value instanceof Error ? value : new Error("操作没有完成。"); }
