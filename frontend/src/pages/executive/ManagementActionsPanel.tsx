import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { api } from "../../api";
import type {
  ManagementAction,
  ManagementActionActiveStatus,
  ManagementActionCreate,
  ManagementActionEvent,
  ManagementActionEventCreate,
  ManagementActionPriority,
  ManagementActionRevisionRequest,
  ManagementActionUpdate,
  ManagementActionVerifyDone,
  Page,
} from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";
import "./ManagementActionsPanel.css";

type PanelMode = { actionId: string; panel: "edit" | "progress" | "outcome" | "report" | "verify" | "cancel" | "history" };
type ActionCommand =
  | { kind: "create"; input: ManagementActionCreate }
  | { kind: "update"; actionId: string; input: ManagementActionUpdate }
  | { kind: "cancel"; actionId: string; input: ManagementActionRevisionRequest }
  | { kind: "progress" | "outcome" | "report"; actionId: string; input: ManagementActionEventCreate }
  | { kind: "verify"; actionId: string; input: ManagementActionVerifyDone };
type ActionResult = ManagementAction | ManagementActionEvent;

const initialDraft = { title: "", description: "", owner: "", priority: "NORMAL" as ManagementActionPriority, dueAt: "", reason: "" };
const statusLabels: Record<ManagementAction["status"], string> = { OPEN: "待开始", IN_PROGRESS: "进行中", CANCELLED: "已取消" };
const priorityLabels: Record<ManagementActionPriority, string> = { LOW: "低", NORMAL: "普通", HIGH: "高", URGENT: "紧急" };
const eventLabels: Record<ManagementActionEvent["event_type"], string> = {
  CREATED: "创建行动", UPDATED: "更新行动", CANCELLED: "取消行动", PROGRESS: "记录进展",
  OUTCOME: "记录结果", DONE_REPORTED: "报告完成", DONE_VERIFIED: "核实完成",
};

