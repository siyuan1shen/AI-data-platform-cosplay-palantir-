import { type FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { ExportJob, ExportRequest } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

const formats = ["json", "csv", "xlsx", "bundle"] as const;

export function ExportCenter({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const exportsQuery = useQuery({
    queryKey: ["exports", projectId],
    queryFn: () => api.listExports(projectId),
    refetchInterval: (query) => query.state.data?.items.some((item) => !["COMPLETED", "FAILED"].includes(item.status)) ? 2000 : false,
  });
  const selected = exportsQuery.data?.items.find((item) => item.id === selectedId) ?? exportsQuery.data?.items[0];
  const detailQuery = useQuery({ queryKey: ["export", projectId, selected?.id], queryFn: () => api.getExport(projectId, selected!.id), enabled: Boolean(selected?.id) });

  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(selected.id);
  }, [selected?.id, selectedId]);

  const createMutation = useMutation({
    mutationFn: (input: ExportRequest) => api.createExport(projectId, input),
    onSuccess: async (job) => { setSelectedId(job.id); setNotice(`${job.format.toUpperCase()} 导出任务已创建。`); await queryClient.invalidateQueries({ queryKey: ["exports", projectId] }); },
    onError: (value) => setError(value),
  });
  const downloadMutation = useMutation({
    mutationFn: (job: ExportJob) => api.downloadExport(projectId, job.id),
    onSuccess: ({ blob, fileName }) => {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = fileName;
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice(`已下载 ${fileName}。`);
    },
    onError: (value) => setError(value),
  });
  const create = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    createMutation.mutate({
      format: String(form.get("format")),
      include_evidence: form.get("include_evidence") === "on",
      include_lineage: form.get("include_lineage") === "on",
      filters: {},
      release_id: null,
    });
  };

  return <section className="panel nested-panel">
    <div className="panel-heading"><div><h3>项目导出</h3><p>完整支持任务列表、创建、详情和文件下载。</p></div><span>{exportsQuery.data?.total ?? 0} 个任务</span></div>
    {notice && <div className="inline-notice">{notice}</div>}
    {error && <StatusMessage tone="danger" title="导出没有完成" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    <div className="export-layout">
      <div className="export-list">{(exportsQuery.data?.items ?? []).map((job) => <button key={job.id} className={selected?.id === job.id ? "selected" : ""} onClick={() => setSelectedId(job.id)}><span><strong>{job.format.toUpperCase()}</strong><small>{formatDate(job.created_at)}</small></span><span className="status-pill">{job.status}</span></button>)}{!exportsQuery.data?.items.length && <p className="empty-copy">尚无导出任务。</p>}</div>
      <div className="export-detail">{selected && detailQuery.data ? <><div className="subsection-heading"><div><h4>{detailQuery.data.format.toUpperCase()} 导出</h4><small>{detailQuery.data.id}</small></div><span className="status-pill">{detailQuery.data.status}</span></div>{detailQuery.data.error && <p className="danger-text">{detailQuery.data.error}</p>}<button className="button primary" disabled={detailQuery.data.status !== "COMPLETED" || downloadMutation.isPending} onClick={() => downloadMutation.mutate(detailQuery.data)}>{downloadMutation.isPending ? "下载中……" : "下载文件"}</button></> : <p className="empty-copy">选择导出任务查看详情。</p>}</div>
    </div>
    <form className="create-form export-form" onSubmit={create}><h4>新建导出</h4><label>格式<select name="format" defaultValue="json">{formats.map((format) => <option key={format} value={format}>{format.toUpperCase()}</option>)}</select></label><label className="checkbox-label compact-check"><input name="include_evidence" type="checkbox" defaultChecked />包含证据</label><label className="checkbox-label compact-check"><input name="include_lineage" type="checkbox" defaultChecked />包含血缘</label><button className="button secondary" disabled={createMutation.isPending}>{createMutation.isPending ? "正在创建……" : "创建导出任务"}</button></form>
  </section>;
}

function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
