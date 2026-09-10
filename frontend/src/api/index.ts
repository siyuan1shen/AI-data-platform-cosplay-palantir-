import { appConfig } from "../app/config";
import { download, request } from "./http";
import { mockApi } from "./mock";
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
  AgentMessage,
  AgentMessageAccepted,
  AgentMessageCreate,
  AgentRun,
  AgentThread,
  AgentThreadCreate,
  AgentThreadUpdate,
  ChangePreview,
  ChangeSet,
  ChangeSetCreate,
  Entity,
  EntityCreate,
  EntityUpdate,
  EvidenceFragment,
  ExportJob,
  ExportRequest,
  Graph,
  GraphQuery,
  Hypothesis,
  HypothesisCreate,
  HypothesisFeedback,
  HypothesisFeedbackCreate,
  ImportConfirmRequest,
  ImportKind,
  ImportPreview,
  ImportResult,
  LearningCase,
  LearningCaseCatalog,
  LearningCaseCreate,
  LearningCaseDraftFromActionCreate,
  LearningCaseDraftFromScenarioCreate,
  LearningCaseUpdate,
  ManagementAnalysisRun,
  ManagementInsight,
  ManagementInsightUpdate,
  ManagementIssue,
  ManagementIssueFeedback,
  ManagementIssueReopen,
  ManagementSignal,
  MaterializationRun,
  MetricDefinition,
  MetricDefinitionCreate,
  MetricDefinitionUpdate,
  MetricObservation,
  MetricObservationCreate,
  MeetingRecord,
  MeetingRecordCreate,
  MeetingRecordUpdate,
  DesignTradeoff,
  InformationRequest,
  InformationRequestUpdate,
  CausalHypothesis,
  ModelProfile,
  ModelProfileCreate,
  ModelProfileUpdate,
  ModelProfileTest,
  EvaluationExecuteCreate,
  EvaluationRun,
  EvaluationExecution,
  EvaluationResult,
  EvaluationSuite,
  EvaluationSuiteCreate,
  EvaluationSuiteUpdate,
  EvaluationCase,
  EvaluationCaseCreate,
  OntologyRelease,
  OntologyReleaseCreate,
  OntologyType,
  OntologyTypeCreate,
  Publication,
  PublicationCreate,
  Relation,
  RelationCreate,
  RelationUpdate,
  RevisionRequest,
  Scenario,
  ScenarioCompareRequest,
  ScenarioComparison,
  ScenarioCreate,
  ScenarioDiff,
  ScenarioRevisionRequest,
  ScenarioUpdate,
  SemanticMapping,
  SemanticMappingCommand,
  SemanticMappingCreate,
  SemanticMappingSuggestionPage,
  SemanticRelationMapping,
  SemanticRelationMappingCommand,
  SemanticRelationMappingCreate,
  SourceDocument,
  SourceIdentity,
  SourceIdentityBind,
  ObservationAssertion,
  ObservationConflict,
  ObservationConflictResolve,
  RawBatch,
  RawRecord,
  RestoreConfirmRequest,
  RestorePreview,
  RestoreResult,
  SemanticDataset,
  SemanticDatasetCreate,
  SemanticDatasetExport,
  SemanticDatasetQuery,
  SemanticDatasetResult,
  SemanticDatasetUpdate,
  SourceSystem,
  SourceAsset,
  SourceAssetUpdate,
  SourceConnectorExtract,
  SourceConnectorExtractRequest,
  SourceConnectorSync,
  SourceConnectorSyncRequest,
  SourceSystemCreate,
  SourceSystemUpdate,
  SourceSystemTest,
  Company,
  CompanyCreate,
  ExecutiveContext,
  Page,
  Project,
  ProjectCreate,
} from "./types";

