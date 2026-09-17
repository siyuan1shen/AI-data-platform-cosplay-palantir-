// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PotentialRecordsPanel } from "./PotentialRecordsPanel";

const mocks = vi.hoisted(() => ({
  records: [] as any[],
  api: {
    listPotentialRecords: vi.fn(),
    createPotentialRecord: vi.fn(),
    editPotentialRecord: vi.fn(),
    getPotentialRecordHistory: vi.fn(),
    acceptPotentialRecord: vi.fn(),
    rejectPotentialRecord: vi.fn(),
    withdrawPotentialRecord: vi.fn(),
  },
}));

vi.mock("../../api", () => ({ api: mocks.api }));

function record(overrides: Record<string, unknown> = {}) {
  return {
    id: "potential-1", company_id: "company-1", project_id: "project-1", potential_type: "PROBLEM_HYPOTHESIS",
    claim: "计划频繁变更可能导致采购加急。", applicability_scope: "试点生产线及其采购流程。",
    valid_from: null, valid_until: null, task_source: "月度经营复盘", supporting_evidence: [], counterevidence: [],
    verification_method: "比较计划变更频次与加急采购记录。", evidence_status: "UNTESTED", human_status: "ACCEPTED",
    version: 3, payload_hash: "a".repeat(64), created_by: "local-owner",
    created_at: "2026-09-12T10:00:00Z", updated_at: "2026-09-12T10:00:00Z", ...overrides,
  };
}

function history() {
  const current = record();
  return {
    versions: [
      { id: "version-1", record_id: current.id, version: 1, payload_hash: "a".repeat(64), snapshot: current, operation: "CREATED", actor_id: "owner-1", reason: null, created_at: "2026-09-12T10:00:00Z" },
      { id: "version-2", record_id: current.id, version: 2, payload_hash: "b".repeat(64), snapshot: { ...current, claim: "旧版本主张。", version: 2 }, operation: "EDITED", actor_id: "owner-1", reason: "补充访谈后调整表述。", created_at: "2026-09-12T11:00:00Z" },
    ],
    audit: [
      { id: "audit-1", record_id: current.id, company_id: current.company_id, project_id: current.project_id, version: 1, operation: "CREATED", actor_id: "owner-1", before_hash: null, after_hash: "a".repeat(64), reason: null, created_at: "2026-09-12T10:00:00Z" },
      { id: "audit-2", record_id: current.id, company_id: current.company_id, project_id: current.project_id, version: 2, operation: "EDITED", actor_id: "owner-1", before_hash: "a".repeat(64), after_hash: "b".repeat(64), reason: "补充访谈后调整表述。", created_at: "2026-09-12T11:00:00Z" },
    ],
  };
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  return render(<QueryClientProvider client={queryClient}><PotentialRecordsPanel projectId="project-1" companyId="company-1" /></QueryClientProvider>);
}

beforeEach(() => {
  cleanup();
  mocks.records = [];
  mocks.api.listPotentialRecords.mockReset().mockImplementation(async (_project: string, includeHistory = false) => {
    const items = mocks.records.filter((item) => includeHistory || (item.human_status === "ACCEPTED" && item.evidence_status !== "REFUTED"));
    return { items: items.map((item) => ({ ...item })), total: items.length };
  });
  mocks.api.createPotentialRecord.mockReset().mockImplementation(async (_project: string, input: Record<string, unknown>) => {
    const saved = record({ ...input, id: "created-potential", version: 1 });
    mocks.records.unshift(saved);
    return { ...saved };
  });
  mocks.api.editPotentialRecord.mockReset().mockImplementation(async (_project: string, _id: string, input: Record<string, unknown>) => {
    const saved = mocks.records[0];
    Object.assign(saved, input, { version: saved.version + 1, updated_at: "2026-09-12T12:00:00Z" });
    return { ...saved };
  });
  mocks.api.getPotentialRecordHistory.mockReset().mockResolvedValue(history());
  for (const [method, nextStatus] of [[mocks.api.acceptPotentialRecord, "ACCEPTED"], [mocks.api.rejectPotentialRecord, "REJECTED"], [mocks.api.withdrawPotentialRecord, "WITHDRAWN"]] as const) {
    method.mockReset().mockImplementation(async (_project: string, _id: string, _input: Record<string, unknown>) => {
      const saved = mocks.records[0];
      saved.human_status = nextStatus;
      saved.version += 1;
      saved.updated_at = "2026-09-12T12:00:00Z";
      return { ...saved };
    });
  }
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("creates a human-authored potential record in the isolated library", async () => {
  renderPanel();
  expect(screen.getByText(/独立于正式企业事实/)).toBeTruthy();
  expect(screen.getByText(/AI 候选不会由此界面伪造或自动写入/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "人工新增潜在认识" }));
  fireEvent.change(screen.getByLabelText("待检验主张"), { target: { value: "加急采购可能与计划变更相关。" } });
  fireEvent.change(screen.getByLabelText("适用范围"), { target: { value: "装配事业部。" } });
  fireEvent.click(screen.getByRole("button", { name: "添加支持证据" }));
  fireEvent.change(screen.getByLabelText("来源"), { target: { value: "月度经营复盘纪要" } });
  fireEvent.change(screen.getByLabelText("摘录"), { target: { value: "本月计划调整后出现三次加急采购。" } });
  fireEvent.click(screen.getByRole("button", { name: "保存到潜在认识库" }));

  expect(await screen.findByText("加急采购可能与计划变更相关。")).toBeTruthy();
  expect(mocks.api.createPotentialRecord).toHaveBeenCalledWith("project-1", expect.objectContaining({
    company_id: "company-1", project_id: "project-1", claim: "加急采购可能与计划变更相关。",
    applicability_scope: "装配事业部。", potential_type: "PROBLEM_HYPOTHESIS", evidence_status: "UNTESTED",
    idempotency_key: expect.any(String),
    supporting_evidence: [expect.objectContaining({ source_ref: "月度经营复盘纪要", excerpt: "本月计划调整后出现三次加急采购。" })],
  }));
  expect(screen.getByText(/这不表示它已成为正式事实/)).toBeTruthy();
});

it("reuses the same idempotency key after an uncertain create retry", async () => {
  mocks.api.createPotentialRecord.mockRejectedValueOnce(new Error("连接中断，结果未知"));
  renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "人工新增潜在认识" }));
  fireEvent.change(screen.getByLabelText("待检验主张"), { target: { value: "排产变更可能增加加急采购。" } });
  fireEvent.change(screen.getByLabelText("适用范围"), { target: { value: "装配事业部。" } });
  fireEvent.click(screen.getByRole("button", { name: "保存到潜在认识库" }));
  expect(await screen.findByText("连接中断，结果未知")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "保存到潜在认识库" }));
  await screen.findByText("排产变更可能增加加急采购。");
  const firstKey = mocks.api.createPotentialRecord.mock.calls[0][1].idempotency_key;
  const retryKey = mocks.api.createPotentialRecord.mock.calls[1][1].idempotency_key;
  expect(firstKey).toEqual(expect.any(String));
  expect(retryKey).toBe(firstKey);
});

