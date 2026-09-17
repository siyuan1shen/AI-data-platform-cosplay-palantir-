import { lazy, Suspense, type ComponentProps, type FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { Entity, EntityCreate, OntologyType, Relation, RelationCreate, RelationParticipantInput, Viewpoint } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";
import type { ProjectionElementData } from "../../features/projection/toElements";
import { useWorkspace } from "../../workspace/WorkspaceContext";

const ProjectionGraphModule = lazy(async () => ({ default: (await import("../../features/projection/ProjectionGraph")).ProjectionGraph }));

function ProjectionGraph(props: ComponentProps<typeof ProjectionGraphModule>) {
  return <Suspense fallback={<div className="graph-loading">正在加载关系图……</div>}><ProjectionGraphModule {...props} /></Suspense>;
}

const viewpointOptions: Array<{ value: Viewpoint; label: string }> = [{ value: "DESIGNED", label: "正式设计" }, { value: "REPORTED", label: "访谈陈述" }, { value: "OBSERVED", label: "现场观察" }, { value: "SYSTEM_BOUND", label: "系统数据" }];

export function ProjectionModelPage() {
  const workspace = useWorkspace();
  if (!workspace.selectedProjectId) return <StatusMessage title="请先选择公司" description="在“企业”中选择公司，系统会自动打开它的统一企业投影。" />;
  return <ProjectionModel key={workspace.selectedProjectId} projectId={workspace.selectedProjectId} refreshWorkspace={workspace.refresh} />;
}

function ProjectionModel({ projectId, refreshWorkspace }: { projectId: string; refreshWorkspace: () => Promise<void> }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [editingEntity, setEditingEntity] = useState<Entity | null>(null);
  const [editingRelation, setEditingRelation] = useState<Relation | null>(null);
  const [search, setSearch] = useState("");
  const [selectedElement, setSelectedElement] = useState<ProjectionElementData | null>(null);

  useEffect(() => {
    setEditingEntity(null);
    setEditingRelation(null);
    setSelectedElement(null);
    setSearch("");
  }, [projectId]);

  const typesQuery = useQuery({ queryKey: ["ontology-types", projectId], queryFn: () => api.listOntologyTypes(projectId) });
  const entitiesQuery = useQuery({ queryKey: ["entities", projectId], queryFn: () => api.listEntities(projectId, true) });
  const relationsQuery = useQuery({ queryKey: ["relations", projectId], queryFn: () => api.listRelations(projectId, true) });
  const graphQuery = useQuery({ queryKey: ["executive-context", projectId, true], queryFn: () => api.getExecutiveContext(projectId, true) });
  const objectTypes = useMemo(() => (typesQuery.data?.items ?? []).filter((item) => item.kind === "OBJECT"), [typesQuery.data]);
  const relationTypes = useMemo(() => (typesQuery.data?.items ?? []).filter((item) => item.kind === "RELATION"), [typesQuery.data]);
  const activeEntities = useMemo(() => (entitiesQuery.data?.items ?? []).filter((item) => item.status !== "RETIRED"), [entitiesQuery.data]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["ontology-types", projectId] });
    await queryClient.invalidateQueries({ queryKey: ["entities", projectId] });
    await queryClient.invalidateQueries({ queryKey: ["relations", projectId] });
    await queryClient.invalidateQueries({ queryKey: ["executive-context", projectId] });
    await refreshWorkspace();
  };
  const installMutation = useMutation({ mutationFn: () => api.installDefaultOntology(projectId), onSuccess: async (items) => { setNotice(`默认本体已就绪，共 ${items.length} 个类型。`); await refresh(); }, onError: (value) => setError(value) });
  const releaseMutation = useMutation({ mutationFn: () => api.releaseOntology(projectId, { label: `本体版本 ${new Date().toLocaleDateString("zh-CN")}`, notes: "由开发端确认发布" }), onSuccess: (release) => setNotice(`本体版本 ${release.version} 已发布。`), onError: (value) => setError(value) });
  const entityMutation = useMutation({
    mutationFn: ({ input, current }: { input: EntityCreate; current: Entity | null }) => current ? api.updateEntity(projectId, current.id, { expected_revision: current.revision, name: input.name, properties: input.properties, viewpoint: input.viewpoint, evidence: input.evidence }) : api.createEntity(projectId, input),
    onSuccess: async (entity) => { setEditingEntity(null); setNotice(`企业对象“${entity.name}”已保存。`); await refresh(); }, onError: (value) => setError(value),
  });
  const retireEntityMutation = useMutation({ mutationFn: (entity: Entity) => api.retireEntity(projectId, entity.id, { expected_revision: entity.revision }), onSuccess: async (entity) => { setNotice(`企业对象“${entity.name}”已退役。`); await refresh(); }, onError: (value) => setError(value) });
  const relationMutation = useMutation({
    mutationFn: ({ input, current }: { input: RelationCreate; current: Relation | null }) => current ? api.updateRelation(projectId, current.id, { expected_revision: current.revision, name: input.name, properties: input.properties, viewpoint: input.viewpoint, participants: input.participants, evidence: input.evidence }) : api.createRelation(projectId, input),
    onSuccess: async (relation) => { setEditingRelation(null); setNotice(`企业关系“${relation.name || relation.type_key}”已保存。`); await refresh(); }, onError: (value) => setError(value),
  });
  const retireRelationMutation = useMutation({ mutationFn: (relation: Relation) => api.retireRelation(projectId, relation.id, { expected_revision: relation.revision }), onSuccess: async (relation) => { setNotice(`企业关系“${relation.name || relation.type_key}”已退役。`); await refresh(); }, onError: (value) => setError(value) });

  const saveEntity = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const properties = parseObject(String(form.get("properties") ?? "{}"));
      const input: EntityCreate = { type_key: String(form.get("type_key")), name: String(form.get("name")).trim(), stable_key: String(form.get("stable_key") ?? "").trim() || null, properties, viewpoint: String(form.get("viewpoint")) as Viewpoint, evidence: editingEntity?.evidence ?? [] };
      if (!input.type_key || !input.name) throw new Error("请选择对象类型并填写名称。");
      entityMutation.mutate({ input, current: editingEntity });
    } catch (value) { setError(value instanceof Error ? value : new Error("对象表单无法解析。")); }
  };
  const saveRelation = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const sourceId = String(form.get("source_id"));
      const targetId = String(form.get("target_id"));
      const preservedParticipants: RelationParticipantInput[] = (editingRelation?.participants ?? []).slice(2).map((participant) => ({
        role_key: participant.role_key,
        entity_id: participant.entity_id,
        ordinal: participant.ordinal,
      }));
      const sourceRole = String(form.get("source_role") ?? "");
      const targetRole = String(form.get("target_role") ?? "");
      const participants: RelationParticipantInput[] = [{ role_key: sourceRole, entity_id: sourceId, ordinal: 0 }, { role_key: targetRole, entity_id: targetId, ordinal: 1 }, ...preservedParticipants];
      const input: RelationCreate = { type_key: String(form.get("type_key")), name: String(form.get("name") ?? "").trim() || null, participants, properties: parseObject(String(form.get("properties") ?? "{}")), viewpoint: String(form.get("viewpoint")) as Viewpoint, evidence: editingRelation?.evidence ?? [] };
      if (!input.type_key || !sourceId || !targetId || !sourceRole || !targetRole || sourceId === targetId) throw new Error("请选择关系类型、两个不同的参与对象以及对应的参与角色。");
      relationMutation.mutate({ input, current: editingRelation });
    } catch (value) { setError(value instanceof Error ? value : new Error("关系表单无法解析。")); }
  };

  return <div className="page-stack">
    <section className="page-hero"><div><p className="eyebrow">TRUSTED PROJECTION</p><h1>企业投影</h1><p>先看企业全景，再按需打开高级编辑。所有对象和关系都受本体约束。</p></div><div className="hero-actions"><input type="search" aria-label="搜索草稿投影" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索部门、岗位、流程或系统" /><button className="button secondary" onClick={() => void refresh()}>刷新</button></div></section>
    {notice && <StatusMessage title="操作成功" description={notice} />}
    {error && <StatusMessage tone="danger" title="建模操作失败" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    {graphQuery.error ? <StatusMessage tone="danger" title="草稿图暂不可用" description={(graphQuery.error as Error).message} action={{ label: "重试", onClick: () => void graphQuery.refetch() }} /> : <section className="projection-workspace developer-projection"><div className="graph-panel"><div className="graph-heading"><div><h2>企业关系全景</h2><p>对象、职责、流程及各类流动关系在同一张图中查看。</p></div><span className="status-pill">草稿修订 {graphQuery.data?.graph.revision ?? 0}</span></div>{graphQuery.data ? <ProjectionGraph graph={graphQuery.data.graph} search={search} onSelect={setSelectedElement} /> : <div className="graph-loading">正在加载企业投影……</div>}</div><aside className="detail-panel">{selectedElement ? <><p className="eyebrow">{selectedElement.modelKind === "entity" ? "OBJECT" : "RELATION"}</p><h2>{selectedElement.label}</h2><dl className="detail-list"><div><dt>类型</dt><dd>{selectedElement.typeKey}</dd></div><div><dt>状态</dt><dd>{selectedElement.status}</dd></div><div><dt>视角</dt><dd>{selectedElement.viewpoint}</dd></div>{selectedElement.designMembership && <div><dt>设计层</dt><dd>{selectedElement.designMembership === "MODELED" ? "正式模型" : "待身份对齐"}</dd></div>}</dl><div className="properties"><h3>设计属性</h3><pre>{JSON.stringify(selectedElement.properties, null, 2)}</pre></div>{selectedElement.modelKind === "entity" && Object.keys(selectedElement.comparison ?? {}).length > 0 && <div className="properties"><h3>现实观测与设计差异</h3><pre>{JSON.stringify(selectedElement.comparison, null, 2)}</pre></div>}</> : <div className="detail-empty"><span>◇</span><h2>选择图中对象</h2><p>点击对象或关系查看类型、状态和属性。</p></div>}</aside></section>}
    <details className="advanced-section projection-editor"><summary><span><strong>手动维护对象、关系与本体</strong><small>Agent 提案以外的必要修正入口</small></span><i>展开 / 收起</i></summary><div className="advanced-section-body advanced-stack"><section className="panel ontology-strip"><div><p className="eyebrow">ONTOLOGY</p><h2>本体约束</h2><p>{typesQuery.data?.total ? `当前 ${typesQuery.data.total} 个类型：${objectTypes.length} 个对象类型、${relationTypes.length} 个关系类型。` : "先安装默认本体，才能准确创建对象和关系。"}</p></div><div className="button-row"><button className="button secondary" disabled={installMutation.isPending} onClick={() => installMutation.mutate()}>安装/补齐默认本体</button><button className="button secondary" disabled={!typesQuery.data?.total || releaseMutation.isPending} onClick={() => releaseMutation.mutate()}>发布本体版本</button></div></section>
    <div className="model-grid">
      <section className="panel model-panel"><div className="panel-heading"><div><h2>企业对象</h2><p>部门、岗位、流程、系统、业务对象等可独立维护。</p></div><span>{entitiesQuery.data?.total ?? 0} 个</span></div><div className="model-list">{(entitiesQuery.data?.items ?? []).map((entity) => <article key={entity.id} className={entity.status === "RETIRED" ? "retired" : ""}><div><strong>{entity.name}</strong><small>{entity.type_key} · {entity.viewpoint} · 修订 {entity.revision}{Object.values(entity.comparison ?? {}).some((value) => typeof value === "object" && value !== null && "status" in value && value.status !== "MATCH") ? " · 存在现实差异" : ""}</small></div><div className="row-actions"><button className="button text-button" disabled={entity.status === "RETIRED"} onClick={() => setEditingEntity(entity)}>编辑</button><button className="button text-button danger-text" disabled={entity.status === "RETIRED" || retireEntityMutation.isPending} onClick={() => retireEntityMutation.mutate(entity)}>退役</button></div></article>)}{!entitiesQuery.data?.items.length && <p className="empty-copy">还没有对象。安装本体后创建第一个部门、岗位或流程。</p>}</div><EntityForm types={objectTypes.map((item) => ({ key: item.key, name: item.name }))} editing={editingEntity} pending={entityMutation.isPending} onSubmit={saveEntity} onCancel={() => setEditingEntity(null)} /></section>
      <section className="panel model-panel"><div className="panel-heading"><div><h2>企业关系</h2><p>资金流、信息流、物流、指令流以及组织依赖都由关系表达。</p></div><span>{relationsQuery.data?.total ?? 0} 条</span></div><div className="model-list">{(relationsQuery.data?.items ?? []).map((relation) => <article key={relation.id} className={relation.status === "RETIRED" ? "retired" : ""}><div><strong>{relation.name || relation.type_key}</strong><small>{relation.type_key} · {relation.participants.map((participant) => participant.entity_name).join(" → ")}</small></div><div className="row-actions"><button className="button text-button" disabled={relation.status === "RETIRED"} onClick={() => setEditingRelation(relation)}>编辑</button><button className="button text-button danger-text" disabled={relation.status === "RETIRED" || retireRelationMutation.isPending} onClick={() => retireRelationMutation.mutate(relation)}>退役</button></div></article>)}{!relationsQuery.data?.items.length && <p className="empty-copy">还没有关系。至少创建两个对象后即可建立联系。</p>}</div><RelationForm types={relationTypes} entities={activeEntities} editing={editingRelation} pending={relationMutation.isPending} onSubmit={saveRelation} onCancel={() => setEditingRelation(null)} /></section>
    </div></div></details>
  </div>;
}

