import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type {
  ImportKind,
  ImportPreview,
  ImportResult,
  OntologyType,
  ObservationConflict,
  ObservationConflictResolve,
  SemanticDataset,
  SemanticDatasetCreate,
  SemanticDatasetResult,
  SemanticMappingCreate,
  SemanticMapping,
  SemanticMappingSuggestion,
  SemanticMappingSuggestionPage,
  SemanticRelationMapping,
  SemanticRelationMappingCreate,
  SourceConnectorExtract,
  SourceConnectorExtractRequest,
  SourceAsset,
  SourceIdentity,
  SourceSystemCreate,
  SourceSystemKind,
  SourceSystem,
  SourceSystemUpdate,
} from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

const sourceKinds: SourceSystemKind[] = ["FILE", "SQLITE", "POSTGRESQL", "REST"];
const fileKinds: ImportKind[] = ["CSV", "XLSX", "JSON"];

export function SourceDataPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileKind, setFileKind] = useState<ImportKind>("CSV");
  const [sourceAsset, setSourceAsset] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mappingText, setMappingText] = useState("{}");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [selectedConnectorId, setSelectedConnectorId] = useState("");
  const [connectorRequest, setConnectorRequest] = useState<SourceConnectorExtractRequest | null>(null);
  const [connectorPreview, setConnectorPreview] = useState<SourceConnectorExtract | null>(null);
  const [nextConnectorRequest, setNextConnectorRequest] = useState<SourceConnectorExtractRequest | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [datasetResult, setDatasetResult] = useState<SemanticDatasetResult | null>(null);
  const [mappingSuggestions, setMappingSuggestions] = useState<SemanticMappingSuggestionPage | null>(null);
  const [suggestionSourceId, setSuggestionSourceId] = useState("");
  const [suggestionAsset, setSuggestionAsset] = useState("");

  const systemsQuery = useQuery({ queryKey: ["source-systems", projectId], queryFn: () => api.listSourceSystems(projectId) });
  const mappingsQuery = useQuery({ queryKey: ["semantic-mappings", projectId], queryFn: () => api.listSemanticMappings(projectId) });
  const relationMappingsQuery = useQuery({ queryKey: ["semantic-relation-mappings", projectId], queryFn: () => api.listSemanticRelationMappings(projectId) });
  const typesQuery = useQuery({ queryKey: ["ontology-types", projectId], queryFn: () => api.listOntologyTypes(projectId) });
  const identitiesQuery = useQuery({ queryKey: ["source-identities", projectId], queryFn: () => api.listSourceIdentities(projectId) });
  const entitiesQuery = useQuery({ queryKey: ["entities", projectId], queryFn: () => api.listEntities(projectId) });
  const fileSources = useMemo(() => (systemsQuery.data?.items ?? []).filter((system) => system.kind === "FILE"), [systemsQuery.data]);
  const connectorSources = useMemo(() => (systemsQuery.data?.items ?? []).filter((system) => system.kind !== "FILE"), [systemsQuery.data]);
  const assetsQuery = useQuery({ queryKey: ["source-assets", projectId, selectedConnectorId], queryFn: () => api.listSourceAssets(projectId, selectedConnectorId), enabled: Boolean(selectedConnectorId) });
  const suggestionAssetsQuery = useQuery({ queryKey: ["mapping-suggestion-assets", projectId, suggestionSourceId], queryFn: () => api.listSourceAssets(projectId, suggestionSourceId), enabled: Boolean(suggestionSourceId) });
  const batchesQuery = useQuery({ queryKey: ["raw-batches", projectId], queryFn: () => api.listRawBatches(projectId) });
  const recordsQuery = useQuery({ queryKey: ["raw-records", projectId, selectedBatchId], queryFn: () => api.listRawRecords(projectId, selectedBatchId), enabled: Boolean(selectedBatchId) });
  const runsQuery = useQuery({ queryKey: ["materialization-runs", projectId], queryFn: () => api.listMaterializationRuns(projectId) });
  const conflictsQuery = useQuery({ queryKey: ["observation-conflicts", projectId], queryFn: () => api.listObservationConflicts(projectId) });
  const datasetsQuery = useQuery({ queryKey: ["semantic-datasets", projectId], queryFn: () => api.listSemanticDatasets(projectId) });

  useEffect(() => {
    if (!fileSources.length) setSelectedSourceId("");
    else if (!fileSources.some((source) => source.id === selectedSourceId)) setSelectedSourceId(fileSources[0].id);
  }, [fileSources, selectedSourceId]);
  useEffect(() => {
    if (!connectorSources.length) setSelectedConnectorId("");
    else if (!connectorSources.some((source) => source.id === selectedConnectorId)) setSelectedConnectorId(connectorSources[0].id);
  }, [connectorSources, selectedConnectorId]);
  useEffect(() => {
    const systems = systemsQuery.data?.items ?? [];
    if (!systems.length) {
      setSuggestionSourceId("");
      return;
    }
    if (!systems.some((source) => source.id === suggestionSourceId)) setSuggestionSourceId(systems[0].id);
  }, [systemsQuery.data, suggestionSourceId]);
  useEffect(() => {
    if (!suggestionAsset) return;
    if (!(suggestionAssetsQuery.data?.items ?? []).some((asset) => asset.asset_key === suggestionAsset)) setSuggestionAsset("");
  }, [suggestionAsset, suggestionAssetsQuery.data]);

  const refresh = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["source-systems", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["semantic-mappings", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["semantic-relation-mappings", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["source-identities", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["source-assets", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["raw-batches", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["materialization-runs", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["observation-conflicts", projectId] }),
    queryClient.invalidateQueries({ queryKey: ["semantic-datasets", projectId] }),
  ]);
  const systemMutation = useMutation({
    mutationFn: (input: SourceSystemCreate) => api.createSourceSystem(projectId, input),
    onSuccess: async (system) => { setNotice(`数据源“${system.name}”已登记。`); await refresh(); },
    onError: (value) => setError(value),
  });
  const systemUpdateMutation = useMutation({
    mutationFn: ({ system, input }: { system: SourceSystem; input: SourceSystemUpdate }) => api.updateSourceSystem(projectId, system.id, input),
    onSuccess: async (system) => { setNotice(`数据源“${system.name}”的连接配置已更新，请执行连接测试。`); await refresh(); },
    onError: (value) => setError(value),
  });
  const testMutation = useMutation({
    mutationFn: (sourceId: string) => api.testSourceSystem(projectId, sourceId),
    onSuccess: async (test) => { setNotice(`${test.ok ? "连接成功" : "连接失败"}：${test.message}`); await refresh(); },
    onError: (value) => setError(value),
  });
  const previewMutation = useMutation({
    mutationFn: () => {
      if (!selectedSourceId || !file) throw new Error("请先选择文件数据源和本地文件。");
      return api.previewSourceSystemImport(projectId, selectedSourceId, file, fileKind);
    },
    onSuccess: (value) => { setPreview(value); setSourceAsset((current) => current || fileStem(value.file_name)); setMappingText(JSON.stringify(value.suggested_mapping, null, 2)); setResult(null); setError(null); },
    onError: (value) => setError(value),
  });
  const confirmMutation = useMutation({
    mutationFn: () => {
      if (!preview) throw new Error("请先完成预览。");
      const asset = sourceAsset.trim();
      return api.confirmImport(projectId, { preview_id: preview.id, mapping: parseStringMap(mappingText), options: asset ? { source_asset: asset } : {} });
    },
    onSuccess: async (value) => {
      setResult(value);
      setPreview(null);
      setFile(null);
      setSourceAsset("");
      setNotice("本次同步已完成：系统数据已写入现实观测层，正式企业设计没有被自动改写。");
      await Promise.all([
        refresh(),
        queryClient.invalidateQueries({ queryKey: ["entities", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["source-identities", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["observation-assertions", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["executive-context", projectId] }),
      ]);
    },
    onError: (value) => setError(value),
  });
  const mappingMutation = useMutation({
    mutationFn: (input: SemanticMappingCreate) => api.createSemanticMapping(projectId, input),
    onSuccess: async () => { setNotice("语义映射草稿已保存。请先校验并批准，系统导入才会使用它。"); await refresh(); },
    onError: (value) => setError(value),
  });
  const mappingCommandMutation = useMutation({
    mutationFn: ({ mapping, command }: { mapping: SemanticMapping; command: "validate" | "approve" | "disable" }) => command === "validate" ? api.validateSemanticMapping(projectId, mapping.id, { expected_revision: mapping.revision }) : command === "approve" ? api.approveSemanticMapping(projectId, mapping.id, { expected_revision: mapping.revision }) : api.disableSemanticMapping(projectId, mapping.id, { expected_revision: mapping.revision }),
    onSuccess: async (mapping) => { setNotice(`语义映射状态已更新为 ${mapping.status}。`); await refresh(); },
    onError: (value) => setError(value),
  });
  const mappingSuggestionMutation = useMutation({
    mutationFn: (input: { target_type_key: string; source_system_id?: string; source_asset?: string; include_existing?: boolean }) => api.suggestSemanticMappings(projectId, input),
    onSuccess: (value) => { setMappingSuggestions(value); setNotice(`已生成 ${value.total} 条只读映射候选；没有写入或批准任何映射。`); setError(null); },
    onError: (value) => setError(value),
  });
  const relationMappingMutation = useMutation({
    mutationFn: (input: SemanticRelationMappingCreate) => api.createSemanticRelationMapping(projectId, input),
    onSuccess: async () => { setNotice("来源关系映射草稿已保存。请先校验并批准，下一次物化才会建立本体关系。"); await refresh(); },
    onError: (value) => setError(value),
  });
  const relationMappingCommandMutation = useMutation({
    mutationFn: ({ mapping, command }: { mapping: SemanticRelationMapping; command: "validate" | "approve" | "disable" }) => command === "validate" ? api.validateSemanticRelationMapping(projectId, mapping.id, { expected_revision: mapping.revision }) : command === "approve" ? api.approveSemanticRelationMapping(projectId, mapping.id, { expected_revision: mapping.revision }) : api.disableSemanticRelationMapping(projectId, mapping.id, { expected_revision: mapping.revision }),
    onSuccess: async (mapping) => { setNotice(`来源关系映射状态已更新为 ${mapping.status}。`); await refresh(); },
    onError: (value) => setError(value),
  });
  const identityMutation = useMutation({
    mutationFn: ({ identity, entityId }: { identity: SourceIdentity; entityId: string }) => api.bindSourceIdentity(projectId, identity.id, { entity_id: entityId, expected_revision: identity.revision }),
    onSuccess: async () => { setNotice("来源身份已绑定；历史观测已迁移到所选正式对象。"); await Promise.all([identitiesQuery.refetch(), entitiesQuery.refetch()]); },
    onError: (value) => setError(value),
  });
  const connectorPreviewMutation = useMutation({
    mutationFn: ({ sourceId, input }: { sourceId: string; input: SourceConnectorExtractRequest }) => api.previewSourceExtract(projectId, sourceId, input),
    onSuccess: (value, variables) => { setConnectorPreview(value); setConnectorRequest(variables.input); setNextConnectorRequest(null); setNotice(`已从 ${value.asset_key} 只读提取 ${value.rows.length} 行并固化为待确认快照，尚未写入。`); },
    onError: (value) => setError(value),
  });
  const connectorSyncMutation = useMutation({
    mutationFn: () => {
      if (!selectedConnectorId || !connectorPreview) throw new Error("请先完成连接器提取预览。");
      return api.syncSourceSystem(projectId, selectedConnectorId, { preview_id: connectorPreview.preview_id });
    },
    onSuccess: async (value) => {
      const canContinue = connectorRequest?.watermark_column && connectorRequest.tie_breaker_column && value.extraction.next_watermark != null && value.extraction.next_tie_breaker != null;
      setNextConnectorRequest(canContinue ? { ...connectorRequest, after_watermark: value.extraction.next_watermark, after_tie_breaker: value.extraction.next_tie_breaker } : null);
      setNotice(`同步完成：确认的是预览快照中的 ${value.extraction.rows.length} 行，新增 ${value.import_result.observations_created} 条现实观测。`);
      setConnectorPreview(null);
      await refresh();
    },
    onError: (value) => setError(value),
  });
  const assetMutation = useMutation({
    mutationFn: ({ sourceId, assetId, revision }: { sourceId: string; assetId: string; revision: number }) => api.updateSourceAsset(projectId, sourceId, assetId, { status: "ACTIVE", expected_revision: revision }),
    onSuccess: async () => { setNotice("来源资产已重新启用；请确认字段映射后重新物化。 "); await refresh(); },
    onError: (value) => setError(value),
  });
  const materializeMutation = useMutation({
    mutationFn: (batchId: string) => api.materializeRawBatch(projectId, batchId),
    onSuccess: async (value) => { setNotice(`物化运行 ${value.status}：处理 ${value.records_processed} 行，生成 ${value.observations_created} 条观测。`); await refresh(); },
    onError: (value) => setError(value),
  });
  const conflictMutation = useMutation({
    mutationFn: ({ conflict, input }: { conflict: ObservationConflict; input: ObservationConflictResolve }) => api.resolveObservationConflict(projectId, conflict.id, input),
    onSuccess: async () => { setNotice("冲突已经裁决，统一读取将采用所选观测。 "); await refresh(); },
    onError: (value) => setError(value),
  });
  const datasetCreateMutation = useMutation({
    mutationFn: (input: SemanticDatasetCreate) => api.createSemanticDataset(projectId, input),
    onSuccess: async (value) => { setNotice(`语义数据集“${value.name}”已创建。`); await refresh(); },
    onError: (value) => setError(value),
  });
  const datasetQueryMutation = useMutation({
    mutationFn: (dataset: SemanticDataset) => api.querySemanticDataset(projectId, dataset.id, { query_snapshot_id: null, limit: 200, offset: 0, include_lineage: true }),
    onSuccess: (value) => { setDatasetResult(value); setNotice(`查询完成：返回 ${value.rows.length} 行。`); },
    onError: (value) => setError(value),
  });
  const datasetRetireMutation = useMutation({
    mutationFn: (dataset: SemanticDataset) => api.updateSemanticDataset(projectId, dataset.id, { status: dataset.status === "ACTIVE" ? "RETIRED" : "ACTIVE", expected_revision: dataset.revision }),
    onSuccess: async () => { setDatasetResult(null); await refresh(); },
    onError: (value) => setError(value),
  });
  const datasetExportMutation = useMutation({
    mutationFn: async (dataset: SemanticDataset) => {
      const job = await api.exportSemanticDataset(projectId, dataset.id, { format: "csv", query_snapshot_id: null });
      if (job.status !== "COMPLETED") throw new Error(job.error || "数据集导出尚未完成。");
      return api.downloadExport(projectId, job.id);
    },
    onSuccess: ({ blob, fileName }) => { const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = fileName; anchor.click(); URL.revokeObjectURL(url); },
    onError: (value) => setError(value),
  });
  const readError = [
    systemsQuery.error,
    mappingsQuery.error,
    relationMappingsQuery.error,
    typesQuery.error,
    identitiesQuery.error,
    entitiesQuery.error,
    batchesQuery.error,
    recordsQuery.error,
    runsQuery.error,
    conflictsQuery.error,
    datasetsQuery.error,
  ].find(Boolean);

  const createSystem = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const input: SourceSystemCreate = {
        name: String(form.get("name") ?? "").trim(),
        kind: String(form.get("kind")) as SourceSystemKind,
        description: String(form.get("description") ?? "").trim() || null,
        connection_profile: parseObject(String(form.get("connection_profile") ?? "{}")),
      };
      systemMutation.mutate(input);
      event.currentTarget.reset();
    } catch (value) { setError(asError(value)); }
  };
  const createMapping = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const input: SemanticMappingCreate = {
      source_system_id: String(form.get("source_system_id")),
      source_asset: String(form.get("source_asset") ?? "").trim(),
      source_field: String(form.get("source_field") ?? "").trim(),
      target_type_key: String(form.get("target_type_key")),
      target_property_key: String(form.get("target_property_key") ?? "").trim(),
      transform_expression: String(form.get("transform_expression") ?? "").trim() || null,
      authority_priority: Number(form.get("authority_priority") || 100),
    };
    if (!input.source_system_id || !input.source_asset || !input.source_field || !input.target_type_key || !input.target_property_key) {
      setError(new Error("请填写完整的来源字段和目标语义。"));
      return;
    }
    mappingMutation.mutate(input);
  };
  const suggestMappings = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const sourceSystemId = String(form.get("source_system_id") ?? "");
    const sourceAsset = String(form.get("source_asset") ?? "").trim();
    const targetTypeKey = String(form.get("target_type_key") ?? "");
    if (!sourceSystemId || !targetTypeKey) {
      setError(new Error("请先选择数据源和目标本体类型。"));
      return;
    }
    mappingSuggestionMutation.mutate({
      source_system_id: sourceSystemId,
      source_asset: sourceAsset || undefined,
      target_type_key: targetTypeKey,
      include_existing: form.get("include_existing") === "on",
    });
  };
  const acceptSuggestedMapping = (suggestion: SemanticMappingSuggestion) => {
    if (suggestion.existing_mapping_id || mappingMutation.isPending) return;
    mappingMutation.mutate(
      {
        source_system_id: suggestion.source_system_id,
        source_asset: suggestion.source_asset,
        source_field: suggestion.source_field,
        target_type_key: suggestion.target_type_key,
        target_property_key: suggestion.target_property_key,
        transform_expression: suggestion.transform_expression,
        authority_priority: 100,
      },
      {
        onSuccess: (mapping) => {
          setMappingSuggestions((current) => current ? {
            ...current,
            items: current.items.map((item) => (
              item.source_system_id === suggestion.source_system_id
              && item.source_asset === suggestion.source_asset
              && item.source_field === suggestion.source_field
              && item.target_type_key === suggestion.target_type_key
              && item.target_property_key === suggestion.target_property_key
                ? { ...item, existing_mapping_id: mapping.id }
                : item
            )),
          } : current);
        },
      },
    );
  };
  const createRelationMapping = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const input: SemanticRelationMappingCreate = {
      source_system_id: String(form.get("source_system_id")),
      source_asset: String(form.get("source_asset") ?? "").trim(),
      source_type_key: String(form.get("source_type_key") ?? "").trim(),
      source_field: String(form.get("source_field") ?? "").trim(),
      relation_type_key: String(form.get("relation_type_key") ?? "").trim(),
      source_role_key: String(form.get("source_role_key") ?? "").trim(),
      target_type_key: String(form.get("target_type_key") ?? "").trim(),
      target_role_key: String(form.get("target_role_key") ?? "").trim(),
      target_asset: String(form.get("target_asset") ?? "").trim() || null,
      transform_expression: String(form.get("transform_expression") ?? "").trim() || null,
    };
    if (!input.source_system_id || !input.source_asset || !input.source_type_key || !input.source_field || !input.relation_type_key || !input.source_role_key || !input.target_type_key || !input.target_role_key) {
      setError(new Error("请填写完整的来源类型、外键字段、关系类型、角色和目标类型。"));
      return;
    }
    relationMappingMutation.mutate(input);
  };
  const previewConnector = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const watermark = String(form.get("watermark_column") ?? "").trim() || null;
    const tieBreaker = String(form.get("tie_breaker_column") ?? "").trim() || null;
    const afterWatermark = parseOptionalValue(String(form.get("after_watermark") ?? ""));
    const afterTieBreaker = parseOptionalValue(String(form.get("after_tie_breaker") ?? ""));
    connectorPreviewMutation.mutate({
      sourceId: selectedConnectorId,
      input: {
        asset_key: String(form.get("asset_key") ?? "").trim(),
        limit: Number(form.get("limit") || 100),
        watermark_column: watermark,
        tie_breaker_column: tieBreaker,
        after_watermark: afterWatermark,
        after_tie_breaker: afterTieBreaker,
      },
    });
  };
  const createDataset = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const columns = JSON.parse(String(form.get("columns") ?? "[]")) as unknown;
      if (!Array.isArray(columns)) throw new Error("数据集列定义必须是 JSON 数组。");
      datasetCreateMutation.mutate({
        key: String(form.get("key") ?? "").trim(),
        name: String(form.get("name") ?? "").trim(),
        description: String(form.get("description") ?? "").trim() || null,
        root_type_key: String(form.get("root_type_key") ?? "").trim(),
        columns: columns as SemanticDatasetCreate["columns"],
      });
    } catch (value) { setError(asError(value)); }
  };

  return <div className="advanced-stack">
    {notice && <StatusMessage title="数据接入结果" description={notice} />}
    {error && <StatusMessage tone="danger" title="数据接入没有完成" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    {readError && <StatusMessage tone="danger" title="数据接入读取失败" description={asError(readError).message} action={{ label: "重试", onClick: () => void refresh() }} />}
    <div className="data-release-grid">
      <section className="panel nested-panel">
        <div className="panel-heading"><div><h3>数据源</h3><p>文件导入、本地 SQLite、PostgreSQL 和通用 REST JSON 只读连接器可用；具体厂商插件仍会明确显示为未适配。</p></div><span>{systemsQuery.data?.total ?? 0} 个</span></div>
        <div className="compact-list">{(systemsQuery.data?.items ?? []).map((system) => <article key={system.id}><div><strong>{system.name}</strong><small>{system.kind} · {system.status} · 已配置：{system.configured_fields?.join("、") || "无"}</small><SourceConfigForm system={system} pending={systemUpdateMutation.isPending} onSave={(input) => systemUpdateMutation.mutate({ system, input })} /></div><button className="button text-button" disabled={testMutation.isPending} onClick={() => testMutation.mutate(system.id)}>测试</button></article>)}{!systemsQuery.data?.items.length && <p className="empty-copy">没有系统数据时可跳过。要导入系统 CSV，请先建立 FILE 数据源。</p>}</div>
        <form className="create-form" onSubmit={createSystem}><h4>登记数据源</h4><div className="form-grid-two"><label>名称<input name="name" required placeholder="例如：ERP 订单接口" /></label><label>类型<select name="kind" defaultValue="FILE">{sourceKinds.map((kind) => <option key={kind}>{kind}</option>)}</select></label></div><label>说明<textarea name="description" rows={2} /></label><label>连接配置 JSON<textarea name="connection_profile" rows={4} defaultValue="{}" spellCheck={false} /><small>SQLite：{`{"database_path":"D:/data/erp.sqlite"}`}；PostgreSQL：{`{"host":"127.0.0.1","database":"erp","user":"readonly","password":"..."}`}；REST：{`{"base_url":"https://erp.example/api","rows_path":"data.items","bearer_token":"..."}`}</small></label><button className="button secondary" disabled={systemMutation.isPending}>保存数据源</button></form>
      </section>
      <section className="panel nested-panel">
        <div className="panel-heading"><div><h3>数据预览与本次同步</h3><p>上传一次系统导出文件，先预览字段和样例，再按已批准的语义映射同步到现实观测层。</p></div></div>
        <form className="create-form source-import-form" onSubmit={(event) => { event.preventDefault(); previewMutation.mutate(); }}>
          <label>FILE 数据源<select value={selectedSourceId} onChange={(event) => { setSelectedSourceId(event.target.value); setPreview(null); setResult(null); }} required><option value="" disabled>{fileSources.length ? "请选择" : "请先建立 FILE 数据源"}</option>{fileSources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
          <div className="form-grid-two"><label>文件类型<select value={fileKind} onChange={(event) => { setFileKind(event.target.value as ImportKind); setPreview(null); setResult(null); }}>{fileKinds.map((kind) => <option key={kind}>{kind}</option>)}</select></label><label className="file-picker compact-file">本地文件<input type="file" accept=".csv,.xlsx,.json" onChange={(event) => { const selected = event.target.files?.[0] ?? null; setFile(selected); setSourceAsset(selected ? fileStem(selected.name) : ""); setPreview(null); setResult(null); setError(null); }} /><span>{file?.name || "选择文件"}</span></label></div>
          <button className="button secondary" disabled={!selectedSourceId || !file || previewMutation.isPending}>{previewMutation.isPending ? "解析中……" : "预览本次同步"}</button>
        </form>
        {preview && <div className="import-preview embedded-preview"><div className="subsection-heading"><div><h4>预览：{preview.file_name}</h4><small>{preview.columns.length} 个字段</small></div><span>{preview.sample_rows.length} 行样例</span></div>{preview.columns.length > 0 && <div className="table-scroll"><table className="data-table"><thead><tr>{preview.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{preview.sample_rows.slice(0, 5).map((row, index) => <tr key={index}>{preview.columns.map((column) => <td key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>}<label>来源资产 / 表名<input value={sourceAsset} onChange={(event) => setSourceAsset(event.target.value)} placeholder="例如：orders" required /><small>默认使用文件名；如果要匹配已批准的语义映射，请改成映射中的资产键，例如 <code>orders</code>，不要直接使用 <code>orders.csv</code>。</small></label><label className="json-editor">本次文件字段映射（来源字段 → 导入字段）<textarea value={mappingText} readOnly rows={7} spellCheck={false} /><small>这里仅展示预览识别结果。真正参与物化的是下方语义映射审核中已批准的映射；请在那里修改、校验并批准。</small></label><div className="button-row"><button className="button text-button" type="button" onClick={() => setPreview(null)}>取消</button><button className="button primary" type="button" disabled={confirmMutation.isPending} onClick={() => confirmMutation.mutate()}>{confirmMutation.isPending ? "同步中……" : "确认写入原始层并物化"}</button></div></div>}
        {result && <><div className="import-result-grid"><span>状态<strong>{result.status}</strong></span><span>待对齐身份<strong>{result.entities_created}</strong></span><span>自动绑定<strong>{result.identities_bound}</strong></span><span>新增观测<strong>{result.observations_created}</strong></span><span>建立关系<strong>{result.relations_created}</strong></span><span>应用映射<strong>{result.mappings_applied}</strong></span><span>跳过行<strong>{result.rows_skipped}</strong></span></div>{result.warnings.length > 0 && <ul className="warning-list">{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}</>}
      </section>
    </div>
    <details className="advanced-section" open>
      <summary><span><strong>数据库只读同步</strong><small>先预览真实行，再确认进入不可变原始层</small></span><i>展开 / 收起</i></summary>
      <div className="advanced-section-body advanced-stack">
        <section className="panel nested-panel">
          <div className="panel-heading"><div><h3>数据库 / REST 提取</h3><p>数据库事务和 REST 请求均为只读；数据库增量同步使用“水位字段 + 唯一排序字段”，REST 可使用接口返回的游标。</p></div><span>{connectorSources.length} 个连接器</span></div>
          <form className="create-form" onSubmit={previewConnector}>
            <label>连接器<select value={selectedConnectorId} onChange={(event) => { setSelectedConnectorId(event.target.value); setConnectorPreview(null); }} required><option value="" disabled>{connectorSources.length ? "请选择" : "请先登记数据库或 REST 数据源"}</option>{connectorSources.map((source) => <option key={source.id} value={source.id}>{source.name}（{source.kind}）</option>)}</select></label>
            <div className="form-grid-two"><label>表、视图或 API 路径<input name="asset_key" required placeholder="public.orders 或 orders" /></label><label>最大行数<input name="limit" type="number" min="1" max="5000" defaultValue="100" /></label></div>
            <div className="form-grid-two"><label>水位字段（可选）<input name="watermark_column" placeholder="updated_at" /></label><label>唯一排序字段（与水位成对）<input name="tie_breaker_column" placeholder="id" /></label></div>
            <div className="form-grid-two"><label>上一水位值（续传时填写）<input name="after_watermark" placeholder="2026-09-07T08:00:00Z" /></label><label>上一唯一排序值（与上一水位成对）<input name="after_tie_breaker" placeholder="10001" /></label></div>
            <button className="button secondary" disabled={!selectedConnectorId || connectorPreviewMutation.isPending}>{connectorPreviewMutation.isPending ? "只读提取中……" : "预览提取结果"}</button>
          </form>
          {nextConnectorRequest && <button className="button secondary" disabled={connectorPreviewMutation.isPending} onClick={() => connectorPreviewMutation.mutate({ sourceId: selectedConnectorId, input: nextConnectorRequest })}>按上一批水位预览下一批</button>}
          {connectorPreview && <div className="import-preview embedded-preview"><div className="subsection-heading"><div><h4>{connectorPreview.asset_key}</h4><small>{connectorPreview.columns.length} 列 · 快照 {connectorPreview.content_sha256.slice(0, 12)}…</small></div><span>{connectorPreview.rows.length} 行</span></div><div className="table-scroll"><table className="data-table"><thead><tr>{connectorPreview.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{connectorPreview.rows.slice(0, 10).map((row, index) => <tr key={index}>{connectorPreview.columns.map((column) => <td key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div><p className="muted">下一水位：{String(connectorPreview.next_watermark ?? "无")} / {String(connectorPreview.next_tie_breaker ?? "无")}。确认时只使用这份快照，不会重新读取源库。</p><button className="button primary" disabled={connectorSyncMutation.isPending || connectorPreview.rows.length === 0} onClick={() => connectorSyncMutation.mutate()}>{connectorSyncMutation.isPending ? "同步中……" : "确认写入原始层并物化"}</button></div>}
          <div className="compact-list">{(assetsQuery.data?.items ?? []).map((asset) => <article key={asset.id}><div><strong>{asset.name}</strong><small>{asset.asset_key} · {asset.status} · {asset.schema_fields.length} 字段 · 修订 {asset.revision}</small></div>{asset.status === "SCHEMA_DRIFT" && <button className="button secondary" disabled={assetMutation.isPending} onClick={() => assetMutation.mutate({ sourceId: selectedConnectorId, assetId: asset.id, revision: asset.revision })}>确认新结构并重新启用</button>}</article>)}</div>
        </section>
      </div>
    </details>
    <details className="advanced-section">
      <summary><span><strong>原始层、血缘与冲突</strong><small>查看每批处理记录，必要时重算或人工裁决</small></span><i>展开 / 收起</i></summary>
      <div className="advanced-section-body data-release-grid">
        <section className="panel nested-panel"><div className="panel-heading"><div><h3>原始批次与物化</h3><p>原始记录不可变；映射升级后可重跑，旧观测保留为历史版本。</p></div><span>{batchesQuery.data?.total ?? 0} 批</span></div><div className="compact-list">{(batchesQuery.data?.items ?? []).map((batch) => { const latestRun = runsQuery.data?.items.find((run) => run.raw_batch_id === batch.id); return <article key={batch.id}><div><strong>{batch.record_count} 行 · {batch.status}</strong><small>{batch.id} · schema {batch.schema_fingerprint.slice(0, 10)}…{latestRun ? ` · 最近物化 ${latestRun.status}/${latestRun.observations_created} 条观测` : " · 尚未物化"}</small></div><div className="row-actions"><button className="button text-button" onClick={() => setSelectedBatchId(batch.id)}>查看原始行</button><button className="button text-button" disabled={materializeMutation.isPending || batch.status === "SCHEMA_DRIFT"} onClick={() => materializeMutation.mutate(batch.id)}>按当前批准映射重算</button></div></article>; })}{!batchesQuery.data?.items.length && <p className="empty-copy">同步文件或数据库后显示原始批次。</p>}</div>{selectedBatchId && <div className="import-preview embedded-preview"><div className="subsection-heading"><h4>不可变原始记录</h4><button className="button text-button" onClick={() => setSelectedBatchId("")}>关闭</button></div>{recordsQuery.isLoading ? <p className="muted">正在读取……</p> : <div className="compact-list">{(recordsQuery.data?.items ?? []).slice(0, 100).map((record) => <article key={record.id}><div><strong>第 {record.row_number} 行 · {record.source_record_key || "无稳定键"}</strong><small>{record.source_locator} · {record.payload_sha256.slice(0, 12)}…</small><pre>{JSON.stringify(record.payload, null, 2)}</pre></div></article>)}</div>}</div>}</section>
        <section className="panel nested-panel"><div className="panel-heading"><div><h3>来源冲突</h3><p>同权威来源给出不同值时不采用“最后写入”，必须显式选择。</p></div><span>{(conflictsQuery.data?.items ?? []).filter((item) => item.status === "OPEN").length} 个待裁决</span></div><div className="compact-list">{(conflictsQuery.data?.items ?? []).map((conflict) => <article key={conflict.id}><div><strong>{conflict.field_key}</strong><small>对象 {conflict.entity_id} · {conflict.status} · {conflict.candidates.length} 个候选</small><div className="tag-line">{conflict.candidates.map((candidate) => <button key={candidate.assertion_id} className="button text-button" disabled={conflict.status !== "OPEN" || conflictMutation.isPending} onClick={() => conflictMutation.mutate({ conflict, input: { resolution_kind: "CHOOSE_ASSERTION", chosen_assertion_id: candidate.assertion_id, override_value: null, rationale: "由开发者在数据质量面板选择可信来源。", resolved_by: "developer", expected_revision: conflict.revision } })}>采用 {JSON.stringify(candidate.value)}（权威 {candidate.authority_priority}）</button>)}</div>{conflict.status === "OPEN" && <ConflictOverrideForm conflict={conflict} pending={conflictMutation.isPending} onResolve={(input) => conflictMutation.mutate({ conflict, input })} />}</div></article>)}{!conflictsQuery.data?.items.length && <p className="empty-copy">当前没有来源值冲突。</p>}</div></section>
      </div>
    </details>
    <details className="advanced-section">
      <summary><span><strong>语义数据集</strong><small>在固定快照上按本体关系连接、检查多值并导出</small></span><i>展开 / 收起</i></summary>
      <div className="advanced-section-body advanced-stack">
        <section className="panel nested-panel"><div className="panel-heading"><div><h3>已定义数据集</h3><p>通常让系统语义 Agent 建立；这里用于人工复核、查询、停用和导出。</p></div><span>{datasetsQuery.data?.total ?? 0} 个</span></div><div className="compact-list">{(datasetsQuery.data?.items ?? []).map((dataset) => <article key={dataset.id}><div><strong>{dataset.name}</strong><small>{dataset.key} · 根类型 {dataset.root_type_key} · {dataset.columns.length} 列 · {dataset.status}</small></div><div className="row-actions"><button className="button text-button" disabled={datasetQueryMutation.isPending || dataset.status !== "ACTIVE"} onClick={() => datasetQueryMutation.mutate(dataset)}>查询</button><button className="button text-button" disabled={datasetExportMutation.isPending || dataset.status !== "ACTIVE"} onClick={() => datasetExportMutation.mutate(dataset)}>导出 CSV</button><button className="button text-button danger-text" disabled={datasetRetireMutation.isPending} onClick={() => datasetRetireMutation.mutate(dataset)}>{dataset.status === "ACTIVE" ? "停用" : "重新启用"}</button></div></article>)}{!datasetsQuery.data?.items.length && <p className="empty-copy">还没有数据集。可告诉系统语义 Agent 要从哪个对象出发、沿哪些关系取哪些字段。</p>}</div>
        {datasetResult && <div className="import-preview embedded-preview"><div className="subsection-heading"><h4>查询结果</h4><span>{datasetResult.rows.length} 行 · {datasetResult.warnings.length} 个警告</span></div>{datasetResult.warnings.length > 0 && <pre>{JSON.stringify(datasetResult.warnings, null, 2)}</pre>}<div className="table-scroll"><table className="data-table"><thead><tr>{datasetResult.columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead><tbody>{datasetResult.rows.slice(0, 100).map((row, index) => <tr key={index}>{datasetResult.columns.map((column) => <td key={column.key}>{JSON.stringify(row[column.key] ?? null)}</td>)}</tr>)}</tbody></table></div></div>}
        <details className="inline-advanced"><summary>手动建立数据集</summary><form className="create-form" onSubmit={createDataset}><div className="form-grid-two"><label>标识<input name="key" required pattern="[a-z0-9_.-]+" placeholder="role_overview" /></label><label>名称<input name="name" required placeholder="岗位总览" /></label></div><div className="form-grid-two"><label>根本体类型<select name="root_type_key" required defaultValue="role">{(typesQuery.data?.items ?? []).filter((type) => ["OBJECT", "METRIC"].includes(type.kind)).map((type) => <option key={type.id} value={type.key}>{type.name}（{type.key}）</option>)}</select></label><label>说明<input name="description" /></label></div><label>列定义 JSON<textarea name="columns" rows={8} spellCheck={false} defaultValue={'[{"key":"name","label":"名称","path":[],"property_key":"__name__","aggregation":"NONE","value_layer":"RESOLVED"}]'} /></label><button className="button secondary" disabled={datasetCreateMutation.isPending}>创建数据集</button></form></details></section>
      </div>
    </details>
    <section className="panel nested-panel">
      <div className="panel-heading"><div><h3>来源身份对齐</h3><p>系统记录只保存为现实观测。精确稳定标识会自动绑定；其余记录必须由人选择正式企业对象。</p></div><span>{(identitiesQuery.data?.items ?? []).filter((item) => item.status === "UNRESOLVED").length} 个待处理</span></div>
      <div className="compact-list mapping-list">{(identitiesQuery.data?.items ?? []).map((identity) => <article key={identity.id}><div><strong>{identity.source_record_key}</strong><small>{identity.source_asset} · {identity.target_type_key} · {identity.status === "BOUND" ? `已绑定 ${identity.entity_name}` : "尚未进入正式投影"}</small></div>{identity.status === "UNRESOLVED" && <form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const entityId = String(form.get("entity_id") ?? ""); if (entityId) identityMutation.mutate({ identity, entityId }); }}><select name="entity_id" required defaultValue=""><option value="" disabled>选择正式对象</option>{(entitiesQuery.data?.items ?? []).filter((entity) => entity.type_key === identity.target_type_key).map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}</select><button className="button text-button" disabled={identityMutation.isPending}>绑定</button></form>}</article>)}{!identitiesQuery.data?.items.length && <p className="empty-copy">导入带稳定标识映射的系统文件后，来源身份会显示在这里。</p>}</div>
    </section>
    <section className="panel nested-panel">
      <div className="panel-heading"><div><h3>语义映射审核</h3><p>Agent 或人工先建立草稿；逐条查看映射预览和校验报告，批准后才参与下一次数据同步。</p></div><span>{(mappingsQuery.data?.items ?? []).filter((item) => item.status === "APPROVED").length} 条已批准 / {mappingsQuery.data?.total ?? 0} 条</span></div>
      <SemanticMappingSuggestionsPanel systems={systemsQuery.data?.items ?? []} types={typesQuery.data?.items ?? []} assets={suggestionAssetsQuery.data?.items ?? []} sourceId={suggestionSourceId} asset={suggestionAsset} onSourceChange={(value) => { setSuggestionSourceId(value); setSuggestionAsset(""); }} onAssetChange={setSuggestionAsset} pending={mappingSuggestionMutation.isPending} suggestions={mappingSuggestions} onSubmit={suggestMappings} accepting={mappingMutation.isPending} onAccept={acceptSuggestedMapping} />
      <div className="compact-list mapping-list semantic-review-list">{(mappingsQuery.data?.items ?? []).map((mapping) => {
        const source = systemsQuery.data?.items.find((item) => item.id === mapping.source_system_id);
        return <article key={mapping.id}><div className="mapping-summary"><strong>{mapping.source_asset}.{mapping.source_field}</strong><small>{source?.name || "未知数据源"} → {mapping.target_type_key}.{mapping.target_property_key}</small><div className="tag-line"><span className={`status-pill mapping-status-${mapping.status.toLowerCase()}`}>{mapping.status}</span><span className="tag">优先级 {mapping.authority_priority}</span></div><details><summary>查看映射预览与校验报告</summary><dl><div><dt>来源</dt><dd>{mapping.source_asset}.{mapping.source_field}</dd></div><div><dt>目标</dt><dd>{mapping.target_type_key}.{mapping.target_property_key}</dd></div><div><dt>转换</dt><dd>{mapping.transform_expression || "直接使用原值"}</dd></div><div><dt>修订</dt><dd>{mapping.revision}</dd></div></dl>{mapping.validation_report && Object.keys(mapping.validation_report).length > 0 ? <pre>{JSON.stringify(mapping.validation_report, null, 2)}</pre> : <p className="muted">尚无校验报告，请先执行校验。</p>}</details></div><div className="row-actions">{mapping.status === "DRAFT" && <button className="button text-button" disabled={mappingCommandMutation.isPending} onClick={() => mappingCommandMutation.mutate({ mapping, command: "validate" })}>校验</button>}{mapping.status === "VALIDATED" && <button className="button secondary" disabled={mappingCommandMutation.isPending} onClick={() => mappingCommandMutation.mutate({ mapping, command: "approve" })}>批准</button>}{mapping.status !== "DISABLED" && <button className="button text-button danger-text" disabled={mappingCommandMutation.isPending} onClick={() => mappingCommandMutation.mutate({ mapping, command: "disable" })}>停用</button>}</div></article>;
      })}{!mappingsQuery.data?.items.length && <p className="empty-copy">还没有字段映射。可让上方系统本体 Agent 生成提案，或在下方手动新增。</p>}</div>
      <form className="create-form" onSubmit={createMapping}><h4>新增映射</h4><div className="form-grid-two"><label>来源系统<select name="source_system_id" required defaultValue=""><option value="" disabled>请选择</option>{(systemsQuery.data?.items ?? []).map((system) => <option key={system.id} value={system.id}>{system.name}</option>)}</select></label><label>目标本体类型<select name="target_type_key" required defaultValue=""><option value="" disabled>请选择</option>{(typesQuery.data?.items ?? []).map((type) => <option key={type.id} value={type.key}>{type.name}（{type.key}）</option>)}</select></label></div><div className="form-grid-two"><label>来源资产/表<input name="source_asset" required placeholder="orders" /></label><label>来源字段<input name="source_field" required placeholder="customer_id" /></label></div><div className="form-grid-two"><label>目标属性<input name="target_property_key" list="target-property-keys" required placeholder="__stable_key__ / __name__ / 属性键" /><datalist id="target-property-keys"><option value="__stable_key__" /><option value="__name__" /></datalist></label><label>权威优先级<input name="authority_priority" type="number" min="0" defaultValue="100" /></label></div><label>转换表达式（可选）<input name="transform_expression" placeholder="trim(value)" /></label><button className="button secondary" disabled={!systemsQuery.data?.total || mappingMutation.isPending}>保存映射</button></form>
    </section>
    <section className="panel nested-panel">
      <div className="panel-heading"><div><h3>来源关系映射</h3><p>把系统里的外键明确映射为本体关系。只有校验并批准后，物化才会建立 SYSTEM_BOUND 关系；找不到目标时保留警告，不会猜测。</p></div><span>{(relationMappingsQuery.data?.items ?? []).filter((item) => item.status === "APPROVED").length} 条已批准 / {relationMappingsQuery.data?.total ?? 0} 条</span></div>
      <div className="compact-list mapping-list semantic-review-list">{(relationMappingsQuery.data?.items ?? []).map((mapping) => { const source = systemsQuery.data?.items.find((item) => item.id === mapping.source_system_id); return <article key={mapping.id}><div className="mapping-summary"><strong>{mapping.source_asset}.{mapping.source_field} → {mapping.relation_type_key}</strong><small>{source?.name || "未知数据源"} · {mapping.source_type_key}.{mapping.source_role_key} → {mapping.target_type_key}.{mapping.target_role_key}{mapping.target_asset ? ` · 目标资产 ${mapping.target_asset}` : ""}</small><div className="tag-line"><span className={`status-pill mapping-status-${mapping.status.toLowerCase()}`}>{mapping.status}</span></div><details><summary>查看关系映射详情</summary><dl><div><dt>来源</dt><dd>{mapping.source_asset}.{mapping.source_field} / {mapping.source_type_key}</dd></div><div><dt>关系</dt><dd>{mapping.relation_type_key}（{mapping.source_role_key} → {mapping.target_role_key}）</dd></div><div><dt>目标</dt><dd>{mapping.target_type_key}{mapping.target_asset ? ` / ${mapping.target_asset}` : "（自动搜索已导入身份）"}</dd></div><div><dt>转换</dt><dd>{mapping.transform_expression || "直接使用外键值"}</dd></div></dl>{mapping.validation_report && Object.keys(mapping.validation_report).length > 0 ? <pre>{JSON.stringify(mapping.validation_report, null, 2)}</pre> : <p className="muted">尚无校验报告，请先执行校验。</p>}</details></div><div className="row-actions">{mapping.status === "DRAFT" && <button className="button text-button" disabled={relationMappingCommandMutation.isPending} onClick={() => relationMappingCommandMutation.mutate({ mapping, command: "validate" })}>校验</button>}{mapping.status === "VALIDATED" && <button className="button secondary" disabled={relationMappingCommandMutation.isPending} onClick={() => relationMappingCommandMutation.mutate({ mapping, command: "approve" })}>批准</button>}{mapping.status !== "DISABLED" && <button className="button text-button danger-text" disabled={relationMappingCommandMutation.isPending} onClick={() => relationMappingCommandMutation.mutate({ mapping, command: "disable" })}>停用</button>}</div></article>; })}{!relationMappingsQuery.data?.items.length && <p className="empty-copy">还没有来源关系映射。典型用法：在岗位表中把 department_id 映射为 role.member → organization_unit.container。</p>}</div>
      <form className="create-form" onSubmit={createRelationMapping}><h4>新增外键关系映射</h4><div className="form-grid-two"><label>来源系统<select name="source_system_id" required defaultValue=""><option value="" disabled>请选择</option>{(systemsQuery.data?.items ?? []).map((system) => <option key={system.id} value={system.id}>{system.name}</option>)}</select></label><label>来源资产/表<input name="source_asset" required placeholder="roles" /></label></div><div className="form-grid-two"><label>来源本体类型<select name="source_type_key" required defaultValue=""><option value="" disabled>请选择对象类型</option>{(typesQuery.data?.items ?? []).filter((type) => type.kind === "OBJECT").map((type) => <option key={type.id} value={type.key}>{type.name}（{type.key}）</option>)}</select></label><label>外键字段<input name="source_field" required placeholder="department_id" /></label></div><div className="form-grid-two"><label>关系类型<select name="relation_type_key" required defaultValue=""><option value="" disabled>请选择关系类型</option>{(typesQuery.data?.items ?? []).filter((type) => type.kind === "RELATION").map((type) => <option key={type.id} value={type.key}>{type.name}（{type.key}）</option>)}</select></label><label>来源角色<input name="source_role_key" list="relation-role-keys" required placeholder="member / reporter" /></label></div><div className="form-grid-two"><label>目标本体类型<select name="target_type_key" required defaultValue=""><option value="" disabled>请选择对象类型</option>{(typesQuery.data?.items ?? []).filter((type) => type.kind === "OBJECT").map((type) => <option key={type.id} value={type.key}>{type.name}（{type.key}）</option>)}</select></label><label>目标角色<input name="target_role_key" list="relation-role-keys" required placeholder="container / manager" /></label></div><div className="form-grid-two"><label>目标资产（可选）<input name="target_asset" placeholder="departments" /></label><label>转换表达式（可选）<input name="transform_expression" placeholder="trim(value)" /></label></div><datalist id="relation-role-keys">{(typesQuery.data?.items ?? []).filter((type) => type.kind === "RELATION").flatMap((type) => type.relation_roles ?? []).map((role) => <option key={role.key} value={role.key} />)}</datalist><button className="button secondary" disabled={!systemsQuery.data?.total || relationMappingMutation.isPending}>保存关系映射</button></form>
    </section>
  </div>;
}

function SemanticMappingSuggestionsPanel({
  systems,
  types,
  assets,
  sourceId,
  asset,
  onSourceChange,
  onAssetChange,
  pending,
  suggestions,
  onSubmit,
  accepting,
  onAccept,
}: {
  systems: SourceSystem[];
  types: OntologyType[];
  assets: SourceAsset[];
  sourceId: string;
  asset: string;
  onSourceChange: (value: string) => void;
  onAssetChange: (value: string) => void;
  pending: boolean;
  suggestions: SemanticMappingSuggestionPage | null;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  accepting: boolean;
  onAccept: (suggestion: SemanticMappingSuggestion) => void;
}) {
  const objectOrMetricTypes = types.filter((type) => ["OBJECT", "METRIC"].includes(type.kind));
  return <details className="inline-advanced">
    <summary>字段候选助手（只读）</summary>
    <div className="advanced-section-body">
      <p className="muted">只按来源字段名与目标本体键/标签生成候选，不读取字段值，也不会自动创建、校验或批准映射。复核无误后可逐条保存为草稿；草稿仍需在下方审核、校验并批准。</p>
      <form className="create-form" onSubmit={onSubmit}>
        <div className="form-grid-two">
          <label>来源系统<select name="source_system_id" required value={sourceId} onChange={(event) => onSourceChange(event.target.value)}><option value="" disabled>请选择</option>{systems.map((system) => <option key={system.id} value={system.id}>{system.name}（{system.kind}）</option>)}</select></label>
          <label>目标本体类型<select name="target_type_key" required defaultValue=""><option value="" disabled>请选择对象或指标</option>{objectOrMetricTypes.map((type) => <option key={type.id} value={type.key}>{type.name}（{type.key}）</option>)}</select></label>
        </div>
        <label>来源资产（可选）<select name="source_asset" value={asset} onChange={(event) => onAssetChange(event.target.value)} disabled={!sourceId}><option value="">全部已登记资产</option>{assets.map((item) => <option key={item.id} value={item.asset_key}>{item.name}（{item.asset_key}）</option>)}</select><small>资产列表来自所选数据源；未登记资产不会被猜测。</small></label>
        <label className="checkbox-row"><input name="include_existing" type="checkbox" />也显示已经存在的映射</label>
        <button className="button secondary" disabled={pending || !systems.length}>{pending ? "查找中……" : "查找只读候选"}</button>
      </form>
      {suggestions && <div className="import-preview embedded-preview">
        <div className="subsection-heading"><div><h4>候选结果</h4><small>候选不会改变项目状态</small></div><span>{suggestions.total} 条</span></div>
        {(suggestions.warnings ?? []).map((warning) => <p className="muted" key={warning}>{warning}</p>)}
        {suggestions.items.length > 0 ? <div className="compact-list mapping-list">{suggestions.items.map((suggestion, index) => <article key={`${suggestion.source_system_id}-${suggestion.source_asset}-${suggestion.source_field}-${suggestion.target_property_key}-${index}`}>
          <div><strong>{suggestion.source_asset}.{suggestion.source_field} → {suggestion.target_type_key}.{suggestion.target_property_key}</strong><small>{suggestion.source_system_name} · {suggestion.target_type_name} / {suggestion.target_property_name} · 置信度 {Math.round(suggestion.confidence * 100)}% · {suggestion.match_kind}</small><p className="muted">{suggestion.rationale} 转换：{suggestion.transform_expression || "直接使用原值"}。{suggestion.existing_mapping_id ? "已存在同目标映射。" : "尚未保存为映射。"}</p></div><div className="row-actions"><span className="tag">仅供人工复核</span>{suggestion.existing_mapping_id ? <span className="tag">已保存草稿或映射</span> : <button className="button text-button" type="button" disabled={accepting} onClick={() => onAccept(suggestion)}>{accepting ? "保存中……" : "保存为草稿"}</button>}</div>
        </article>)}</div> : <p className="empty-copy">没有找到字段名完全匹配的候选。请检查来源资产结构或手动建立映射。</p>}
      </div>}
    </div>
  </details>;
}

function parseObject(value: string): Record<string, unknown> {
  const parsed = JSON.parse(value || "{}");
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("连接配置必须是 JSON 对象。");
  return parsed as Record<string, unknown>;
}

function SourceConfigForm({ system, pending, onSave }: { system: SourceSystem; pending: boolean; onSave: (input: SourceSystemUpdate) => void }) {
  const [config, setConfig] = useState("{}");
  const [localError, setLocalError] = useState("");
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try { onSave({ connection_profile: parseObject(config), expected_revision: system.revision }); setLocalError(""); } catch (value) { setLocalError(asError(value).message); }
  };
  return <details className="inline-advanced"><summary>重新配置连接</summary><form className="create-form" onSubmit={submit}><label>连接配置 JSON<textarea value={config} onChange={(event) => setConfig(event.target.value)} rows={4} spellCheck={false} /></label><small>已有密码不会回显；提交对象将整体替换旧连接配置。</small>{localError && <small className="danger-text">{localError}</small>}<button className="button secondary" disabled={pending}>保存配置</button></form></details>;
}

function ConflictOverrideForm({ conflict, pending, onResolve }: { conflict: ObservationConflict; pending: boolean; onResolve: (input: ObservationConflictResolve) => void }) {
  const [value, setValue] = useState("");
  const [rationale, setRationale] = useState("");
  const submitOverride = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    let parsed: unknown = value;
    try { parsed = JSON.parse(value); } catch { /* ordinary text is a valid override */ }
    onResolve({ resolution_kind: "OVERRIDE", chosen_assertion_id: null, override_value: parsed, rationale: rationale.trim(), resolved_by: "developer", expected_revision: conflict.revision });
  };
  return <details className="inline-advanced"><summary>覆盖值或忽略冲突</summary><form className="create-form" onSubmit={submitOverride}><label>人工覆盖值<input value={value} onChange={(event) => setValue(event.target.value)} required placeholder="文本或 JSON" /></label><label>裁决依据<input value={rationale} onChange={(event) => setRationale(event.target.value)} required /></label><div className="button-row"><button className="button secondary" disabled={pending}>采用覆盖值</button><button type="button" className="button text-button danger-text" disabled={pending || !rationale.trim()} onClick={() => onResolve({ resolution_kind: "IGNORE", chosen_assertion_id: null, override_value: null, rationale: rationale.trim(), resolved_by: "developer", expected_revision: conflict.revision })}>保留冲突但忽略</button></div></form></details>;
}

function parseStringMap(value: string): Record<string, string> {
  const parsed = JSON.parse(value || "{}");
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || Object.values(parsed).some((item) => typeof item !== "string")) {
    throw new Error("字段映射必须是“来源字段 → 目标字段”的字符串 JSON 对象。");
  }
  return parsed as Record<string, string>;
}

function fileStem(fileName: string): string {
  const baseName = fileName.split(/[\\/]/).pop() ?? fileName;
  return baseName.replace(/\.[^.]+$/, "") || "imported_asset";
}

function parseOptionalValue(value: string): unknown | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  try { return JSON.parse(trimmed) as unknown; } catch { return trimmed; }
}

function asError(value: unknown) { return value instanceof Error ? value : new Error("输入无法解析。"); }
