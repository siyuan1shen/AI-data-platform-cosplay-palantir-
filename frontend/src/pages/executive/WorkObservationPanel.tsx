import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import type {
  WorkObservationAnalysis,
  WorkObservationPackage,
  WorkObservationPreview,
  WorkObservationVirtualCandidate,
} from "../../api/types";

const examplePackage: WorkObservationPackage = {
  format_version: "1.0",
  batch_id: "offline-batch-001",
  source_id: "device-001",
  events: [
    {
      event_id: "event-001",
      session_id: "session-001",
      sequence: 1,
      observed_at: "2026-09-13T09:30:00Z",
      source_employee_key: "employee-001",
      source_role_key: "sales",
      activity: { app: "Chrome", domain: "crm.example.com", category: "CRM", context: "customer-detail" },
      state: "FOREGROUND",
    },
  ],
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请检查数据包后重试。";
}

export function WorkObservationPanel({ projectId }: { projectId: string }) {
  const [packageText, setPackageText] = useState(() => JSON.stringify(examplePackage, null, 2));
  const [preview, setPreview] = useState<WorkObservationPreview | null>(null);
  const [analysis, setAnalysis] = useState<WorkObservationAnalysis | null>(null);
  const [persistedAnalysisLoaded, setPersistedAnalysisLoaded] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [leftEmployees, setLeftEmployees] = useState<string[]>([]);
  const [rightEmployees, setRightEmployees] = useState<string[]>([]);
  const [bindingSourceId, setBindingSourceId] = useState("");
  const [bindingEmployeeKey, setBindingEmployeeKey] = useState("");
  const [bindingEntityId, setBindingEntityId] = useState("");
  const [bindingRoleKey, setBindingRoleKey] = useState("");
  const [virtualModelId, setVirtualModelId] = useState("");
  const [candidateMessage, setCandidateMessage] = useState<string | null>(null);
  useEffect(() => {
    // A projection page stays mounted while the top company selector changes.
    // Never carry a preview, analysis or candidate from the previous company.
    setPreview(null);
    setAnalysis(null);
    setPersistedAnalysisLoaded(false);
    setCandidates([]);
    setMessage(null);
    setCandidateMessage(null);
    setLeftEmployees([]);
    setRightEmployees([]);
  }, [projectId]);
  const coverageQuery = useQuery({
    queryKey: ["work-observation-coverage", projectId],
    queryFn: () => api.getWorkObservationCoverage(projectId),
    enabled: Boolean(projectId),
  });
  const bindingsQuery = useQuery({
    queryKey: ["work-observation-identity-bindings", projectId],
    queryFn: () => api.listWorkObservationIdentityBindings(projectId),
    enabled: Boolean(projectId),
  });
  const entitiesQuery = useQuery({
    queryKey: ["project-entities-for-work-observation", projectId],
    queryFn: () => api.listEntities(projectId),
    enabled: Boolean(projectId),
  });
  const analysesQuery = useQuery({
    queryKey: ["work-observation-analyses", projectId],
    queryFn: () => api.listWorkObservationAnalyses(projectId, 0, 20),
    enabled: Boolean(projectId),
  });
  useEffect(() => {
    // Load the latest stored result once for this project.  Do not reload it
    // after a new package is imported: that would make a stale analysis appear
    // to belong to the newly selected package.
    if (persistedAnalysisLoaded || !analysesQuery.data) return;
    setPersistedAnalysisLoaded(true);
    if (!analysesQuery.data.items.length || analysis) return;
    const latest = analysesQuery.data.items[0];
    setAnalysis(latest);
    const employees = Object.keys(latest.result.employee_paths ?? {});
    setLeftEmployees(employees[0] ? [employees[0]] : []);
    setRightEmployees(employees[1] ? [employees[1]] : employees[0] ? [employees[0]] : []);
  }, [analysis, analysesQuery.data, persistedAnalysisLoaded]);
  const previewMutation = useMutation({
    mutationFn: (input: WorkObservationPackage) => api.previewWorkObservationImport(projectId, input),
    onSuccess: (value) => {
      setPreview(value);
      setMessage(`预览已生成：${value.event_count} 条事件，确认后才会进入观察库。`);
    },
    onError: (error) => setMessage(errorMessage(error)),
  });
  const confirmMutation = useMutation({
    mutationFn: () => {
      if (!preview) throw new Error("请先生成导入预览。");
      return api.confirmWorkObservationImport(projectId, preview.id, preview.payload_hash);
    },
    onSuccess: (value) => {
      setMessage(`导入完成：新增 ${value.accepted_count} 条，重复 ${value.duplicate_count} 条。`);
      setPreview(null);
      setAnalysis(null);
      setCandidates([]);
      void coverageQuery.refetch();
      void analysesQuery.refetch();
    },
    onError: (error) => setMessage(errorMessage(error)),
  });
  const analysisMutation = useMutation({
    mutationFn: () => api.createWorkObservationAnalysis(projectId, { gap_seconds: 900 }),
    onSuccess: (value) => {
      setAnalysis(value);
      const employees = Object.keys(value.result.employee_paths ?? {});
      setLeftEmployees(employees[0] ? [employees[0]] : []);
      setRightEmployees(employees[1] ? [employees[1]] : employees[0] ? [employees[0]] : []);
      setMessage("分析完成。下方只展示观察到的路径差异，不代表绩效或因果关系。 ");
    },
    onError: (error) => setMessage(errorMessage(error)),
  });
  const comparisonMutation = useMutation({
    mutationFn: () => {
      if (!analysis || !leftEmployees.length || !rightEmployees.length) throw new Error("请先选择两组员工。 ");
      return api.compareWorkObservation(projectId, {
        analysis_id: analysis.id,
        left_employee_keys: leftEmployees,
        right_employee_keys: rightEmployees,
      });
    },
    onSuccess: (value) => setMessage(`对比完成：左侧独有 ${value.result.only_left?.length ?? 0} 条，右侧独有 ${value.result.only_right?.length ?? 0} 条。`),
    onError: (error) => setMessage(errorMessage(error)),
  });
  const [candidates, setCandidates] = useState<WorkObservationVirtualCandidate[]>([]);
  const candidateMutation = useMutation({
    mutationFn: () => {
      if (!analysis) throw new Error("请先生成工作观察分析。");
      return api.proposeWorkObservationVirtualCandidates(projectId, analysis.id, { min_count: 2 });
    },
    onSuccess: (value) => {
      setCandidates(value);
      setCandidateMessage(`已生成 ${value.length} 个候选。它们不会自动进入正式模型，必须人工确认。`);
    },
    onError: (error) => setCandidateMessage(errorMessage(error)),
  });
  const candidateDecisionMutation = useMutation({
    mutationFn: ({ candidate, decision }: { candidate: WorkObservationVirtualCandidate; decision: "CONFIRM" | "REJECT" }) =>
      api.decideWorkObservationVirtualCandidate(projectId, candidate.id, {
        decision,
        reason: decision === "CONFIRM" ? "管理者确认该观察活动补充岗位虚模。" : "管理者拒绝该观察活动候选。",
        virtual_work_model_id: decision === "CONFIRM" ? virtualModelId || null : null,
      }),
    onSuccess: (value) => {
      setCandidates((items) => items.map((item) => item.id === value.id ? value : item));
      setCandidateMessage(value.status === "CONFIRMED" ? "候选已写入岗位虚模草稿，仍需在虚模页面审核发布。" : "候选已拒绝并留痕。 ");
    },
    onError: (error) => setCandidateMessage(errorMessage(error)),
  });
  const bindingMutation = useMutation({
    mutationFn: () => {
      if (!bindingSourceId || !bindingEmployeeKey || !bindingEntityId) {
        throw new Error("请填写来源、员工标识并选择正式企业对象。");
      }
      return api.bindWorkObservationIdentity(projectId, {
        source_id: bindingSourceId,
        source_employee_key: bindingEmployeeKey,
        formal_entity_id: bindingEntityId,
        formal_role_key: bindingRoleKey || null,
      });
    },
    onSuccess: () => {
      setMessage("来源员工标识已绑定到正式企业对象。新的预览会显示绑定状态。 ");
      void bindingsQuery.refetch();
      setBindingEmployeeKey("");
      setBindingEntityId("");
      setBindingRoleKey("");
    },
    onError: (error) => setMessage(errorMessage(error)),
  });
  const employees = useMemo(
    () => Object.keys(analysis?.result.employee_paths ?? {}),
    [analysis],
  );

  const previewPackage = () => {
    try {
      const parsed = JSON.parse(packageText) as WorkObservationPackage;
      previewMutation.mutate(parsed);
    } catch {
      setMessage("数据包不是有效 JSON。 ");
    }
  };

  const readFile = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setPackageText(String(reader.result ?? ""));
      setPreview(null);
      setAnalysis(null);
      setCandidates([]);
      setMessage("数据包已替换，请重新生成预览后再确认导入。 ");
    };
    reader.onerror = () => setMessage("读取离线数据包失败。 ");
    reader.readAsText(file, "utf-8");
  };

  return (
    <section className="work-observation-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">WORK OBSERVATION</p>
          <h2>岗位工作观察</h2>
          <p>独立采集器把活动数据送到这里；系统只做时间顺序和简单差异统计，不自动判断绩效。</p>
        </div>
        <div className="inline-actions">
          <label className="button secondary file-button">
            导入离线包
            <input type="file" accept="application/json,.json" onChange={(event) => readFile(event.target.files?.[0])} />
          </label>
          <button className="button secondary" onClick={() => void coverageQuery.refetch()}>刷新</button>
        </div>
      </div>

      <div className="summary-grid compact work-observation-summary">
        <article><span>事件</span><strong>{coverageQuery.data?.event_count ?? 0}</strong><small>已确认进入观察库</small></article>
        <article><span>来源员工</span><strong>{coverageQuery.data?.employee_count ?? 0}</strong><small>未绑定时只代表来源标识</small></article>
        <article><span>会话</span><strong>{coverageQuery.data?.session_count ?? 0}</strong><small>按会话切分路径</small></article>
        <article><span>数据包</span><strong>{coverageQuery.data?.batch_count ?? 0}</strong><small>重复包不会重复计入</small></article>
      </div>

      <details className="work-observation-import" open>
        <summary>预览并确认工作观察数据</summary>
        <p className="muted">在线上传和离线导入使用相同格式。预览不会写入事件，必须点击确认；未绑定的来源员工不会被当作正式人员。</p>
          <textarea aria-label="工作观察 JSON 数据包" value={packageText} onChange={(event) => { setPackageText(event.target.value); setPreview(null); setAnalysis(null); setCandidates([]); setMessage("数据包已修改，请重新生成预览后再确认导入。 "); }} rows={9} />
        <div className="inline-actions">
          <button className="button primary" onClick={previewPackage} disabled={previewMutation.isPending}>生成预览</button>
          <button className="button secondary" onClick={() => confirmMutation.mutate()} disabled={!preview || confirmMutation.isPending}>确认导入</button>
          {preview && <span className="status-pill">预览 {preview.event_count} 条 · {preview.payload_hash.slice(0, 10)}…</span>}
        </div>
        {preview && (
          <div className="work-observation-preview-details">
            <div className="analysis-facts">
              <span>时间：{preview.first_observed_at ?? "未知"} 至 {preview.last_observed_at ?? "未知"}</span>
              <span>来源员工 {preview.employee_keys?.length ?? 0} 个</span>
              <span>重复 {preview.duplicate_event_count ?? 0} 条</span>
              <span>冲突 {preview.conflict_event_count ?? 0} 条</span>
            </div>
            <p className="muted">身份状态：{preview.identity_status === "SOURCE_KEYS_UNVERIFIED" ? "来源标识未绑定正式人员/岗位" : preview.identity_status}</p>
            {!!preview.warnings?.length && <ul className="work-observation-warnings">{preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
            {!!preview.sample_events?.length && (
              <details>
                <summary>查看前 {preview.sample_events.length} 条样例</summary>
                <pre className="work-observation-sample">{JSON.stringify(preview.sample_events, null, 2)}</pre>
              </details>
            )}
          </div>
        )}
      </details>

      <details className="work-observation-import">
        <summary>来源身份绑定（可选）</summary>
        <p className="muted">把采集器中的来源员工标识绑定到已建模的正式企业对象。未绑定数据仍可分析，但不会被解释为正式员工。</p>
        <div className="work-observation-binding-form">
          <label>采集来源<input value={bindingSourceId} onChange={(event) => setBindingSourceId(event.target.value)} placeholder="device-001" /></label>
          <label>来源员工标识<input value={bindingEmployeeKey} onChange={(event) => setBindingEmployeeKey(event.target.value)} placeholder="employee-001" /></label>
          <label>正式企业对象<select value={bindingEntityId} onChange={(event) => setBindingEntityId(event.target.value)}><option value="">选择已建模对象</option>{entitiesQuery.data?.items.map((entity) => <option value={entity.id} key={entity.id}>{entity.name} · {entity.type_key}</option>)}</select></label>
          <label>正式岗位标识（可选）<input value={bindingRoleKey} onChange={(event) => setBindingRoleKey(event.target.value)} placeholder="sales" /></label>
          <button className="button secondary" onClick={() => bindingMutation.mutate()} disabled={bindingMutation.isPending}>保存绑定</button>
        </div>
        {!!bindingsQuery.data?.items.length && <p className="muted">当前已配置 {bindingsQuery.data.total} 条来源绑定。</p>}
      </details>

      <div className="work-observation-analysis">
        <div className="section-heading compact-heading">
          <div><h3>路径与工作方式对比</h3><p>同一员工的连续活动会压缩成片段，不同员工之间不会拼接路径。</p></div>
          <button className="button primary" onClick={() => analysisMutation.mutate()} disabled={analysisMutation.isPending || !coverageQuery.data?.event_count}>生成分析</button>
        </div>
        {analysis && (
          <>
            <div className="analysis-facts"><span>{analysis.event_count} 条事件</span><span>{analysis.segment_count} 个片段</span><span>{analysis.employee_count} 名员工</span></div>
            {!!analysis.result.edges?.length && <details className="work-observation-flow" open><summary>活动转换关系（只表示观察到的先后顺序）</summary><div className="work-observation-flow-grid">{analysis.result.edges.slice(0, 30).map((edge, index) => <article key={`${String(edge.from)}-${String(edge.to)}-${index}`}><strong>{String(edge.from)} → {String(edge.to)}</strong><span>出现 {String(edge.count ?? 0)} 次 · {String(edge.employee_count ?? 0)} 名员工</span></article>)}</div></details>}
            <div className="comparison-controls">
              <label>左侧员工组<select multiple value={leftEmployees} onChange={(event) => setLeftEmployees(Array.from(event.target.selectedOptions, (option) => option.value))}>{employees.map((employee) => <option key={employee}>{employee}</option>)}</select></label>
              <label>右侧员工组<select multiple value={rightEmployees} onChange={(event) => setRightEmployees(Array.from(event.target.selectedOptions, (option) => option.value))}>{employees.map((employee) => <option key={employee}>{employee}</option>)}</select></label>
              <button className="button secondary" onClick={() => comparisonMutation.mutate()} disabled={comparisonMutation.isPending || !leftEmployees.length || !rightEmployees.length}>比较路径</button>
            </div>
            <div className="path-list">
              {Object.entries(analysis.result.employee_paths ?? {}).map(([employee, paths]) => (
                <article key={employee}><strong>{employee}</strong>{paths.slice(0, 3).map((item) => <p key={`${employee}-${item.path.join("-")}`}>{item.path.join(" → ")} <span>×{item.count}</span></p>)}</article>
              ))}
            </div>
            <p className="muted">{(analysis.result.limitations ?? []).join(" ")}</p>
            <details className="work-observation-import">
              <summary>补充岗位虚模候选（必须人工确认）</summary>
              <p className="muted">系统只把重复出现的活动整理成候选，不会自动写入正式企业模型。确认前请填写已有岗位虚模 ID；确认后写入虚模草稿。</p>
              <div className="inline-actions">
                <button className="button secondary" onClick={() => candidateMutation.mutate()} disabled={candidateMutation.isPending}>生成活动候选</button>
                <input aria-label="岗位虚模 ID" value={virtualModelId} onChange={(event) => setVirtualModelId(event.target.value)} placeholder="确认时填写岗位虚模 ID" />
              </div>
              {!!candidates.length && <div className="work-observation-flow-grid">{candidates.map((candidate) => <article key={candidate.id}><strong>{candidate.label}</strong><span>{candidate.status} · 观察片段 {candidate.evidence_segment_ids.length} 条</span>{candidate.status === "PROPOSED" && <div className="inline-actions"><button className="button secondary" onClick={() => candidateDecisionMutation.mutate({ candidate, decision: "CONFIRM" })} disabled={candidateDecisionMutation.isPending || !virtualModelId}>确认写入虚模草稿</button><button className="button secondary" onClick={() => candidateDecisionMutation.mutate({ candidate, decision: "REJECT" })} disabled={candidateDecisionMutation.isPending}>拒绝</button></div>}</article>)}</div>}
              {candidateMessage && <p className="work-observation-message" role="status">{candidateMessage}</p>}
            </details>
          </>
        )}
      </div>

      {message && <p className="work-observation-message" role="status">{message}</p>}
    </section>
  );
}
