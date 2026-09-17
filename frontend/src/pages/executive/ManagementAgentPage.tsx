import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AgentWorkspace } from "../../features/agents/AgentWorkspace";
import { ManagementResults } from "../../features/agents/ManagementResults";
import { ManagementActionsPanel } from "./ManagementActionsPanel";
import { PotentialRecordsPanel } from "./PotentialRecordsPanel";
import { StatusMessage } from "../../components/StatusMessage";
import { api } from "../../api";
import type { ManagementObservationKind, ScenarioCaseInput, ScenarioRun, ScenarioRunComparison } from "../../api/types";
import { useWorkspace } from "../../workspace/WorkspaceContext";

export function ManagementAgentPage() {
  const workspace = useWorkspace();
  if (!workspace.selectedProjectId) return <StatusMessage title="请先选择企业" description="管理 Agent 只在一个明确的企业投影空间内回答和提出方案。" />;
  return <div className="page-stack">
    <section className="page-hero"><div><p className="eyebrow">MANAGEMENT COPILOT</p><h1>管理决策 Agent</h1><p>先记录日常信息，再根据已发布模型和可用数据进行查询；未经确认的推测不会写入可信投影。</p></div><Link className="button secondary" to="/developer/build">去建设端补充模型</Link></section>
    <ManagementWorkspaceStatus projectId={workspace.selectedProjectId} />
    <ManagementObservationComposer projectId={workspace.selectedProjectId} />
    <ScenarioSimulationPanel projectId={workspace.selectedProjectId} />
    <AgentWorkspace key={`${workspace.selectedProjectId}-management-agent`} projectId={workspace.selectedProjectId} kind="MANAGEMENT" title="管理决策 Agent" description="每个对话拥有独立上下文。可以引用企业事实和材料，并把有价值的内容保存为假设、方案或行动。" />
    <ManagementResults key={`${workspace.selectedProjectId}-management-results`} projectId={workspace.selectedProjectId} />
    <details className="advanced-section management-review-drawer">
      <summary><span><strong>潜在问题与现实行动</strong><small>Agent 提出的候选由管理者确认；现实行动单独记录，不会伪装成系统已执行</small></span><i>展开 / 收起</i></summary>
      <div className="advanced-section-body advanced-stack">
        <PotentialRecordsPanel projectId={workspace.selectedProjectId} companyId={workspace.selectedCompanyId} />
        <ManagementActionsPanel projectId={workspace.selectedProjectId} />
      </div>
    </details>
  </div>;
}

