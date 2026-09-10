import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import type { ModelProfile, ModelProfileCreate, ModelProvider } from "../../api/types";
import { StatusMessage } from "../../components/StatusMessage";

const providerLabels: Record<ModelProvider, string> = {
  DEEPSEEK: "DeepSeek",
  OPENAI_COMPATIBLE: "OpenAI 兼容接口",
  MOCK: "本地模拟模型（仅测试流程）",
};

const providerDefaults: Record<ModelProvider, { baseUrl: string; model: string }> = {
  DEEPSEEK: { baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  OPENAI_COMPATIBLE: { baseUrl: "https://api.openai.com/v1", model: "" },
  MOCK: { baseUrl: "http://127.0.0.1/mock", model: "deterministic-mock" },
};

export function ModelProfilesPanel() {
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState<ModelProvider>("DEEPSEEK");
  const [baseUrl, setBaseUrl] = useState(providerDefaults.DEEPSEEK.baseUrl);
  const [model, setModel] = useState(providerDefaults.DEEPSEEK.model);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const profilesQuery = useQuery({ queryKey: ["model-profiles"], queryFn: api.listModelProfiles });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["model-profiles"] });
  const createMutation = useMutation({
    mutationFn: (input: ModelProfileCreate) => api.createModelProfile(input),
    onSuccess: async (profile) => { setNotice(`模型配置“${profile.name}”已保存。`); await refresh(); },
    onError: (value) => setError(asError(value)),
  });
  const updateMutation = useMutation({
    mutationFn: ({ profile, input }: { profile: ModelProfile; input: { enabled?: boolean; is_default?: boolean } }) => api.updateModelProfile(profile.id, input),
    onSuccess: async (profile) => { setNotice(`模型配置“${profile.name}”已更新。`); await refresh(); },
    onError: (value) => setError(asError(value)),
  });
  const testMutation = useMutation({
    mutationFn: (profile: ModelProfile) => api.testModelProfile(profile.id),
    onSuccess: (result) => setNotice(result.ok ? `${result.model} 连接成功，用时 ${result.latency_ms ?? 0}ms。` : result.message),
    onError: (value) => setError(asError(value)),
  });
  const deleteMutation = useMutation({
    mutationFn: (profile: ModelProfile) => api.deleteModelProfile(profile.id),
    onSuccess: async () => { setNotice("模型配置已删除，密钥记录也已一并移除。"); await refresh(); },
    onError: (value) => setError(asError(value)),
  });

  const selectProvider = (next: ModelProvider) => {
    setProvider(next);
    setBaseUrl(providerDefaults[next].baseUrl);
    setModel(providerDefaults[next].model);
  };
  const create = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    createMutation.mutate({
      name: String(form.get("name") ?? "").trim(),
      provider,
      base_url: baseUrl.trim(),
      model: model.trim(),
      api_key: String(form.get("api_key") ?? "").trim() || null,
      temperature: Number(form.get("temperature") ?? 0.2),
      timeout_seconds: Number(form.get("timeout_seconds") ?? 90),
      is_default: form.get("is_default") === "on",
    });
  };
  const remove = (profile: ModelProfile) => {
    if (window.confirm(`确定删除模型配置“${profile.name}”吗？已保存的密钥也会删除。`)) {
      deleteMutation.mutate(profile);
    }
  };

  return <section className="panel model-profile-panel">
    <div className="panel-heading"><div><p className="eyebrow">MODEL SETTINGS</p><h2>模型与密钥</h2><p>密钥仅加密保存在本机数据目录，页面和接口都不会回显原文。</p></div><span>{profilesQuery.data?.total ?? 0} 个配置</span></div>
    {notice && <div className="inline-notice">{notice}</div>}
    {error && <StatusMessage tone="danger" title="模型配置操作失败" description={error.message} action={{ label: "关闭", onClick: () => setError(null) }} />}
    {profilesQuery.error && <StatusMessage tone="danger" title="无法读取模型配置" description={(profilesQuery.error as Error).message} action={{ label: "重试", onClick: () => void profilesQuery.refetch() }} />}
    <div className="model-profile-layout">
      <div className="model-profile-list">
        {(profilesQuery.data?.items ?? []).map((profile) => <article key={profile.id}>
          <div><strong>{profile.name}{profile.is_default ? " · 默认" : ""}</strong><small>{providerLabels[profile.provider]} · {profile.model} · {profile.enabled ? "已启用" : "已停用"}</small><span>{profile.has_api_key ? "已保存密钥" : "未保存密钥"}</span></div>
          <div className="row-actions">
            <button className="button text-button" disabled={testMutation.isPending || !profile.enabled} onClick={() => testMutation.mutate(profile)}>测试</button>
            {!profile.is_default && <button className="button text-button" disabled={!profile.enabled || updateMutation.isPending} onClick={() => updateMutation.mutate({ profile, input: { is_default: true } })}>设为默认</button>}
            <button className="button text-button" disabled={profile.is_default || updateMutation.isPending} onClick={() => updateMutation.mutate({ profile, input: { enabled: !profile.enabled } })}>{profile.enabled ? "停用" : "启用"}</button>
            <button className="button text-button danger-text" disabled={deleteMutation.isPending} onClick={() => remove(profile)}>删除</button>
          </div>
        </article>)}
        {!profilesQuery.data?.items.length && <p className="empty-copy">尚未配置模型。可以先用本地模拟模型验证流程，真实建模需配置可用模型。</p>}
      </div>
      <form className="create-form" onSubmit={create}>
        <h3>新增模型配置</h3>
        <label>配置名称<input name="name" required placeholder="例如：默认 DeepSeek" /></label>
        <label>接口类型<select value={provider} onChange={(event) => selectProvider(event.target.value as ModelProvider)}>{Object.entries(providerLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>接口地址<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} required /></label>
        <label>模型名称<input value={model} onChange={(event) => setModel(event.target.value)} required /></label>
        <label>API 密钥<input name="api_key" type="password" autoComplete="new-password" required={provider !== "MOCK"} placeholder={provider === "MOCK" ? "模拟模型不需要" : "只写入，不回显"} /></label>
        <div className="form-grid-two"><label>温度<input name="temperature" type="number" min="0" max="2" step="0.1" defaultValue="0.2" /></label><label>超时（秒）<input name="timeout_seconds" type="number" min="5" max="600" defaultValue="90" /></label></div>
        <label className="checkbox-row"><input name="is_default" type="checkbox" />设为默认模型</label>
        <button className="button primary" disabled={createMutation.isPending}>{createMutation.isPending ? "正在保存……" : "保存模型配置"}</button>
      </form>
    </div>
  </section>;
}

function asError(value: unknown) { return value instanceof Error ? value : new Error("模型配置操作失败。"); }
