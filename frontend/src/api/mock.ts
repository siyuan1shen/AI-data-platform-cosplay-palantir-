import type {
  ActionApprovalRequest,
  ActionDefinition,
  ActionDefinitionCreate,
  ActionDefinitionUpdate,
  ActionInvocation,
  ActionInvocationCreate,
  ActionInvocationStatus,
  ActionLog,
  ActionObservation,
  ActionObservationCreate,
  ActionRetryRequest,
  Company,
  CompanyCreate,
  ExecutiveContext,
  ObservationCreate,
  ObservationUpdate,
  ObservationView,
  ObservationHistoryView,
  ObservationExtractRequest,
  ObservationExtractionView,
  ObservationExtractionHistoryView,
  ObservationAttachment,
  ObservationIngestion,
  ObservationIngestionRequest,
  ModelProfile,
  Page,
  Project,
  ProjectCreate,
  PotentialRecord,
  PotentialRecordCreate,
  PotentialRecordUpdate,
  PotentialRecordStatusRequest,
  PotentialHistory,
  PotentialVersion,
  PotentialAudit,
  PotentialOperation,
  PotentialPage,
  WorkObservationBatch,
  WorkObservationAnalysis,
  WorkObservationComparison,
  WorkObservationCoverage,
  WorkObservationImportResult,
  WorkObservationIdentityBinding,
  WorkObservationIdentityBindingHistory,
  WorkObservationPreview,
  WorkObservationVirtualCandidate,
  ManagementAction,
  ManagementActionCreate,
  ManagementActionUpdate,
  ManagementActionRevisionRequest,
  ManagementActionEventCreate,
  ManagementActionVerifyDone,
  ManagementActionEvent,
} from "./types";

const now = () => new Date().toISOString();
const companyId = "10000000-0000-4000-8000-000000000001";
const projectId = "20000000-0000-4000-8000-000000000001";

const companies: Company[] = [
  {
    id: companyId,
    name: "示例精密制造",
    industry: "制造业",
    description: "用于前端独立开发的契约型示例企业",
    canonical_project_id: projectId,
    created_at: now(),
    updated_at: now(),
  },
];

const projects: Project[] = [
  {
    id: projectId,
    company_id: companyId,
    name: "企业总体投影",
    description: "组织、流程和信息流的初始建模项目",
    status: "ACTIVE",
    is_primary: true,
    canonical_project_id: projectId,
    revision: 3,
    created_at: now(),
    updated_at: now(),
  },
];

const actionDefinitions: ActionDefinition[] = [];
const actionInvocations: ActionInvocation[] = [];
const actionLogs: ActionLog[] = [];
const actionObservations: ActionObservation[] = [];
const managementObservations: ObservationView[] = [];
const managementObservationHistory = new Map<string, ObservationHistoryView["items"]>();
const managementObservationExtractions = new Map<string, ObservationExtractionView[]>();
const managementObservationAttachments = new Map<string, ObservationAttachment[]>();
const managementObservationAttachmentFiles = new Map<string, File>();
const potentialRecords: PotentialRecord[] = [];
const potentialRecordHistory = new Map<string, PotentialHistory>();
const managementActions: ManagementAction[] = [];
const managementActionHistory = new Map<string, ManagementActionEvent[]>();
const managementActionCreateRequests = new Map<string, { requestHash: string; actionId: string }>();

const newId = () => crypto.randomUUID();

const defaultActionSeeds: Array<Pick<ActionDefinition, "key" | "name" | "description" | "risk_level" | "require_approval" | "execution_mode" | "target_type_key">> = [
  { key: "save_hypothesis", name: "保存管理假设", description: "把管理层提出的假设保存到项目上下文。", risk_level: "LOW", require_approval: false, execution_mode: "INTERNAL", target_type_key: null },
  { key: "create_scenario", name: "创建调整情景", description: "创建一份不影响正式投影的管理调整情景。", risk_level: "LOW", require_approval: false, execution_mode: "INTERNAL", target_type_key: null },
  { key: "create_entity", name: "创建企业对象", description: "在草稿投影中创建一个新的企业对象。", risk_level: "MEDIUM", require_approval: true, execution_mode: "INTERNAL", target_type_key: null },
  { key: "create_relation", name: "创建企业关系", description: "在草稿投影中创建对象之间的关系。", risk_level: "MEDIUM", require_approval: true, execution_mode: "INTERNAL", target_type_key: null },
  { key: "apply_change_set", name: "应用变更集", description: "将已经确认的建模变更应用到草稿投影。", risk_level: "HIGH", require_approval: true, execution_mode: "INTERNAL", target_type_key: null },
];

const addLog = (projectId: string, invocationId: string, eventType: string, actor: string, fromStatus: ActionInvocationStatus | null, toStatus: ActionInvocationStatus | null, details: Record<string, unknown> = {}) => {
  actionLogs.unshift({ id: newId(), project_id: projectId, invocation_id: invocationId, event_type: eventType, from_status: fromStatus, to_status: toStatus, actor, details, created_at: now() });
};

const seedActionsForProject = (project: Project) => {
  defaultActionSeeds.forEach((seed, index) => actionDefinitions.push({
    ...seed,
    id: `60000000-0000-4000-8000-${String(actionDefinitions.length + index + 1).padStart(12, "0")}`,
    project_id: project.id,
    created_by: "developer",
    enabled: true,
    revision: 1,
    status: "VALIDATED",
    parameters: [],
    preconditions: [],
    effects: [],
    created_at: now(),
    updated_at: now(),
  }));
};

projects.forEach(seedActionsForProject);

