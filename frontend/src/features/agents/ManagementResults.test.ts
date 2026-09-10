import { describe, expect, it } from "vitest";
import type { ManagementInsight } from "../../api/types";
import { managementInsightUpdate } from "./ManagementResults";

const insight = {
  id: "10000000-0000-4000-8000-000000000001",
  revision: 7,
  management_feedback: "保留原有反馈",
} as ManagementInsight;

describe("managementInsightUpdate", () => {
  it("只改状态时不提交反馈字段，避免清空已有反馈", () => {
    expect(managementInsightUpdate(insight, "CONFIRMED", {})).toEqual({
      expected_revision: 7,
      provided_by: "management",
      status: "CONFIRMED",
    });
  });

  it("用户实际编辑反馈后才提交新值或显式清空", () => {
    expect(managementInsightUpdate(insight, "MONITORING", { [insight.id]: " 新事实 " }))
      .toMatchObject({ management_feedback: "新事实" });
    expect(managementInsightUpdate(insight, "DISMISSED", { [insight.id]: "" }))
      .toMatchObject({ management_feedback: null });
  });
});
