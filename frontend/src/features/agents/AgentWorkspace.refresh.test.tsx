// @vitest-environment jsdom
import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { AgentWorkspace } from "./AgentWorkspace";

vi.mock("../../api", () => ({ api: {
  listAgentThreads: async () => ({ items: [{ id: "thread", agent_kind: "MANAGEMENT", trashed: false, title: "管理分析", updated_at: "2026-09-08T00:00:00Z" }] }),
  listAgentMessages: async () => ({ items: [] }),
  listAgentRuns: async () => ({ items: [] }),
  listDocuments: async () => ({ items: [] }),
  listModelProfiles: async () => ({ items: [] }),
  listLearningCases: async () => ({ items: [] }),
  listLearningCaseCatalog: async () => ({ items: [] }),
} }));
vi.mock("../actions/ActionCenter", () => ({ ActionCenter: () => null }));

it("refreshes visible business results when the Agent completes an automatic action", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  let insightCount = 0;
  function Insights() {
    const result = useQuery({ queryKey: ["management-insights", "project"], queryFn: async () => insightCount });
    return <p>洞察数量：{result.data}</p>;
  }
  client.setQueryData(["agent-runs", "project"], { items: [{
    id: "run", thread_id: "thread", status: "RUNNING_TOOLS", updated_at: "2026-09-08", action_invocation_ids: [],
  }] });
  const view = render(<QueryClientProvider client={client}><MemoryRouter>
    <AgentWorkspace projectId="project" kind="MANAGEMENT" title="管理" description="分析" />
    <Insights />
  </MemoryRouter></QueryClientProvider>);
  await screen.findByText("洞察数量：0");
  insightCount = 5;
  await act(async () => {
    client.setQueryData(["agent-runs", "project"], { items: [{
      id: "run", thread_id: "thread", status: "COMPLETED", updated_at: "2026-09-08", action_invocation_ids: [],
    }] });
  });
  await waitFor(() => expect(screen.getByText("洞察数量：5")).toBeTruthy());
  view.unmount();
  client.clear();
});