function ScenarioSimulationPanel({ projectId }: { projectId: string }) {
  const [scenarioId, setScenarioId] = useState("");
  const [scenarioName, setScenarioName] = useState("产能变化推演");
  const [goal, setGoal] = useState("比较事件发生前后的完成量和积压变化");
  const [periods, setPeriods] = useState("1,2,3,4,5");
  const [demand, setDemand] = useState("90,90,90,90,90");
  const [baselineCapacity, setBaselineCapacity] = useState("100,100,100,100,100");
  const [impactCapacity, setImpactCapacity] = useState("80,80,80,80,80");
  const [unit, setUnit] = useState("件");
  const [flowMode, setFlowMode] = useState<ScenarioCaseInput["flow_mode"]>("STORABLE_GOODS");
  const [leftRunId, setLeftRunId] = useState("");
  const [rightRunId, setRightRunId] = useState("");
  const [comparison, setComparison] = useState<ScenarioRunComparison | null>(null);
  const [notice, setNotice] = useState("");
  const scenariosQuery = useQuery({ queryKey: ["scenarios", projectId], queryFn: () => api.listScenarios(projectId) });
  const selectedScenario = scenariosQuery.data?.items.find((item) => item.id === scenarioId) ?? scenariosQuery.data?.items[0];
  const runsQuery = useQuery({ queryKey: ["scenario-runs", projectId, selectedScenario?.id], queryFn: () => api.listScenarioRuns(projectId, selectedScenario!.id), enabled: Boolean(selectedScenario?.id) });
  const createMutation = useMutation({
    mutationFn: () => api.createScenario(projectId, { name: scenarioName.trim(), goal: goal.trim(), description: "由管理 Agent 进入的数值推演。", assumptions: ["未输入的经营指标不在本次计算中。"], expected_benefits: [], risks: [], validation_metrics: ["completed", "ending_backlog"], overlay_operations: [] }),
    onSuccess: async (item) => { setScenarioId(item.id); setNotice(`已建立情景“${item.name}”，尚未修改正式投影。`); await scenariosQuery.refetch(); },
  });
  const runMutation = useMutation({
    mutationFn: () => {
      const periodValues = splitNumbers(periods);
      const demandValues = splitNumbers(demand);
      const baseValues = splitNumbers(baselineCapacity);
      const impactValues = splitNumbers(impactCapacity);
      if (!selectedScenario) throw new Error("请先建立一个情景。");
      return api.runScenario(projectId, selectedScenario.id, {
        created_by: "management",
        cases: [
          { key: "baseline", label: "基准", periods: periodValues.map(String), demand: demandValues, capacity: baseValues, initial_inventory: 0, initial_backlog: 0, unit: unit.trim(), flow_mode: flowMode },
          { key: "impact", label: "事件发生后", periods: periodValues.map(String), demand: demandValues, capacity: impactValues, initial_inventory: 0, initial_backlog: 0, unit: unit.trim(), flow_mode: flowMode },
        ],
      });
    },
    onSuccess: async (run) => {
      setComparison(null);
      setNotice(run.status === "SUCCEEDED" ? "推演完成：结果已冻结，可在来源说明中复核条件和规则。" : "推演未完成，请检查输入和单位。");
      await runsQuery.refetch();
    },
  });
  const compareMutation = useMutation({
    mutationFn: () => {
      if (!leftRunId || !rightRunId) throw new Error("请选择两次推演运行结果。");
      return api.compareScenarioRuns(projectId, { left_run_id: leftRunId, right_run_id: rightRunId });
    },
    onSuccess: (value) => { setComparison(value); setNotice("推演结果已比较；差值只代表两次显式输入的差异。"); },
  });
  const runCases = runMutation.data?.result.cases ?? [];
  return <section className="panel scenario-simulation-panel">
    <div className="panel-heading"><div><p className="eyebrow">WHAT-IF / ACTION</p><h2>情景推演</h2><p>先在隔离情景中运行受控计算，再由程序计算可计算的结果；不会直接改写正式企业投影。</p></div><span className="status-pill">{selectedScenario ? "已选择情景" : "待建立"}</span></div>
    {notice && <div className="inline-notice">{notice}</div>}
    {(createMutation.error || runMutation.error) && <StatusMessage tone="danger" title="推演没有完成" description={String((createMutation.error || runMutation.error) ?? "")} />}
    <div className="form-grid-two">
      <label>情景名称<input value={scenarioName} onChange={(event) => setScenarioName(event.target.value)} /></label>
      <label>目标<input value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
    </div>
    <div className="button-row"><button className="button secondary" disabled={!scenarioName.trim() || createMutation.isPending} onClick={() => createMutation.mutate()}>建立情景</button>{scenariosQuery.data?.items.length ? <select value={selectedScenario?.id ?? ""} onChange={(event) => setScenarioId(event.target.value)}>{scenariosQuery.data.items.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.status}</option>)}</select> : null}</div>
    <details className="inline-advanced" open={Boolean(selectedScenario)}><summary>配置一组可计算的流量条件</summary><div className="form-grid-two"><label>周期（逗号分隔）<input value={periods} onChange={(event) => setPeriods(event.target.value)} /></label><label>需求<small>必须与周期数量一致</small><input value={demand} onChange={(event) => setDemand(event.target.value)} /></label><label>基准产能<input value={baselineCapacity} onChange={(event) => setBaselineCapacity(event.target.value)} /></label><label>事件后产能<input value={impactCapacity} onChange={(event) => setImpactCapacity(event.target.value)} /></label><label>单位<input value={unit} onChange={(event) => setUnit(event.target.value)} placeholder="例如：件、单、人时" /></label><label>能力类型<select value={flowMode} onChange={(event) => setFlowMode(event.target.value as ScenarioCaseInput["flow_mode"])}><option value="STORABLE_GOODS">可储存实物</option><option value="NON_STORABLE_SERVICE">不可储存服务</option></select></label></div><p className="field-help">可储存实物会结转库存；不可储存服务的闲置能力不会结转到下一期。</p><button className="button primary" disabled={!selectedScenario || !unit.trim() || runMutation.isPending} onClick={() => runMutation.mutate()}>{runMutation.isPending ? "正在计算……" : "执行受控推演并计算"}</button></details>
    {runCases.length > 0 && <div className="summary-strip">{runCases.map((item) => <article key={String(item.key)}><span>{String(item.label)}</span><strong>{String(item.totals?.completed ?? "—")} {String(item.unit ?? "")}</strong><small>期末积压 {String(item.totals?.ending_backlog ?? "—")} {String(item.unit ?? "")}</small></article>)}</div>}
    {!!runsQuery.data?.items.length && <details className="scenario-history"><summary>历史运行与结果比较（{runsQuery.data.total} 次）</summary><div className="form-grid-two"><label>左侧运行<select value={leftRunId} onChange={(event) => setLeftRunId(event.target.value)}><option value="">选择运行</option>{runsQuery.data.items.map((run: ScenarioRun) => <option key={run.id} value={run.id}>{formatScenarioRun(run)}</option>)}</select></label><label>右侧运行<select value={rightRunId} onChange={(event) => setRightRunId(event.target.value)}><option value="">选择运行</option>{runsQuery.data.items.map((run: ScenarioRun) => <option key={run.id} value={run.id}>{formatScenarioRun(run)}</option>)}</select></label></div><button className="button secondary" onClick={() => compareMutation.mutate()} disabled={!leftRunId || !rightRunId || leftRunId === rightRunId || compareMutation.isPending}>{compareMutation.isPending ? "正在比较……" : "比较两次运行"}</button>{comparison && <div className="scenario-comparison-result">{comparison.cases.map((item) => <article key={String(item.key)}><strong>{String(item.left_label ?? item.key)} → {String(item.right_label ?? item.key)}</strong><span>完成量变化 {String(item.completed_delta ?? "—")} · 期末积压变化 {String(item.ending_backlog_delta ?? "—")}</span></article>)}<p className="field-help">{comparison.limitations.join(" ")}</p></div>}</details>}
    <p className="field-help">输出只说明显式条件下的计算结果；利润、人员压力和因果关系没有输入规则时不会被系统补猜。</p>
  </section>;
}