export function ManagementActionsPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(initialDraft);
  const [idempotencyKey, setIdempotencyKey] = useState(createIdempotencyKey);
  const [mode, setMode] = useState<PanelMode | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const actionsQuery = useQuery({
    queryKey: ["management-actions", projectId],
    queryFn: () => api.listManagementActions(projectId, true),
  });
  const historyQuery = useQuery({
    queryKey: ["management-action-history", projectId, mode?.actionId],
    queryFn: () => api.getManagementActionHistory(projectId, mode!.actionId),
    enabled: mode?.panel === "history",
  });
  const mutation = useMutation<ActionResult, Error, ActionCommand>({
    mutationFn: (command) => {
      if (command.kind === "create") return api.createManagementAction(projectId, command.input);
      if (command.kind === "update") return api.updateManagementAction(projectId, command.actionId, command.input);
      if (command.kind === "cancel") return api.cancelManagementAction(projectId, command.actionId, command.input);
      if (command.kind === "progress") return api.appendManagementActionProgress(projectId, command.actionId, command.input);
      if (command.kind === "outcome") return api.appendManagementActionOutcome(projectId, command.actionId, command.input);
      if (command.kind === "report") return api.reportManagementActionDone(projectId, command.actionId, command.input);
      if (command.kind === "verify") return api.verifyManagementActionDone(projectId, command.actionId, command.input);
      throw new Error("未知的行动操作。");
    },
    onSuccess: async (_result, command) => {
      const messages: Record<ActionCommand["kind"], string> = {
        create: "现实管理行动已创建。", update: "行动信息已更新并记录版本。", cancel: "行动已取消，历史仍保留。",
        progress: "行动进展已记录。", outcome: "行动结果已记录。", report: "执行人已报告完成，仍需人工核实。",
        verify: "人工核实已记录。",
      };
      setNotice(messages[command.kind]);
      setError(null);
      if (command.kind === "create") {
        setDraft(initialDraft);
        setIdempotencyKey(createIdempotencyKey());
      }
      setMode(null);
      await queryClient.invalidateQueries({ queryKey: ["management-actions", projectId] });
      await queryClient.invalidateQueries({ queryKey: ["management-action-history", projectId] });
    },
    onError: (value) => setError(asError(value)),
  });

  const createAction = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    mutation.mutate({ kind: "create", input: {
      title: draft.title.trim(), description: draft.description.trim() || null,
      owner: draft.owner.trim() || null, priority: draft.priority,
      due_at: toIso(draft.dueAt), reason: draft.reason.trim() || null,
      idempotency_key: idempotencyKey,
    } });
  };

  const submitActionForm = (event: FormEvent<HTMLFormElement>, action: ManagementAction, panel: NonNullable<PanelMode>["panel"]) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const expected_revision = action.revision;
    if (panel === "edit") {
      mutation.mutate({ kind: "update", actionId: action.id, input: {
        expected_revision,
        owner: String(data.get("owner") ?? "").trim() || null,
        status: String(data.get("status")) as ManagementActionActiveStatus,
        due_at: toIso(String(data.get("due_at") ?? "")),
        reason: String(data.get("reason") ?? "").trim(),
      } });
    } else if (panel === "cancel") {
      mutation.mutate({ kind: "cancel", actionId: action.id, input: { expected_revision, reason: String(data.get("reason") ?? "").trim() } });
    } else if (panel === "verify") {
      mutation.mutate({ kind: "verify", actionId: action.id, input: {
        expected_revision, verification_note: String(data.get("verification_note") ?? "").trim(),
        reason: String(data.get("reason") ?? "").trim() || null,
      } });
    } else {
      const kind = panel as "progress" | "outcome" | "report";
      mutation.mutate({ kind, actionId: action.id, input: {
        expected_revision, message: String(data.get("message") ?? "").trim(),
        reason: String(data.get("reason") ?? "").trim() || null, details: {},
      } });
    }
  };

  const togglePanel = (actionId: string, panel: NonNullable<PanelMode>["panel"]) => {
    setError(null);
    setNotice("");
    setMode((current) => current?.actionId === actionId && current.panel === panel ? null : { actionId, panel });
  };

  if (actionsQuery.isLoading) return <section className="panel management-actions-panel"><p className="panel-loading">正在读取现实管理行动……</p></section>;
  if (actionsQuery.error) return <section className="panel management-actions-panel"><StatusMessage tone="danger" title="现实管理行动读取失败" description={(actionsQuery.error as Error).message} action={{ label: "重试", onClick: () => void actionsQuery.refetch() }} /></section>;

  const items = actionsQuery.data?.items ?? [];
  const activeCount = items.filter((item) => item.status !== "CANCELLED").length;
  const reportedCount = items.filter((item) => item.reported_done).length;
  const verifiedCount = items.filter((item) => item.verified_done).length;

  return <section className="panel management-actions-panel">
    <div className="panel-heading">
      <div><p className="eyebrow">REAL-WORLD MANAGEMENT</p><h2>现实管理行动</h2><p>记录企业人员要做的事、推进过程与实际结果；不会调用系统动作或修改企业投影。</p></div>
      <button className="button secondary" disabled={mutation.isPending} onClick={() => void actionsQuery.refetch()}>刷新</button>
    </div>
    <div className="management-action-distinction" role="note">
      <strong>现实行动 ≠ Agent ActionInvocation</strong>
      <span>这里是管理者分派和跟踪的现实工作，不会自动执行 ERP、MES 或其它系统操作。</span>
      <p><b>报告完成 ≠ 核实完成。</b>执行人报告后仍显示“待核实”；由管理者核对结果后再单独确认。</p>
    </div>
    {notice && <div className="inline-notice management-action-notice" role="status">{notice}</div>}
    {error && <div className="management-action-error"><StatusMessage tone="danger" title="行动没有完成" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} /></div>}

    <div className="management-action-summary" aria-label="行动概况">
      <span>行动 <strong>{items.length}</strong></span><span>未取消 <strong>{activeCount}</strong></span>
      <span>报告完成 <strong>{reportedCount}</strong></span><span>已核实 <strong>{verifiedCount}</strong></span>
    </div>

    <form className="management-action-create" onSubmit={createAction}>
      <h3>新建现实行动</h3>
      <label>行动名称<input required maxLength={200} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <label>负责人<input value={draft.owner} onChange={(event) => setDraft({ ...draft, owner: event.target.value })} placeholder="姓名或岗位" /></label>
      <label>优先级<select value={draft.priority} onChange={(event) => setDraft({ ...draft, priority: event.target.value as ManagementActionPriority })}><option value="LOW">低</option><option value="NORMAL">普通</option><option value="HIGH">高</option><option value="URGENT">紧急</option></select></label>
      <label>到期时间<input type="datetime-local" value={draft.dueAt} onChange={(event) => setDraft({ ...draft, dueAt: event.target.value })} /></label>
      <label className="management-action-wide">说明<textarea rows={2} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
      <label className="management-action-wide">创建理由（可选）<input value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} /></label>
      <button className="button primary management-action-wide" disabled={mutation.isPending || !draft.title.trim()}>{mutation.isPending ? "正在保存…" : "创建行动"}</button>
    </form>

    <div className="management-action-list">
      <div className="management-action-list-heading"><h3>行动清单</h3><span>每次提交使用页面显示的当前 revision</span></div>
      {!items.length ? <p className="empty-copy">当前企业尚无现实管理行动。</p> : items.map((action) => {
        const openMode = mode?.actionId === action.id ? mode.panel : null;
        const cancelled = action.status === "CANCELLED";
        return <article className="management-action-card" key={action.id}>
          <div className="management-action-card-heading">
            <div><span className={`status-pill ${cancelled ? "" : "status-effective"}`}>{statusLabels[action.status]}</span><span className="tag">优先级：{priorityLabels[action.priority]}</span><h4>{action.title}</h4></div>
            <small>revision {action.revision}</small>
          </div>
          {action.description && <p className="management-action-description">{action.description}</p>}
          <dl className="management-action-meta"><div><dt>负责人</dt><dd>{action.owner || "未指定"}</dd></div><div><dt>到期</dt><dd>{formatDate(action.due_at)}</dd></div>
            <div><dt>执行人报告</dt><dd>{action.reported_done ? `已报告 · ${formatDate(action.reported_done_at)}` : "未报告"}</dd></div>
            <div><dt>管理者核实</dt><dd>{action.verified_done ? `已核实 · ${formatDate(action.verified_done_at)}` : action.reported_done ? "待核实" : "尚未报告"}</dd></div></dl>
          <div className="management-action-buttons">
            <button className="button secondary" disabled={mutation.isPending || cancelled} onClick={() => togglePanel(action.id, "edit")}>编辑行动</button>
            <button className="button secondary" disabled={mutation.isPending || cancelled} onClick={() => togglePanel(action.id, "progress")}>记录进展</button>
            <button className="button secondary" disabled={mutation.isPending || cancelled} onClick={() => togglePanel(action.id, "outcome")}>记录结果</button>
            <button className="button secondary" disabled={mutation.isPending || cancelled || action.reported_done} onClick={() => togglePanel(action.id, "report")}>报告完成</button>
            <button className="button secondary" disabled={mutation.isPending || cancelled || !action.reported_done || action.verified_done} onClick={() => togglePanel(action.id, "verify")}>人工核实</button>
            <button className="button text-button" disabled={mutation.isPending || cancelled || action.reported_done} onClick={() => togglePanel(action.id, "cancel")}>取消</button>
            <button className="button text-button" disabled={mutation.isPending} onClick={() => togglePanel(action.id, "history")}>查看历史</button>
          </div>
          {openMode && openMode !== "history" && <ActionCommandForm action={action} panel={openMode} pending={mutation.isPending} onSubmit={(event) => submitActionForm(event, action, openMode)} onClose={() => setMode(null)} />}
          {openMode === "history" && <ActionHistory action={action} query={historyQuery} />}
        </article>;
      })}
    </div>
  </section>;
}

