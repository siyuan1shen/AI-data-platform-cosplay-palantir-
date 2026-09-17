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

  const error = workspace.error ?? companyMutation.error;
  const projection = workspace.selectedProject;

  return (
    <div className="page-stack">
      <section className="page-hero">
        <div><p className="eyebrow">ENTERPRISE SPACE</p><h1>企业</h1><p>每家公司只有一份统一企业投影；专题任务、对话和草稿都附着在这份投影上。</p></div>
        <button
          className="button primary"
          disabled={!workspace.selectedProjectId}
          onClick={() => navigate("/executive")}
        >
          以使用者视角预览
        </button>
      </section>

      {notice && <StatusMessage title="操作成功" description={notice} />}
      {error && <StatusMessage tone="danger" title="操作没有完成" description={(error as Error).message} action={{ label: "重新加载", onClick: () => void workspace.refresh() }} />}

      <section className="summary-grid">
        <article><span>公司</span><strong>{workspace.companies.length}</strong><small>当前建模范围</small></article>
        <article><span>统一企业投影</span><strong>{projection ? "1" : "—"}</strong><small>全公司共享的建模空间</small></article>
        <article><span>当前修订</span><strong>{projection?.revision ?? "—"}</strong><small>{projection?.status ?? "尚未建立企业空间"}</small></article>
      </section>

      <div className="portfolio-layout">
        <section className="panel">
          <div className="panel-heading"><div><h2>企业目录</h2><p>选择公司后，系统自动打开这家公司的统一企业投影。</p></div></div>
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

        <section className="panel enterprise-space-card">
          <div className="panel-heading"><div><h2>{workspace.selectedCompany?.name ?? "尚未选择公司"} · 企业投影</h2><p>所有材料、部门、岗位、流程、系统映射、观察和历史版本都归属于这份投影。</p></div></div>
          {projection ? <div className="space-summary"><div><span>投影名称</span><strong>{projection.name}</strong></div><div><span>状态</span><strong>{projection.status}</strong></div><div><span>修订</span><strong>{projection.revision}</strong></div><div><span>用途</span><strong>统一企业空间</strong></div></div> : <p className="empty-copy">正在建立企业空间，请稍候。</p>}
          <div className="inline-notice">专题工作不再创建新的企业模型；请在“材料与企业建模”中新建对话或草稿。</div>
        </section>
      </div>
    </div>
  );
}
