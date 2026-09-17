import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api";
import type { ManagementObservationKind, ObservationAttachment, ObservationIngestion, ObservationIngestionRequest, ObservationView } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";
import { useWorkspace } from "../../workspace/WorkspaceContext";

const kinds: Array<{ value: ManagementObservationKind; label: string }> = [
  { value: "MEETING", label: "会议纪要" },
  { value: "WORK_REPORT", label: "工作汇报" },
  { value: "METRIC_RESULT", label: "绩效或指标结果" },
  { value: "INCIDENT", label: "事件 / 问题" },
  { value: "OTHER", label: "其他信息" },
];

const formatDate = (value: string | null) => value ? new Date(value).toLocaleString() : "未填写发生时间";
const formatBytes = (value: number) => value < 1024 ? `${value} B` : value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / (1024 * 1024)).toFixed(2)} MB`;

export function ManagementInboxPage() {
  const { selectedProjectId } = useWorkspace();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<ManagementObservationKind>("MEETING");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [occurredAt, setOccurredAt] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadKind, setUploadKind] = useState<ManagementObservationKind>("MEETING");
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadOccurredAt, setUploadOccurredAt] = useState("");
  const [fileInputKey, setFileInputKey] = useState(0);
  const [ingestionResult, setIngestionResult] = useState<ObservationIngestion | null>(null);
  const [attachmentsObservationId, setAttachmentsObservationId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editKind, setEditKind] = useState<ManagementObservationKind>("MEETING");
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [editOccurredAt, setEditOccurredAt] = useState("");
  const [includeWithdrawn, setIncludeWithdrawn] = useState(false);
  const [historyId, setHistoryId] = useState<string | null>(null);
  const [extractingObservationId, setExtractingObservationId] = useState<string | null>(null);
  const [consentedObservationId, setConsentedObservationId] = useState<string | null>(null);
  const [selectedModelProfileId, setSelectedModelProfileId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const objectUrls = useRef<string[]>([]);

  const observationsQuery = useQuery({
    queryKey: ["management-observations", selectedProjectId, includeWithdrawn],
    queryFn: () => api.listManagementObservations(selectedProjectId!, includeWithdrawn),
    enabled: Boolean(selectedProjectId),
  });
  const historyQuery = useQuery({
    queryKey: ["management-observation-history", selectedProjectId, historyId],
    queryFn: () => api.getManagementObservationHistory(selectedProjectId!, historyId!),
    enabled: Boolean(selectedProjectId && historyId),
  });
  const profilesQuery = useQuery({ queryKey: ["model-profiles"], queryFn: api.listModelProfiles });
  const usableProfiles = (profilesQuery.data?.items ?? []).filter((profile) => profile.enabled && profile.has_api_key && profile.provider !== "MOCK");
  const selectedProfile = usableProfiles.find((profile) => profile.id === selectedModelProfileId)
    ?? usableProfiles.find((profile) => profile.is_default)
    ?? usableProfiles[0];
  const extractionHistoryQuery = useQuery({
    queryKey: ["management-observation-extractions", selectedProjectId, extractingObservationId],
    queryFn: () => api.listManagementObservationExtractions(selectedProjectId!, extractingObservationId!),
    enabled: Boolean(selectedProjectId && extractingObservationId),
  });
  const attachmentsQuery = useQuery({
    queryKey: ["management-observation-attachments", selectedProjectId, attachmentsObservationId],
    queryFn: () => api.listManagementObservationAttachments(selectedProjectId!, attachmentsObservationId!),
    enabled: Boolean(selectedProjectId && attachmentsObservationId),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["management-observations", selectedProjectId] });
  useEffect(() => () => objectUrls.current.forEach((url) => URL.revokeObjectURL(url)), []);
  const createMutation = useMutation({
    mutationFn: () => api.createManagementObservation(selectedProjectId!, {
      kind, title: title.trim(), content: content.trim(), occurred_at: occurredAt ? new Date(occurredAt).toISOString() : null,
    }),
    onSuccess: () => { setTitle(""); setContent(""); setOccurredAt(""); setError(null); void refresh(); },
    onError: (cause) => setError((cause as Error).message),
  });
  const reviseMutation = useMutation({
    mutationFn: ({ observation, nextKind, nextTitle, nextContent, nextOccurredAt }: { observation: ObservationView; nextKind: ManagementObservationKind; nextTitle: string; nextContent: string; nextOccurredAt: string }) =>
      api.reviseManagementObservation(selectedProjectId!, observation.id, {
        expected_revision: observation.revision, kind: nextKind, title: nextTitle.trim(), content: nextContent.trim(),
        occurred_at: nextOccurredAt ? new Date(nextOccurredAt).toISOString() : null,
      }),
    onSuccess: () => { setEditingId(null); setError(null); void refresh(); if (historyId) void queryClient.invalidateQueries({ queryKey: ["management-observation-history", selectedProjectId, historyId] }); },
    onError: (cause) => setError((cause as Error).message),
  });
  const withdrawMutation = useMutation({
    mutationFn: (observation: ObservationView) => api.withdrawManagementObservation(selectedProjectId!, observation.id, observation.revision),
    onSuccess: () => { setError(null); void refresh(); },
    onError: (cause) => setError((cause as Error).message),
  });
  const extractionMutation = useMutation({
    mutationFn: (observation: ObservationView) => api.extractManagementObservation(selectedProjectId!, observation.id, {
      model_profile_id: selectedProfile!.id,
      allow_external_model: true,
    }),
    onSuccess: (_result, observation) => {
      setError(null);
      setConsentedObservationId(null);
      void queryClient.invalidateQueries({ queryKey: ["management-observation-extractions", selectedProjectId, observation.id] });
    },
    onError: (cause) => setError((cause as Error).message),
  });
  const ingestionMutation = useMutation({
    mutationFn: (input: ObservationIngestionRequest) => api.ingestManagementObservation(selectedProjectId!, input),
    onSuccess: (result) => {
      setIngestionResult(result);
      setAttachmentsObservationId(result.observation.id);
      setUploadFile(null);
      setUploadTitle("");
      setUploadOccurredAt("");
      setFileInputKey((value) => value + 1);
      setError(null);
      void refresh();
    },
    onError: (cause) => setError((cause as Error).message),
  });
  const attachmentFileMutation = useMutation({
    mutationFn: ({ observationId, attachment }: { observationId: string; attachment: ObservationAttachment; mode: "view" | "download"; previewWindow?: Window }) =>
      api.downloadManagementObservationAttachment(selectedProjectId!, observationId, attachment.id),
    onSuccess: (file, variables) => {
      const url = URL.createObjectURL(file.blob);
      objectUrls.current.push(url);
      if (variables.mode === "view") {
        if (variables.previewWindow && !variables.previewWindow.closed) {
          variables.previewWindow.opener = null;
          variables.previewWindow.location.href = url;
          variables.previewWindow.focus();
        }
        return;
      }
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.fileName;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
    },
    onError: (cause, variables) => {
      variables.previewWindow?.close();
      setError((cause as Error).message);
    },
  });

  if (!selectedProjectId) return <StatusMessage title="请先选择企业" description="日常信息会记录在当前企业投影的独立观察库中。" />;

  const submitNew = (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !content.trim()) return;
    createMutation.mutate();
  };
  const submitUpload = (event: FormEvent) => {
    event.preventDefault();
    if (!uploadFile) { setError("请选择要上传的原始文件。"); return; }
    const extension = uploadFile.name.split(".").pop()?.toLowerCase() ?? "";
    if (!["csv", "xlsx", "docx", "pdf", "txt", "json"].includes(extension)) {
      setError("支持 CSV、XLSX、DOCX、可提取文字的 PDF、TXT 和 JSON 文件。");
      return;
    }
    if (uploadFile.size > 25 * 1024 * 1024) { setError("文件不能超过 25 MB。"); return; }
    ingestionMutation.mutate({
      file: uploadFile,
      observation_kind: uploadKind,
      title: uploadTitle.trim() || null,
      occurred_at: uploadOccurredAt ? new Date(uploadOccurredAt).toISOString() : null,
    });
  };
  const openAttachment = (observationId: string, attachment: ObservationAttachment, mode: "view" | "download") => {
    if (mode === "view") {
      const previewWindow = window.open("about:blank", "_blank");
      if (!previewWindow) {
        setError("浏览器阻止了新标签页，请允许弹窗后重试，或直接下载原文件。");
        return;
      }
      previewWindow.opener = null;
      attachmentFileMutation.mutate({ observationId, attachment, mode, previewWindow });
      return;
    }
    attachmentFileMutation.mutate({ observationId, attachment, mode });
  };
  const beginEdit = (observation: ObservationView) => {
    setEditingId(observation.id);
    setEditKind(observation.kind);
    setEditTitle(observation.title);
    setEditContent(observation.content);
    setEditOccurredAt(observation.occurred_at ? new Date(observation.occurred_at).toISOString().slice(0, 16) : "");
  };
  const saveEdit = (event: FormEvent, observation: ObservationView) => {
    event.preventDefault();
    reviseMutation.mutate({ observation, nextKind: editKind, nextTitle: editTitle, nextContent: editContent, nextOccurredAt: editOccurredAt });
  };

  return <div className="page-stack management-inbox">
    <section className="page-hero"><div><p className="eyebrow">MANAGEMENT INPUT</p><h1>日常信息收件箱</h1><p>收集会议、工作汇报、绩效结果和突发情况，为管理分析提供上下文。</p></div></section>
    <StatusMessage title="独立观察库 · 尚未验证的输入" description="这里保存原始管理信息，不会自动成为企业正式事实或触发行动。分析和纳入正式投影需经过后续流程与人工确认。" />
    {error && <StatusMessage tone="danger" title="操作未完成" description={error} action={{ label: "关闭", onClick: () => setError(null) }} />}

    <section className="panel">
      <div className="panel-heading"><div><h2>记录一条信息</h2><p>尽量写清楚事件、时间、涉及岗位或部门，以及信息来源。</p></div></div>
      <form className="management-inbox-form" onSubmit={submitNew}>
        <label>信息类型<select value={kind} onChange={(event) => setKind(event.target.value as ManagementObservationKind)}>{kinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        <label>发生时间（可选）<input type="datetime-local" value={occurredAt} onChange={(event) => setOccurredAt(event.target.value)} /></label>
        <label className="management-inbox-wide">标题<input maxLength={200} required value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：8 月经营例会关于交付延迟的讨论" /></label>
        <label className="management-inbox-wide">内容<textarea required maxLength={50000} rows={5} value={content} onChange={(event) => setContent(event.target.value)} placeholder="记录原始信息；请区分事实、转述和个人判断。" /></label>
        <div className="management-inbox-actions"><button className="button primary" disabled={createMutation.isPending || !title.trim() || !content.trim()}>{createMutation.isPending ? "正在保存…" : "保存到观察库"}</button><span>每次修改都会保留历史版本。</span></div>
      </form>
      <div className="management-upload-divider"><span>或上传原始调研 / 业务材料</span></div>
      <form className="management-ingestion-form" onSubmit={submitUpload}>
        <label>原始文件<input key={fileInputKey} aria-label="选择原始文件" type="file" accept=".csv,.xlsx,.docx,.pdf,.txt,.json" required onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)} /><small>支持 CSV、XLSX、DOCX、可提取文字的 PDF、TXT、JSON；单文件最大 25 MB。原文件单独保存在观察库附件中。</small></label>
        <label>信息类型<select aria-label="上传信息类型" value={uploadKind} onChange={(event) => setUploadKind(event.target.value as ManagementObservationKind)}>{kinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        <label>标题（可选）<input maxLength={200} value={uploadTitle} onChange={(event) => setUploadTitle(event.target.value)} placeholder={uploadFile?.name ?? "默认使用文件名"} /></label>
        <label>发生时间（可选）<input type="datetime-local" value={uploadOccurredAt} onChange={(event) => setUploadOccurredAt(event.target.value)} /></label>
        <div className="management-inbox-actions"><button className="button primary" disabled={!uploadFile || ingestionMutation.isPending}>{ingestionMutation.isPending ? "正在上传并解析…" : "上传并创建观察记录"}</button>{uploadFile && <span>{uploadFile.name} · {formatBytes(uploadFile.size)}</span>}</div>
      </form>
      {ingestionResult && <div className={`management-ingestion-result ${ingestionResult.preview_truncated ? "truncated" : ""}`} role={ingestionResult.preview_truncated || ingestionResult.warnings?.length ? "alert" : "status"}>
        <div><strong>原文件已加入观察库：{ingestionResult.attachment.file_name}</strong><span>解析器 {ingestionResult.parser_version} · 提取字符数 {ingestionResult.extracted_character_count.toLocaleString()} · {formatBytes(ingestionResult.attachment.size_bytes)}</span></div>
        {ingestionResult.preview_truncated && <p className="management-ingestion-truncated"><strong>预览已截断：</strong>解析预览达到 50,000 字符上限；完整原文件仍保存在观察库附件中，可以打开或下载。</p>}
        {ingestionResult.warnings?.length ? <ul>{ingestionResult.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul> : null}
        <button className="button secondary" onClick={() => document.getElementById("management-observations")?.scrollIntoView({ behavior: "smooth" })}>查看记录和原文件附件</button>
      </div>}
    </section>

    <section className="panel" id="management-observations">
      <div className="panel-heading"><div><h2>已收集的信息</h2><p>记录数：{observationsQuery.data?.total ?? 0}</p></div><label className="management-inbox-filter"><input type="checkbox" checked={includeWithdrawn} onChange={(event) => setIncludeWithdrawn(event.target.checked)} />显示已撤回</label></div>
      {observationsQuery.isLoading ? <p className="panel-loading">正在读取观察库…</p> : observationsQuery.error ? <StatusMessage tone="danger" title="无法读取观察库" description={(observationsQuery.error as Error).message} action={{ label: "重试", onClick: () => void observationsQuery.refetch() }} /> : observationsQuery.data?.items.length ? <div className="management-inbox-list">
        {observationsQuery.data.items.map((observation) => <article key={observation.id} className={observation.status === "WITHDRAWN" ? "is-withdrawn" : ""}>
          {editingId === observation.id ? <form className="management-inbox-form edit" onSubmit={(event) => saveEdit(event, observation)}>
            <label>信息类型<select value={editKind} onChange={(event) => setEditKind(event.target.value as ManagementObservationKind)}>{kinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            <label>发生时间<input type="datetime-local" value={editOccurredAt} onChange={(event) => setEditOccurredAt(event.target.value)} /></label>
            <label className="management-inbox-wide">标题<input maxLength={200} required value={editTitle} onChange={(event) => setEditTitle(event.target.value)} /></label>
            <label className="management-inbox-wide">内容<textarea required maxLength={50000} rows={5} value={editContent} onChange={(event) => setEditContent(event.target.value)} /></label>
            <div className="management-inbox-actions"><button className="button primary" disabled={reviseMutation.isPending}>保存修订</button><button type="button" className="button secondary" onClick={() => setEditingId(null)}>取消</button></div>
          </form> : <>
            <header><div><span className="management-inbox-kind">{kinds.find((item) => item.value === observation.kind)?.label ?? observation.kind}</span><span className={`management-inbox-status ${observation.status === "WITHDRAWN" ? "withdrawn" : ""}`}>{observation.status === "WITHDRAWN" ? "已撤回" : "待分析"}</span><h3>{observation.title}</h3></div><small>修订 {observation.revision}</small></header>
            <p className="management-inbox-content">{observation.content}</p>
            <footer><span>{formatDate(observation.occurred_at)} · 更新于 {formatDate(observation.updated_at)}</span><div><button className="button secondary" onClick={() => setHistoryId(historyId === observation.id ? null : observation.id)}>{historyId === observation.id ? "收起历史" : "查看历史"}</button><button className="button secondary" onClick={() => setAttachmentsObservationId(attachmentsObservationId === observation.id ? null : observation.id)}>{attachmentsObservationId === observation.id ? "收起原文件" : "查看原文件附件"}</button>{observation.status !== "WITHDRAWN" && <><button className="button secondary" onClick={() => beginEdit(observation)}>修订</button><button className="button secondary" onClick={() => { setExtractingObservationId(extractingObservationId === observation.id ? null : observation.id); setConsentedObservationId(null); }}>{extractingObservationId === observation.id ? "收起 AI 整理" : "AI 整理原文"}</button><button className="button secondary danger-button" disabled={withdrawMutation.isPending} onClick={() => { if (window.confirm("撤回后记录仍会保留在历史中，确定继续吗？")) withdrawMutation.mutate(observation); }}>撤回</button></>}</div></footer>
            {attachmentsObservationId === observation.id && <div className="management-attachment-panel"><strong>原始文件附件</strong>{attachmentsQuery.isLoading ? <span>正在读取附件列表…</span> : attachmentsQuery.error ? <span role="alert">读取失败：{(attachmentsQuery.error as Error).message}</span> : attachmentsQuery.data?.items.length ? attachmentsQuery.data.items.map((attachment) => <article key={attachment.id}><div><strong>{attachment.file_name}</strong><span>{attachment.media_type} · {formatBytes(attachment.size_bytes)} · 上传于 {formatDate(attachment.created_at)}</span><small>原文件保存在独立观察库，下载内容为原始字节。</small></div><div><button className="button secondary" disabled={attachmentFileMutation.isPending} onClick={() => openAttachment(observation.id, attachment, "view")}>在新标签打开</button><button className="button primary" disabled={attachmentFileMutation.isPending} onClick={() => openAttachment(observation.id, attachment, "download")}>{attachmentFileMutation.isPending ? "正在读取…" : "下载原文件"}</button></div></article>) : <span>这条记录没有上传附件。</span>}</div>}
            {historyId === observation.id && <div className="management-inbox-history"><strong>变更历史</strong>{historyQuery.isLoading ? <span>正在读取…</span> : historyQuery.error ? <span role="alert">{(historyQuery.error as Error).message}</span> : historyQuery.data?.items.map((version) => <details key={version.id}><summary>修订 {version.revision} · {version.operation} · {formatDate(version.created_at)}</summary><pre>{String(version.snapshot.title ?? "")}\n\n{String(version.snapshot.content ?? "")}</pre></details>)}</div>}
            {extractingObservationId === observation.id && <div className="management-inbox-extraction">
              <div><strong>AI 整理只生成待核对草稿</strong><span>它不会验证陈述真假，也不会写入正式企业模型或潜在库。</span></div>
              {usableProfiles.length > 0 ? <>
                <label>模型配置<select aria-label="管理输入模型" value={selectedProfile?.id ?? ""} onChange={(event) => setSelectedModelProfileId(event.target.value)}>{usableProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>)}</select></label>
                <label className="management-inbox-consent"><input type="checkbox" checked={consentedObservationId === observation.id} onChange={(event) => setConsentedObservationId(event.target.checked ? observation.id : null)} />我同意将这条记录的原文发送给所选模型服务整理</label>
                <button className="button primary" disabled={!selectedProfile || consentedObservationId !== observation.id || extractionMutation.isPending} onClick={() => extractionMutation.mutate(observation)}>{extractionMutation.isPending ? "正在整理…" : "开始整理"}</button>
              </> : <span>尚无已启用的真实模型配置。请先在<Link to="/developer/advanced">建设端模型设置</Link>配置模型；模拟模型不会伪造提取结果。</span>}
              {extractionHistoryQuery.isLoading ? <span>正在读取整理记录…</span> : extractionHistoryQuery.error ? <span role="alert">读取整理记录失败：{(extractionHistoryQuery.error as Error).message}</span> : extractionHistoryQuery.data?.items.map((run) => <details key={run.id}>
                <summary>{run.status === "COMPLETED" ? "整理完成" : "整理未完成"} · 来源修订 {run.source_revision} · {formatDate(run.created_at)}</summary>
                {run.status === "FAILED" ? <p>没有生成可用结果，原始信息仍保留。错误代码：{run.error_code}</p> : <>
                  {run.source_revision !== observation.revision && <p className="management-inbox-stale">本次整理基于旧修订，不能代表当前原文。</p>}
                  {run.items.map((item, index) => <div className="management-inbox-extract-item" key={`${run.id}-${index}`}><strong>{item.kind} · AI 整理陈述</strong><p>{item.statement}</p><blockquote>原文依据：“{item.supporting_quote}”</blockquote><small>说话者：{item.speaker ?? "未识别"} · 时间：{item.time_expression ?? "未识别"}</small></div>)}
                  {run.unresolved.length > 0 && <div><strong>待澄清</strong><ul>{run.unresolved.map((item, index) => <li key={`${run.id}-unresolved-${index}`}>{item}</li>)}</ul></div>}
                  <small>由模型“{run.model_name}”整理；逐字引用经过程序校验，陈述本身仍需人工核对。</small>
                </>}
              </details>)}
            </div>}
          </>}
        </article>)}
      </div> : <div className="empty-copy">当前企业还没有日常管理信息。</div>}
    </section>
  </div>;
}
