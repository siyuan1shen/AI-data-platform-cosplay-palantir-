// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWorkspace, WorkspaceProvider } from "./WorkspaceContext";

const apiMocks = vi.hoisted(() => ({
  listCompanies: vi.fn(),
  listProjects: vi.fn(),
}));

vi.mock("../api", () => ({ api: apiMocks }));

function Harness() {
  const workspace = useWorkspace();
  return (
    <div>
      <output data-testid="company">{workspace.selectedCompany?.name ?? ""}</output>
      <output data-testid="project">{workspace.selectedProject?.name ?? ""}</output>
      <button type="button" onClick={() => workspace.selectCompany("company-b")}>
        切换公司
      </button>
      <select
        aria-label="当前项目"
        value={workspace.selectedProjectId}
        onChange={(event) => workspace.selectProject(event.target.value)}
      >
        {workspace.projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function renderWorkspace() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceProvider>
        <Harness />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

describe("公司与项目上下文", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    apiMocks.listCompanies.mockResolvedValue({
      total: 2,
      items: [
        { id: "company-a", name: "甲公司", industry: null, description: null },
        { id: "company-b", name: "乙公司", industry: null, description: null },
      ],
    });
    apiMocks.listProjects.mockImplementation(async (companyId?: string) => ({
      total: companyId === "company-b" ? 1 : 2,
      items:
        companyId === "company-b"
          ? [
              {
                id: "project-b-1",
                company_id: "company-b",
                name: "乙公司投影",
                description: null,
                status: "ACTIVE",
                revision: 0,
              },
            ]
          : [
              {
                id: "project-a-1",
                company_id: "company-a",
                name: "甲公司组织",
                description: null,
                status: "ACTIVE",
                revision: 0,
              },
              {
                id: "project-a-2",
                company_id: "company-a",
                name: "甲公司流程",
                description: null,
                status: "ACTIVE",
                revision: 1,
              },
            ],
    }));
  });

  it("切换公司时清空旧项目并只加载新公司的项目", async () => {
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByTestId("company").textContent).toBe("甲公司");
      expect(screen.getByTestId("project").textContent).toBe("甲公司组织");
    });

    fireEvent.click(screen.getByRole("button", { name: "切换公司" }));

    await waitFor(() => {
      expect(screen.getByTestId("company").textContent).toBe("乙公司");
      expect(screen.getByTestId("project").textContent).toBe("乙公司投影");
    });
    expect(screen.getByRole("option").textContent).toBe("乙公司投影");
    expect(screen.queryByText("甲公司组织")).toBeNull();
    expect(apiMocks.listProjects).toHaveBeenCalledWith("company-b");
  });

  it("恢复合法的公司和项目选择，非法项目不会跨公司保留", async () => {
    localStorage.setItem("enterprise-insight.company-id", "company-a");
    localStorage.setItem("enterprise-insight.project-id", "project-a-2");
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByTestId("project").textContent).toBe("甲公司流程");
    });

    fireEvent.click(screen.getByRole("button", { name: "切换公司" }));

    await waitFor(() => {
      expect(screen.getByTestId("project").textContent).toBe("乙公司投影");
    });
    expect(localStorage.getItem("enterprise-insight.project-id")).toBe("project-b-1");
  });

  it("恢复到另一家公司时会丢弃不属于该公司的旧项目", async () => {
    localStorage.setItem("enterprise-insight.company-id", "company-b");
    localStorage.setItem("enterprise-insight.project-id", "project-a-2");
    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByTestId("company").textContent).toBe("乙公司");
      expect(screen.getByTestId("project").textContent).toBe("乙公司投影");
    });
    expect(screen.queryByText("甲公司流程")).toBeNull();
    expect(localStorage.getItem("enterprise-insight.project-id")).toBe("project-b-1");
  });
});
