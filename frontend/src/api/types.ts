import type { components } from "./schema";

export type ApiSchemas = components["schemas"];
export type Company = ApiSchemas["CompanyView"];
export type CompanyCreate = ApiSchemas["CompanyCreate"];
export type Project = ApiSchemas["ProjectView"];
export type ProjectCreate = ApiSchemas["ProjectCreate"];
export type ExecutiveContext = ApiSchemas["ExecutiveContextView"];
export type Graph = ApiSchemas["GraphView"];
export type Entity = ApiSchemas["EntityView"];
export type Relation = ApiSchemas["RelationView"];
export type ErrorResponse = ApiSchemas["ErrorResponse"];
export type ActionDefinition = ApiSchemas["ActionDefinitionView"];
export type ActionDefinitionCreate = ApiSchemas["ActionDefinitionCreate"];
export type ActionDefinitionUpdate = ApiSchemas["ActionDefinitionUpdate"];
export type ActionInvocation = ApiSchemas["ActionInvocationView"];
export type ActionInvocationCreate = ApiSchemas["ActionInvocationCreate"];
export type ActionApprovalRequest = ApiSchemas["ActionApprovalRequest"];
export type ActionRetryRequest = ApiSchemas["ActionRetryRequest"];
export type ActionLog = ApiSchemas["ActionLogView"];
export type ActionObservation = ApiSchemas["ActionObservationView"];
export type ActionObservationCreate = ApiSchemas["ActionObservationCreate"];
export type ActionInvocationStatus = ApiSchemas["ActionInvocationStatus"];
export type ActionRiskLevel = ApiSchemas["ActionRiskLevel"];
export type ImportKind = ApiSchemas["ImportKind"];
export type ImportPreview = ApiSchemas["ImportPreviewView"];
export type ImportConfirmRequest = ApiSchemas["ImportConfirmRequest"];
export type ImportResult = ApiSchemas["ImportResultView"];
export type SourceDocument = ApiSchemas["SourceDocumentView"];
export type EvidenceFragment = ApiSchemas["EvidenceFragmentView"];
export type AgentKind = ApiSchemas["AgentKind"];
export type AgentThread = ApiSchemas["AgentThreadView"];
export type AgentThreadCreate = ApiSchemas["AgentThreadCreate"];
export type AgentThreadUpdate = ApiSchemas["AgentThreadUpdate"];
export type AgentMessage = ApiSchemas["AgentMessageView"];
export type AgentMessageCreate = ApiSchemas["AgentMessageCreate"];
export type AgentMessageAccepted = ApiSchemas["AgentMessageAccepted"];
export type AgentRun = ApiSchemas["AgentRunView"];
export type EntityCreate = ApiSchemas["EntityCreate"];
export type EntityUpdate = ApiSchemas["EntityUpdate"];
export type RelationCreate = ApiSchemas["RelationCreate"];
export type RelationUpdate = ApiSchemas["RelationUpdate"];
export type RelationParticipantInput = ApiSchemas["RelationParticipantInput"];
export type RevisionRequest = ApiSchemas["RevisionRequest"];
export type OntologyType = ApiSchemas["OntologyTypeView"];
export type OntologyTypeCreate = ApiSchemas["OntologyTypeCreate"];
export type OntologyRelease = ApiSchemas["OntologyReleaseView"];
export type OntologyReleaseCreate = ApiSchemas["OntologyReleaseCreate"];
export type ChangeSet = ApiSchemas["ChangeSetView"];
export type ChangeSetCreate = ApiSchemas["ChangeSetCreate"];
export type ChangePreview = ApiSchemas["ChangePreview"];
export type ChangeSetValidation = ApiSchemas["ChangeSetValidation"];
export type ChangeOperation = ApiSchemas["ChangeOperation"];
export type SourceSystem = ApiSchemas["SourceSystemView"];
export type SourceSystemCreate = ApiSchemas["SourceSystemCreate"];
export type SourceSystemUpdate = ApiSchemas["SourceSystemUpdate"];
export type SourceSystemTest = ApiSchemas["SourceSystemTestView"];
export type SourceSystemKind = ApiSchemas["SourceSystemKind"];
export type SourceAsset = ApiSchemas["SourceAssetView"];
export type SourceAssetUpdate = ApiSchemas["SourceAssetUpdate"];
export type SourceConnectorExtractRequest = ApiSchemas["SourceConnectorExtractRequest"];
export type SourceConnectorExtract = ApiSchemas["SourceConnectorExtractView"];
export type SourceConnectorSyncRequest = ApiSchemas["SourceConnectorSyncRequest"];
export type SourceConnectorSync = ApiSchemas["SourceConnectorSyncView"];
export type RawBatch = ApiSchemas["RawBatchView"];
export type RawRecord = ApiSchemas["RawRecordView"];
export type MaterializationRun = ApiSchemas["MaterializationRunView"];
export type SourceIdentity = ApiSchemas["SourceIdentityView"];
export type SourceIdentityBind = ApiSchemas["SourceIdentityBind"];
export type ObservationAssertion = ApiSchemas["ObservationAssertionView"];
export type ObservationConflict = ApiSchemas["ObservationConflictView"];
export type ObservationConflictResolve = ApiSchemas["ObservationConflictResolve"];
export type SemanticMapping = ApiSchemas["SemanticMappingView"];
export type SemanticMappingCreate = ApiSchemas["SemanticMappingCreate"];
export type SemanticMappingCommand = ApiSchemas["SemanticMappingCommand"];
export type SemanticMappingSuggestion = ApiSchemas["SemanticMappingSuggestionView"];
export type SemanticMappingSuggestionPage = ApiSchemas["SemanticMappingSuggestionPage"];
export type SemanticRelationMapping = ApiSchemas["SemanticRelationMappingView"];
export type SemanticRelationMappingCreate = ApiSchemas["SemanticRelationMappingCreate"];
export type SemanticRelationMappingCommand = ApiSchemas["SemanticRelationMappingCommand"];
export type SemanticDataset = ApiSchemas["SemanticDatasetView"];
export type SemanticDatasetCreate = ApiSchemas["SemanticDatasetCreate"];
export type SemanticDatasetUpdate = ApiSchemas["SemanticDatasetUpdate"];
export type SemanticDatasetQuery = ApiSchemas["SemanticDatasetQuery"];
export type SemanticDatasetResult = ApiSchemas["SemanticDatasetResult"];
export type SemanticDatasetExport = ApiSchemas["SemanticDatasetExport"];
export type Publication = ApiSchemas["PublicationView"];
export type PublicationCreate = ApiSchemas["PublicationCreate"];
export type Hypothesis = ApiSchemas["HypothesisView"];
export type HypothesisCreate = ApiSchemas["HypothesisCreate"];
export type HypothesisFeedbackCreate = ApiSchemas["HypothesisFeedbackCreate"];
export type HypothesisFeedback = ApiSchemas["HypothesisFeedbackView"];
export type HypothesisStatus = ApiSchemas["HypothesisStatus"];
export type Scenario = ApiSchemas["ScenarioView"];
export type ScenarioCreate = ApiSchemas["ScenarioCreate"];
export type ScenarioUpdate = ApiSchemas["ScenarioUpdate"];
export type ScenarioRevisionRequest = ApiSchemas["ScenarioRevisionRequest"];
export type ScenarioDiff = ApiSchemas["ScenarioDiffView"];
export type ScenarioCompareRequest = ApiSchemas["ScenarioCompareRequest"];
export type ScenarioComparison = ApiSchemas["ScenarioComparisonView"];
export interface ScenarioCaseInput {
  key: string;
  label: string;
  periods: string[];
  demand: number[];
  capacity: number[];
  initial_inventory: number;
  initial_backlog: number;
  unit: string;
  flow_mode: "STORABLE_GOODS" | "NON_STORABLE_SERVICE";
}
export interface ScenarioSimulationRequest {
  cases: ScenarioCaseInput[];
  created_by?: string;
}
export interface ScenarioRun {
  id: string;
  project_id: string;
  scenario_id: string;
  scenario_revision: number;
  baseline_revision: number;
  status: string;
  input_snapshot: Record<string, unknown>;
  rule_snapshot: Record<string, unknown>;
  result: { cases?: Array<Record<string, any>>; limitations?: string[] };
  errors: Array<Record<string, any>>;
  created_by: string;
  created_at: string;
}
export interface ScenarioRunComparison {
  left_run_id: string;
  right_run_id: string;
  cases: Array<Record<string, any>>;
  limitations: string[];
}
export type GraphQuery = ApiSchemas["GraphQuery"];
export type ExportRequest = ApiSchemas["ExportRequest"];
export type ExportJob = ApiSchemas["ExportJobView"];
export type Viewpoint = ApiSchemas["Viewpoint"];
export type ModelProfile = ApiSchemas["ModelProfileView"];
export type ModelProfileCreate = ApiSchemas["ModelProfileCreate"];
export type ModelProfileUpdate = ApiSchemas["ModelProfileUpdate"];
export type ModelProfileTest = ApiSchemas["ModelProfileTestView"];
export type ModelProvider = ApiSchemas["ModelProvider"];
export type EvaluationExecuteCreate = ApiSchemas["EvaluationExecuteCreate"];
export type EvaluationRun = ApiSchemas["EvaluationRunView"];
export type EvaluationExecution = ApiSchemas["EvaluationExecutionView"];
export type EvaluationResult = ApiSchemas["EvaluationResultView"];
export type EvaluationSuite = ApiSchemas["EvaluationSuiteView"];
export type EvaluationSuiteCreate = ApiSchemas["EvaluationSuiteCreate"];
export type EvaluationSuiteUpdate = ApiSchemas["EvaluationSuiteUpdate"];
export type EvaluationCase = ApiSchemas["EvaluationCaseView"];
export type EvaluationCaseCreate = ApiSchemas["EvaluationCaseCreate"];
export type LearningCase = ApiSchemas["LearningCaseView"];
export type LearningCaseCreate = ApiSchemas["LearningCaseCreate"];
export type LearningCaseDraftFromActionCreate = ApiSchemas["LearningCaseDraftFromActionCreate"];
export type LearningCaseDraftFromScenarioCreate = ApiSchemas["LearningCaseDraftFromScenarioCreate"];
export type LearningCaseUpdate = ApiSchemas["LearningCaseUpdate"];
export type LearningCaseCatalog = ApiSchemas["LearningCaseCatalogView"];
export type LearningCaseStatus = ApiSchemas["LearningCaseStatus"];
export type ManagementAnalysisRun = ApiSchemas["ManagementAnalysisRunView"];
export type ManagementInsight = ApiSchemas["ManagementInsightView"];
export type ManagementInsightUpdate = ApiSchemas["ManagementInsightUpdate"];
export type ManagementInsightStatus = ApiSchemas["ManagementInsightStatus"];
export type ManagementInsightClassification = ApiSchemas["ManagementInsightClassification"];
export type ManagementIssue = ApiSchemas["ManagementIssueView"];
export type ManagementIssueFeedback = ApiSchemas["ManagementIssueFeedbackView"];
export type ManagementIssueReopen = ApiSchemas["ManagementIssueReopen"];
export type ManagementSignal = ApiSchemas["ManagementSignalView"];
export type ManagementAction = ApiSchemas["ManagementActionView"];
export type ManagementActionCreate = ApiSchemas["ManagementActionCreate"];
export type ManagementActionUpdate = ApiSchemas["ManagementActionUpdate"];
export type ManagementActionRevisionRequest = ApiSchemas["ManagementActionRevisionRequest"];
export type ManagementActionEventCreate = ApiSchemas["ManagementActionEventCreate"];
export type ManagementActionVerifyDone = ApiSchemas["ManagementActionVerifyDone"];
export type ManagementActionEvent = ApiSchemas["ManagementActionEventView"];
export type ManagementActionStatus = ApiSchemas["ManagementActionStatus"];
export type ManagementActionActiveStatus = ApiSchemas["ManagementActionActiveStatus"];
export type ManagementActionPriority = ApiSchemas["ManagementActionPriority"];
export type ManagementActionEventType = ApiSchemas["ManagementActionEventType"];
export type ManagementObservationKind = ApiSchemas["ManagementObservationKind"];
export type ObservationCreate = ApiSchemas["ObservationCreate"];
export type ObservationUpdate = ApiSchemas["ObservationUpdate"];
export type ObservationView = ApiSchemas["ObservationView"];
export type ObservationHistoryView = ApiSchemas["ObservationHistoryView"];
export type ObservationExtractRequest = ApiSchemas["ObservationExtractRequest"];
export type ObservationExtractionView = ApiSchemas["ObservationExtractionView"];
export type ObservationExtractionHistoryView = ApiSchemas["ObservationExtractionHistoryView"];
export type ObservationAttachment = ApiSchemas["ObservationAttachmentView"];
export type ObservationIngestion = ApiSchemas["ObservationIngestionView"];
export type ObservationIngestionRequest = Omit<ApiSchemas["Body_ingest_management_observation_api_v3_projects__project_id__observation_ingestions_post"], "file" | "observation_kind"> & {
  file: File;
  observation_kind: ManagementObservationKind;
};
export type MetricDefinition = ApiSchemas["MetricDefinitionView"];
export type MetricDefinitionCreate = ApiSchemas["MetricDefinitionCreate"];
export type MetricDefinitionUpdate = ApiSchemas["MetricDefinitionUpdate"];
export type MetricObservation = ApiSchemas["MetricObservationView"];
export type MetricObservationCreate = ApiSchemas["MetricObservationCreate"];
export type MeetingRecord = ApiSchemas["MeetingRecordView"];
export type MeetingRecordCreate = ApiSchemas["MeetingRecordCreate"];
export type MeetingRecordUpdate = ApiSchemas["MeetingRecordUpdate"];
export type DesignTradeoff = ApiSchemas["DesignTradeoffView"];
export type InformationRequest = ApiSchemas["InformationRequestView"];
export type InformationRequestUpdate = ApiSchemas["InformationRequestUpdate"];
export type CausalHypothesis = ApiSchemas["CausalHypothesisView"];
export type PotentialType = "RELATION_HYPOTHESIS" | "PROBLEM_HYPOTHESIS" | "DESIGN_TRADEOFF" | "CAUSAL_HYPOTHESIS" | "MANAGEMENT_LEARNING";
export type PotentialHumanStatus = "ACCEPTED" | "REJECTED" | "WITHDRAWN";
export type PotentialEvidenceStatus = "UNTESTED" | "SUPPORTED" | "CONTESTED" | "REFUTED" | "STALE";
export type PotentialOperation = "CREATED" | "ACCEPTED" | "EDITED" | "REJECTED" | "WITHDRAWN";
export interface PotentialEvidence { source_ref: string; excerpt: string; observed_at?: string | null; note?: string | null; }
export interface PotentialFields {
  company_id: string;
  project_id: string;
  potential_type: PotentialType;
  claim: string;
  applicability_scope: string;
  valid_from: string | null;
  valid_until: string | null;
  task_source: string;
  supporting_evidence: PotentialEvidence[];
  counterevidence: PotentialEvidence[];
  verification_method: string;
  evidence_status: PotentialEvidenceStatus;
}
export interface PotentialRecordCreate extends PotentialFields { idempotency_key?: string; }
export interface PotentialRecordUpdate {
  expected_version: number;
  reason: string;
  potential_type?: PotentialType;
  claim?: string;
  applicability_scope?: string;
  valid_from?: string | null;
  valid_until?: string | null;
  task_source?: string;
  supporting_evidence?: PotentialEvidence[];
  counterevidence?: PotentialEvidence[];
  verification_method?: string;
  evidence_status?: PotentialEvidenceStatus;
}
export interface PotentialRecordStatusRequest { expected_version: number; reason: string; }
export interface PotentialRecord extends PotentialFields {
  id: string;
  human_status: PotentialHumanStatus;
  version: number;
  payload_hash: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}
