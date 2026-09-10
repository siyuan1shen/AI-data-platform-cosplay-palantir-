// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ImportWorkbench } from "./ImportWorkbench";

const apiMocks = vi.hoisted(() => ({
  previewImport: vi.fn(),
  confirmImport: vi.fn(),
  listDocuments: vi.fn(),
  listFragments: vi.fn(),
}));

vi.mock("../../api", () => ({ api: apiMocks }));

function renderWorkbench() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ImportWorkbench projectId="project-uat" />
    </QueryClientProvider>,
  );
}

describe("材料导入工作台", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.listDocuments.mockResolvedValue({ items: [], total: 0 });
    apiMocks.listFragments.mockResolvedValue({ items: [], total: 0 });
  });

  it("允许业务人员选择文件、检查预览并确认写入", async () => {
    const preview = {
      id: "preview-1",
      project_id: "project-uat",
      source_system_id: null,
      file_name: "companycheck.csv",
      kind: "COMPANYCHECK_CSV",
      detected_encoding: "utf-8-sig",
      columns: ["公司标识", "问卷编号", "问题", "合并答案", "访谈视角"],
      sample_rows: [
        {
          公司标识: "青岚精密",
          问卷编号: "QN-01",
          问题: "订单承诺由谁确认？",
          合并答案: "销售先承诺，计划复核产能。",
          访谈视角: "销售负责人",
        },
      ],
      suggested_mapping: {
        company: "公司标识",
        questionnaire: "问卷编号",
        question: "问题",
        answer: "合并答案",
        perspective: "访谈视角",
      },
      warnings: [],
      expires_at: "2026-09-09T08:00:00Z",
    };
    apiMocks.previewImport.mockResolvedValue(preview);
    apiMocks.confirmImport.mockResolvedValue({
      id: "result-1",
      project_id: "project-uat",
      status: "COMPLETED",
      documents_created: 1,
      fragments_created: 1,
      claims_created: 1,
      entities_created: 0,
      entities_updated: 0,
      identities_bound: 0,
      observations_created: 0,
      mappings_applied: 0,
      rows_skipped: 0,
      warnings: [],
      created_at: "2026-09-08T08:00:00Z",
    });

    renderWorkbench();
    const file = new File(
      ["公司标识,问卷编号,问题,合并答案,访谈视角\n青岚精密,QN-01,订单承诺由谁确认？,销售先承诺,销售负责人\n"],
      "companycheck.csv",
      { type: "text/csv" },
    );
    const fileInput = screen.getByLabelText(/选择文件/) as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });
    expect(fileInput.files?.[0]).toBe(file);
    expect(screen.getByText("companycheck.csv")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "预览导入" }));
    await waitFor(() =>
      expect(apiMocks.previewImport).toHaveBeenCalledWith(
        "project-uat",
        file,
        "COMPANYCHECK_CSV",
      ),
    );
    expect(await screen.findByRole("heading", { name: "导入预览" })).toBeTruthy();
    expect(screen.getByText("订单承诺由谁确认？")).toBeTruthy();
    expect((screen.getByLabelText("字段映射") as HTMLTextAreaElement).value).toContain(
      '"answer": "合并答案"',
    );

    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    await waitFor(() =>
      expect(apiMocks.confirmImport).toHaveBeenCalledWith("project-uat", {
        preview_id: "preview-1",
        mapping: preview.suggested_mapping,
        options: {},
      }),
    );
    expect(await screen.findByText("导入完成")).toBeTruthy();
    expect(screen.getByText(/建立 1 份文档、1 个证据片段、1 条声明/)).toBeTruthy();
  });

  it("切换材料类型或文件时清空上一次导入的映射和企业筛选", async () => {
    apiMocks.previewImport.mockResolvedValue({
      id: "preview-2",
      project_id: "project-uat",
      source_system_id: null,
      file_name: "multi-company.csv",
      kind: "COMPANYCHECK_CSV",
      detected_encoding: "utf-8",
      columns: ["公司", "问题", "答案"],
      sample_rows: [{ 公司: "青岚精密", 问题: "问题", 答案: "答案" }],
      suggested_mapping: { company: "公司", question: "问题", answer: "答案" },
      warnings: [],
      expires_at: "2026-09-09T08:00:00Z",
    });
    renderWorkbench();
    const fileInput = screen.getByLabelText(/选择文件/) as HTMLInputElement;
    const file = new File(["公司,问题,答案\n青岚精密,问题,答案\n"], "multi-company.csv", { type: "text/csv" });
    fireEvent.change(fileInput, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "预览导入" }));
    await screen.findByRole("heading", { name: "导入预览" });

    const mapping = screen.getByLabelText("字段映射") as HTMLTextAreaElement;
    fireEvent.change(mapping, { target: { value: '{"company":"旧公司"}' } });
    fireEvent.change(screen.getByPlaceholderText("例如：麦数科技"), { target: { value: "旧公司" } });
    const replacementFile = new File(["公司,问题,答案\n青岚精密,新问题,新答案\n"], "replacement.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText(/选择文件/), { target: { files: [replacementFile] } });
    fireEvent.click(screen.getByRole("button", { name: "预览导入" }));
    await screen.findByRole("heading", { name: "导入预览" });
    expect((screen.getByPlaceholderText("例如：麦数科技") as HTMLInputElement).value).toBe("");

    fireEvent.change(screen.getByPlaceholderText("例如：麦数科技"), { target: { value: "旧公司" } });
    const kindSelect = screen.getByRole("combobox", { name: "材料类型" });
    fireEvent.change(kindSelect, { target: { value: "CSV" } });
    fireEvent.change(kindSelect, { target: { value: "COMPANYCHECK_CSV" } });
    fireEvent.click(screen.getByRole("button", { name: "预览导入" }));
    await screen.findByRole("heading", { name: "导入预览" });
    expect((screen.getByPlaceholderText("例如：麦数科技") as HTMLInputElement).value).toBe("");
  });
});
