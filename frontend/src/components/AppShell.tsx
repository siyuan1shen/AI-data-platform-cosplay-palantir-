import { type PropsWithChildren, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { appConfig } from "../app/config";
import { StatusMessage } from "./StatusMessage";
import { useWorkspace } from "../workspace/WorkspaceContext";

type Surface = "developer" | "executive";

interface AppShellProps extends PropsWithChildren {
  surface: Surface;
}

export function AppShell({ surface, children }: AppShellProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const workspace = useWorkspace();
  const isPreview = new URLSearchParams(location.search).get("preview") === "true";
  const requestedProjectId = new URLSearchParams(location.search).get("project") ?? "";
  const requestedProjectKnown = workspace.projects.some((project) => project.id === requestedProjectId);
  const allProjectsQuery = useQuery({
    queryKey: ["all-projects-for-deep-link"],
    queryFn: () => api.listProjects(),
    enabled: Boolean(requestedProjectId) && !requestedProjectKnown,
  });
  const deepLinkedProject = workspace.projects.find((project) => project.id === requestedProjectId)
    ?? allProjectsQuery.data?.items.find((project) => project.id === requestedProjectId);
  const deepLinkError = Boolean(
    requestedProjectId
      && allProjectsQuery.isFetched
      && !deepLinkedProject,
  );

  useEffect(() => {
    if (!requestedProjectId) return;
    if (!deepLinkedProject) return;
    if (deepLinkedProject.company_id !== workspace.selectedCompanyId) {
      workspace.selectCompany(deepLinkedProject.company_id);
      return;
    }
    if (requestedProjectId !== workspace.selectedProjectId) workspace.selectProject(requestedProjectId);
  }, [deepLinkedProject, requestedProjectId, workspace.selectedCompanyId, workspace.selectedProjectId]);

  const syncProjectUrl = (projectId: string) => {
    const params = new URLSearchParams(location.search);
    if (surface === "executive" && projectId) params.set("project", projectId);
    else params.delete("project");
    navigate(`${location.pathname}${params.size ? `?${params}` : ""}`, { replace: true });
  };

  const selectCompany = (companyId: string) => {
    workspace.selectCompany(companyId);
    syncProjectUrl("");
  };

  const selectProject = (projectId: string) => {
    workspace.selectProject(projectId);
    syncProjectUrl(projectId);
  };

  const switchSurface = (next: Surface) => {
    if (next === "executive") {
      const params = new URLSearchParams();
      if (workspace.selectedProjectId) params.set("project", workspace.selectedProjectId);
      navigate(`/executive${params.size ? `?${params}` : ""}`);
      return;
    }
    navigate("/developer");
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand" aria-label="企业数字投影">
          <span className="brand-mark">EI</span>
          <span><strong>企业数字投影</strong><small>Enterprise Insight</small></span>
        </div>

        <div className="workspace-selectors" aria-label="当前企业和项目">
          <label>
            <span>公司</span>
            <select
              value={workspace.selectedCompanyId}
              onChange={(event) => selectCompany(event.target.value)}
              disabled={!workspace.companies.length}
            >
              {!workspace.companies.length && <option value="">暂无公司</option>}
              {workspace.companies.map((company) => (
                <option key={company.id} value={company.id}>{company.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span>项目</span>
            <select
              value={workspace.selectedProjectId}
              onChange={(event) => selectProject(event.target.value)}
              disabled={!workspace.projects.length}
            >
              {!workspace.projects.length && <option value="">暂无项目</option>}
              {workspace.projects.map((project) => (
                <option key={project.id} value={project.id}>{project.name}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="surface-switch" aria-label="界面切换">
          <button className={surface === "developer" ? "active" : ""} onClick={() => switchSurface("developer")}>建设端</button>
          <button className={surface === "executive" ? "active" : ""} onClick={() => switchSurface("executive")}>使用端</button>
        </div>
      </header>

      {surface === "executive" && isPreview && (
        <div className="preview-banner" role="status">
          当前为开发草稿预览，不代表已经正式发布的企业状态。
        </div>
      )}

      <div className="shell-body">
        <aside className="sidebar">
          <div className="sidebar-heading">{surface === "developer" ? "建设控制台" : "管理工作台"}</div>
          <nav>
            {surface === "developer" ? (
              <>
                <NavLink to="/developer" end>公司与项目</NavLink>
                <NavLink to="/developer/build">Agent 建模工作台</NavLink>
                <NavLink to="/developer/model">企业投影</NavLink>
                <NavLink to="/developer/advanced">高级工具</NavLink>
              </>
            ) : (
              <>
                <NavLink to={`/executive${location.search}`} end>企业数字投影</NavLink>
                <NavLink to={`/executive/agent${location.search}`}>管理 Agent</NavLink>
                <NavLink to={`/executive/decisions${location.search}`}>结果与确认</NavLink>
              </>
            )}
          </nav>
          <div className="migration-progress">
            <span>推荐顺序</span>
            <strong>{surface === "developer" ? "材料 → Agent → 投影 → 审核" : "投影 → 询问 → 确认"}</strong>
            <small>低频系统功能已集中到高级工具。</small>
          </div>
          <div className="connection-state">
            <i />
            {appConfig.useMocks ? "契约型 Mock" : "连接后端 /api/v3"}
          </div>
        </aside>
        <main className="main-content">
          {deepLinkError ? (
            <StatusMessage
              tone="danger"
              title="项目链接无效或无权访问"
              description="当前链接中的项目不存在，或不属于可访问的企业。请从顶部重新选择公司和项目。"
            />
          ) : children}
        </main>
      </div>
    </div>
  );
}