export interface PotentialVersion {
  id: string;
  record_id: string;
  version: number;
  payload_hash: string;
  snapshot: Record<string, unknown>;
  operation: PotentialOperation;
  actor_id: string;
  reason: string | null;
  created_at: string;
}
export interface PotentialAudit {
  id: string;
  record_id: string;
  company_id: string;
  project_id: string;
  version: number;
  operation: PotentialOperation;
  actor_id: string;
  before_hash: string | null;
  after_hash: string;
  reason: string | null;
  created_at: string;
}
export interface PotentialHistory { versions: PotentialVersion[]; audit: PotentialAudit[]; }
export interface PotentialPage { items: PotentialRecord[]; total: number; }
export type RestorePreview = ApiSchemas["RestorePreviewView"];
export type RestoreConfirmRequest = ApiSchemas["RestoreConfirmRequest"];
export type RestoreResult = ApiSchemas["RestoreResultView"];

export interface Page<T> {
  items: T[];
  total: number;
}

export interface WorkObservationActivity {
  app: string;
  domain?: string | null;
  category?: string | null;
  context?: string | null;
}

export interface WorkObservationEvent {
  event_id: string;
  session_id: string;
  sequence: number;
  observed_at: string;
  source_employee_key: string;
  source_role_key?: string | null;
  activity: WorkObservationActivity;
  state?: string;
  metadata?: Record<string, unknown>;
}

