import { describe, expect, it } from "vitest";
import type { ActionInvocation, AgentRun } from "../../api/types";
import { canResumeAgentRun, shouldPollActionInvocations, summarizeActionPayload } from "./ActionCenter";

const invocation = (status: ActionInvocation["status"]) => ({ status }) as ActionInvocation;

describe("动作状态轮询", () => {
  it("对后端正在执行的动作持续刷新", () => {
    expect(shouldPollActionInvocations([invocation("RUNNING")])).toBe(true);
  });

  it("等待人工处理或已经终止的动作不做无意义轮询", () => {
    expect(shouldPollActionInvocations([invocation("WAITING_APPROVAL"), invocation("OBSERVING"), invocation("SUCCEEDED")])).toBe(false);
  });

  it("动作全部终止后允许把人工执行结果回传给等待中的 Agent", () => {
    const run = { status: "WAITING_REVIEW" } as AgentRun;
    expect(canResumeAgentRun(run, [invocation("SUCCEEDED")])).toBe(true);
    expect(canResumeAgentRun(run, [invocation("WAITING_APPROVAL")])).toBe(false);
    expect(canResumeAgentRun({ status: "COMPLETED" } as AgentRun, [invocation("SUCCEEDED")])).toBe(false);
  });
});

describe("动作结果摘要", () => {
  it("把批量投影预演压缩为业务可读计数", () => {
    expect(summarizeActionPayload({ valid: true, would_change: [
      { kind: "CREATE_ENTITY" }, { kind: "UPDATE_ENTITY" },
      { kind: "CREATE_RELATION" },
    ] })).toBe("校验通过；预计变更 2 个对象、1 条关系。");
  });

  it("把原子执行结果显示为一行摘要", () => {
    expect(summarizeActionPayload({ resource: "CHANGE_SET", status: "APPLIED", operation_count: 43 }))
      .toBe("已原子应用 43 项变更，状态：APPLIED。");
  });
});
