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
  Page,
  Project,
  ProjectCreate,
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
    revision: 3,
    created_at: now(),
    updated_at: now(),
  },
];

const actionDefinitions: ActionDefinition[] = [];
const actionInvocations: ActionInvocation[] = [];
const actionLogs: ActionLog[] = [];
const actionObservations: ActionObservation[] = [];

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
  async listCompanies(): Promise<Page<Company>> {
    return { items: [...companies], total: companies.length };
  },

  async createCompany(input: CompanyCreate): Promise<Company> {
    const value: Company = {
      id: newId(),
      name: input.name,
      industry: input.industry ?? null,
      description: input.description ?? null,
      created_at: now(),
      updated_at: now(),
    };
    companies.push(value);
    return value;
  },

  async listProjects(selectedCompanyId?: string): Promise<Page<Project>> {
    const items = selectedCompanyId
      ? projects.filter((project) => project.company_id === selectedCompanyId)
      : [...projects];
    return { items, total: items.length };
  },

  async createProject(selectedCompanyId: string, input: ProjectCreate): Promise<Project> {
    const value: Project = {
      id: newId(),
      company_id: selectedCompanyId,
      name: input.name,
      description: input.description ?? null,
      status: "DRAFT",
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
