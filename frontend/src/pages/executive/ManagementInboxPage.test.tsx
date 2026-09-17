// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ManagementInboxPage } from "./ManagementInboxPage";

const mocks = vi.hoisted(() => ({
  observations: [] as Array<Record<string, unknown>>,
  attachments: [] as Array<Record<string, unknown>>,
  api: {
    listModelProfiles: vi.fn(),
    listManagementObservations: vi.fn(),
    createManagementObservation: vi.fn(),
    reviseManagementObservation: vi.fn(),
    withdrawManagementObservation: vi.fn(),
    getManagementObservationHistory: vi.fn(),
    extractManagementObservation: vi.fn(),
    listManagementObservationExtractions: vi.fn(),
    ingestManagementObservation: vi.fn(),
    listManagementObservationAttachments: vi.fn(),
    downloadManagementObservationAttachment: vi.fn(),
  },
}));

vi.mock("../../api", () => ({ api: mocks.api }));
vi.mock("../../workspace/WorkspaceContext", () => ({ useWorkspace: () => ({ selectedProjectId: "project-1" }) }));

function observation(overrides: Record<string, unknown> = {}) {
  return {
    id: "observation-1", company_id: "company-1", project_id: "project-1", kind: "MEETING",
    title: "交付例会", content: "部分订单出现延迟。", content_sha256: "hash", occurred_at: null,
    submitted_by: "local-owner", status: "ACTIVE", revision: 1,
    created_at: "2026-09-12T10:00:00Z", updated_at: "2026-09-12T10:00:00Z", ...overrides,
  };
}

