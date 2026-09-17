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
  const requestedProjectKnown = workspace.projects.some((project) => project.id === requestedProjectId && project.is_primary);
  const allProjectsQuery = useQuery({
    queryKey: ["all-projects-for-deep-link"],
    queryFn: () => api.listProjects(),
    enabled: Boolean(requestedProjectId) && !requestedProjectKnown,
  });
  const deepLinkedProject = workspace.projects.find((project) => project.id === requestedProjectId && project.is_primary)
    ?? allProjectsQuery.data?.items.find((project) => project.id === requestedProjectId && project.is_primary);
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
    // Company is the user-facing scope. The canonical projection key is an
    // implementation detail and must not create a second project selector.
    void projectId;
    params.delete("project");
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
      navigate("/executive");
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

        <div className="workspace-selectors" aria-label="当前企业投影">
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
          <div className="projection-scope-label" aria-label="企业统一投影">
            <span>统一企业投影</span>
            <strong>{workspace.selectedCompany?.name ?? "正在建立企业空间"}</strong>
          </div>
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
                <NavLink to="/developer" end>企业</NavLink>
                <NavLink to="/developer/build">材料与企业建模</NavLink>
                <NavLink to="/developer/advanced">信息系统接入</NavLink>
              </>
            ) : (
              <>
                <NavLink to={`/executive${location.search}`} end>企业数字投影</NavLink>
                <NavLink to={`/executive/agent${location.search}`}>管理 Agent</NavLink>
              </>
            )}
          </nav>
          <div className="migration-progress">
            <span>推荐顺序</span>
            <strong>{surface === "developer" ? "材料 → 建模 → 接入" : "投影 → 询问"}</strong>
            <small>{surface === "developer" ? "系统接入和冲突处理在接入流程内完成。" : "来源、证据和确认收纳在管理 Agent 对话中。"}</small>
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
              title="企业投影链接无效或无权访问"
              description="当前链接中的企业投影不存在，或不属于可访问的公司。请从顶部重新选择公司。"
            />
          ) : children}
        </main>
      </div>
    </div>
  );
}
