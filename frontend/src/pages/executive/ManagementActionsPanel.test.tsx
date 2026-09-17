// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ManagementAction, ManagementActionCreate, ManagementActionEvent, ManagementActionEventCreate, ManagementActionEventType, ManagementActionUpdate, ManagementActionVerifyDone } from "../../api/types";
import { ManagementActionsPanel } from "./ManagementActionsPanel";

const mocks = vi.hoisted(() => ({
  actions: [] as Array<Record<string, unknown>>,
  eventsById: new Map<string, Array<Record<string, unknown>>>(),
  api: {
    listManagementActions: vi.fn(),
    createManagementAction: vi.fn(),
    updateManagementAction: vi.fn(),
    cancelManagementAction: vi.fn(),
    appendManagementActionProgress: vi.fn(),
    appendManagementActionOutcome: vi.fn(),
    reportManagementActionDone: vi.fn(),
    verifyManagementActionDone: vi.fn(),
    getManagementActionHistory: vi.fn(),
  },
}));

vi.mock("../../api", () => ({ api: mocks.api }));

function action(overrides: Partial<ManagementAction> = {}): ManagementAction {
  const timestamp = "2026-09-12T10:00:00Z";
  return {
    id: "action-1", company_id: "company-1", project_id: "project-1", title: "梳理交付流程",
    description: "找出交付延迟的主要原因。", owner: "运营负责人", priority: "HIGH", status: "OPEN",
    due_at: null, reported_done: false, reported_done_at: null, reported_done_by: null,
    verified_done: false, verified_done_at: null, verified_done_by: null, revision: 1,
    created_by: "local-owner", created_at: timestamp, updated_at: timestamp, ...overrides,
  };
}

function appendEvent(item: ManagementAction, eventType: ManagementActionEventType, message: string, reason: string | null = null, bump = true) {
  if (bump) {
    item.revision += 1;
    item.updated_at = "2026-09-12T11:00:00Z";
  }
  const history = mocks.eventsById.get(item.id) ?? [];
  const event: ManagementActionEvent = {
    id: `event-${history.length + 1}`, action_id: item.id, company_id: item.company_id,
    project_id: item.project_id, actor_id: "local-owner", created_at: "2026-09-12T11:00:00Z",
    details: {}, event_type: eventType, from_status: null, message, reason,
    revision: item.revision, to_status: null,
  };
  history.push(event as unknown as Record<string, unknown>);
  mocks.eventsById.set(item.id, history);
  return event;
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  return render(<QueryClientProvider client={queryClient}><ManagementActionsPanel projectId="project-1" /></QueryClientProvider>);
}