function attachment(overrides: Record<string, unknown> = {}) {
  return {
    id: "attachment-1", observation_id: "observation-1", file_name: "访谈记录.txt", media_type: "text/plain",
    size_bytes: 1024, content_sha256: "a".repeat(64), created_at: "2026-09-12T10:00:00Z", ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  const view = render(<QueryClientProvider client={queryClient}><ManagementInboxPage /></QueryClientProvider>);
  return { ...view, queryClient };
}

beforeEach(() => {
  cleanup();
  mocks.observations = [];
  mocks.attachments = [];
  mocks.api.listManagementObservations.mockReset().mockImplementation(async (_project: string, includeWithdrawn = false) => {
    const items = mocks.observations.filter((item) => includeWithdrawn || item.status !== "WITHDRAWN");
    return { items, total: items.length };
  });
  mocks.api.listModelProfiles.mockReset().mockResolvedValue({ items: [{
    id: "profile-1", name: "测试模型", provider: "DEEPSEEK", base_url: "https://example.invalid/v1",
    model: "test-model", has_api_key: true, temperature: 0, timeout_seconds: 30, enabled: true,
    is_default: true, created_at: "2026-09-12T10:00:00Z", updated_at: "2026-09-12T10:00:00Z",
  }], total: 1 });
  mocks.api.createManagementObservation.mockReset().mockImplementation(async (_project: string, input: Record<string, unknown>) => {
    const value = observation({ ...input, id: "created-1", revision: 1 });
    mocks.observations.unshift(value);
    return value;
  });
  mocks.api.reviseManagementObservation.mockReset().mockImplementation(async (_project: string, _id: string, input: Record<string, unknown>) => {
    const value = mocks.observations[0];
    Object.assign(value, input, { revision: 2, updated_at: "2026-09-12T11:00:00Z" });
    return value;
  });
  mocks.api.withdrawManagementObservation.mockReset().mockImplementation(async (_project: string, _id: string, _revision: number) => {
    Object.assign(mocks.observations[0], { status: "WITHDRAWN", revision: 3 });
    return mocks.observations[0];
  });
  mocks.api.getManagementObservationHistory.mockReset().mockResolvedValue({ items: [
    { id: "version-1", observation_id: "observation-1", revision: 1, snapshot: { title: "交付例会", content: "初始记录。" }, operation: "CREATED", actor_id: "local-owner", created_at: "2026-09-12T10:00:00Z" },
  ], total: 1 });
  mocks.api.extractManagementObservation.mockReset().mockImplementation(async (_project: string, observationId: string, _input: Record<string, unknown>) => ({
    id: "extraction-1", observation_id: observationId, source_revision: 1, model_profile_id: "profile-1",
    model_name: "test-model", status: "COMPLETED", error_code: null,
    items: [{ kind: "EVENT", statement: "生产主管报告订单延迟。", supporting_quote: "订单延迟。", speaker: "生产主管", time_expression: null }],
    unresolved: [], created_at: "2026-09-12T10:00:00Z",
  }));
  mocks.api.listManagementObservationExtractions.mockReset().mockImplementation(async (_project: string, observationId: string) => ({
    items: [{
      id: "extraction-1", observation_id: observationId, source_revision: 1, model_profile_id: "profile-1",
      model_name: "test-model", status: "COMPLETED", error_code: null,
      items: [{ kind: "EVENT", statement: "生产主管报告订单延迟。", supporting_quote: "订单延迟。", speaker: "生产主管", time_expression: null }],
      unresolved: [], created_at: "2026-09-12T10:00:00Z",
    }], total: 1,
  }));
  mocks.api.ingestManagementObservation.mockReset().mockImplementation(async (_project: string, input: Record<string, unknown>) => {
    const uploaded = input.file as File;
    const created = observation({ id: "ingested-1", kind: input.observation_kind, title: input.title || uploaded.name, content: "解析后的预览文本。", occurred_at: input.occurred_at ?? null });
    mocks.observations.unshift(created);
    const fileAttachment = attachment({ id: "uploaded-attachment", observation_id: created.id, file_name: uploaded.name, media_type: uploaded.type || "text/plain", size_bytes: uploaded.size });
    mocks.attachments = [fileAttachment];
    return { observation: created, attachment: fileAttachment, parser_version: "v1", extracted_character_count: 50000, preview_truncated: true, warnings: ["解析预览已达到上限；原文件完整保留。"] };
  });
  mocks.api.listManagementObservationAttachments.mockReset().mockImplementation(async (_project: string, observationId: string) => {
    const items = mocks.attachments.filter((item) => item.observation_id === observationId);
    return { items, total: items.length };
  });
  mocks.api.downloadManagementObservationAttachment.mockReset().mockResolvedValue({ blob: new Blob(["原文件字节"], { type: "text/plain" }), fileName: "访谈记录.txt" });
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("captures information in the observation store and refreshes the inbox", async () => {
  renderPage();
  fireEvent.change(screen.getByLabelText("标题"), { target: { value: "采购例会纪要" } });
  fireEvent.change(screen.getByLabelText("内容"), { target: { value: "供应商交货较计划晚两天。" } });
  fireEvent.click(screen.getByRole("button", { name: "保存到观察库" }));

  expect(await screen.findByRole("heading", { name: "采购例会纪要" })).toBeTruthy();
  expect(mocks.api.createManagementObservation).toHaveBeenCalledWith("project-1", expect.objectContaining({
    kind: "MEETING", title: "采购例会纪要", content: "供应商交货较计划晚两天。",
  }));
  expect(screen.getByText(/独立观察库 · 尚未验证的输入/)).toBeTruthy();
});

it("uploads an original file, displays parser/truncation warnings, and opens its attachment metadata", async () => {
  renderPage();
  const file = new File(["访谈原文"], "访谈记录.txt", { type: "text/plain" });
  fireEvent.change(screen.getByLabelText("选择原始文件"), { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText("上传信息类型"), { target: { value: "WORK_REPORT" } });
  fireEvent.change(screen.getByLabelText("标题（可选）"), { target: { value: "生产访谈纪要" } });
  fireEvent.submit(screen.getByRole("button", { name: "上传并创建观察记录" }).closest("form")!);

  expect(await screen.findByText((_, element) => Boolean(element?.classList.contains("management-ingestion-truncated")))).toBeTruthy();
  expect(screen.getByText(/50,000 字符上限/)).toBeTruthy();
  expect(screen.getByText(/解析器 v1 · 提取字符数 50,000/)).toBeTruthy();
  expect(await screen.findByText("访谈记录.txt")).toBeTruthy();
  expect(mocks.api.ingestManagementObservation).toHaveBeenCalledWith("project-1", expect.objectContaining({
    file, observation_kind: "WORK_REPORT", title: "生产访谈纪要", occurred_at: null,
  }));
  expect(mocks.api.listManagementObservationAttachments).toHaveBeenCalledWith("project-1", "ingested-1");
});

it("lists an existing original attachment and requests a download from the attachment route", async () => {
  mocks.observations.push(observation());
  mocks.attachments.push(attachment());
  const createObjectUrl = vi.fn().mockReturnValue("blob:attachment-preview");
  const revokeObjectUrl = vi.fn();
  const NativeURL = URL;
  class MockURL extends NativeURL {}
  Object.assign(MockURL, { createObjectURL: createObjectUrl, revokeObjectURL: revokeObjectUrl });
  vi.stubGlobal("URL", MockURL);
  const previewWindow = { closed: false, opener: window, location: { href: "about:blank" }, focus: vi.fn(), close: vi.fn() } as unknown as Window;
  const openWindow = vi.spyOn(window, "open").mockReturnValue(previewWindow);
  const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "查看原文件附件" }));
  expect(await screen.findByText((_, element) =>
    element?.tagName === "SPAN" && /text\/plain · 1\.0 KB · 上传于/.test(element.textContent ?? ""),
  )).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "在新标签打开" }));
  await waitFor(() => expect(mocks.api.downloadManagementObservationAttachment).toHaveBeenCalledWith("project-1", "observation-1", "attachment-1"));
  expect(openWindow).toHaveBeenCalledWith("about:blank", "_blank");
  expect(previewWindow.location.href).toBe("blob:attachment-preview");

  fireEvent.click(screen.getByRole("button", { name: "下载原文件" }));

  await waitFor(() => expect(mocks.api.downloadManagementObservationAttachment).toHaveBeenCalledTimes(2));
  expect(createObjectUrl).toHaveBeenCalledTimes(2);
  expect(createObjectUrl).toHaveBeenCalledWith(expect.any(Blob));
  expect(anchorClick).toHaveBeenCalled();
});

