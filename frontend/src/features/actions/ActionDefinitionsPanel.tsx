import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { ActionDefinition, ActionRiskLevel } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

const riskLabels: Record<ActionRiskLevel, string> = { LOW: "低风险", MEDIUM: "中风险", HIGH: "高风险", CRITICAL: "极高风险" };
const statusLabels: Record<ActionDefinition["status"], string> = { DRAFT: "草稿", VALIDATED: "已校验", PUBLISHED: "已发布", RETIRED: "已停用" };

interface ActionDefinitionsPanelProps {
  projectId: string;
  onOpenExecution?: () => void;
}

export function ActionDefinitionsPanel({ projectId, onOpenExecution }: ActionDefinitionsPanelProps) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const definitionsQuery = useQuery({
    queryKey: ["action-definitions", projectId],
    queryFn: () => api.listActionDefinitions(projectId),
  });

  const updateMutation = useMutation({
    mutationFn: ({ definition, enabled }: { definition: ActionDefinition; enabled: boolean }) => api.updateActionDefinition(projectId, definition.id, { expected_revision: definition.revision, enabled }),
    onSuccess: async (definition) => {
      await queryClient.invalidateQueries({ queryKey: ["action-definitions", projectId] });
      setNotice(`已${definition.enabled ? "启用" : "停用"}“${definition.name}”。`);
    },
    onError: (value) => setError(value),
  });
  const validateMutation = useMutation({
    mutationFn: (definition: ActionDefinition) => api.validateActionDefinition(projectId, definition.id),
    onSuccess: async (definition) => {
      await queryClient.invalidateQueries({ queryKey: ["action-definitions", projectId] });
      setNotice(`动作“${definition.name}”已完成校验。`);
    },
    onError: (value) => setError(value),
  });

  return (
    <section className="panel action-definitions-panel">
      <div className="panel-heading">
        <div><p className="eyebrow">ACTION CATALOG</p><h2>动作定义</h2><p>动作是可追踪的业务能力。先校验定义，再由使用端创建调用。</p></div>
        {onOpenExecution && <button className="button secondary" onClick={onOpenExecution}>打开执行区</button>}
      </div>
      {notice && <div className="inline-notice">{notice}</div>}
      {error && <StatusMessage tone="danger" title="动作定义操作失败" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
      {definitionsQuery.isLoading ? <p className="panel-loading">正在读取动作定义……</p> : definitionsQuery.error ? (
        <StatusMessage tone="danger" title="无法读取动作定义" description={(definitionsQuery.error as Error).message} action={{ label: "重试", onClick: () => void definitionsQuery.refetch() }} />
      ) : (
        <div className="action-definition-list">
          {(definitionsQuery.data?.items ?? []).map((definition) => (
            <article className="action-definition-row" key={definition.id}>
              <div className="action-definition-main">
                <div className="action-title-line"><strong>{definition.name}</strong><code>{definition.key}</code></div>
                <p>{definition.description || "没有填写说明。"}</p>
                <div className="tag-line"><span className={`tag risk-${definition.risk_level.toLowerCase()}`}>{riskLabels[definition.risk_level]}</span><span className="tag">{definition.require_approval ? "需要审批" : "可直接执行"}</span><span className="tag">{statusLabels[definition.status]}</span><span className="tag">修订 {definition.revision}</span></div>
              </div>
              <div className="action-row-buttons">
                {definition.status === "DRAFT" && <button className="button text-button" disabled={validateMutation.isPending} onClick={() => validateMutation.mutate(definition)}>校验</button>}
                {definition.status !== "RETIRED" && <button className="button text-button" disabled={updateMutation.isPending} onClick={() => updateMutation.mutate({ definition, enabled: !definition.enabled })}>{definition.enabled ? "停用" : "启用"}</button>}
              </div>
            </article>
          ))}
          {!definitionsQuery.data?.items.length && <p className="empty-copy">当前项目还没有动作定义。</p>}
        </div>
      )}
      <div className="inline-advanced action-catalog-note">
        <strong>动作能力由平台注册</strong>
        <p>这里不再允许填写一个没有执行器的自定义动作键。只有已经接入工具注册表、拥有输入契约和执行器的动作才会出现在目录中；扩展动作时请同步实现后端工具，再由项目安装默认动作。</p>
      </div>
    </section>
  );
}