function EntityForm({ types, editing, pending, onSubmit, onCancel }: { types: Array<{ key: string; name: string }>; editing: Entity | null; pending: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void; onCancel: () => void }) {
  return <form className="create-form" onSubmit={onSubmit} key={editing?.id ?? "new-entity"}><h3>{editing ? `编辑 ${editing.name}` : "新建企业对象"}</h3><label>对象类型<select name="type_key" required defaultValue={editing?.type_key ?? ""} disabled={Boolean(editing)}><option value="" disabled>请选择</option>{types.map((type) => <option key={type.key} value={type.key}>{type.name}（{type.key}）</option>)}</select></label><label>名称<input name="name" required defaultValue={editing?.name ?? ""} /></label><label>稳定标识（可选）<input name="stable_key" defaultValue={editing?.stable_key ?? ""} disabled={Boolean(editing)} placeholder="例如：department.sales" /></label><label>信息视角<select name="viewpoint" defaultValue={editing?.viewpoint ?? "DESIGNED"}>{viewpointOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><label>属性 JSON<textarea name="properties" rows={5} defaultValue={JSON.stringify(editing?.properties ?? {}, null, 2)} spellCheck={false} /></label><div className="button-row">{editing && <button type="button" className="button text-button" onClick={onCancel}>取消编辑</button>}<button className="button secondary" disabled={!types.length || pending}>{pending ? "正在保存……" : editing ? "保存修改" : "创建对象"}</button></div></form>;
}

function RelationForm({ types, entities, editing, pending, onSubmit, onCancel }: { types: OntologyType[]; entities: Entity[]; editing: Relation | null; pending: boolean; onSubmit: (event: FormEvent<HTMLFormElement>) => void; onCancel: () => void }) {
  const participants = editing?.participants ?? [];
  const [typeKey, setTypeKey] = useState(editing?.type_key ?? "");
  const selectedType = types.find((type) => type.key === typeKey);
  const roleOptions = selectedType?.relation_roles ?? [];
  const [sourceRole, setSourceRole] = useState(participants[0]?.role_key ?? roleOptions[0]?.key ?? "");
  const [targetRole, setTargetRole] = useState(participants[1]?.role_key ?? roleOptions[1]?.key ?? "");

  useEffect(() => {
    const nextRoles = types.find((type) => type.key === typeKey)?.relation_roles ?? [];
    setSourceRole((current) => nextRoles.some((role) => role.key === current) ? current : nextRoles[0]?.key ?? "");
    setTargetRole((current) => nextRoles.some((role) => role.key === current) ? current : nextRoles[1]?.key ?? nextRoles[0]?.key ?? "");
  }, [typeKey, types]);

  return <form className="create-form" onSubmit={onSubmit} key={editing?.id ?? "new-relation"}><h3>{editing ? `编辑 ${editing.name || editing.type_key}` : "新建企业关系"}</h3><label>关系类型<select name="type_key" required value={typeKey} disabled={Boolean(editing)} onChange={(event) => setTypeKey(event.target.value)}><option value="" disabled>请选择</option>{types.map((type) => <option key={type.key} value={type.key}>{type.name}（{type.key}）</option>)}</select></label><label>关系名称（可选）<input name="name" defaultValue={editing?.name ?? ""} placeholder="例如：订单信息传递" /></label><div className="participant-grid"><label>参与对象一<select name="source_id" required defaultValue={participants[0]?.entity_id ?? ""}><option value="" disabled>请选择</option>{entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}</select></label><label>参与角色<select name="source_role" required value={sourceRole} disabled={!roleOptions.length} onChange={(event) => setSourceRole(event.target.value)}><option value="" disabled>请选择角色</option>{roleOptions.map((role) => <option key={role.key} value={role.key}>{role.name}（{role.key}）</option>)}</select>{roleOptions[0] && <small>本体角色：{roleOptions.map((role) => { const allowedTypes = role.allowed_type_keys ?? []; return `${role.name}${allowedTypes.length ? `（${allowedTypes.join("、")}）` : ""}`; }).join("；")}</small>}</label><label>参与对象二<select name="target_id" required defaultValue={participants[1]?.entity_id ?? ""}><option value="" disabled>请选择</option>{entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}</select></label><label>参与角色<select name="target_role" required value={targetRole} disabled={!roleOptions.length} onChange={(event) => setTargetRole(event.target.value)}><option value="" disabled>请选择角色</option>{roleOptions.map((role) => <option key={role.key} value={role.key}>{role.name}（{role.key}）</option>)}</select></label></div><label>信息视角<select name="viewpoint" defaultValue={editing?.viewpoint ?? "DESIGNED"}>{viewpointOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><label>属性 JSON<textarea name="properties" rows={5} defaultValue={JSON.stringify(editing?.properties ?? {}, null, 2)} spellCheck={false} /></label><div className="button-row">{editing && <button type="button" className="button text-button" onClick={onCancel}>取消编辑</button>}<button className="button secondary" disabled={!types.length || entities.length < 2 || !roleOptions.length || pending}>{pending ? "正在保存……" : editing ? "保存修改" : "创建关系"}</button></div></form>;
}

function parseObject(value: string): Record<string, unknown> { const parsed = JSON.parse(value || "{}"); if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("属性必须是 JSON 对象，例如 {}。"); return parsed as Record<string, unknown>; }