export const mockApi = {
  async getWorkObservationCoverage(selectedProjectId: string): Promise<WorkObservationCoverage> {
    return { project_id: selectedProjectId, event_count: 0, foreground_event_count: 0, employee_count: 0, session_count: 0, source_count: 0, first_observed_at: null, last_observed_at: null, batch_count: 0 };
  },

  async ingestWorkObservationBatch(selectedProjectId: string, input: { batch_id: string; source_id: string; format_version: string; events: unknown[] }): Promise<WorkObservationImportResult> {
    return { batch_id: newId(), source_batch_id: input.batch_id, status: "IMPORTED", accepted_count: input.events.length, duplicate_count: 0, event_count: input.events.length, duplicate: false };
  },

  async listWorkObservationBatches(_selectedProjectId: string): Promise<Page<WorkObservationBatch>> {
    return { items: [], total: 0 };
  },

  async previewWorkObservationImport(selectedProjectId: string, input: { batch_id: string; source_id: string; format_version: string; events: unknown[] }): Promise<WorkObservationPreview> {
    return { id: newId(), project_id: selectedProjectId, source_batch_id: input.batch_id, source_id: input.source_id, format_version: input.format_version, payload_hash: "mock-preview-hash-00000000000000000000000000000000000000000000000000", event_count: input.events.length, status: "READY", created_at: now() };
  },

  async confirmWorkObservationImport(_projectId: string, previewId: string, _payloadHash: string): Promise<WorkObservationImportResult> {
    return { batch_id: previewId, source_batch_id: "mock-batch", status: "IMPORTED", accepted_count: 0, duplicate_count: 0, event_count: 0, duplicate: false };
  },

  async listWorkObservationIdentityBindings(_selectedProjectId: string): Promise<Page<WorkObservationIdentityBinding>> {
    return { items: [], total: 0 };
  },

  async bindWorkObservationIdentity(selectedProjectId: string, input: { source_id: string; source_employee_key: string; formal_entity_id: string; formal_role_key?: string | null }): Promise<WorkObservationIdentityBinding> {
    return { id: newId(), project_id: selectedProjectId, source_id: input.source_id, source_employee_key: input.source_employee_key, formal_entity_id: input.formal_entity_id, formal_role_key: input.formal_role_key ?? null, status: "ACTIVE", created_at: now(), updated_at: now() };
  },

  async retireWorkObservationIdentity(selectedProjectId: string, input: { source_id: string; source_employee_key: string; reason: string }): Promise<WorkObservationIdentityBinding> {
    return { id: newId(), project_id: selectedProjectId, source_id: input.source_id, source_employee_key: input.source_employee_key, formal_entity_id: "00000000-0000-4000-8000-000000000000", formal_role_key: null, status: "RETIRED", created_at: now(), updated_at: now() };
  },

  async listWorkObservationIdentityHistory(_selectedProjectId: string): Promise<Page<WorkObservationIdentityBindingHistory>> {
    return { items: [], total: 0 };
  },

  async createWorkObservationAnalysis(selectedProjectId: string): Promise<WorkObservationAnalysis> {
    return { id: newId(), project_id: selectedProjectId, status: "COMPLETED", filters: {}, event_count: 0, segment_count: 0, employee_count: 0, result: { employee_paths: {}, nodes: [], edges: [], patterns: [], segments: [], limitations: ["Mock 模式没有工作观察数据。"] }, created_at: now() };
  },

  async listWorkObservationAnalyses(_selectedProjectId: string): Promise<Page<WorkObservationAnalysis>> {
    return { items: [], total: 0 };
  },

  async getWorkObservationAnalysis(selectedProjectId: string, analysisId: string): Promise<WorkObservationAnalysis> {
    return { id: analysisId, project_id: selectedProjectId, status: "COMPLETED", filters: {}, event_count: 0, segment_count: 0, employee_count: 0, result: { employee_paths: {}, nodes: [], edges: [], patterns: [], segments: [], limitations: ["Mock 模式没有工作观察数据。"] }, created_at: now() };
  },

  async proposeWorkObservationVirtualCandidates(selectedProjectId: string, analysisId: string): Promise<WorkObservationVirtualCandidate[]> {
    return [{ id: newId(), project_id: selectedProjectId, analysis_id: analysisId, candidate_type: "ACTIVITY", label: "Mock:未配置工作观察活动", role_key: null, properties: { trust: "OBSERVED_WORK_PATTERN_CANDIDATE" }, evidence_segment_ids: [], status: "PROPOSED", virtual_work_model_id: null, virtual_node_id: null, virtual_edge_id: null, decision_reason: null, created_at: now(), decided_at: null }];
  },

  async listWorkObservationVirtualCandidates(_selectedProjectId: string, _status?: string): Promise<Page<WorkObservationVirtualCandidate>> {
    return { items: [], total: 0 };
  },

  async decideWorkObservationVirtualCandidate(selectedProjectId: string, candidateId: string, input: { decision: "CONFIRM" | "REJECT"; reason: string; virtual_work_model_id?: string | null; position_node_id?: string | null }): Promise<WorkObservationVirtualCandidate> {
    return { id: candidateId, project_id: selectedProjectId, analysis_id: newId(), candidate_type: "ACTIVITY", label: "Mock候选", role_key: null, properties: {}, evidence_segment_ids: [], status: input.decision === "CONFIRM" ? "CONFIRMED" : "REJECTED", virtual_work_model_id: input.virtual_work_model_id ?? null, virtual_node_id: null, virtual_edge_id: null, decision_reason: input.reason, created_at: now(), decided_at: now() };
  },

  async getWorkObservationGraph(_selectedProjectId: string, _analysisId?: string): Promise<{ analysis_id: string | null; graph: Record<string, unknown> | null }> {
    return { analysis_id: null, graph: null };
  },

  async getWorkObservationSegment(_selectedProjectId: string, _analysisId: string, segmentId: string): Promise<Record<string, unknown>> {
    return { id: segmentId, unavailable: true };
  },

  async compareWorkObservation(selectedProjectId: string, input: { analysis_id: string; left_employee_keys: string[]; right_employee_keys: string[] }): Promise<WorkObservationComparison> {
    return { id: newId(), project_id: selectedProjectId, analysis_id: input.analysis_id, left_employee_keys: input.left_employee_keys, right_employee_keys: input.right_employee_keys, result: { left_paths: [], right_paths: [], only_left: [], only_right: [], shared: [], limitations: ["Mock 模式没有工作观察数据。"] }, created_at: now() };
  },

  async listModelProfiles(): Promise<Page<ModelProfile>> {
    return { items: [], total: 0 };
  },

  async listPotentialRecords(selectedProjectId: string, includeHistory = false): Promise<PotentialPage> {
    const items = potentialRecords.filter((item) => item.project_id === selectedProjectId && (includeHistory || (item.human_status === "ACCEPTED" && item.evidence_status !== "REFUTED")));
    return { items: items.map((item) => ({ ...item })), total: items.length };
  },

  async createPotentialRecord(selectedProjectId: string, input: PotentialRecordCreate): Promise<PotentialRecord> {
    const project = projects.find((item) => item.id === selectedProjectId);
    if (!project || input.project_id !== project.id || input.company_id !== project.company_id) throw new Error("潜在认识的公司和项目必须与当前项目一致。");
    const timestamp = now();
    const record: PotentialRecord = {
      ...input, id: newId(), human_status: "ACCEPTED", version: 1,
      payload_hash: mockPotentialHash(), created_by: "local-owner", created_at: timestamp, updated_at: timestamp,
    };
    potentialRecords.unshift(record);
    potentialRecordHistory.set(record.id, emptyPotentialHistory());
    appendPotentialHistory(record, "CREATED", "local-owner", null, null);
    return { ...record };
  },

  async editPotentialRecord(selectedProjectId: string, recordId: string, input: PotentialRecordUpdate): Promise<PotentialRecord> {
    const record = findPotentialRecord(selectedProjectId, recordId);
    assertPotentialVersion(record, input.expected_version);
    const beforeHash = record.payload_hash;
    const { expected_version: _expectedVersion, reason, ...changes } = input;
    Object.assign(record, changes, { version: record.version + 1, updated_at: now(), payload_hash: mockPotentialHash() });
    appendPotentialHistory(record, "EDITED", "local-owner", reason, beforeHash);
    return { ...record };
  },

  async getPotentialRecordHistory(selectedProjectId: string, recordId: string): Promise<PotentialHistory> {
    findPotentialRecord(selectedProjectId, recordId);
    const history = potentialRecordHistory.get(recordId) ?? emptyPotentialHistory();
    return { versions: history.versions.map((item) => ({ ...item, snapshot: { ...item.snapshot } })), audit: history.audit.map((item) => ({ ...item })) };
  },

  async acceptPotentialRecord(selectedProjectId: string, recordId: string, input: PotentialRecordStatusRequest): Promise<PotentialRecord> {
    return changePotentialStatus(selectedProjectId, recordId, input, "ACCEPTED");
  },

  async rejectPotentialRecord(selectedProjectId: string, recordId: string, input: PotentialRecordStatusRequest): Promise<PotentialRecord> {
    return changePotentialStatus(selectedProjectId, recordId, input, "REJECTED");
  },

  async withdrawPotentialRecord(selectedProjectId: string, recordId: string, input: PotentialRecordStatusRequest): Promise<PotentialRecord> {
    return changePotentialStatus(selectedProjectId, recordId, input, "WITHDRAWN");
  },

  async listManagementActions(selectedProjectId: string, includeCancelled = true): Promise<Page<ManagementAction>> {
    const items = managementActions.filter((item) => item.project_id === selectedProjectId && (includeCancelled || item.status !== "CANCELLED"));
    return { items: [...items].sort((a, b) => b.created_at.localeCompare(a.created_at)), total: items.length };
  },

  async createManagementAction(selectedProjectId: string, input: ManagementActionCreate): Promise<ManagementAction> {
    const project = projects.find((item) => item.id === selectedProjectId);
    if (!project) throw new Error("找不到当前企业项目。");
    const idempotencyKey = input.idempotency_key?.trim();
    const { idempotency_key: _ignoredKey, ...requestContent } = input;
    const requestHash = JSON.stringify(requestContent);
    const requestMapKey = idempotencyKey ? `${selectedProjectId}:${idempotencyKey}` : null;
    const priorRequest = requestMapKey ? managementActionCreateRequests.get(requestMapKey) : undefined;
    if (priorRequest) {
      if (priorRequest.requestHash !== requestHash) throw new Error("该提交标识已用于不同的管理行动内容。请勿复用原提交内容的幂等标识。");
      const priorAction = findManagementAction(selectedProjectId, priorRequest.actionId);
      return { ...priorAction };
    }
    const timestamp = now();
    const action: ManagementAction = {
      id: newId(), company_id: project.company_id, project_id: selectedProjectId,
      title: input.title, description: input.description ?? null, owner: input.owner ?? null,
      priority: input.priority, status: "OPEN", due_at: input.due_at ?? null,
      reported_done: false, reported_done_at: null, reported_done_by: null,
      verified_done: false, verified_done_at: null, verified_done_by: null,
      revision: 1, created_by: "local-owner", created_at: timestamp, updated_at: timestamp,
    };
    managementActions.unshift(action);
    managementActionHistory.set(action.id, []);
    appendManagementActionEvent(action, "CREATED", input.reason ?? null, "创建现实管理行动", {
      title: action.title, description: action.description, owner: action.owner,
      priority: action.priority, status: action.status, due_at: action.due_at,
    }, null, "OPEN", false);
    if (requestMapKey) managementActionCreateRequests.set(requestMapKey, { requestHash, actionId: action.id });
    return { ...action };
  },

  async getManagementAction(selectedProjectId: string, actionId: string): Promise<ManagementAction> {
    return { ...findManagementAction(selectedProjectId, actionId) };
  },

  async updateManagementAction(selectedProjectId: string, actionId: string, input: ManagementActionUpdate): Promise<ManagementAction> {
    const action = findManagementAction(selectedProjectId, actionId);
    assertManagementActionRevision(action, input.expected_revision);
    if (action.status === "CANCELLED") throw new Error("已取消的管理行动不能修改。");
    const previousStatus = action.status;
    if (input.title !== undefined && input.title !== null) action.title = input.title;
    if (input.description !== undefined) action.description = input.description;
    if (input.owner !== undefined) action.owner = input.owner;
    if (input.priority !== undefined && input.priority !== null) action.priority = input.priority;
    if (input.status !== undefined && input.status !== null) action.status = input.status;
    if (input.due_at !== undefined) action.due_at = input.due_at;
    appendManagementActionEvent(action, "UPDATED", input.reason ?? null, "更新现实管理行动", {
      title: input.title, description: input.description, owner: input.owner,
      priority: input.priority, status: input.status, due_at: input.due_at,
    }, previousStatus === action.status ? null : previousStatus, previousStatus === action.status ? null : action.status);
    return { ...action };
  },

  async cancelManagementAction(selectedProjectId: string, actionId: string, input: ManagementActionRevisionRequest): Promise<ManagementAction> {
    const action = findManagementAction(selectedProjectId, actionId);
    assertManagementActionRevision(action, input.expected_revision);
    if (action.status === "CANCELLED") return { ...action };
    if (action.reported_done) throw new Error("已报告完成的管理行动需先核实，不能直接取消。");
    const previousStatus = action.status;
    action.status = "CANCELLED";
    appendManagementActionEvent(action, "CANCELLED", input.reason ?? null, "取消现实管理行动", {}, previousStatus, "CANCELLED");
    return { ...action };
  },

  async appendManagementActionProgress(selectedProjectId: string, actionId: string, input: ManagementActionEventCreate): Promise<ManagementActionEvent> {
    return appendManagementActionMessageEvent(selectedProjectId, actionId, input, "PROGRESS");
  },

  async appendManagementActionOutcome(selectedProjectId: string, actionId: string, input: ManagementActionEventCreate): Promise<ManagementActionEvent> {
    return appendManagementActionMessageEvent(selectedProjectId, actionId, input, "OUTCOME");
  },

  async reportManagementActionDone(selectedProjectId: string, actionId: string, input: ManagementActionEventCreate): Promise<ManagementAction> {
    const action = findManagementAction(selectedProjectId, actionId);
    assertManagementActionRevision(action, input.expected_revision);
    if (action.status === "CANCELLED") throw new Error("已取消的管理行动不能报告完成。");
    if (action.reported_done) throw new Error("该管理行动已报告完成。");
    const timestamp = now();
    action.reported_done = true;
    action.reported_done_at = timestamp;
    action.reported_done_by = "local-owner";
    appendManagementActionEvent(action, "DONE_REPORTED", input.reason ?? null, input.message, input.details ?? {});
    return { ...action };
  },

  async verifyManagementActionDone(selectedProjectId: string, actionId: string, input: ManagementActionVerifyDone): Promise<ManagementAction> {
    const action = findManagementAction(selectedProjectId, actionId);
    assertManagementActionRevision(action, input.expected_revision);
    if (!action.reported_done) throw new Error("只能核实已报告完成的管理行动。");
    if (action.verified_done) throw new Error("该管理行动已经核实完成。");
    const timestamp = now();
    action.verified_done = true;
    action.verified_done_at = timestamp;
    action.verified_done_by = "local-owner";
    appendManagementActionEvent(action, "DONE_VERIFIED", input.reason ?? null, input.verification_note);
    return { ...action };
  },

  async getManagementActionHistory(selectedProjectId: string, actionId: string): Promise<Page<ManagementActionEvent>> {
    findManagementAction(selectedProjectId, actionId);
    const items = (managementActionHistory.get(actionId) ?? []).map((item) => ({ ...item, details: { ...item.details } }));
    return { items, total: items.length };
  },

  async listManagementObservations(selectedProjectId: string, includeWithdrawn = false): Promise<Page<ObservationView>> {
    const items = managementObservations.filter((item) => item.project_id === selectedProjectId && (includeWithdrawn || item.status !== "WITHDRAWN"));
    return { items: [...items].sort((a, b) => b.updated_at.localeCompare(a.updated_at)), total: items.length };
  },

  async ingestManagementObservation(selectedProjectId: string, input: ObservationIngestionRequest): Promise<ObservationIngestion> {
    const extension = input.file.name.split(".").pop()?.toLowerCase() ?? "";
    const supported = new Set(["csv", "xlsx", "docx", "pdf", "txt", "json"]);
    if (!supported.has(extension)) throw new Error("支持 CSV、XLSX、DOCX、可提取文字的 PDF、TXT 和 JSON 文件。");
    if (input.file.size > 25 * 1024 * 1024) throw new Error("文件不能超过 25 MB。");
    const plainText = new Set(["csv", "txt", "json"]);
    const warnings: string[] = [];
    let extracted = "";
    if (plainText.has(extension)) extracted = await input.file.text();
    else warnings.push("当前为前端 Mock 模式，不执行 XLSX、DOCX 或 PDF 解析；连接真实后端后才会生成解析预览，原文件仍可在本 Mock 会话中下载。");
    const previewTruncated = extracted.length > 50_000;
    if (previewTruncated) {
      extracted = extracted.slice(0, 50_000);
      warnings.push("提取文本预览超过 50,000 字符，已截断显示；原文件仍保留。");
    }
    const content = extracted || "Mock 模式未生成文本预览；可下载原文件查看。";
    const observation = await mockApi.createManagementObservation(selectedProjectId, {
      kind: input.observation_kind,
      title: input.title?.trim() || input.file.name,
      content,
      occurred_at: input.occurred_at ?? null,
      idempotency_key: null,
    });
    const id = newId();
    const mediaType = input.file.type || mimeTypeForExtension(extension);
    const attachment: ObservationAttachment = {
      id, observation_id: observation.id, file_name: input.file.name, media_type: mediaType,
      size_bytes: input.file.size, content_sha256: "mock-only", created_at: now(),
    };
    managementObservationAttachments.set(observation.id, [attachment]);
    managementObservationAttachmentFiles.set(id, input.file);
    return {
      observation, attachment, parser_version: "mock-no-parser", extracted_character_count: extracted.length,
      preview_truncated: previewTruncated, warnings,
    };
  },

  async listManagementObservationAttachments(selectedProjectId: string, observationId: string): Promise<Page<ObservationAttachment>> {
    const observation = managementObservations.find((item) => item.project_id === selectedProjectId && item.id === observationId);
    if (!observation) throw new Error("找不到这条观察记录。");
    const items = managementObservationAttachments.get(observationId) ?? [];
    return { items: items.map((item) => ({ ...item })), total: items.length };
  },

  async downloadManagementObservationAttachment(selectedProjectId: string, observationId: string, attachmentId: string): Promise<{ blob: Blob; fileName: string }> {
    const attachments = await mockApi.listManagementObservationAttachments(selectedProjectId, observationId);
    const attachment = attachments.items.find((item) => item.id === attachmentId);
    const file = managementObservationAttachmentFiles.get(attachmentId);
    if (!attachment || !file) throw new Error("找不到原始附件。");
    return { blob: file, fileName: attachment.file_name };
  },

  async createManagementObservation(selectedProjectId: string, input: ObservationCreate): Promise<ObservationView> {
    const project = projects.find((item) => item.id === selectedProjectId);
    if (!project) throw new Error("找不到当前项目。");
    const timestamp = now();
    const value: ObservationView = {
      id: newId(), company_id: project.company_id, project_id: project.id, kind: input.kind,
      title: input.title.trim(), content: input.content.trim(), content_sha256: "mock-only",
      occurred_at: input.occurred_at ?? null, submitted_by: "local-owner", status: "ACTIVE", revision: 1,
      created_at: timestamp, updated_at: timestamp,
    };
    managementObservations.unshift(value);
    managementObservationHistory.set(value.id, [{ id: newId(), observation_id: value.id, revision: 1, snapshot: { ...value }, operation: "CREATED", actor_id: value.submitted_by, created_at: timestamp }]);
    return value;
  },

  async reviseManagementObservation(selectedProjectId: string, observationId: string, input: ObservationUpdate): Promise<ObservationView> {
    const value = managementObservations.find((item) => item.id === observationId && item.project_id === selectedProjectId);
    if (!value) throw new Error("找不到这条记录。");
    if (value.revision !== input.expected_revision) throw new Error("记录已被其他操作更新，请刷新后重试。");
    Object.assign(value, { kind: input.kind, title: input.title.trim(), content: input.content.trim(), occurred_at: input.occurred_at ?? null, revision: value.revision + 1, updated_at: now() });
    managementObservationHistory.get(value.id)?.unshift({ id: newId(), observation_id: value.id, revision: value.revision, snapshot: { ...value }, operation: "REVISED", actor_id: value.submitted_by, created_at: value.updated_at });
    return value;
  },

  async withdrawManagementObservation(selectedProjectId: string, observationId: string, expectedRevision: number): Promise<ObservationView> {
    const value = managementObservations.find((item) => item.id === observationId && item.project_id === selectedProjectId);
    if (!value) throw new Error("找不到这条记录。");
    if (value.revision !== expectedRevision) throw new Error("记录已被其他操作更新，请刷新后重试。");
    value.status = "WITHDRAWN";
    value.revision += 1;
    value.updated_at = now();
    managementObservationHistory.get(value.id)?.unshift({ id: newId(), observation_id: value.id, revision: value.revision, snapshot: { ...value }, operation: "WITHDRAWN", actor_id: value.submitted_by, created_at: value.updated_at });
    return value;
  },

  async getManagementObservationHistory(selectedProjectId: string, observationId: string): Promise<ObservationHistoryView> {
    const value = managementObservations.find((item) => item.id === observationId && item.project_id === selectedProjectId);
    if (!value) throw new Error("找不到这条记录。");
    const items = managementObservationHistory.get(observationId) ?? [];
    return { items: [...items], total: items.length };
  },

  async extractManagementObservation(selectedProjectId: string, observationId: string, input: ObservationExtractRequest): Promise<ObservationExtractionView> {
    const observation = managementObservations.find((item) => item.id === observationId && item.project_id === selectedProjectId);
    if (!observation) throw new Error("找不到这条记录。");
    const value: ObservationExtractionView = {
      id: newId(), observation_id: observation.id, source_revision: observation.revision,
      model_profile_id: input.model_profile_id, model_name: "演示模式", status: "FAILED", items: [], unresolved: [],
      error_code: "MOCK_MODEL_NOT_AVAILABLE", created_at: now(),
    };
    const history = managementObservationExtractions.get(observation.id) ?? [];
    history.unshift(value);
    managementObservationExtractions.set(observation.id, history);
    return value;
  },

  async listManagementObservationExtractions(_selectedProjectId: string, observationId: string): Promise<ObservationExtractionHistoryView> {
    const items = managementObservationExtractions.get(observationId) ?? [];
    return { items: [...items], total: items.length };
  },
  async listCompanies(): Promise<Page<Company>> {
    return { items: [...companies], total: companies.length };
  },

  async createCompany(input: CompanyCreate): Promise<Company> {
    const value: Company = {
      id: newId(),
      name: input.name,
      industry: input.industry ?? null,
      description: input.description ?? null,
      canonical_project_id: null,
      created_at: now(),
      updated_at: now(),
    };
    companies.push(value);
    const workspace: Project = {
      id: newId(),
      company_id: value.id,
      name: "企业总体投影",
      description: "公司统一的企业数字投影",
      status: "ACTIVE",
      is_primary: true,
      canonical_project_id: null,
      revision: 0,
      created_at: now(),
      updated_at: now(),
    };
    workspace.canonical_project_id = workspace.id;
    value.canonical_project_id = workspace.id;
    projects.push(workspace);
    seedActionsForProject(workspace);
    return value;
  },

  async listProjects(selectedCompanyId?: string, ensureWorkspace = false): Promise<Page<Project>> {
    const items = selectedCompanyId
      ? projects.filter((project) => project.company_id === selectedCompanyId)
      : [...projects];
    const ordered = [...items].sort((left, right) => Number(right.is_primary) - Number(left.is_primary));
    return { items: ensureWorkspace ? ordered.filter((project) => project.is_primary).slice(0, 1) : ordered, total: ensureWorkspace ? Math.min(ordered.filter((project) => project.is_primary).length, 1) : ordered.length };
  },

  async createProject(selectedCompanyId: string, input: ProjectCreate): Promise<Project> {
    const value: Project = {
      id: newId(),
      company_id: selectedCompanyId,
      name: input.name,
      description: input.description ?? null,
      status: "DRAFT",
      is_primary: false,
      canonical_project_id: null,
      revision: 0,
      created_at: now(),
      updated_at: now(),
    };
    projects.push(value);
    seedActionsForProject(value);
    return value;
  },

  async getExecutiveContext(selectedProjectId: string, preview: boolean): Promise<ExecutiveContext> {
    const project = projects.find((item) => item.id === selectedProjectId);
    if (!project) throw new Error("没有找到所选项目。");
    const company = companies.find((item) => item.id === project.company_id);
    if (!company) throw new Error("没有找到项目所属公司。");

    const entityIds = {
      company: "30000000-0000-4000-8000-000000000001",
      sales: "30000000-0000-4000-8000-000000000002",
      production: "30000000-0000-4000-8000-000000000003",
      order: "30000000-0000-4000-8000-000000000004",
    };
    const timestamps = { created_at: now(), updated_at: now() };

    return {
      company,
      project,
      publication: preview
        ? null
        : {
            id: "40000000-0000-4000-8000-000000000001",
            project_id: project.id,
            version: 1,
            label: "管理层正式版",
            notes: "契约型Mock发布版本",
            project_revision: project.revision,
            ontology_release_id: null,
            entity_count: 4,
            relation_count: 3,
            created_at: now(),
          },
      graph: {
        project_id: project.id,
        revision: project.revision,
        release_id: preview ? null : "40000000-0000-4000-8000-000000000001",
        scenario_id: null,
        entities: [
          {
            id: entityIds.company,
            project_id: project.id,
            type_key: "company",
            name: company.name,
            stable_key: "company.root",
            design_membership: "MODELED",
            properties: { industry: company.industry },
            viewpoint: "DESIGNED",
            evidence: [],
            status: preview ? "DRAFT" : "PUBLISHED",
            revision: project.revision,
            ...timestamps,
          },
          {
            id: entityIds.sales,
            project_id: project.id,
            type_key: "organization_unit",
            name: "销售部",
            stable_key: "department.sales",
            design_membership: "MODELED",
            properties: { purpose: "识别客户需求并取得订单" },
            viewpoint: "REPORTED",
            evidence: [],
            status: preview ? "DRAFT" : "PUBLISHED",
            revision: project.revision,
            ...timestamps,
          },
          {
            id: entityIds.production,
            project_id: project.id,
            type_key: "organization_unit",
            name: "生产部",
            stable_key: "department.production",
            design_membership: "MODELED",
            properties: { purpose: "按交付要求组织制造" },
            viewpoint: "REPORTED",
            evidence: [],
            status: preview ? "DRAFT" : "PUBLISHED",
            revision: project.revision,
            ...timestamps,
          },
          {
            id: entityIds.order,
            project_id: project.id,
            type_key: "business_object",
            name: "客户订单",
            stable_key: "business_object.customer_order",
            design_membership: "MODELED",
            properties: { lifecycle: "需求确认至交付" },
            viewpoint: "DESIGNED",
            evidence: [],
            status: preview ? "DRAFT" : "PUBLISHED",
            revision: project.revision,
            ...timestamps,
          },
        ],
        relations: [
          {
            id: "50000000-0000-4000-8000-000000000001",
            project_id: project.id,
            type_key: "contains",
            name: "组织包含",
            participants: [
              { role_key: "parent", entity_id: entityIds.company, entity_name: company.name, entity_type_key: "company", ordinal: 0 },
              { role_key: "child", entity_id: entityIds.sales, entity_name: "销售部", entity_type_key: "organization_unit", ordinal: 1 },
            ],
            properties: {}, viewpoint: "DESIGNED", evidence: [],
            status: preview ? "DRAFT" : "PUBLISHED", revision: project.revision, ...timestamps,
          },
          {
            id: "50000000-0000-4000-8000-000000000002",
            project_id: project.id,
            type_key: "contains",
            name: "组织包含",
            participants: [
              { role_key: "parent", entity_id: entityIds.company, entity_name: company.name, entity_type_key: "company", ordinal: 0 },
              { role_key: "child", entity_id: entityIds.production, entity_name: "生产部", entity_type_key: "organization_unit", ordinal: 1 },
            ],
            properties: {}, viewpoint: "DESIGNED", evidence: [],
            status: preview ? "DRAFT" : "PUBLISHED", revision: project.revision, ...timestamps,
          },
          {
            id: "50000000-0000-4000-8000-000000000003",
            project_id: project.id,
            type_key: "information_flow",
            name: "订单信息传递",
            participants: [
              { role_key: "sender", entity_id: entityIds.sales, entity_name: "销售部", entity_type_key: "organization_unit", ordinal: 0 },
              { role_key: "subject", entity_id: entityIds.order, entity_name: "客户订单", entity_type_key: "business_object", ordinal: 1 },
              { role_key: "receiver", entity_id: entityIds.production, entity_name: "生产部", entity_type_key: "organization_unit", ordinal: 2 },
            ],
            properties: { channel: "订单评审" }, viewpoint: "REPORTED", evidence: [],
            status: preview ? "DRAFT" : "PUBLISHED", revision: project.revision, ...timestamps,
          },
        ],
      },
      open_hypotheses: 2,
      active_scenarios: 1,
    };
  },

  async listActionDefinitions(selectedProjectId: string): Promise<Page<ActionDefinition>> {
    const items = actionDefinitions.filter((item) => item.project_id === selectedProjectId);
    return { items: [...items], total: items.length };
  },

  async createActionDefinition(selectedProjectId: string, input: ActionDefinitionCreate): Promise<ActionDefinition> {
    const value: ActionDefinition = {
      ...input,
      id: newId(),
      project_id: selectedProjectId,
      revision: 1,
      status: "DRAFT",
      parameters: input.parameters ?? [],
      preconditions: input.preconditions ?? [],
      effects: input.effects ?? [],
      created_at: now(),
      updated_at: now(),
    };
    actionDefinitions.push(value);
    return value;
  },

  async updateActionDefinition(selectedProjectId: string, actionId: string, input: ActionDefinitionUpdate): Promise<ActionDefinition> {
    const item = actionDefinitions.find((candidate) => candidate.project_id === selectedProjectId && candidate.id === actionId);
    if (!item) throw new Error("没有找到该动作定义。");
    if (item.revision !== input.expected_revision) throw new Error("动作定义已发生变化，请刷新后重试。");
    Object.assign(item, {
      ...(input.name === undefined ? {} : { name: input.name ?? item.name }),
      ...(input.description === undefined ? {} : { description: input.description }),
      ...(input.enabled === undefined ? {} : { enabled: input.enabled ?? item.enabled }),
      ...(input.execution_mode === undefined ? {} : { execution_mode: input.execution_mode ?? item.execution_mode }),
      ...(input.parameters === undefined ? {} : { parameters: input.parameters ?? [] }),
      ...(input.preconditions === undefined ? {} : { preconditions: input.preconditions ?? [] }),
      ...(input.effects === undefined ? {} : { effects: input.effects ?? [] }),
      ...(input.require_approval === undefined ? {} : { require_approval: input.require_approval ?? item.require_approval }),
      ...(input.risk_level === undefined ? {} : { risk_level: input.risk_level ?? item.risk_level }),
      ...(input.target_type_key === undefined ? {} : { target_type_key: input.target_type_key }),
      revision: item.revision + 1,
      updated_at: now(),
    });
    return { ...item };
  },

  async validateActionDefinition(selectedProjectId: string, actionId: string): Promise<ActionDefinition> {
    const item = actionDefinitions.find((candidate) => candidate.project_id === selectedProjectId && candidate.id === actionId);
    if (!item) throw new Error("没有找到该动作定义。");
    item.status = "VALIDATED";
    item.updated_at = now();
    return { ...item };
  },

  async listActionInvocations(selectedProjectId: string, status?: ActionInvocationStatus): Promise<Page<ActionInvocation>> {
    const items = actionInvocations.filter((item) => item.project_id === selectedProjectId && (!status || item.status === status));
    return { items: [...items], total: items.length };
  },

  async createActionInvocation(selectedProjectId: string, input: ActionInvocationCreate): Promise<ActionInvocation> {
    const definition = actionDefinitions.find((item) => item.project_id === selectedProjectId && item.id === input.action_definition_id);
    if (!definition) throw new Error("没有找到要调用的动作定义。");
    const value: ActionInvocation = {
      id: newId(),
      project_id: selectedProjectId,
      action_definition_id: definition.id,
      action_key: definition.key,
      action_name: definition.name,
      source_agent_run_id: input.source_agent_run_id ?? null,
      idempotency_key: input.idempotency_key ?? newId(),
      requested_by: input.requested_by ?? "management",
      target_entity_ids: input.target_entity_ids ?? [],
      input: input.input ?? {},
      status: "DRAFT",
      risk_level: definition.risk_level,
      require_approval: definition.require_approval,
      preflight: null,
      result: null,
      error: null,
      approved_by: null,
      approved_at: null,
      started_at: null,
      finished_at: null,
      created_at: now(),
      updated_at: now(),
    };
    actionInvocations.unshift(value);
    addLog(selectedProjectId, value.id, "CREATED", value.requested_by, null, value.status);
    return { ...value };
  },

  async getActionInvocation(selectedProjectId: string, invocationId: string): Promise<ActionInvocation> {
    const item = actionInvocations.find((candidate) => candidate.project_id === selectedProjectId && candidate.id === invocationId);
    if (!item) throw new Error("没有找到该动作调用。");
    return { ...item };
  },

  async dryRunAction(selectedProjectId: string, invocationId: string): Promise<ActionInvocation> {
    return transitionInvocation(selectedProjectId, invocationId, "DRY_RUN_COMPLETED", "DRY_RUN", { preflight: { ok: true, changes: [], note: "Mock 预演未写入企业投影。" } });
  },
  async approveAction(selectedProjectId: string, invocationId: string, input: ActionApprovalRequest): Promise<ActionInvocation> {
    return transitionInvocation(selectedProjectId, invocationId, "APPROVED", "APPROVED", { approved_by: input.approved_by, approved_at: now() });
  },
  async executeAction(selectedProjectId: string, invocationId: string): Promise<ActionInvocation> {
    const value = transitionInvocation(selectedProjectId, invocationId, "SUCCEEDED", "EXECUTED", { result: { applied: true, message: "Mock 动作已完成。" }, started_at: now(), finished_at: now() });
    return value;
  },
  async cancelAction(selectedProjectId: string, invocationId: string): Promise<ActionInvocation> {
    return transitionInvocation(selectedProjectId, invocationId, "CANCELLED", "CANCELLED");
  },
  async retryAction(selectedProjectId: string, invocationId: string, input: ActionRetryRequest): Promise<ActionInvocation> {
    const original = actionInvocations.find((candidate) => candidate.project_id === selectedProjectId && candidate.id === invocationId);
    if (!original) throw new Error("没有找到要重试的动作调用。");
    return mockApi.createActionInvocation(selectedProjectId, { action_definition_id: original.action_definition_id, input: original.input, target_entity_ids: original.target_entity_ids, requested_by: input.requested_by, idempotency_key: input.idempotency_key ?? null });
  },
  async rollbackAction(selectedProjectId: string, invocationId: string): Promise<ActionInvocation> {
    return transitionInvocation(selectedProjectId, invocationId, "ROLLED_BACK", "ROLLED_BACK");
  },

  async listActionLogs(selectedProjectId: string, invocationId?: string): Promise<Page<ActionLog>> {
    const items = actionLogs.filter((item) => item.project_id === selectedProjectId && (!invocationId || item.invocation_id === invocationId));
    return { items: [...items], total: items.length };
  },

  async listActionObservations(selectedProjectId: string, invocationId: string): Promise<Page<ActionObservation>> {
    const items = actionObservations.filter((item) => item.project_id === selectedProjectId && item.invocation_id === invocationId);
    return { items: [...items], total: items.length };
  },

  async addActionObservation(selectedProjectId: string, invocationId: string, input: ActionObservationCreate): Promise<ActionObservation> {
    const value: ActionObservation = { ...input, id: newId(), project_id: selectedProjectId, invocation_id: invocationId, metric_definition_id: null, metric_observation_id: null, observed_at: input.observed_at ?? now(), created_at: now() };
    actionObservations.unshift(value);
    return value;
  },
};

