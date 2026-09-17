import { useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type {
  PotentialEvidence,
  PotentialEvidenceStatus,
  PotentialHistory,
  PotentialRecord,
  PotentialRecordCreate,
  PotentialType,
} from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

type EvidenceDraft = PotentialEvidence & { id: string; observed_at_input: string };
type PotentialDraft = Omit<PotentialRecordCreate, "company_id" | "project_id" | "valid_from" | "valid_until" | "supporting_evidence" | "counterevidence"> & {
  valid_from: string;
  valid_until: string;
  supporting_evidence: EvidenceDraft[];
  counterevidence: EvidenceDraft[];
};
type StatusAction = "ACCEPTED" | "REJECTED" | "WITHDRAWN";

const typeOptions: Array<{ value: PotentialType; label: string }> = [
  { value: "RELATION_HYPOTHESIS", label: "关系假设" },
  { value: "PROBLEM_HYPOTHESIS", label: "问题假设" },
  { value: "DESIGN_TRADEOFF", label: "架构权衡" },
  { value: "CAUSAL_HYPOTHESIS", label: "因果假设" },
  { value: "MANAGEMENT_LEARNING", label: "管理经验" },
];
const evidenceStatusLabels: Record<PotentialEvidenceStatus, string> = {
  UNTESTED: "尚未检验", SUPPORTED: "有支持证据", CONTESTED: "存在争议", REFUTED: "已被反驳", STALE: "证据可能过期",
};
const statusLabels: Record<PotentialRecord["human_status"], string> = {
  ACCEPTED: "纳入潜在库（非事实确认）", REJECTED: "已拒绝", WITHDRAWN: "已撤回，保留历史",
};
const operationLabels: Record<string, string> = {
  CREATED: "人工创建", ACCEPTED: "重新纳入潜在库", EDITED: "人工修订", REJECTED: "人工拒绝", WITHDRAWN: "人工撤回",
};
let evidenceDraftSequence = 0;

function blankDraft(): PotentialDraft {
  return {
    potential_type: "PROBLEM_HYPOTHESIS", claim: "", applicability_scope: "", valid_from: "", valid_until: "",
    task_source: "管理者手工记录", supporting_evidence: [], counterevidence: [],
    verification_method: "尚未定义", evidence_status: "UNTESTED",
  };
}

function draftFromRecord(record: PotentialRecord): PotentialDraft {
  const toEvidenceDrafts = (items: PotentialEvidence[]) => items.map((item) => ({
    ...item, id: nextEvidenceId(), observed_at_input: item.observed_at ? toLocalDateTime(item.observed_at) : "",
  }));
  return {
    potential_type: record.potential_type, claim: record.claim, applicability_scope: record.applicability_scope,
    valid_from: record.valid_from ? toLocalDateTime(record.valid_from) : "",
    valid_until: record.valid_until ? toLocalDateTime(record.valid_until) : "",
    task_source: record.task_source, supporting_evidence: toEvidenceDrafts(record.supporting_evidence),
    counterevidence: toEvidenceDrafts(record.counterevidence), verification_method: record.verification_method,
    evidence_status: record.evidence_status,
  };
}

function nextEvidenceId() { evidenceDraftSequence += 1; return `evidence-${evidenceDraftSequence}`; }

export function PotentialRecordsPanel({ projectId, companyId }: { projectId: string; companyId: string }) {
  const queryClient = useQueryClient();
  const createIdempotencyKey = useRef<string | null>(null);
  const [includeHistory, setIncludeHistory] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [historyId, setHistoryId] = useState<string | null>(null);
  const [statusAction, setStatusAction] = useState<{ recordId: string; action: StatusAction } | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const recordsQuery = useQuery({
    queryKey: ["potential-records", projectId, includeHistory],
    queryFn: () => api.listPotentialRecords(projectId, includeHistory),
  });
  const historyQuery = useQuery({
    queryKey: ["potential-record-history", projectId, historyId],
    queryFn: () => api.getPotentialRecordHistory(projectId, historyId!),
    enabled: Boolean(historyId),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["potential-records", projectId] });
  const createMutation = useMutation({
    mutationFn: (input: PotentialRecordCreate) => api.createPotentialRecord(projectId, input),
    onSuccess: async (record) => { createIdempotencyKey.current = null; setNotice(`“${record.claim}”已由你手工加入潜在库；这不表示它已成为正式事实。`); setCreateOpen(false); await refresh(); },
    onError: (value) => setError(asError(value)),
  });
  const editMutation = useMutation({
    mutationFn: ({ record, draft, reason }: { record: PotentialRecord; draft: PotentialDraft; reason: string }) =>
      api.editPotentialRecord(projectId, record.id, { ...draftToFields(draft), expected_version: record.version, reason }),
    onSuccess: async (record) => { setNotice(`已保存第 ${record.version} 版修订，变更原因已记入历史。`); setEditingId(null); await refresh(); },
    onError: (value) => setError(asError(value)),
  });
  const actionMutation = useMutation({
    mutationFn: ({ record, action, reason }: { record: PotentialRecord; action: StatusAction; reason: string }) => {
      const input = { expected_version: record.version, reason };
      if (action === "ACCEPTED") return api.acceptPotentialRecord(projectId, record.id, input);
      if (action === "REJECTED") return api.rejectPotentialRecord(projectId, record.id, input);
      return api.withdrawPotentialRecord(projectId, record.id, input);
    },
    onSuccess: async (record) => { setNotice(`“${record.claim}”的人工状态已更新；操作人、理由和版本已留痕。`); setStatusAction(null); await refresh(); },
    onError: (value) => setError(asError(value)),
  });

  const submitCreate = (draft: PotentialDraft) => {
    createIdempotencyKey.current ??= globalThis.crypto?.randomUUID?.() ?? `potential-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    createMutation.mutate({
      ...draftToFields(draft),
      company_id: companyId,
      project_id: projectId,
      idempotency_key: createIdempotencyKey.current,
    });
  };
  const submitEdit = (event: FormEvent<HTMLFormElement>, record: PotentialRecord, draft: PotentialDraft) => {
    event.preventDefault();
    const reason = String(new FormData(event.currentTarget).get("edit_reason") ?? "").trim();
    if (!reason) { setError(new Error("请写明本次修订的原因，便于后续追溯。")); return; }
    editMutation.mutate({ record, draft, reason });
  };
  const submitStatus = (event: FormEvent<HTMLFormElement>, record: PotentialRecord, action: StatusAction) => {
    event.preventDefault();
    const reason = String(new FormData(event.currentTarget).get("action_reason") ?? "").trim();
    if (!reason) { setError(new Error("请写明本次人工判断的理由。")); return; }
    if (action === "WITHDRAWN" && !window.confirm("撤回后记录不能再编辑或恢复，但完整历史仍会保留。确定撤回吗？")) return;
    actionMutation.mutate({ record, action, reason });
  };

  return <section className="panel potential-panel" aria-labelledby="potential-records-heading">
    <div className="panel-heading"><div><h2 id="potential-records-heading">潜在认识库</h2><p>仅保存人工记录与人工判断；不代表事实，也不会写入正式企业投影。</p></div><span>{recordsQuery.data?.total ?? 0} 条</span></div>
    <div className="potential-separation-notice"><strong>独立于正式企业事实</strong><span>这里的内容用于保存管理层的假设、问题线索和经验。纳入潜在库 ≠ 证实为真；AI 候选不会由此界面伪造或自动写入。</span></div>
    {notice && <StatusMessage title="已记录" description={notice} action={{ label: "关闭", onClick: () => setNotice("") }} />}
    {error && <StatusMessage tone="danger" title="操作未完成" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    {recordsQuery.error && <StatusMessage tone="danger" title="无法读取潜在认识库" description={(recordsQuery.error as Error).message} action={{ label: "重试", onClick: () => void recordsQuery.refetch() }} />}
    <div className="potential-toolbar">
      <label><input type="checkbox" checked={includeHistory} onChange={(event) => setIncludeHistory(event.target.checked)} />包含已拒绝、已撤回及已反驳记录</label>
      <button className="button primary" onClick={() => { setCreateOpen((value) => !value); setEditingId(null); }}>{createOpen ? "收起人工记录" : "人工新增潜在认识"}</button>
    </div>
    {createOpen && <div className="potential-form-wrap"><h3>人工新增</h3><p>请把它作为待检验的管理认识来描述，不要把推测写成确定事实。</p><PotentialRecordForm submitLabel="保存到潜在认识库" pending={createMutation.isPending} onSubmit={submitCreate} /></div>}
    {recordsQuery.isLoading ? <p className="empty-copy">正在读取潜在认识记录…</p> : recordsQuery.data?.items.length ? <div className="potential-record-list">
      {recordsQuery.data.items.map((record) => {
        const editing = editingId === record.id;
        const action = statusAction?.recordId === record.id ? statusAction.action : null;
        const showingHistory = historyId === record.id;
        return <article className="potential-record" key={record.id}>
          <header><div><span className="potential-type-tag">{typeOptions.find((item) => item.value === record.potential_type)?.label ?? record.potential_type}</span><span className={`potential-status-tag ${record.human_status.toLowerCase()}`}>{statusLabels[record.human_status]}</span><h3>{record.claim}</h3></div><small>版本 {record.version}</small></header>
          <p className="potential-scope"><strong>适用范围：</strong>{record.applicability_scope}</p>
          <div className="potential-meta"><span>证据状态：{evidenceStatusLabels[record.evidence_status]}</span><span>来源任务：{record.task_source}</span><span>创建于：{formatDate(record.created_at)}</span>{record.valid_from && <span>有效起：{formatDate(record.valid_from)}</span>}{record.valid_until && <span>有效止：{formatDate(record.valid_until)}</span>}</div>
          <details className="potential-evidence-details"><summary>证据与检验方式（支持 {record.supporting_evidence.length} · 反证 {record.counterevidence.length}）</summary><EvidenceReadOnly title="支持证据" items={record.supporting_evidence} /><EvidenceReadOnly title="反证" items={record.counterevidence} /><p><strong>检验方式：</strong>{record.verification_method}</p></details>
          <div className="potential-actions">
            <button className="button secondary" onClick={() => { setHistoryId(showingHistory ? null : record.id); setEditingId(null); setStatusAction(null); }}>{showingHistory ? "收起历史" : "查看不可变历史"}</button>
            {record.human_status !== "WITHDRAWN" && <button className="button secondary" onClick={() => { setEditingId(editing ? null : record.id); setHistoryId(null); setStatusAction(null); }}>{editing ? "取消修订" : "编辑并留原因"}</button>}
            {record.human_status === "ACCEPTED" && <button className="button text-button danger-text" onClick={() => { setStatusAction({ recordId: record.id, action: "REJECTED" }); setEditingId(null); setHistoryId(null); }}>拒绝保留</button>}
            {record.human_status === "REJECTED" && <button className="button secondary" onClick={() => { setStatusAction({ recordId: record.id, action: "ACCEPTED" }); setEditingId(null); setHistoryId(null); }}>重新纳入潜在库</button>}
            {record.human_status !== "WITHDRAWN" && <button className="button secondary danger-button" onClick={() => { setStatusAction({ recordId: record.id, action: "WITHDRAWN" }); setEditingId(null); setHistoryId(null); }}>撤回（保留历史）</button>}
          </div>
          {editing && <div className="potential-form-wrap"><h4>修订记录 · 当前版本 {record.version}</h4><PotentialRecordForm key={`${record.id}-${record.version}`} initial={draftFromRecord(record)} submitLabel="保存修订" pending={editMutation.isPending} onSubmit={(draft, event) => submitEdit(event, record, draft)} onCancel={() => setEditingId(null)} includeReason /></div>}
          {action && <form className="potential-reason-form" onSubmit={(event) => submitStatus(event, record, action)}><strong>{action === "REJECTED" ? "拒绝保留" : action === "ACCEPTED" ? "重新纳入潜在库" : "撤回记录"}</strong><p>{action === "WITHDRAWN" ? "撤回为终态；记录与历史仍保留。" : "此人工决定将记录在新的不可变版本中。"}</p><label>人工判断理由<textarea name="action_reason" required maxLength={4000} rows={3} /></label><div><button className="button secondary" type="button" onClick={() => setStatusAction(null)}>取消</button><button className={action === "REJECTED" || action === "WITHDRAWN" ? "button secondary danger-button" : "button primary"} disabled={actionMutation.isPending}>{actionMutation.isPending ? "正在保存…" : "确认并留痕"}</button></div></form>}
          {showingHistory && <PotentialHistoryView history={historyQuery.data} loading={historyQuery.isLoading} error={historyQuery.error as Error | null} />}
        </article>;
      })}
    </div> : <p className="empty-copy">{includeHistory ? "当前企业还没有潜在认识记录。可以由管理者手工新增。" : "当前没有可显示的潜在认识。已拒绝或撤回的记录可通过上方筛选查看。"}</p>}
  </section>;
}

function PotentialRecordForm({ initial, submitLabel, pending, onSubmit, onCancel, includeReason = false }: {
  initial?: PotentialDraft;
  submitLabel: string;
  pending: boolean;
  onSubmit: (draft: PotentialDraft, event: FormEvent<HTMLFormElement>) => void;
  onCancel?: () => void;
  includeReason?: boolean;
}) {
  const [draft, setDraft] = useState<PotentialDraft>(() => initial ? { ...initial } : blankDraft());
  const update = <K extends keyof PotentialDraft>(key: K, value: PotentialDraft[K]) => setDraft((current) => ({ ...current, [key]: value }));
  return <form className="potential-form" onSubmit={(event) => { event.preventDefault(); onSubmit(draft, event); }}>
    <label>认识类型<select value={draft.potential_type} onChange={(event) => update("potential_type", event.target.value as PotentialType)}>{typeOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
    <label className="potential-form-wide">待检验主张<textarea value={draft.claim} onChange={(event) => update("claim", event.target.value)} required maxLength={12000} rows={3} placeholder="例如：生产计划频繁变更可能导致采购加急。" /></label>
    <label className="potential-form-wide">适用范围<textarea value={draft.applicability_scope} onChange={(event) => update("applicability_scope", event.target.value)} required maxLength={4000} rows={2} placeholder="适用于哪个部门、流程、时间或条件？" /></label>
    <label>来源任务<input value={draft.task_source} onChange={(event) => update("task_source", event.target.value)} required maxLength={2000} placeholder="例如：月度经营复盘" /></label>
    <label>证据状态<select value={draft.evidence_status} onChange={(event) => update("evidence_status", event.target.value as PotentialEvidenceStatus)}>{Object.entries(evidenceStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label>有效开始（可选）<input type="datetime-local" value={draft.valid_from} onChange={(event) => update("valid_from", event.target.value)} /></label>
    <label>有效结束（可选）<input type="datetime-local" value={draft.valid_until} onChange={(event) => update("valid_until", event.target.value)} /></label>
    <EvidenceEditor title="支持证据" items={draft.supporting_evidence} onChange={(items) => update("supporting_evidence", items)} />
    <EvidenceEditor title="反证" items={draft.counterevidence} onChange={(items) => update("counterevidence", items)} />
    <label className="potential-form-wide">检验方式<input value={draft.verification_method} onChange={(event) => update("verification_method", event.target.value)} required maxLength={4000} /></label>
    {includeReason && <label className="potential-form-wide">本次修订原因<textarea name="edit_reason" required maxLength={4000} rows={2} placeholder="说明为什么修改；该原因将进入不可变历史。" /></label>}
    <div className="potential-form-actions"><button className="button primary" disabled={pending}>{pending ? "正在保存…" : submitLabel}</button>{onCancel && <button className="button secondary" type="button" onClick={onCancel}>取消</button>}</div>
  </form>;
}

function EvidenceEditor({ title, items, onChange }: { title: string; items: EvidenceDraft[]; onChange: (items: EvidenceDraft[]) => void }) {
  const update = (id: string, patch: Partial<EvidenceDraft>) => onChange(items.map((item) => item.id === id ? { ...item, ...patch } : item));
  return <fieldset className="potential-evidence-editor"><legend>{title}</legend>{items.map((item) => <div className="potential-evidence-row" key={item.id}>
    <label>来源<input value={item.source_ref} onChange={(event) => update(item.id, { source_ref: event.target.value })} required maxLength={500} placeholder="会议纪要、报告或访谈" /></label>
    <label>摘录<textarea value={item.excerpt} onChange={(event) => update(item.id, { excerpt: event.target.value })} required maxLength={12000} rows={2} placeholder="记录直接支持或反驳主张的原文" /></label>
    <label>记录时间<input type="datetime-local" value={item.observed_at_input} onChange={(event) => update(item.id, { observed_at_input: event.target.value })} /></label>
    <label>备注（可选）<input value={item.note ?? ""} onChange={(event) => update(item.id, { note: event.target.value })} maxLength={4000} /></label>
    <button className="button text-button danger-text" type="button" onClick={() => onChange(items.filter((entry) => entry.id !== item.id))}>移除此证据</button>
  </div>)}<button className="button secondary" type="button" onClick={() => onChange([...items, { id: nextEvidenceId(), source_ref: "", excerpt: "", observed_at: null, note: null, observed_at_input: "" }])}>添加{title}</button></fieldset>;
}

function EvidenceReadOnly({ title, items }: { title: string; items: PotentialEvidence[] }) {
  return <div className="potential-evidence-readonly"><strong>{title}</strong>{items.length ? items.map((item, index) => <blockquote key={`${item.source_ref}-${index}`}><small>{item.source_ref}</small><p>{item.excerpt}</p>{item.note && <span>{item.note}</span>}</blockquote>) : <span>未记录</span>}</div>;
}

function PotentialHistoryView({ history, loading, error }: { history?: PotentialHistory; loading: boolean; error: Error | null }) {
  if (loading) return <div className="potential-history"><strong>不可变历史</strong><span>正在读取…</span></div>;
  if (error) return <div className="potential-history" role="alert">历史读取失败：{error.message}</div>;
  return <div className="potential-history"><div><strong>不可变版本记录</strong><span>版本 {history?.versions.length ?? 0} 个 · 审计事件 {history?.audit.length ?? 0} 条</span></div>
    {history?.versions.map((version) => <details key={version.id}><summary>v{version.version} · {operationLabels[version.operation] ?? version.operation} · {formatDate(version.created_at)}</summary><p>记录人：{version.actor_id}{version.reason ? ` · 原因：${version.reason}` : " · 创建时无变更原因"}</p><PotentialSnapshot snapshot={version.snapshot} /></details>)}
    <div className="potential-audit-list"><strong>审计链</strong>{history?.audit.map((event) => <article key={event.id}><span>v{event.version} · {operationLabels[event.operation] ?? event.operation} · {event.actor_id}</span><small>{formatDate(event.created_at)}{event.reason ? ` · ${event.reason}` : ""}</small><code>前：{event.before_hash?.slice(0, 16) ?? "无"} → 后：{event.after_hash.slice(0, 16)}</code></article>)}</div>
  </div>;
}

function PotentialSnapshot({ snapshot }: { snapshot: Record<string, unknown> }) {
  const supporting = Array.isArray(snapshot.supporting_evidence) ? snapshot.supporting_evidence as PotentialEvidence[] : [];
  const counterevidence = Array.isArray(snapshot.counterevidence) ? snapshot.counterevidence as PotentialEvidence[] : [];
  return <div className="potential-snapshot">
    <dl>
      <div><dt>主张</dt><dd>{String(snapshot.claim ?? "—")}</dd></div>
      <div><dt>适用范围</dt><dd>{String(snapshot.applicability_scope ?? "—")}</dd></div>
      <div><dt>来源任务</dt><dd>{String(snapshot.task_source ?? "—")}</dd></div>
      <div><dt>证据状态</dt><dd>{evidenceStatusLabels[String(snapshot.evidence_status) as PotentialEvidenceStatus] ?? String(snapshot.evidence_status ?? "—")}</dd></div>
      <div><dt>检验方式</dt><dd>{String(snapshot.verification_method ?? "—")}</dd></div>
      <div><dt>支持 / 反证</dt><dd>{supporting.length} / {counterevidence.length} 条</dd></div>
    </dl>
    <details><summary>查看完整版本快照</summary><pre>{JSON.stringify(snapshot, null, 2)}</pre></details>
  </div>;
}

function draftToFields(draft: PotentialDraft): Omit<PotentialRecordCreate, "company_id" | "project_id"> {
  const evidence = (items: EvidenceDraft[]): PotentialEvidence[] => items.filter((item) => item.source_ref.trim() && item.excerpt.trim()).map((item) => ({
    source_ref: item.source_ref.trim(), excerpt: item.excerpt.trim(), note: item.note?.trim() || null,
    observed_at: item.observed_at_input ? new Date(item.observed_at_input).toISOString() : null,
  }));
  return {
    potential_type: draft.potential_type, claim: draft.claim.trim(), applicability_scope: draft.applicability_scope.trim(),
    valid_from: draft.valid_from ? new Date(draft.valid_from).toISOString() : null,
    valid_until: draft.valid_until ? new Date(draft.valid_until).toISOString() : null,
    task_source: draft.task_source.trim(), supporting_evidence: evidence(draft.supporting_evidence),
    counterevidence: evidence(draft.counterevidence), verification_method: draft.verification_method.trim(),
    evidence_status: draft.evidence_status,
  };
}

function toLocalDateTime(value: string) {
  const date = new Date(value);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function asError(value: unknown) { return value instanceof Error ? value : new Error("操作没有完成，请刷新后重试。"); }
