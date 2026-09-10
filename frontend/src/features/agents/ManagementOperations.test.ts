import { describe, expect, it } from "vitest";
import type { InformationRequest, MeetingRecord } from "../../api/types";
import { completeMeetingActions, informationRequestUpdate, parseManagementValue } from "./ManagementOperations";

describe("管理闭环请求构造", () => {
  it("回答信息请求时携带修订号并清理首尾空格", () => {
    const item = { revision: 4 } as InformationRequest;
    expect(informationRequestUpdate(item, "ANSWERED", "  已核实为季度预算  ")).toEqual({
      expected_revision: 4,
      status: "ANSWERED",
      answer: "已核实为季度预算",
    });
    expect(informationRequestUpdate(item, "CANCELLED", "不应提交")).toEqual({ expected_revision: 4, status: "CANCELLED" });
  });

  it("只把会议中的开放行动项置为完成", () => {
    const item = {
      revision: 2,
      action_items: [
        { title: "复核数据", status: "OPEN" },
        { title: "旧行动", status: "CANCELLED" },
      ],
    } as MeetingRecord;
    expect(completeMeetingActions(item)).toEqual({
      expected_revision: 2,
      action_items: [
        { title: "复核数据", status: "DONE" },
        { title: "旧行动", status: "CANCELLED" },
      ],
    });
  });

  it("指标观测值保留 JSON 类型，普通文本保持为文本", () => {
    expect(parseManagementValue("42.5")).toBe(42.5);
    expect(parseManagementValue('{"min":10,"max":20}')).toEqual({ min: 10, max: 20 });
    expect(parseManagementValue("正常")).toBe("正常");
    expect(parseManagementValue("   ")).toBeNull();
  });
});