function transitionInvocation(selectedProjectId: string, invocationId: string, status: ActionInvocationStatus, eventType: string, patch: Partial<ActionInvocation> = {}) {
  const item = actionInvocations.find((candidate) => candidate.project_id === selectedProjectId && candidate.id === invocationId);
  if (!item) throw new Error("没有找到该动作调用。");
  const previous = item.status;
  Object.assign(item, { ...patch, status, updated_at: now() });
  addLog(selectedProjectId, invocationId, eventType, "management", previous, status);
  return { ...item };
}

function findManagementAction(selectedProjectId: string, actionId: string): ManagementAction {
  const action = managementActions.find((item) => item.project_id === selectedProjectId && item.id === actionId);
  if (!action) throw new Error("找不到当前项目中的现实管理行动。");
  return action;
}

function assertManagementActionRevision(action: ManagementAction, expectedRevision: number) {
  if (action.revision !== expectedRevision) throw new Error("管理行动版本已变化，请刷新后重新检查再操作。");
}

function appendManagementActionEvent(
  action: ManagementAction,
  eventType: ManagementActionEvent["event_type"],
  reason: string | null,
  message: string | null,
  details: Record<string, unknown> = {},
  fromStatus: ManagementAction["status"] | null = null,
  toStatus: ManagementAction["status"] | null = null,
  bumpRevision = true,
): ManagementActionEvent {
  const timestamp = now();
  if (bumpRevision) {
    action.revision += 1;
    action.updated_at = timestamp;
  }
  const event: ManagementActionEvent = {
    id: newId(), action_id: action.id, company_id: action.company_id, project_id: action.project_id,
    actor_id: "local-owner", created_at: timestamp, details: { ...details }, event_type: eventType,
    from_status: fromStatus, message, reason, revision: action.revision, to_status: toStatus,
  };
  const history = managementActionHistory.get(action.id) ?? [];
  history.push(event);
  managementActionHistory.set(action.id, history);
  return event;
}