const realApi = {
  listCompanies: () => request<Page<Company>>("/api/v3/companies"),
  createCompany: (input: CompanyCreate) =>
    request<Company>("/api/v3/companies", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  listProjects: (companyId?: string) =>
    request<Page<Project>>(
      `/api/v3/projects${companyId ? `?company_id=${encodeURIComponent(companyId)}` : ""}`,
    ),
  createProject: (companyId: string, input: ProjectCreate) =>
    request<Project>(`/api/v3/companies/${encodeURIComponent(companyId)}/projects`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  getExecutiveContext: (projectId: string, preview: boolean) =>
    request<ExecutiveContext>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/executive/context?preview=${preview}`,
    ),
  queryGraph: (projectId: string, input: GraphQuery) =>
    request<Graph>(`/api/v3/projects/${encodeURIComponent(projectId)}/graph/query`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  listActionDefinitions: (projectId: string) =>
    request<Page<ActionDefinition>>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-definitions`,
    ),
  createActionDefinition: (projectId: string, input: ActionDefinitionCreate) =>
    request<ActionDefinition>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-definitions`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  updateActionDefinition: (projectId: string, actionId: string, input: ActionDefinitionUpdate) =>
    request<ActionDefinition>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-definitions/${encodeURIComponent(actionId)}`,
      { method: "PATCH", body: JSON.stringify(input) },
    ),
  validateActionDefinition: (projectId: string, actionId: string) =>
    request<ActionDefinition>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-definitions/${encodeURIComponent(actionId)}/validate`,
      { method: "POST" },
    ),
  listActionInvocations: (projectId: string, status?: ActionInvocationStatus) =>
    request<Page<ActionInvocation>>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-invocations${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
  createActionInvocation: (projectId: string, input: ActionInvocationCreate) =>
    request<ActionInvocation>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-invocations`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  getActionInvocation: (projectId: string, invocationId: string) =>
    request<ActionInvocation>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-invocations/${encodeURIComponent(invocationId)}`,
    ),
  dryRunAction: (projectId: string, invocationId: string) =>
    actionInvocationCommand(projectId, invocationId, "dry-run"),
  approveAction: (projectId: string, invocationId: string, input: ActionApprovalRequest) =>
    actionInvocationCommand(projectId, invocationId, "approve", input),
  executeAction: (projectId: string, invocationId: string) =>
    actionInvocationCommand(projectId, invocationId, "execute"),
  cancelAction: (projectId: string, invocationId: string) =>
    actionInvocationCommand(projectId, invocationId, "cancel"),
  retryAction: (projectId: string, invocationId: string, input: ActionRetryRequest) =>
    actionInvocationCommand(projectId, invocationId, "retry", input),
  rollbackAction: (projectId: string, invocationId: string) =>
    actionInvocationCommand(projectId, invocationId, "rollback"),
  listActionLogs: (projectId: string, invocationId?: string) =>
    request<Page<ActionLog>>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-logs${invocationId ? `?invocation_id=${encodeURIComponent(invocationId)}` : ""}`,
    ),
  listActionObservations: (projectId: string, invocationId: string) =>
    request<Page<ActionObservation>>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-invocations/${encodeURIComponent(invocationId)}/observations`,
    ),
  addActionObservation: (projectId: string, invocationId: string, input: ActionObservationCreate) =>
    request<ActionObservation>(
      `/api/v3/projects/${encodeURIComponent(projectId)}/action-invocations/${encodeURIComponent(invocationId)}/observations`,
      { method: "POST", body: JSON.stringify(input) },
    ),
  previewImport: (projectId: string, file: File, kind: ImportKind) => {
    const body = new FormData();
    body.append("file", file);
    body.append("kind", kind);
    return request<ImportPreview>(`/api/v3/projects/${encodeURIComponent(projectId)}/imports/preview`, { method: "POST", body });
  },
  confirmImport: (projectId: string, input: ImportConfirmRequest) =>
    request<ImportResult>(`/api/v3/projects/${encodeURIComponent(projectId)}/imports/confirm`, { method: "POST", body: JSON.stringify(input) }),
  listDocuments: (projectId: string) => request<Page<SourceDocument>>(`/api/v3/projects/${encodeURIComponent(projectId)}/documents`),
  listFragments: (projectId: string, documentId: string) => request<Page<EvidenceFragment>>(`/api/v3/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}/fragments`),
  listAgentThreads: (projectId: string, includeTrashed = false) => request<Page<AgentThread>>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-threads?include_trashed=${includeTrashed}`),
  createAgentThread: (projectId: string, input: AgentThreadCreate) => request<AgentThread>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-threads`, { method: "POST", body: JSON.stringify(input) }),
  updateAgentThread: (projectId: string, threadId: string, input: AgentThreadUpdate) => request<AgentThread>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-threads/${encodeURIComponent(threadId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  trashAgentThread: (projectId: string, threadId: string) => request<AgentThread>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-threads/${encodeURIComponent(threadId)}`, { method: "DELETE" }),
  listAgentMessages: (projectId: string, threadId: string) => request<Page<AgentMessage>>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-threads/${encodeURIComponent(threadId)}/messages`),
  sendAgentMessage: (projectId: string, threadId: string, input: AgentMessageCreate) => request<AgentMessageAccepted>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-threads/${encodeURIComponent(threadId)}/messages`, { method: "POST", body: JSON.stringify(input) }),
  listAgentRuns: (projectId: string) => request<Page<AgentRun>>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-runs`),
  getAgentRun: (projectId: string, runId: string) => request<AgentRun>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-runs/${encodeURIComponent(runId)}`),
  executeAgentRun: (projectId: string, runId: string) => request<AgentRun>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-runs/${encodeURIComponent(runId)}/execute`, { method: "POST" }),
  continueAgentRun: (projectId: string, runId: string) => request<AgentRun>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-runs/${encodeURIComponent(runId)}/continue`, { method: "POST" }),
  cancelAgentRun: (projectId: string, runId: string) => request<AgentRun>(`/api/v3/projects/${encodeURIComponent(projectId)}/agent-runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),
  listModelProfiles: () => request<Page<ModelProfile>>("/api/v3/model-profiles"),
  createModelProfile: (input: ModelProfileCreate) => request<ModelProfile>("/api/v3/model-profiles", { method: "POST", body: JSON.stringify(input) }),
  updateModelProfile: (profileId: string, input: ModelProfileUpdate) => request<ModelProfile>(`/api/v3/model-profiles/${encodeURIComponent(profileId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  testModelProfile: (profileId: string) => request<ModelProfileTest>(`/api/v3/model-profiles/${encodeURIComponent(profileId)}/test`, { method: "POST" }),
  deleteModelProfile: (profileId: string) => request<void>(`/api/v3/model-profiles/${encodeURIComponent(profileId)}`, { method: "DELETE" }),
  executeEvaluationSuite: (projectId: string, suiteId: string, input: EvaluationExecuteCreate) => request<EvaluationRun>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/suites/${encodeURIComponent(suiteId)}/execute`, { method: "POST", body: JSON.stringify(input) }),
  scheduleEvaluationSuite: (projectId: string, suiteId: string, input: EvaluationExecuteCreate) => request<EvaluationExecution>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/suites/${encodeURIComponent(suiteId)}/execute-async`, { method: "POST", body: JSON.stringify(input) }),
  getEvaluationExecution: (projectId: string, executionId: string) => request<EvaluationExecution>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/executions/${encodeURIComponent(executionId)}`),
  listEvaluationRuns: (projectId: string, suiteId?: string) => request<Page<EvaluationRun>>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/runs${suiteId ? `?suite_id=${encodeURIComponent(suiteId)}` : ""}`),
  listEvaluationResults: (projectId: string, runId: string) => request<Page<EvaluationResult>>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/runs/${encodeURIComponent(runId)}/results`),
  listEvaluationSuites: (projectId: string) => request<Page<EvaluationSuite>>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/suites`),
  createEvaluationSuite: (projectId: string, input: EvaluationSuiteCreate) => request<EvaluationSuite>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/suites`, { method: "POST", body: JSON.stringify(input) }),
  updateEvaluationSuite: (projectId: string, suiteId: string, input: EvaluationSuiteUpdate) => request<EvaluationSuite>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/suites/${encodeURIComponent(suiteId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  listEvaluationCases: (projectId: string, suiteId: string) => request<Page<EvaluationCase>>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/suites/${encodeURIComponent(suiteId)}/cases`),
  createEvaluationCase: (projectId: string, suiteId: string, input: EvaluationCaseCreate) => request<EvaluationCase>(`/api/v3/projects/${encodeURIComponent(projectId)}/evaluations/suites/${encodeURIComponent(suiteId)}/cases`, { method: "POST", body: JSON.stringify(input) }),
  listOntologyTypes: (projectId: string) => request<Page<OntologyType>>(`/api/v3/projects/${encodeURIComponent(projectId)}/ontology/types`),
  createOntologyType: (projectId: string, input: OntologyTypeCreate) => request<OntologyType>(`/api/v3/projects/${encodeURIComponent(projectId)}/ontology/types`, { method: "POST", body: JSON.stringify(input) }),
  installDefaultOntology: (projectId: string) => request<OntologyType[]>(`/api/v3/projects/${encodeURIComponent(projectId)}/ontology/default-pack`, { method: "POST" }),
  listOntologyReleases: (projectId: string) => request<Page<OntologyRelease>>(`/api/v3/projects/${encodeURIComponent(projectId)}/ontology/releases`),
  releaseOntology: (projectId: string, input: OntologyReleaseCreate) => request<OntologyRelease>(`/api/v3/projects/${encodeURIComponent(projectId)}/ontology/releases`, { method: "POST", body: JSON.stringify(input) }),
  listEntities: (projectId: string, includeRetired = false, includeUnmodeled = false) => request<Page<Entity>>(`/api/v3/projects/${encodeURIComponent(projectId)}/entities?include_retired=${includeRetired}&include_unmodeled=${includeUnmodeled}`),
  createEntity: (projectId: string, input: EntityCreate) => request<Entity>(`/api/v3/projects/${encodeURIComponent(projectId)}/entities`, { method: "POST", body: JSON.stringify(input) }),
  updateEntity: (projectId: string, entityId: string, input: EntityUpdate) => request<Entity>(`/api/v3/projects/${encodeURIComponent(projectId)}/entities/${encodeURIComponent(entityId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  retireEntity: (projectId: string, entityId: string, input: RevisionRequest) => request<Entity>(`/api/v3/projects/${encodeURIComponent(projectId)}/entities/${encodeURIComponent(entityId)}/retire`, { method: "POST", body: JSON.stringify(input) }),
  listRelations: (projectId: string, includeRetired = false) => request<Page<Relation>>(`/api/v3/projects/${encodeURIComponent(projectId)}/relations?include_retired=${includeRetired}`),
  createRelation: (projectId: string, input: RelationCreate) => request<Relation>(`/api/v3/projects/${encodeURIComponent(projectId)}/relations`, { method: "POST", body: JSON.stringify(input) }),
  updateRelation: (projectId: string, relationId: string, input: RelationUpdate) => request<Relation>(`/api/v3/projects/${encodeURIComponent(projectId)}/relations/${encodeURIComponent(relationId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  retireRelation: (projectId: string, relationId: string, input: RevisionRequest) => request<Relation>(`/api/v3/projects/${encodeURIComponent(projectId)}/relations/${encodeURIComponent(relationId)}/retire`, { method: "POST", body: JSON.stringify(input) }),
  listChangeSets: (projectId: string) => request<Page<ChangeSet>>(`/api/v3/projects/${encodeURIComponent(projectId)}/change-sets`),
  createChangeSet: (projectId: string, input: ChangeSetCreate) => request<ChangeSet>(`/api/v3/projects/${encodeURIComponent(projectId)}/change-sets`, { method: "POST", body: JSON.stringify(input) }),
  previewChangeSet: (projectId: string, changeSetId: string) => request<ChangePreview>(`/api/v3/projects/${encodeURIComponent(projectId)}/change-sets/${encodeURIComponent(changeSetId)}/preview`),
  validateChangeSet: (projectId: string, changeSetId: string) => changeSetCommand(projectId, changeSetId, "validate"),
  approveChangeSet: (projectId: string, changeSetId: string) => changeSetCommand(projectId, changeSetId, "approve"),
  applyChangeSet: (projectId: string, changeSetId: string) => changeSetCommand(projectId, changeSetId, "apply"),
  listSourceSystems: (projectId: string) => request<Page<SourceSystem>>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-systems`),
  createSourceSystem: (projectId: string, input: SourceSystemCreate) => request<SourceSystem>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-systems`, { method: "POST", body: JSON.stringify(input) }),
  updateSourceSystem: (projectId: string, sourceId: string, input: SourceSystemUpdate) => request<SourceSystem>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-systems/${encodeURIComponent(sourceId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  testSourceSystem: (projectId: string, sourceId: string) => request<SourceSystemTest>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-systems/${encodeURIComponent(sourceId)}/test`, { method: "POST" }),
  listSourceAssets: (projectId: string, sourceId: string) => request<Page<SourceAsset>>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-systems/${encodeURIComponent(sourceId)}/assets`),
  updateSourceAsset: (projectId: string, sourceId: string, assetId: string, input: SourceAssetUpdate) => request<SourceAsset>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-systems/${encodeURIComponent(sourceId)}/assets/${encodeURIComponent(assetId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  previewSourceExtract: (projectId: string, sourceId: string, input: SourceConnectorExtractRequest) => request<SourceConnectorExtract>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-systems/${encodeURIComponent(sourceId)}/extract/preview`, { method: "POST", body: JSON.stringify(input) }),
  syncSourceSystem: (projectId: string, sourceId: string, input: SourceConnectorSyncRequest) => request<SourceConnectorSync>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-systems/${encodeURIComponent(sourceId)}/sync`, { method: "POST", body: JSON.stringify(input) }),
  previewSourceSystemImport: (projectId: string, sourceId: string, file: File, kind: ImportKind) => {
    const body = new FormData();
    body.append("file", file);
    body.append("kind", kind);
    return request<ImportPreview>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-systems/${encodeURIComponent(sourceId)}/imports/preview`, { method: "POST", body });
  },
  listSemanticMappings: (projectId: string) => request<Page<SemanticMapping>>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-mappings`),
  suggestSemanticMappings: (projectId: string, input: { target_type_key: string; source_system_id?: string; source_asset?: string; include_existing?: boolean }) => {
    const params = new URLSearchParams({ target_type_key: input.target_type_key });
    if (input.source_system_id) params.set("source_system_id", input.source_system_id);
    if (input.source_asset) params.set("source_asset", input.source_asset);
    if (input.include_existing) params.set("include_existing", "true");
    return request<SemanticMappingSuggestionPage>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-mapping-suggestions?${params.toString()}`);
  },
  createSemanticMapping: (projectId: string, input: SemanticMappingCreate) => request<SemanticMapping>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-mappings`, { method: "POST", body: JSON.stringify(input) }),
  validateSemanticMapping: (projectId: string, mappingId: string, input: SemanticMappingCommand) => request<SemanticMapping>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-mappings/${encodeURIComponent(mappingId)}/validate`, { method: "POST", body: JSON.stringify(input) }),
  approveSemanticMapping: (projectId: string, mappingId: string, input: SemanticMappingCommand) => request<SemanticMapping>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-mappings/${encodeURIComponent(mappingId)}/approve`, { method: "POST", body: JSON.stringify(input) }),
  disableSemanticMapping: (projectId: string, mappingId: string, input: SemanticMappingCommand) => request<SemanticMapping>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-mappings/${encodeURIComponent(mappingId)}/disable`, { method: "POST", body: JSON.stringify(input) }),
  listSemanticRelationMappings: (projectId: string) => request<Page<SemanticRelationMapping>>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-relation-mappings`),
  createSemanticRelationMapping: (projectId: string, input: SemanticRelationMappingCreate) => request<SemanticRelationMapping>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-relation-mappings`, { method: "POST", body: JSON.stringify(input) }),
  validateSemanticRelationMapping: (projectId: string, mappingId: string, input: SemanticRelationMappingCommand) => request<SemanticRelationMapping>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-relation-mappings/${encodeURIComponent(mappingId)}/validate`, { method: "POST", body: JSON.stringify(input) }),
  approveSemanticRelationMapping: (projectId: string, mappingId: string, input: SemanticRelationMappingCommand) => request<SemanticRelationMapping>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-relation-mappings/${encodeURIComponent(mappingId)}/approve`, { method: "POST", body: JSON.stringify(input) }),
  disableSemanticRelationMapping: (projectId: string, mappingId: string, input: SemanticRelationMappingCommand) => request<SemanticRelationMapping>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-relation-mappings/${encodeURIComponent(mappingId)}/disable`, { method: "POST", body: JSON.stringify(input) }),
  listSourceIdentities: (projectId: string, sourceSystemId?: string) => request<Page<SourceIdentity>>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-identities${sourceSystemId ? `?source_system_id=${encodeURIComponent(sourceSystemId)}` : ""}`),
  bindSourceIdentity: (projectId: string, identityId: string, input: SourceIdentityBind) => request<SourceIdentity>(`/api/v3/projects/${encodeURIComponent(projectId)}/source-identities/${encodeURIComponent(identityId)}/binding`, { method: "PATCH", body: JSON.stringify(input) }),
  listObservationAssertions: (projectId: string, entityId?: string) => request<Page<ObservationAssertion>>(`/api/v3/projects/${encodeURIComponent(projectId)}/observation-assertions${entityId ? `?entity_id=${encodeURIComponent(entityId)}` : ""}`),
  listRawBatches: (projectId: string) => request<Page<RawBatch>>(`/api/v3/projects/${encodeURIComponent(projectId)}/raw-batches`),
  listRawRecords: (projectId: string, batchId: string) => request<Page<RawRecord>>(`/api/v3/projects/${encodeURIComponent(projectId)}/raw-batches/${encodeURIComponent(batchId)}/records`),
  listMaterializationRuns: (projectId: string) => request<Page<MaterializationRun>>(`/api/v3/projects/${encodeURIComponent(projectId)}/materialization-runs`),
  materializeRawBatch: (projectId: string, batchId: string) => request<MaterializationRun>(`/api/v3/projects/${encodeURIComponent(projectId)}/raw-batches/${encodeURIComponent(batchId)}/materialize`, { method: "POST" }),
  listObservationConflicts: (projectId: string) => request<Page<ObservationConflict>>(`/api/v3/projects/${encodeURIComponent(projectId)}/observation-conflicts`),
  resolveObservationConflict: (projectId: string, conflictId: string, input: ObservationConflictResolve) => request<ObservationConflict>(`/api/v3/projects/${encodeURIComponent(projectId)}/observation-conflicts/${encodeURIComponent(conflictId)}/resolve`, { method: "POST", body: JSON.stringify(input) }),
  listSemanticDatasets: (projectId: string) => request<Page<SemanticDataset>>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-datasets`),
  createSemanticDataset: (projectId: string, input: SemanticDatasetCreate) => request<SemanticDataset>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-datasets`, { method: "POST", body: JSON.stringify(input) }),
  updateSemanticDataset: (projectId: string, datasetId: string, input: SemanticDatasetUpdate) => request<SemanticDataset>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-datasets/${encodeURIComponent(datasetId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  querySemanticDataset: (projectId: string, datasetId: string, input: SemanticDatasetQuery) => request<SemanticDatasetResult>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-datasets/${encodeURIComponent(datasetId)}/query`, { method: "POST", body: JSON.stringify(input) }),
  exportSemanticDataset: (projectId: string, datasetId: string, input: SemanticDatasetExport) => request<ExportJob>(`/api/v3/projects/${encodeURIComponent(projectId)}/semantic-datasets/${encodeURIComponent(datasetId)}/export`, { method: "POST", body: JSON.stringify(input) }),
  listPublications: (projectId: string) => request<Page<Publication>>(`/api/v3/projects/${encodeURIComponent(projectId)}/publications`),
  publishProject: (projectId: string, input: PublicationCreate) => request<Publication>(`/api/v3/projects/${encodeURIComponent(projectId)}/publications`, { method: "POST", body: JSON.stringify(input) }),
  listHypotheses: (projectId: string) => request<Page<Hypothesis>>(`/api/v3/projects/${encodeURIComponent(projectId)}/hypotheses`),
  createHypothesis: (projectId: string, input: HypothesisCreate) => request<Hypothesis>(`/api/v3/projects/${encodeURIComponent(projectId)}/hypotheses`, { method: "POST", body: JSON.stringify(input) }),
  addHypothesisFeedback: (projectId: string, hypothesisId: string, input: HypothesisFeedbackCreate) => request<HypothesisFeedback>(`/api/v3/projects/${encodeURIComponent(projectId)}/hypotheses/${encodeURIComponent(hypothesisId)}/feedback`, { method: "POST", body: JSON.stringify(input) }),
  listScenarios: (projectId: string) => request<Page<Scenario>>(`/api/v3/projects/${encodeURIComponent(projectId)}/scenarios`),
  createScenario: (projectId: string, input: ScenarioCreate) => request<Scenario>(`/api/v3/projects/${encodeURIComponent(projectId)}/scenarios`, { method: "POST", body: JSON.stringify(input) }),
  updateScenario: (projectId: string, scenarioId: string, input: ScenarioUpdate) => request<Scenario>(`/api/v3/projects/${encodeURIComponent(projectId)}/scenarios/${encodeURIComponent(scenarioId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  getScenarioDiff: (projectId: string, scenarioId: string) => request<ScenarioDiff>(`/api/v3/projects/${encodeURIComponent(projectId)}/scenarios/${encodeURIComponent(scenarioId)}/diff`),
  compareScenarios: (projectId: string, input: ScenarioCompareRequest) => request<ScenarioComparison>(`/api/v3/projects/${encodeURIComponent(projectId)}/scenario-comparisons`, { method: "POST", body: JSON.stringify(input) }),
  rebaseScenario: (projectId: string, scenarioId: string, input: ScenarioRevisionRequest) => request<Scenario>(`/api/v3/projects/${encodeURIComponent(projectId)}/scenarios/${encodeURIComponent(scenarioId)}/rebase`, { method: "POST", body: JSON.stringify(input) }),
  applyScenario: (projectId: string, scenarioId: string, input: ScenarioRevisionRequest) => request<Scenario>(`/api/v3/projects/${encodeURIComponent(projectId)}/scenarios/${encodeURIComponent(scenarioId)}/apply`, { method: "POST", body: JSON.stringify(input) }),
  listExports: (projectId: string) => request<Page<ExportJob>>(`/api/v3/projects/${encodeURIComponent(projectId)}/exports`),
  createExport: (projectId: string, input: ExportRequest) => request<ExportJob>(`/api/v3/projects/${encodeURIComponent(projectId)}/exports`, { method: "POST", body: JSON.stringify(input) }),
  getExport: (projectId: string, exportId: string) => request<ExportJob>(`/api/v3/projects/${encodeURIComponent(projectId)}/exports/${encodeURIComponent(exportId)}`),
  downloadExport: (projectId: string, exportId: string) => download(`/api/v3/projects/${encodeURIComponent(projectId)}/exports/${encodeURIComponent(exportId)}/download`),
  listLearningCases: (projectId: string) => request<Page<LearningCase>>(`/api/v3/projects/${encodeURIComponent(projectId)}/learning-cases`),
  createLearningCase: (projectId: string, input: LearningCaseCreate) => request<LearningCase>(`/api/v3/projects/${encodeURIComponent(projectId)}/learning-cases`, { method: "POST", body: JSON.stringify(input) }),
  createLearningCaseFromAction: (projectId: string, input: LearningCaseDraftFromActionCreate) => request<LearningCase>(`/api/v3/projects/${encodeURIComponent(projectId)}/learning-cases/from-action`, { method: "POST", body: JSON.stringify(input) }),
  createLearningCaseFromScenario: (projectId: string, input: LearningCaseDraftFromScenarioCreate) => request<LearningCase>(`/api/v3/projects/${encodeURIComponent(projectId)}/learning-cases/from-scenario`, { method: "POST", body: JSON.stringify(input) }),
  updateLearningCase: (projectId: string, caseId: string, input: LearningCaseUpdate) => request<LearningCase>(`/api/v3/projects/${encodeURIComponent(projectId)}/learning-cases/${encodeURIComponent(caseId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  listLearningCaseCatalog: (filters: { industry?: string; search?: string } = {}) => {
    const query = new URLSearchParams();
    if (filters.industry) query.set("industry", filters.industry);
    if (filters.search) query.set("search", filters.search);
    return request<Page<LearningCaseCatalog>>(`/api/v3/learning-cases/catalog${query.size ? `?${query}` : ""}`);
  },
  listManagementAnalysisRuns: (projectId: string) => request<Page<ManagementAnalysisRun>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/analysis-runs`),
  listManagementInsights: (projectId: string) => request<Page<ManagementInsight>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/insights`),
  updateManagementInsight: (projectId: string, insightId: string, input: ManagementInsightUpdate) => request<ManagementInsight>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/insights/${encodeURIComponent(insightId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  listManagementIssues: (projectId: string) => request<Page<ManagementIssue>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/issues`),
  listManagementIssueOccurrences: (projectId: string, issueId: string) => request<Page<ManagementInsight>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/issues/${encodeURIComponent(issueId)}/occurrences`),
  listManagementIssueFeedback: (projectId: string, issueId: string) => request<Page<ManagementIssueFeedback>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/issues/${encodeURIComponent(issueId)}/feedback`),
  reopenManagementIssue: (projectId: string, issueId: string, input: ManagementIssueReopen) => request<ManagementIssue>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/issues/${encodeURIComponent(issueId)}/reopen`, { method: "POST", body: JSON.stringify(input) }),
  listManagementSignals: (projectId: string) => request<Page<ManagementSignal>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/signals`),
  listManagementMetrics: (projectId: string) => request<Page<MetricDefinition>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/metrics`),
  createManagementMetric: (projectId: string, input: MetricDefinitionCreate) => request<MetricDefinition>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/metrics`, { method: "POST", body: JSON.stringify(input) }),
  updateManagementMetric: (projectId: string, metricId: string, input: MetricDefinitionUpdate) => request<MetricDefinition>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/metrics/${encodeURIComponent(metricId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  listManagementMetricObservations: (projectId: string, metricId: string) => request<Page<MetricObservation>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/metrics/${encodeURIComponent(metricId)}/observations`),
  addManagementMetricObservation: (projectId: string, metricId: string, input: MetricObservationCreate) => request<MetricObservation>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/metrics/${encodeURIComponent(metricId)}/observations`, { method: "POST", body: JSON.stringify(input) }),
  listManagementMeetings: (projectId: string) => request<Page<MeetingRecord>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/meetings`),
  createManagementMeeting: (projectId: string, input: MeetingRecordCreate) => request<MeetingRecord>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/meetings`, { method: "POST", body: JSON.stringify(input) }),
  updateManagementMeeting: (projectId: string, meetingId: string, input: MeetingRecordUpdate) => request<MeetingRecord>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/meetings/${encodeURIComponent(meetingId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  listDesignTradeoffs: (projectId: string) => request<Page<DesignTradeoff>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/tradeoffs`),
  listInformationRequests: (projectId: string) => request<Page<InformationRequest>>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/information-requests`),
  updateInformationRequest: (projectId: string, requestId: string, input: InformationRequestUpdate) => request<InformationRequest>(`/api/v3/projects/${encodeURIComponent(projectId)}/management/information-requests/${encodeURIComponent(requestId)}`, { method: "PATCH", body: JSON.stringify(input) }),
  listCausalHypotheses: (projectId: string) => request<Page<CausalHypothesis>>(`/api/v3/projects/${encodeURIComponent(projectId)}/causal-hypotheses`),
  previewRestore: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<RestorePreview>("/api/v3/restores/preview", { method: "POST", body });
  },
  confirmRestore: (input: RestoreConfirmRequest) => request<RestoreResult>("/api/v3/restores/confirm", { method: "POST", body: JSON.stringify(input) }),
};

function actionInvocationCommand<TBody extends object | undefined = undefined>(
  projectId: string,
  invocationId: string,
  command: "dry-run" | "approve" | "execute" | "cancel" | "retry" | "rollback",
  body?: TBody,
) {
  return request<ActionInvocation>(
    `/api/v3/projects/${encodeURIComponent(projectId)}/action-invocations/${encodeURIComponent(invocationId)}/${command}`,
    { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) },
  );
}

function changeSetCommand(projectId: string, changeSetId: string, command: "validate" | "approve" | "apply") {
  return request<ChangeSet>(`/api/v3/projects/${encodeURIComponent(projectId)}/change-sets/${encodeURIComponent(changeSetId)}/${command}`, { method: "POST" });
}

export const api: typeof realApi = appConfig.useMocks
  ? { ...realApi, ...mockApi }
  : realApi;