function ActionCommandForm({ action, panel, pending, onSubmit, onClose }: {
  action: ManagementAction;
  panel: Exclude<NonNullable<PanelMode>["panel"], "history">;
  pending: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
}) {
  return <form className="management-action-command" onSubmit={onSubmit}>
    <div className="management-action-revision-note">提交基于当前 revision {action.revision}；成功后版本会递增。</div>
    {panel === "edit" && <>
      <label>负责人<input name="owner" defaultValue={action.owner ?? ""} /></label>
      <label>状态<select name="status" defaultValue={action.status === "IN_PROGRESS" ? "IN_PROGRESS" : "OPEN"}><option value="OPEN">待开始</option><option value="IN_PROGRESS">进行中</option></select></label>
      <label>到期时间<input name="due_at" type="datetime-local" defaultValue={toLocalInput(action.due_at)} /></label>
      <label className="management-action-wide">修改理由<textarea name="reason" required rows={2} /></label>
      <button className="button primary" disabled={pending}>保存更新</button>
    </>}
    {(panel === "progress" || panel === "outcome" || panel === "report") && <>
      <label className="management-action-wide">{panel === "progress" ? "进展内容" : panel === "outcome" ? "实际结果" : "完成说明"}<textarea name="message" required rows={3} /></label>
      <label className="management-action-wide">补充理由（可选）<input name="reason" /></label>
      <button className="button primary" disabled={pending}>{panel === "progress" ? "保存进展" : panel === "outcome" ? "保存结果" : "报告已完成"}</button>
    </>}
    {panel === "verify" && <>
      <p className="management-action-verify-warning">执行人已报告完成，但这不代表任务已经核实。请根据结果或凭据进行人工确认。</p>
      <label className="management-action-wide">核实说明<textarea name="verification_note" required rows={3} /></label>
      <label className="management-action-wide">核实理由（可选）<input name="reason" /></label>
      <button className="button primary" disabled={pending}>确认核实完成</button>
    </>}
    {panel === "cancel" && <>
      <p className="management-action-verify-warning">取消不会删除行动或历史记录。</p>
      <label className="management-action-wide">取消理由<textarea name="reason" required rows={2} /></label>
      <button className="button primary danger-action" disabled={pending}>确认取消</button>
    </>}
    <button type="button" className="button text-button" disabled={pending} onClick={onClose}>收起</button>
  </form>;
}