function appendManagementActionMessageEvent(
  selectedProjectId: string,
  actionId: string,
  input: ManagementActionEventCreate,
  eventType: "PROGRESS" | "OUTCOME",
): ManagementActionEvent {
  const action = findManagementAction(selectedProjectId, actionId);
  assertManagementActionRevision(action, input.expected_revision);
  if (action.status === "CANCELLED") throw new Error("已取消的管理行动不能追加进展或结果。");
  return appendManagementActionEvent(action, eventType, input.reason ?? null, input.message, input.details ?? {});
}

function emptyPotentialHistory(): PotentialHistory { return { versions: [], audit: [] }; }

function findPotentialRecord(selectedProjectId: string, recordId: string): PotentialRecord {
  const record = potentialRecords.find((item) => item.id === recordId && item.project_id === selectedProjectId);
  if (!record) throw new Error("找不到该项目中的潜在认识记录。");
  return record;
}

function assertPotentialVersion(record: PotentialRecord, expectedVersion: number) {
  if (record.version !== expectedVersion) throw new Error("记录版本已变化，请刷新后重新检查再操作。");
  if (record.human_status === "WITHDRAWN") throw new Error("已撤回的记录不能再次修改或改变状态。");
}

function changePotentialStatus(selectedProjectId: string, recordId: string, input: PotentialRecordStatusRequest, nextStatus: PotentialRecord["human_status"]): PotentialRecord {
  const record = findPotentialRecord(selectedProjectId, recordId);
  assertPotentialVersion(record, input.expected_version);
  if (!input.reason.trim()) throw new Error("请填写本次人工判断的理由。");
  if (nextStatus === "ACCEPTED" && record.human_status !== "REJECTED") throw new Error("只有已拒绝的记录可以重新接受。");
  if (nextStatus === "REJECTED" && record.human_status !== "ACCEPTED") throw new Error("只有已纳入潜在库的记录可以拒绝。");
  const beforeHash = record.payload_hash;
  const operation: PotentialOperation = nextStatus;
  Object.assign(record, { human_status: nextStatus, version: record.version + 1, updated_at: now(), payload_hash: mockPotentialHash() });
  appendPotentialHistory(record, operation, "local-owner", input.reason.trim(), beforeHash);
  return { ...record };
}