it("shows revision history and withdraws without physically deleting the record", async () => {
  mocks.observations.push(observation());
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "查看历史" }));
  expect(await screen.findByText(/修订 1 · CREATED/)).toBeTruthy();
  expect(screen.getByText(/初始记录/)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "撤回" }));
  await waitFor(() => expect(mocks.api.withdrawManagementObservation).toHaveBeenCalledWith("project-1", "observation-1", 1));
  fireEvent.click(screen.getByRole("checkbox", { name: "显示已撤回" }));
  expect(await screen.findByText("已撤回")).toBeTruthy();
  expect(mocks.observations).toHaveLength(1);
});

it("requires per-record consent before calling the input Agent and labels results as drafts", async () => {
  mocks.observations.push(observation());
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "AI 整理原文" }));
  fireEvent.click(await screen.findByRole("button", { name: "开始整理" }));
  expect(mocks.api.extractManagementObservation).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("checkbox", { name: /同意将这条记录的原文发送/ }));
  fireEvent.click(screen.getByRole("button", { name: "开始整理" }));
  expect(await screen.findByText("生产主管报告订单延迟。" )).toBeTruthy();
  expect(mocks.api.extractManagementObservation).toHaveBeenCalledWith("project-1", "observation-1", {
    model_profile_id: "profile-1", allow_external_model: true,
  });
  expect(screen.getByText("AI 整理只生成待核对草稿")).toBeTruthy();
});