beforeEach(() => {
  cleanup();
  mocks.actions = [];
  mocks.eventsById.clear();
  let createdSequence = 0;
  mocks.api.listManagementActions.mockReset().mockImplementation(async (projectId: string, includeCancelled = true) => {
    const items = mocks.actions.filter((item) => item.project_id === projectId && (includeCancelled || item.status !== "CANCELLED"));
    return { items, total: items.length };
  });
  mocks.api.createManagementAction.mockReset().mockImplementation(async (projectId: string, input: ManagementActionCreate) => {
    const item = action({ id: `created-action-${++createdSequence}`, project_id: projectId, title: input.title, description: input.description ?? null, owner: input.owner ?? null, priority: input.priority, due_at: input.due_at ?? null });
    mocks.actions.unshift(item as unknown as Record<string, unknown>);
    mocks.eventsById.set(item.id, []);
    appendEvent(item, "CREATED", "创建现实管理行动", input.reason ?? null, false);
    return item;
  });
  mocks.api.updateManagementAction.mockReset().mockImplementation(async (_project: string, actionId: string, input: ManagementActionUpdate) => {
    const item = mocks.actions.find((candidate) => candidate.id === actionId) as unknown as ManagementAction;
    expect(input.expected_revision).toBe(item.revision);
    Object.assign(item, { owner: input.owner, status: input.status, due_at: input.due_at });
    appendEvent(item, "UPDATED", "更新现实管理行动", input.reason ?? null);
    return { ...item };
  });
  mocks.api.cancelManagementAction.mockReset().mockImplementation(async (_project: string, actionId: string, input: { expected_revision: number; reason?: string | null }) => {
    const item = mocks.actions.find((candidate) => candidate.id === actionId) as unknown as ManagementAction;
    expect(input.expected_revision).toBe(item.revision);
    item.status = "CANCELLED";
    appendEvent(item, "CANCELLED", "取消现实管理行动", input.reason ?? null);
    return { ...item };
  });
  const addMessage = (eventType: "PROGRESS" | "OUTCOME") => async (_project: string, actionId: string, input: ManagementActionEventCreate) => {
    const item = mocks.actions.find((candidate) => candidate.id === actionId) as unknown as ManagementAction;
    expect(input.expected_revision).toBe(item.revision);
    return appendEvent(item, eventType, input.message, input.reason ?? null);
  };
  mocks.api.appendManagementActionProgress.mockReset().mockImplementation(addMessage("PROGRESS"));
  mocks.api.appendManagementActionOutcome.mockReset().mockImplementation(addMessage("OUTCOME"));
  mocks.api.reportManagementActionDone.mockReset().mockImplementation(async (_project: string, actionId: string, input: ManagementActionEventCreate) => {
    const item = mocks.actions.find((candidate) => candidate.id === actionId) as unknown as ManagementAction;
    expect(input.expected_revision).toBe(item.revision);
    item.reported_done = true;
    item.reported_done_at = "2026-09-12T11:00:00Z";
    item.reported_done_by = "local-owner";
    appendEvent(item, "DONE_REPORTED", input.message, input.reason ?? null);
    return { ...item };
  });
  mocks.api.verifyManagementActionDone.mockReset().mockImplementation(async (_project: string, actionId: string, input: ManagementActionVerifyDone) => {
    const item = mocks.actions.find((candidate) => candidate.id === actionId) as unknown as ManagementAction;
    expect(input.expected_revision).toBe(item.revision);
    item.verified_done = true;
    item.verified_done_at = "2026-09-12T12:00:00Z";
    item.verified_done_by = "local-owner";
    appendEvent(item, "DONE_VERIFIED", input.verification_note, input.reason ?? null);
    return { ...item };
  });
  mocks.api.getManagementActionHistory.mockReset().mockImplementation(async (_project: string, actionId: string) => {
    const items = mocks.eventsById.get(actionId) ?? [];
    return { items, total: items.length };
  });
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("creates, edits, records progress/outcome, separates reported from verified completion, and shows history", async () => {
  renderPanel();
  await screen.findByRole("heading", { name: "现实管理行动" });
  expect(screen.getByText("报告完成 ≠ 核实完成。")).toBeTruthy();
  expect(screen.getByText(/现实行动 ≠ Agent ActionInvocation/)).toBeTruthy();

  fireEvent.change(screen.getByLabelText("行动名称"), { target: { value: "复盘本月交付延迟" } });
  fireEvent.change(screen.getByLabelText("负责人"), { target: { value: "交付经理" } });
  fireEvent.change(screen.getByLabelText("优先级"), { target: { value: "URGENT" } });
  fireEvent.change(screen.getByLabelText("到期时间"), { target: { value: "2026-09-30T09:30" } });
  fireEvent.change(screen.getByLabelText("创建理由（可选）"), { target: { value: "连续两周出现延期" } });
  fireEvent.submit(screen.getByRole("button", { name: "创建行动" }).closest("form")!);

  const title = await screen.findByRole("heading", { name: "复盘本月交付延迟" });
  expect(mocks.api.createManagementAction).toHaveBeenCalledWith("project-1", expect.objectContaining({
    title: "复盘本月交付延迟", owner: "交付经理", priority: "URGENT", due_at: expect.any(String), reason: "连续两周出现延期", idempotency_key: expect.any(String),
  }));
  let card = title.closest("article")!;

  fireEvent.click(within(card).getByRole("button", { name: "编辑行动" }));
  fireEvent.change(within(card).getByLabelText("负责人"), { target: { value: "供应链负责人" } });
  fireEvent.change(within(card).getByLabelText("状态"), { target: { value: "IN_PROGRESS" } });
  fireEvent.change(within(card).getByLabelText("到期时间"), { target: { value: "2026-10-02T16:00" } });
  fireEvent.change(within(card).getByLabelText("修改理由"), { target: { value: "负责人调整并延长时限" } });
  fireEvent.submit(within(card).getByRole("button", { name: "保存更新" }).closest("form")!);
  await screen.findByText("行动信息已更新并记录版本。");
  expect(mocks.api.updateManagementAction).toHaveBeenCalledWith("project-1", "created-action-1", expect.objectContaining({
    expected_revision: 1, owner: "供应链负责人", status: "IN_PROGRESS", due_at: expect.any(String), reason: "负责人调整并延长时限",
  }));

  card = screen.getByRole("heading", { name: "复盘本月交付延迟" }).closest("article")!;
  fireEvent.click(within(card).getByRole("button", { name: "记录进展" }));
  fireEvent.change(within(card).getByLabelText("进展内容"), { target: { value: "已核对订单和排产记录。" } });
  fireEvent.submit(within(card).getByRole("button", { name: "保存进展" }).closest("form")!);
  await screen.findByText("行动进展已记录。");
  expect(mocks.api.appendManagementActionProgress).toHaveBeenCalledWith("project-1", "created-action-1", expect.objectContaining({ expected_revision: 2, message: "已核对订单和排产记录。" }));

  card = screen.getByRole("heading", { name: "复盘本月交付延迟" }).closest("article")!;
  fireEvent.click(within(card).getByRole("button", { name: "记录结果" }));
  fireEvent.change(within(card).getByLabelText("实际结果"), { target: { value: "延期订单减少 30%。" } });
  fireEvent.submit(within(card).getByRole("button", { name: "保存结果" }).closest("form")!);
  await screen.findByText("行动结果已记录。");
  expect(mocks.api.appendManagementActionOutcome).toHaveBeenCalledWith("project-1", "created-action-1", expect.objectContaining({ expected_revision: 3, message: "延期订单减少 30%。" }));

  card = screen.getByRole("heading", { name: "复盘本月交付延迟" }).closest("article")!;
  fireEvent.click(within(card).getByRole("button", { name: "报告完成" }));
  fireEvent.change(within(card).getByLabelText("完成说明"), { target: { value: "执行人认为行动已完成。" } });
  fireEvent.submit(within(card).getByRole("button", { name: "报告已完成" }).closest("form")!);
  await within(card).findByText(/已报告 ·/);
  expect(mocks.api.reportManagementActionDone).toHaveBeenCalledWith("project-1", "created-action-1", expect.objectContaining({ expected_revision: 4, message: "执行人认为行动已完成。" }));
  expect(within(card).getByText(/待核实/)).toBeTruthy();

  fireEvent.click(within(card).getByRole("button", { name: "人工核实" }));
  fireEvent.change(within(card).getByLabelText("核实说明"), { target: { value: "凭据已核对，数据与实际一致。" } });
  fireEvent.submit(within(card).getByRole("button", { name: "确认核实完成" }).closest("form")!);
  await screen.findByText("人工核实已记录。");
  expect(mocks.api.verifyManagementActionDone).toHaveBeenCalledWith("project-1", "created-action-1", expect.objectContaining({ expected_revision: 5, verification_note: "凭据已核对，数据与实际一致。" }));

  card = screen.getByRole("heading", { name: "复盘本月交付延迟" }).closest("article")!;
  expect(await within(card).findByText(/已核实 ·/)).toBeTruthy();
  fireEvent.click(within(card).getByRole("button", { name: "查看历史" }));
  expect(await within(card).findByText("报告完成")).toBeTruthy();
  expect(await within(card).findByText("核实完成")).toBeTruthy();
});

it("cancels an action with a reason while retaining its history", async () => {
  const existing = action();
  mocks.actions.push(existing as unknown as Record<string, unknown>);
  mocks.eventsById.set(existing.id, []);
  appendEvent(existing, "CREATED", "创建现实管理行动", null, false);
  renderPanel();
  const card = (await screen.findByRole("heading", { name: "梳理交付流程" })).closest("article")!;

  fireEvent.click(within(card).getByRole("button", { name: "取消" }));
  fireEvent.change(within(card).getByLabelText("取消理由"), { target: { value: "项目方向已调整" } });
  fireEvent.submit(within(card).getByRole("button", { name: "确认取消" }).closest("form")!);

  expect(await screen.findByText("行动已取消，历史仍保留。")).toBeTruthy();
  expect(mocks.api.cancelManagementAction).toHaveBeenCalledWith("project-1", "action-1", { expected_revision: 1, reason: "项目方向已调整" });
  expect(within(card).getByText("已取消")).toBeTruthy();
  expect(within(card).getByRole("button", { name: "查看历史" })).toBeTruthy();
});

it("reuses the create idempotency key for a retry and rotates it only after success", async () => {
  mocks.api.createManagementAction.mockRejectedValueOnce(new Error("临时网络中断"));
  renderPanel();
  await screen.findByRole("heading", { name: "现实管理行动" });
  fireEvent.change(screen.getByLabelText("行动名称"), { target: { value: "首次创建尝试" } });
  const createForm = screen.getByRole("button", { name: "创建行动" }).closest("form")!;

  fireEvent.submit(createForm);
  await screen.findByText("临时网络中断");
  const firstKey = (mocks.api.createManagementAction.mock.calls[0][1] as ManagementActionCreate).idempotency_key;
  expect(firstKey).toBeTruthy();

  fireEvent.submit(createForm);
  await screen.findByRole("heading", { name: "首次创建尝试" });
  const retryKey = (mocks.api.createManagementAction.mock.calls[1][1] as ManagementActionCreate).idempotency_key;
  expect(retryKey).toBe(firstKey);

  fireEvent.change(screen.getByLabelText("行动名称"), { target: { value: "下一项行动" } });
  fireEvent.submit(createForm);
  await screen.findByRole("heading", { name: "下一项行动" });
  const nextKey = (mocks.api.createManagementAction.mock.calls[2][1] as ManagementActionCreate).idempotency_key;
  expect(nextKey).toBeTruthy();
  expect(nextKey).not.toBe(firstKey);
});
