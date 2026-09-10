import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { LearningCase, LearningCaseCreate, LearningCaseUpdate } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

export function LearningCasesPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<LearningCase | null>(null);
  const [search, setSearch] = useState("");
  const [industry, setIndustry] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const casesQuery = useQuery({ queryKey: ["learning-cases", projectId], queryFn: () => api.listLearningCases(projectId) });
  const catalogQuery = useQuery({ queryKey: ["learning-case-catalog", industry, search], queryFn: () => api.listLearningCaseCatalog({ industry: industry.trim() || undefined, search: search.trim() || undefined }) });

  const refresh = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["learning-cases", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["learning-case-catalog"] }),
  ]);
  const saveMutation = useMutation({
    mutationFn: ({ input, current }: { input: LearningCaseCreate; current: LearningCase | null }) => current
      ? api.updateLearningCase(projectId, current.id, toUpdate(input, current.revision))
      : api.createLearningCase(projectId, input),
    onSuccess: async (item) => { setEditing(null); setNotice(`案例“${item.title}”已保存。`); await refresh(); },
    onError: (value) => setError(value),
  });
  const retireMutation = useMutation({
    mutationFn: (item: LearningCase) => api.updateLearningCase(projectId, item.id, { expected_revision: item.revision, status: "RETIRED" }),
    onSuccess: async (item) => { if (editing?.id === item.id) setEditing(null); setNotice(`案例“${item.title}”已归档。`); await refresh(); },
    onError: (value) => setError(value),
  });

  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const reusable = form.get("reusable") === "on";
    const reusableSummary = String(form.get("reusable_summary") ?? "").trim() || null;
    if (reusable && !reusableSummary) { setError(new Error("允许跨公司参考时，请填写不含企业身份和敏感细节的匿名摘要。")); return; }
    saveMutation.mutate({
      current: editing,
      input: {
        title: String(form.get("title") ?? "").trim(),
        challenge: String(form.get("challenge") ?? "").trim(),
        context: String(form.get("context") ?? "").trim() || null,
        intervention: String(form.get("intervention") ?? "").trim() || null,
        outcome: String(form.get("outcome") ?? "").trim() || null,
        industry: String(form.get("industry") ?? "").trim() || null,
        organization_scale: String(form.get("organization_scale") ?? "").trim() || null,
        lessons: split(String(form.get("lessons") ?? "")),
        tags: split(String(form.get("tags") ?? "")),
        evidence: editing?.evidence ?? [],
        status: String(form.get("status")) as LearningCaseCreate["status"],
        reusable,
        reusable_summary: reusableSummary,
      },
    });
  };

  return <section className="panel nested-panel">
    <div className="panel-heading"><div><h3>学习案例</h3><p>本项目保留完整案例；跨公司目录只读取匿名摘要，供 Agent 参考。</p></div><span>{casesQuery.data?.total ?? 0} 个本地案例</span></div>
    {notice && <div className="inline-notice">{notice}</div>}
    {error && <StatusMessage tone="danger" title="案例操作没有完成" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    <div className="case-layout">
      <div>
        <div className="subsection-heading"><h4>本项目案例</h4><span>可新增、编辑和归档</span></div>
        <div className="case-list">{(casesQuery.data?.items ?? []).map((item) => <article key={item.id} className={item.status === "RETIRED" ? "retired" : ""}><div className="subsection-heading"><div><strong>{item.title}</strong><small>{item.industry || "未标行业"} · 修订 {item.revision}</small></div><span className="status-pill">{item.status}</span></div>{item.origin_kind !== "MANUAL" && <div className="inline-notice"><strong>{originLabel(item)}</strong><br /><small>{originSource(item)}</small></div>}<p>{item.challenge}</p><div className="tag-line">{item.tags?.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div><div className="row-actions"><button className="button text-button" disabled={item.status === "RETIRED"} onClick={() => setEditing(item)}>编辑</button><button className="button text-button danger-text" disabled={item.status === "RETIRED" || retireMutation.isPending} onClick={() => retireMutation.mutate(item)}>归档</button></div></article>)}{!casesQuery.data?.items.length && <p className="empty-copy">Agent 或管理者确认的实践经验可沉淀为案例。</p>}</div>
      </div>
      <div>
        <div className="subsection-heading"><h4>跨公司匿名目录</h4><span>{catalogQuery.data?.total ?? 0} 个</span></div>
        <div className="catalog-filters"><input value={industry} onChange={(event) => setIndustry(event.target.value)} placeholder="行业筛选" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索匿名摘要" /></div>
        <div className="case-list catalog-list">{(catalogQuery.data?.items ?? []).map((item) => <article key={item.id}><small>{[item.industry, item.organization_scale].filter(Boolean).join(" · ") || "匿名案例"}</small><p>{item.reusable_summary}</p><div className="tag-line">{item.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div></article>)}{!catalogQuery.data?.items.length && <p className="empty-copy">没有匹配的可复用匿名案例。</p>}</div>
      </div>
    </div>
    <form className="create-form case-form" onSubmit={save} key={editing?.id ?? "new-case"}><h4>{editing ? `编辑：${editing.title}` : "新增项目案例"}</h4><div className="form-grid-two"><label>标题<input name="title" required defaultValue={editing?.title ?? ""} /></label><label>状态<select name="status" defaultValue={editing?.status ?? "DRAFT"}><option value="DRAFT">草稿</option><option value="CONFIRMED">已确认</option><option value="RETIRED">已归档</option></select></label></div><label>问题/挑战<textarea name="challenge" required rows={3} defaultValue={editing?.challenge ?? ""} /></label><label>背景<textarea name="context" rows={2} defaultValue={editing?.context ?? ""} /></label><div className="form-grid-two"><label>采取的干预<textarea name="intervention" rows={3} defaultValue={editing?.intervention ?? ""} /></label><label>结果<textarea name="outcome" rows={3} defaultValue={editing?.outcome ?? ""} /></label></div><div className="form-grid-two"><label>行业<input name="industry" defaultValue={editing?.industry ?? ""} /></label><label>组织规模<input name="organization_scale" defaultValue={editing?.organization_scale ?? ""} /></label></div><div className="form-grid-two"><label>经验（每行一项）<textarea name="lessons" rows={3} defaultValue={editing?.lessons?.join("\n") ?? ""} /></label><label>标签（每行一项）<textarea name="tags" rows={3} defaultValue={editing?.tags?.join("\n") ?? ""} /></label></div><label className="checkbox-label compact-check"><input name="reusable" type="checkbox" defaultChecked={editing?.reusable ?? false} />允许跨公司匿名参考</label><label>匿名摘要<textarea name="reusable_summary" rows={3} defaultValue={editing?.reusable_summary ?? ""} placeholder="只写可复用规律，不写公司名称和内部标识" /></label><div className="button-row">{editing && <button type="button" className="button text-button" onClick={() => setEditing(null)}>取消编辑</button>}<button className="button secondary" disabled={saveMutation.isPending}>{saveMutation.isPending ? "保存中……" : "保存案例"}</button></div></form>
  </section>;
}

function split(value: string) { return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean); }
function toUpdate(input: LearningCaseCreate, expectedRevision: number): LearningCaseUpdate { return { ...input, expected_revision: expectedRevision }; }
function shortId(value: string | null | undefined) { return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : "未知"; }
function originLabel(item: LearningCase) { return item.origin_kind === "ACTION_RESULT" ? "来自已观测的行动结果" : "来自尚待验证的方案"; }
function originSource(item: LearningCase) {
  if (item.origin_kind === "ACTION_RESULT") return `行动 ${shortId(item.source_action_invocation_id)} · ${item.source_action_observation_ids.length} 条结果观测`;
  return `方案 ${shortId(item.source_scenario_id)} · 尚无现实结果，不能作为已验证经验`;
}