it("edits with a reason and expected version, then displays immutable versions and audit entries", async () => {
  mocks.records.push(record());
  renderPanel();
  fireEvent.click(await screen.findByRole("button", { name: "编辑并留原因" }));
  fireEvent.change(screen.getByLabelText("待检验主张"), { target: { value: "计划变更可能与采购加急相关。" } });
  fireEvent.change(screen.getByLabelText("本次修订原因"), { target: { value: "补充访谈后改为更谨慎的表述。" } });
  fireEvent.click(screen.getByRole("button", { name: "保存修订" }));

  await waitFor(() => expect(mocks.api.editPotentialRecord).toHaveBeenCalledWith("project-1", "potential-1", expect.objectContaining({
    expected_version: 3, reason: "补充访谈后改为更谨慎的表述。", claim: "计划变更可能与采购加急相关。",
  })));
  expect(await screen.findByText(/第 4 版修订/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "查看不可变历史" }));
  expect((await screen.findAllByText(/v2 · 人工修订/)).length).toBe(2);
  expect((screen.getAllByText(/补充访谈后调整表述/).length)).toBeGreaterThan(0);
  expect(screen.getByText(/审计链/)).toBeTruthy();
});

it("requires reasons for reject, re-accept and withdraw, and sends the current version", async () => {
  mocks.records.push(record());
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderPanel();

  fireEvent.click(await screen.findByRole("button", { name: "拒绝保留" }));
  fireEvent.change(screen.getByLabelText("人工判断理由"), { target: { value: "访谈和记录均不支持该关联。" } });
  fireEvent.click(screen.getByRole("button", { name: "确认并留痕" }));
  await waitFor(() => expect(mocks.api.rejectPotentialRecord).toHaveBeenCalledWith("project-1", "potential-1", { expected_version: 3, reason: "访谈和记录均不支持该关联。" }));

  fireEvent.click(screen.getByRole("checkbox", { name: /包含已拒绝/ }));
  fireEvent.click(await screen.findByRole("button", { name: "重新纳入潜在库" }));
  fireEvent.change(screen.getByLabelText("人工判断理由"), { target: { value: "新材料出现，重新纳入待检验。" } });
  fireEvent.click(screen.getByRole("button", { name: "确认并留痕" }));
  await waitFor(() => expect(mocks.api.acceptPotentialRecord).toHaveBeenCalledWith("project-1", "potential-1", { expected_version: 4, reason: "新材料出现，重新纳入待检验。" }));

  fireEvent.click(await screen.findByRole("button", { name: "撤回（保留历史）" }));
  fireEvent.change(screen.getByLabelText("人工判断理由"), { target: { value: "该认识不再适用于当前组织。" } });
  fireEvent.click(screen.getByRole("button", { name: "确认并留痕" }));
  await waitFor(() => expect(mocks.api.withdrawPotentialRecord).toHaveBeenCalledWith("project-1", "potential-1", { expected_version: 5, reason: "该认识不再适用于当前组织。" }));
  expect(screen.getByText(/已撤回，保留历史/)).toBeTruthy();
});