function ActionHistory({ action, query }: { action: ManagementAction; query: UseQueryResult<Page<ManagementActionEvent>, Error> }) {
  return <div className="management-action-history">
    <div className="management-action-revision-note">行动历史 · 当前 revision {action.revision} · 事件只追加，不覆盖</div>
    {query.isLoading ? <p>正在读取历史…</p> : query.error ? <p role="alert">读取历史失败：{(query.error as Error).message}</p> : query.data?.items.length ? <ol>
      {query.data.items.map((event) => <li key={event.id}>
        <div><strong>{eventLabels[event.event_type]}</strong><span>revision {event.revision} · {event.actor_id} · {formatDate(event.created_at)}</span></div>
        {event.message && <p>{event.message}</p>}{event.reason && <small>理由：{event.reason}</small>}
        {(event.from_status || event.to_status) && <small>状态：{event.from_status ? statusLabels[event.from_status] : "—"} → {event.to_status ? statusLabels[event.to_status] : "—"}</small>}
        {Object.keys(event.details).length > 0 && <details><summary>查看变更详情</summary><pre>{JSON.stringify(event.details, null, 2)}</pre></details>}
      </li>)}
    </ol> : <p>暂无历史事件。</p>}
  </div>;
}

function formatDate(value: string | null) { return value ? new Date(value).toLocaleString() : "未设置"; }
function toIso(value: string) { return value ? new Date(value).toISOString() : null; }
function createIdempotencyKey() {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `management-action-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
function toLocalInput(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}
function asError(value: unknown) { return value instanceof Error ? value : new Error("操作没有完成。"); }