function appendPotentialHistory(record: PotentialRecord, operation: PotentialOperation, actorId: string, reason: string | null, beforeHash: string | null) {
  const history = potentialRecordHistory.get(record.id) ?? emptyPotentialHistory();
  const timestamp = now();
  const snapshot = JSON.parse(JSON.stringify(record)) as Record<string, unknown>;
  const version: PotentialVersion = {
    id: newId(), record_id: record.id, version: record.version, payload_hash: record.payload_hash,
    snapshot, operation, actor_id: actorId, reason, created_at: timestamp,
  };
  const audit: PotentialAudit = {
    id: newId(), record_id: record.id, company_id: record.company_id, project_id: record.project_id,
    version: record.version, operation, actor_id: actorId, before_hash: beforeHash,
    after_hash: record.payload_hash, reason, created_at: timestamp,
  };
  history.versions.push(version);
  history.audit.push(audit);
  potentialRecordHistory.set(record.id, history);
}

function mockPotentialHash() {
  const id = newId().replaceAll("-", "");
  return `${id}${id}`;
}

function mimeTypeForExtension(extension: string) {
  const types: Record<string, string> = {
    csv: "text/csv", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", pdf: "application/pdf",
    txt: "text/plain", json: "application/json",
  };
  return types[extension] ?? "application/octet-stream";
}
