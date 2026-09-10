import { type FormEvent, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import { StatusMessage } from "../../components/StatusMessage";
import { useWorkspace } from "../../workspace/WorkspaceContext";

export function PortfolioPage() {
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [notice, setNotice] = useState("");

  const companyMutation = useMutation({
    mutationFn: api.createCompany,
    onSuccess: async (company) => {
      await queryClient.invalidateQueries({ queryKey: ["companies"] });
      workspace.selectCompany(company.id);
      setNotice(`已创建公司“${company.name}”。`);
    },
  });

  const projectMutation = useMutation({
    mutationFn: ({ name, description }: { name: string; description?: string }) =>
      api.createProject(workspace.selectedCompanyId, { name, description: description || null }),
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ["projects", workspace.selectedCompanyId] });
      workspace.selectProject(project.id);
      setNotice(`已创建项目“${project.name}”。`);
    },
  });

  const createCompany = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const currentForm = event.currentTarget;
    const form = new FormData(currentForm);
    const name = String(form.get("name") ?? "").trim();
    if (!name) return;
    companyMutation.mutate({
      name,
      industry: String(form.get("industry") ?? "").trim() || null,
      description: String(form.get("description") ?? "").trim() || null,
    });
    currentForm.reset();
  };

  const createProject = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const currentForm = event.currentTarget;
    const form = new FormData(currentForm);
    const name = String(form.get("name") ?? "").trim();
    if (!name || !workspace.selectedCompanyId) return;
    projectMutation.mutate({
      name,
      description: String(form.get("description") ?? "").trim() || undefined,
    });
    currentForm.reset();
  };

  const error = workspace.error ?? companyMutation.error ?? projectMutation.error;

  return (
    <div className="page-stack">
      <section className="page-hero">
        <div><p className="eyebrow">BUILD CONTEXT</p><h1>公司与项目</h1><p>先确定企业边界，再为一次独立建模工作建立项目。</p></div>
        <button
          className="button primary"
          disabled={!workspace.selectedProjectId}
          onClick={() => navigate(`/executive?project=${workspace.selectedProjectId}&preview=true`)}
        >
          以使用者视角预览
        </button>
      </section>

      {notice && <StatusMessage title="操作成功" description={notice} />}
      {error && <StatusMessage tone="danger" title="操作没有完成" description={(error as Error).message} action={{ label: "重新加载", onClick: () => void workspace.refresh() }} />}

      <section className="summary-grid">
        <article><span>公司</span><strong>{workspace.companies.length}</strong><small>当前建模范围</small></article>
        <article><span>当前公司项目</span><strong>{workspace.projects.length}</strong><small>彼此独立的建模任务</small></article>
        <article><span>当前修订</span><strong>{workspace.selectedProject?.revision ?? "—"}</strong><small>{workspace.selectedProject?.status ?? "尚未选择项目"}</small></article>
      </section>

      <div className="portfolio-layout">
        <section className="panel">
          <div className="panel-heading"><div><h2>企业目录</h2><p>选择一家公司，右侧项目会自动切换。</p></div></div>
          {workspace.loading ? <p className="panel-loading">正在读取公司……</p> : (
            <div className="selection-list">
              {workspace.companies.map((company) => (
                <button
                  key={company.id}
                  className={company.id === workspace.selectedCompanyId ? "selected" : ""}
                  onClick={() => workspace.selectCompany(company.id)}
                >
                  <span><strong>{company.name}</strong><small>{company.industry || "未填写行业"}</small></span>
                  <span className="selection-arrow">→</span>
                </button>
              ))}
              {!workspace.companies.length && <p className="empty-copy">还没有公司，请在下方建立第一家公司。</p>}
            </div>
          )}
          <form className="create-form" onSubmit={createCompany}>
            <h3>新建公司</h3>
            <label>公司名称<input name="name" maxLength={200} required placeholder="例如：麦数科技" /></label>
            <label>行业（可选）<input name="industry" maxLength={200} placeholder="例如：制造业" /></label>
            <label>说明（可选）<textarea name="description" maxLength={4000} rows={3} placeholder="建模范围或企业背景" /></label>
            <button className="button secondary" disabled={companyMutation.isPending}>{companyMutation.isPending ? "正在创建……" : "创建公司"}</button>
          </form>
        </section>

        <section className="panel">
          <div className="panel-heading"><div><h2>{workspace.selectedCompany?.name ?? "尚未选择公司"} · 项目</h2><p>一个项目对应一套可独立修订和发布的企业投影。</p></div></div>
          <div className="selection-list">
            {workspace.projects.map((project) => (
              <button
                key={project.id}
                className={project.id === workspace.selectedProjectId ? "selected" : ""}
                onClick={() => workspace.selectProject(project.id)}
              >
                <span><strong>{project.name}</strong><small>{project.status} · 修订 {project.revision}</small></span>
                <span className="selection-arrow">→</span>
              </button>
            ))}
            {workspace.selectedCompanyId && !workspace.projects.length && <p className="empty-copy">这家公司还没有项目。</p>}
          </div>
          <form className="create-form" onSubmit={createProject}>
            <h3>新建企业投影项目</h3>
            <label>项目名称<input name="name" maxLength={200} required placeholder="例如：企业总体投影" /></label>
            <label>目标说明（可选）<textarea name="description" maxLength={4000} rows={3} placeholder="本次建模希望回答什么问题" /></label>
            <button className="button secondary" disabled={!workspace.selectedCompanyId || projectMutation.isPending}>{projectMutation.isPending ? "正在创建……" : "创建项目"}</button>
          </form>
        </section>
      </div>
    </div>
  );
}
