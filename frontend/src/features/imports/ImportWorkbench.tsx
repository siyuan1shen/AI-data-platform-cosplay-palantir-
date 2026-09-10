import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { ImportKind, ImportPreview, ImportResult } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

const importKinds: Array<{ value: ImportKind; label: string }> = [
  { value: "COMPANYCHECK_CSV", label: "CompanyCheck 答案 CSV" },
  { value: "CSV", label: "通用 CSV" },
  { value: "XLSX", label: "Excel 工作簿" },
  { value: "DOCX", label: "Word 调研报告" },
  { value: "PDF", label: "PDF 材料" },
  { value: "TXT", label: "纯文本" },
  { value: "JSON", label: "JSON 数据" },
];

export function ImportWorkbench({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<ImportKind>("COMPANYCHECK_CSV");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mappingText, setMappingText] = useState("{}");
  const [targetCompany, setTargetCompany] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [expandedDocumentId, setExpandedDocumentId] = useState("");

  const documentsQuery = useQuery({ queryKey: ["documents", projectId], queryFn: () => api.listDocuments(projectId) });
  const fragmentsQuery = useQuery({ queryKey: ["fragments", projectId, expandedDocumentId], queryFn: () => api.listFragments(projectId, expandedDocumentId), enabled: Boolean(expandedDocumentId) });
  const previewMutation = useMutation({
    mutationFn: ({ selectedFile, selectedKind }: { selectedFile: File; selectedKind: ImportKind }) => api.previewImport(projectId, selectedFile, selectedKind),
    onSuccess: (value) => { setPreview(value); setMappingText(JSON.stringify(value.suggested_mapping, null, 2)); setResult(null); setError(null); },
    onError: (value) => setError(value),
  });
  const confirmMutation = useMutation({
    mutationFn: ({ value, mapping, target }: { value: ImportPreview; mapping: Record<string, string>; target: string }) => api.confirmImport(projectId, { preview_id: value.id, mapping, options: target ? { target_company: target } : {} }),
    onSuccess: async (value) => { setResult(value); setPreview(null); setFile(null); setTargetCompany(""); await queryClient.invalidateQueries({ queryKey: ["documents", projectId] }); },
    onError: (value) => setError(value),
  });

  const startPreview = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file) { setError(new Error("请先选择一个文件。")); return; }
    previewMutation.mutate({ selectedFile: file, selectedKind: kind });
  };
  const confirm = () => {
    if (!preview) return;
    try {
      const mapping = JSON.parse(mappingText) as unknown;
      if (!mapping || typeof mapping !== "object" || Array.isArray(mapping) || Object.values(mapping).some((value) => typeof value !== "string")) throw new Error("字段映射必须是“原字段名 → 目标字段名”的字符串 JSON 对象。");
      confirmMutation.mutate({ value: preview, mapping: mapping as Record<string, string>, target: targetCompany.trim() });
    } catch (value) { setError(value instanceof Error ? value : new Error("字段映射无法解析。")); }
  };

  return <section className="panel import-workbench">
    <div className="panel-heading"><div><p className="eyebrow">SOURCE MATERIAL</p><h2>材料导入</h2><p>先预览、检查字段和警告，再确认写入项目证据库。</p></div></div>
    {error && <StatusMessage tone="danger" title="材料处理失败" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    {result && <><StatusMessage title="导入完成" description={`状态 ${result.status}；建立 ${result.documents_created} 份文档、${result.fragments_created} 个证据片段、${result.claims_created} 条声明；跳过 ${result.rows_skipped} 行。`} />{result.warnings.length > 0 && <ul className="warning-list">{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}</>}
    <form className="import-form" onSubmit={startPreview}>
      <label>材料类型<select value={kind} onChange={(event) => { setKind(event.target.value as ImportKind); setPreview(null); setResult(null); setMappingText("{}"); setTargetCompany(""); }}>{importKinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      <label className="file-picker">选择文件<input type="file" accept=".csv,.xlsx,.docx,.pdf,.txt,.json" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPreview(null); setResult(null); setError(null); setMappingText("{}"); setTargetCompany(""); }} /><span>{file?.name || "选择本地问卷、访谈或报告"}</span></label>
      <button className="button primary" disabled={!file || previewMutation.isPending}>{previewMutation.isPending ? "正在解析……" : "预览导入"}</button>
    </form>
    {preview && <div className="import-preview">
      <div className="subsection-heading"><div><h3>导入预览</h3><small>{preview.file_name} · {preview.detected_encoding || "自动识别编码"}</small></div><span>有效期至 {formatDate(preview.expires_at)}</span></div>
      {preview.warnings.length > 0 && <ul className="warning-list">{preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
      {preview.columns.length > 0 && <div className="table-scroll"><table className="data-table"><thead><tr>{preview.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{preview.sample_rows.slice(0, 8).map((row, index) => <tr key={index}>{preview.columns.map((column) => <td key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>}
      {!preview.columns.length && <div className="document-preview-copy"><strong>内容片段预览</strong>{preview.sample_rows.slice(0, 6).map((row, index) => <article key={index}><code>{String(row.locator ?? `fragment:${index + 1}`)}</code><p>{String(row.text ?? "")}</p></article>)}</div>}
      {preview.columns.length > 0 && preview.kind === "COMPANYCHECK_CSV" && <label className="json-editor">CompanyCheck 字段映射<textarea aria-label="字段映射" value={mappingText} onChange={(event) => setMappingText(event.target.value)} rows={6} spellCheck={false} /><small>仅用于识别公司、问卷、问题和回答字段；通用 CSV 会原样保存为证据，系统本体字段请在“系统语义 Agent”中建立并批准映射。</small></label>}
      {preview.kind === "COMPANYCHECK_CSV" && <label>目标企业标识（多公司文件必填）<input value={targetCompany} onChange={(event) => setTargetCompany(event.target.value)} placeholder="例如：麦数科技" /><small>预览若包含多个企业，确认时只会导入这里填写的企业；单企业文件可留空。</small></label>}
      <div className="button-row"><button className="button text-button" onClick={() => setPreview(null)}>取消</button><button className="button primary" disabled={confirmMutation.isPending} onClick={confirm}>{confirmMutation.isPending ? "正在导入……" : "确认导入"}</button></div>
    </div>}
    <div className="document-library">
      <div className="subsection-heading"><h3>已导入材料</h3><span>{documentsQuery.data?.total ?? 0} 份</span></div>
      {documentsQuery.isLoading ? <p className="muted">正在读取材料……</p> : <div className="document-list">{(documentsQuery.data?.items ?? []).map((document) => <button key={document.id} className={expandedDocumentId === document.id ? "selected" : ""} onClick={() => setExpandedDocumentId(expandedDocumentId === document.id ? "" : document.id)}><span><strong>{document.file_name}</strong><small>{document.kind} · {document.status}</small></span><span>查看片段</span></button>)}{!documentsQuery.data?.items.length && <p className="empty-copy">还没有材料。CompanyCheck 导出的 CSV、访谈和报告都从这里进入。</p>}</div>}
      {expandedDocumentId && <div className="fragment-list">{fragmentsQuery.isLoading ? <p className="muted">正在读取片段……</p> : (fragmentsQuery.data?.items ?? []).map((fragment) => <article key={fragment.id}><code>{fragment.locator}</code><p>{fragment.text}</p></article>)}</div>}
    </div>
  </section>;
}

function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