export interface WorkObservationPackage {
  format_version: string;
  batch_id: string;
  source_id: string;
  events: WorkObservationEvent[];
}

export interface WorkObservationBatch {
  id: string;
  project_id: string;
  source_batch_id: string;
  source_id: string;
  format_version: string;
  payload_hash: string;
  event_count: number;
  accepted_count: number;
  duplicate_count: number;
  status: string;
  created_at: string;
}

export interface WorkObservationImportResult {
  batch_id: string;
  source_batch_id: string;
  status: string;
  accepted_count: number;
  duplicate_count: number;
  event_count: number;
  duplicate: boolean;
}

export interface WorkObservationIdentityBinding {
  id: string;
  project_id: string;
  source_id: string;
  source_employee_key: string;
  formal_entity_id: string;
  formal_role_key?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface WorkObservationIdentityBindingHistory {
  id: string;
  project_id: string;
  source_id: string;
  source_employee_key: string;
  formal_entity_id?: string | null;
  formal_role_key?: string | null;
  operation: string;
  actor_id: string;
  reason?: string | null;
  created_at: string;
}

export interface WorkObservationVirtualCandidate {
  id: string;
  project_id: string;
  analysis_id: string;
  candidate_type: string;
  label: string;
  role_key?: string | null;
  properties: Record<string, unknown>;
  evidence_segment_ids: string[];
  status: string;
  virtual_work_model_id?: string | null;
  virtual_node_id?: string | null;
  virtual_edge_id?: string | null;
  decision_reason?: string | null;
  created_at: string;
  decided_at?: string | null;
}

export interface WorkObservationPreview {
  id: string;
  project_id: string;
  source_batch_id: string;
  source_id: string;
  format_version: string;
  payload_hash: string;
  event_count: number;
  status: string;
  created_at: string;
  sample_events?: Array<Record<string, unknown>>;
  employee_keys?: string[];
  role_keys?: string[];
  state_counts?: Record<string, number>;
  first_observed_at?: string | null;
  last_observed_at?: string | null;
  duplicate_event_count?: number;
  conflict_event_count?: number;
  identity_status?: string;
  warnings?: string[];
}

export interface WorkObservationCoverage {
  project_id: string;
  event_count: number;
  foreground_event_count: number;
  employee_count: number;
  session_count: number;
  source_count: number;
  first_observed_at: string | null;
  last_observed_at: string | null;
  batch_count: number;
}

export interface WorkObservationAnalysis {
  id: string;
  project_id: string;
  status: string;
  filters: Record<string, unknown>;
  event_count: number;
  segment_count: number;
  employee_count: number;
  result: {
    nodes?: Array<Record<string, unknown>>;
    edges?: Array<Record<string, unknown>>;
    patterns?: Array<Record<string, unknown>>;
    employee_paths?: Record<string, Array<{ path: string[]; count: number }>>;
    segments?: Array<Record<string, unknown>>;
    limitations?: string[];
    [key: string]: unknown;
  };
  created_at: string;
}

export interface WorkObservationComparison {
  id: string;
  project_id: string;
  analysis_id: string;
  left_employee_keys: string[];
  right_employee_keys: string[];
  result: {
    left_paths?: Array<{ path: string[]; count: number }>;
    right_paths?: Array<{ path: string[]; count: number }>;
    only_left?: string[][];
    only_right?: string[][];
    shared?: string[][];
    limitations?: string[];
    [key: string]: unknown;
  };
  created_at: string;
}
