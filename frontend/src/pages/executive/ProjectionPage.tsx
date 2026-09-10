import { lazy, Suspense, type ComponentProps, useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { api } from "../../api";
import { StatusMessage } from "../../components/StatusMessage";
import type { ProjectionElementData } from "../../features/projection/toElements";
import { useWorkspace } from "../../workspace/WorkspaceContext";

const ProjectionGraphModule = lazy(async () => ({ default: (await import("../../features/projection/ProjectionGraph")).ProjectionGraph }));

function ProjectionGraph(props: ComponentProps<typeof ProjectionGraphModule>) {
  return <Suspense fallback={<div className="projection-canvas-wrap"><div className="graph-loading">正在加载关系图……</div></div>}><ProjectionGraphModule {...props} /></Suspense>;
}

export function ProjectionPage() {
  const workspace = useWorkspace();
  const location = useLocation();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<ProjectionElementData | null>(null);
  const preview = new URLSearchParams(location.search).get("preview") === "true";
  const projectId = workspace.selectedProjectId;

  useEffect(() => {
    setSearch("");
    setSelected(null);
  }, [projectId]);

  const contextQuery = useQuery({
    queryKey: ["executive-context", projectId, preview],
    queryFn: () => api.getExecutiveContext(projectId, preview),
    enabled: Boolean(projectId),
  });
  const data = contextQuery.data;
  const selectElement = useCallback((value: ProjectionElementData | null) => setSelected(value), []);
  const typeCount = useMemo(() => new Set(data?.graph.entities.map((entity) => entity.type_key) ?? []).size, [data]);

  if (!projectId) {
    return <StatusMessage title="请先建立企业投影项目" description="切换到建设端，新建公司和项目后即可预览企业投影。" />;
  }
  if (contextQuery.isLoading) {
    return <StatusMessage title="正在加载企业投影" description="正在读取对象、关系和发布状态。" />;
  }
  if (contextQuery.error || !data) {
    return <StatusMessage tone="danger" title={preview ? "草稿预览暂不可用" : "还没有可查看的正式版本"} description={(contextQuery.error as Error)?.message ?? "没有取得企业投影上下文。"} action={{ label: "重新加载", onClick: () => void contextQuery.refetch() }} />;
  }
  if (!preview && !data.publication) {
    return <StatusMessage title="还没有可查看的正式版本" description="建设端需要先完成企业投影并正式发布；当前使用端不会展示未发布草稿。" />;
  }

  return (
    <div className="page-stack">
      <section className="page-hero projection-hero">
        <div>
          <p className="eyebrow">{preview ? "DRAFT PROJECTION" : "ENTERPRISE PROJECTION"}</p>
          <h1>{data.company.name}</h1>
          <p>{data.project.name} · 修订 {data.graph.revision}{data.publication ? ` · 发布版本 ${data.publication.version}` : " · 当前草稿"}</p>
        </div>
        <div className="hero-actions">
          <input aria-label="搜索投影对象" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索部门、岗位、流程或对象" />
          <button className="button secondary" onClick={() => void contextQuery.refetch()}>刷新</button>
        </div>
      </section>

      <section className="summary-grid compact">
        <article><span>企业对象</span><strong>{data.graph.entities.length}</strong><small>{typeCount} 种语义类型</small></article>
        <article><span>直接关系</span><strong>{data.graph.relations.length}</strong><small>多参与者关系完整呈现</small></article>
        <article><span>待讨论假设</span><strong>{data.open_hypotheses}</strong><small>不属于正式事实</small></article>
        <article><span>活动情景</span><strong>{data.active_scenarios}</strong><small>不会改写正式投影</small></article>
      </section>

      <section className="projection-workspace">
        <div className="graph-panel">
          <div className="graph-heading">
            <div><h2>企业关系全景</h2><p>关系以独立菱形节点呈现，可保留多方参与者及其角色。</p></div>
            <div className="legend"><span><i className="entity-dot" />企业对象</span><span><i className="relation-dot" />关系</span></div>
          </div>
          <ProjectionGraph graph={data.graph} search={search} onSelect={selectElement} />
        </div>

        <aside className="detail-panel">
          {selected ? (
            <>
              <p className="eyebrow">{selected.modelKind === "entity" ? "OBJECT" : "RELATION"}</p>
              <h2>{selected.label}</h2>
              <dl>
                <div><dt>类型</dt><dd>{selected.typeKey}</dd></div>
                <div><dt>视角</dt><dd>{selected.viewpoint}</dd></div>
                <div><dt>状态</dt><dd>{selected.status}</dd></div>
                <div><dt>证据</dt><dd>{selected.evidenceCount} 条</dd></div>
              </dl>
              <h3>属性</h3>
              {Object.keys(selected.properties).length ? (
                <dl className="property-list">
                  {Object.entries(selected.properties).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value ?? "—")}</dd></div>)}
                </dl>
              ) : <p className="muted">没有附加属性。</p>}
            </>
          ) : (
            <div className="detail-empty"><span>◇</span><h2>选择对象或关系</h2><p>点击图中元素，在这里查看类型、状态、属性和证据数量。</p></div>
          )}
        </aside>
      </section>
    </div>
  );
}