function splitNumbers(value: string): number[] {
  const parts = value.split(",").map((item) => item.trim());
  if (parts.some((item) => !item)) throw new Error("周期、需求和产能不能有空项。");
  const result = parts.map(Number);
  if (!result.length || result.some((item) => !Number.isFinite(item) || item < 0)) throw new Error("周期、需求和产能必须是非负数字，并使用逗号分隔。");
  return result;
}

function formatScenarioRun(run: ScenarioRun) {
  const firstCase = run.result.cases?.[0];
  return `${run.status} · ${new Date(run.created_at).toLocaleString()} · ${String(firstCase?.totals?.completed ?? "—")}`;
}

function ManagementWorkspaceStatus({ projectId }: { projectId: string }) {
  const publicationsQuery = useQuery({ queryKey: ["publications", projectId], queryFn: () => api.listPublications(projectId) });
  if (publicationsQuery.isLoading) return <div className="inline-notice">正在读取企业投影发布状态……</div>;
  if (publicationsQuery.error) return <StatusMessage tone="danger" title="发布状态读取失败" description={(publicationsQuery.error as Error).message} />;
  if (!publicationsQuery.data?.total) return <div className="inline-notice">当前还没有正式发布版本：现在可以记录会议、观察和问题；涉及正式职责、金额或系统数据的查询会明确提示缺口。完成建设端审核后，管理 Agent 才会读取正式企业模型。</div>;
  const current = publicationsQuery.data.items[0];
  return <div className="inline-notice">当前使用企业投影 v{current?.version ?? "—"}。管理 Agent 会区分正式事实、管理观察、潜在假设和工作观察，并在复杂任务中说明实际读取范围。</div>;
}

const observationKinds: Array<{ value: ManagementObservationKind; label: string }> = [
  { value: "MEETING", label: "会议纪要" },
  { value: "WORK_REPORT", label: "工作汇报" },
  { value: "METRIC_RESULT", label: "绩效或指标结果" },
  { value: "INCIDENT", label: "事件 / 问题" },
  { value: "OTHER", label: "其他信息" },
];

function ManagementObservationComposer({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<ManagementObservationKind>("MEETING");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [notice, setNotice] = useState("");
  const mutation = useMutation({
    mutationFn: () => api.createManagementObservation(projectId, { kind, title: title.trim(), content: content.trim(), occurred_at: null }),
    onSuccess: async () => {
      setTitle("");
      setContent("");
      setNotice("已保存到管理观察库；它不会直接改写正式企业投影。 ");
      await queryClient.invalidateQueries({ queryKey: ["management-observations", projectId] });
    },
  });
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (title.trim() && content.trim()) mutation.mutate();
  };
  return <details className="advanced-section management-input-drawer">
    <summary><span><strong>记录日常管理信息</strong><small>未发布时也可使用；所有修改和来源会留痕，记录不会直接进入正式库。</small></span><i>展开 / 收起</i></summary>
    <div className="advanced-section-body">
      {notice && <div className="inline-notice">{notice}</div>}
      {mutation.error && <StatusMessage tone="danger" title="观察没有保存" description={(mutation.error as Error).message} />}
      <form className="create-form" onSubmit={submit}>
        <div className="form-grid-two"><label>信息类型<select value={kind} onChange={(event) => setKind(event.target.value as ManagementObservationKind)}>{observationKinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} required placeholder="例如：本周交付例会" /></label></div>
        <label>内容<textarea value={content} onChange={(event) => setContent(event.target.value)} rows={4} required placeholder="粘贴会议纪要、观察或结果，不需要先整理成结构化字段。" /></label>
        <button className="button secondary" disabled={mutation.isPending}>{mutation.isPending ? "正在保存……" : "保存到管理观察库"}</button>
      </form>
    </div>
  </details>;
}
