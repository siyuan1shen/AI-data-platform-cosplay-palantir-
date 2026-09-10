import { describe, expect, it } from "vitest";
import { agentRunStages, getAgentRunStageState, getMaterialCoverage } from "./AgentWorkspace";

describe("Agent 运行阶段", () => {
  it("把当前阶段之前标为完成，并保留后续阶段为等待", () => {
    expect(getAgentRunStageState("RUNNING_TOOLS", "QUEUED")).toBe("complete");
    expect(getAgentRunStageState("RUNNING_TOOLS", "PLANNING")).toBe("complete");
    expect(getAgentRunStageState("RUNNING_TOOLS", "RUNNING_TOOLS")).toBe("current");
    expect(getAgentRunStageState("RUNNING_TOOLS", "VALIDATING")).toBe("pending");
  });

  it("完成状态覆盖全部阶段，失败状态不虚报已经完成的阶段", () => {
    expect(agentRunStages.every((stage) => getAgentRunStageState("COMPLETED", stage.status) === "complete")).toBe(true);
    expect(agentRunStages.every((stage) => getAgentRunStageState("FAILED", stage.status) === "pending")).toBe(true);
  });

  it("从运行检查点读取材料覆盖，而不是把缺失片段显示成已读", () => {
    expect(getMaterialCoverage({ context_manifest: { material_coverage: [
      { source_document_id: "doc-1", available_fragments: 45, included_fragments: 40 },
      { source_document_id: "invalid", available_fragments: "45", included_fragments: 45 },
    ] } } as never)).toEqual([
      { source_document_id: "doc-1", available_fragments: 45, included_fragments: 40 },
    ]);
  });
});
