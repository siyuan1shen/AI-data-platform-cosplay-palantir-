import { type FormEvent, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { RestorePreview, RestoreResult } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

export function RestoreCenter({ onRestored }: { onRestored: () => Promise<void> }) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<RestorePreview | null>(null);
  const [result, setResult] = useState<RestoreResult | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const previewMutation = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("请先选择项目恢复 ZIP。");
      return api.previewRestore(file);
    },
    onSuccess: (value) => { setPreview(value); setResult(null); setError(null); },
    onError: (value) => setError(value),
  });
  const confirmMutation = useMutation({
    mutationFn: () => {
      if (!preview) throw new Error("请先完成恢复预览。");
      return api.confirmRestore({ preview_id: preview.id });
    },
    onSuccess: async (value) => {
      setResult(value);
      setPreview(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["companies"] }),
        queryClient.invalidateQueries({ queryKey: ["projects"] }),
        onRestored(),
      ]);
    },
    onError: (value) => setError(value),
  });
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    previewMutation.mutate();
  };
  const summary = preview?.summary;
  const canConfirm = Boolean(summary?.can_confirm);
  return <details className="advanced-section">
    <summary><span><strong>恢复项目包</strong><small>在空白实例预览并恢复完整项目；失败不会留下半个项目</small></span><i>展开 / 收起</i></summary>
    <div className="advanced-section-body advanced-stack">
      {error && <StatusMessage tone="danger" title="恢复没有完成" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
      {result && <StatusMessage title="项目恢复完成" description={`已恢复项目 ${result.project_id}；${result.source_connections_reset} 个外部连接需要重新配置。`} />}
      <section className="panel nested-panel">
        <div className="panel-heading"><div><h3>选择恢复包</h3><p>使用“项目导出”生成的 BUNDLE ZIP。模型密钥和外部系统密码不会进入包中。</p></div></div>
        <form className="create-form" onSubmit={submit}><label className="file-picker compact-file">恢复 ZIP<input type="file" accept=".zip" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPreview(null); }} /><span>{file?.name || "选择文件"}</span></label><button className="button secondary" disabled={!file || previewMutation.isPending}>{previewMutation.isPending ? "校验中……" : "预览恢复内容"}</button></form>
        {summary && <div className="import-preview embedded-preview"><div className="subsection-heading"><div><h4>{String((summary.project as { name?: string })?.name ?? "未知项目")}</h4><small>{String((summary.company as { name?: string })?.name ?? "未知企业")}</small></div><span>{String(summary.total_records ?? 0)} 条记录</span></div><p>{canConfirm ? "完整性与标识检查通过，可以原子恢复。" : `发现 ${Array.isArray(summary.conflicts) ? summary.conflicts.length : 0} 个标识冲突，不能写入。`}</p><details><summary>查看表级记录数和限制</summary><pre>{JSON.stringify({ table_counts: summary.table_counts, excluded: summary.excluded, warnings: summary.warnings, conflicts: summary.conflicts }, null, 2)}</pre></details><div className="button-row"><button className="button text-button" onClick={() => setPreview(null)}>取消</button><button className="button primary" disabled={!canConfirm || confirmMutation.isPending} onClick={() => confirmMutation.mutate()}>{confirmMutation.isPending ? "恢复中……" : "确认恢复"}</button></div></div>}
      </section>
    </div>
  </details>;
}
