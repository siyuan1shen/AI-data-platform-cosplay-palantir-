import { beforeEach, describe, expect, it, vi } from "vitest";
import { appConfig } from "../app/config";
import { api } from "./index";
import { request } from "./http";

describe("语义映射候选 API 客户端", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("把项目、数据源、资产和已有映射开关完整编码到只读请求", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, warnings: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await api.suggestSemanticMappings("project/1", {
      target_type_key: "role",
      source_system_id: "source/2",
      source_asset: "岗位表.csv",
      include_existing: true,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/semantic-mapping-suggestions`
      + "?target_type_key=role&source_system_id=source%2F2"
      + "&source_asset=%E5%B2%97%E4%BD%8D%E8%A1%A8.csv&include_existing=true",
    );
    expect(init).toEqual(expect.objectContaining({ headers: expect.any(Headers) }));
    expect((init?.headers as Headers).get("accept")).toBe("application/json");
  });

  it("把后端结构化校验详情转换成可读错误，而不是显示 object Object", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        error: {
          code: "REQUEST_VALIDATION_FAILED",
          message: "请检查输入内容。",
          details: [
            { path: "name", message: "长度不足，请检查最小长度要求", type: "string_too_short" },
            { path: "industry", message: "不能为空", type: "missing" },
          ],
          trace_id: "trace-1",
        },
      }), {
        status: 422,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(request("/api/v3/companies", { method: "POST", body: "{}" })).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      message: "请检查输入内容。 name：长度不足，请检查最小长度要求 industry：不能为空（追踪 ID：trace-1）",
    });
  });

  it("兼容没有 trace_id 的标准错误响应", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        error: {
          code: "SOURCE_NOT_FOUND",
          message: "没有找到数据源。",
          details: [],
        },
      }), {
        status: 404,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(request("/api/v3/projects/missing/source-systems/nope")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      message: "没有找到数据源。",
      traceId: "",
    });
  });
});

describe("潜在认识 API 客户端", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("使用后端现有的项目路由，并为修订和人工状态操作发送版本与理由", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const projectId = "project/1";
    const recordId = "record/2";

    await api.listPotentialRecords(projectId, true);
    await api.createPotentialRecord(projectId, {
      company_id: "company-1", project_id: projectId, potential_type: "PROBLEM_HYPOTHESIS",
      claim: "待检验主张", applicability_scope: "某部门", valid_from: null, valid_until: null,
      task_source: "手工记录", supporting_evidence: [], counterevidence: [],
      verification_method: "后续核对", evidence_status: "UNTESTED",
    });
    await api.editPotentialRecord(projectId, recordId, { expected_version: 4, reason: "补充依据", claim: "修订后的主张" });
    await api.getPotentialRecordHistory(projectId, recordId);
    await api.acceptPotentialRecord(projectId, recordId, { expected_version: 5, reason: "重新纳入" });
    await api.rejectPotentialRecord(projectId, recordId, { expected_version: 6, reason: "缺少支持" });
    await api.withdrawPotentialRecord(projectId, recordId, { expected_version: 7, reason: "不再适用" });

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/potential-records?include_history=true`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/potential-records`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/potential-records/record%2F2`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/potential-records/record%2F2/history`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/potential-records/record%2F2/accept`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/potential-records/record%2F2/reject`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/potential-records/record%2F2/withdraw`,
    ]);
    expect(fetchMock.mock.calls[2][1]).toEqual(expect.objectContaining({ method: "PATCH" }));
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({ expected_version: 4, reason: "补充依据", claim: "修订后的主张" });
    for (const callIndex of [4, 5, 6]) {
      expect(fetchMock.mock.calls[callIndex][1]).toEqual(expect.objectContaining({ method: "POST" }));
      expect(JSON.parse(String(fetchMock.mock.calls[callIndex][1]?.body))).toEqual(expect.objectContaining({ expected_version: callIndex + 1, reason: expect.any(String) }));
    }
  });
});

describe("观察附件 API 客户端", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("以 multipart 上传文件并通过实际附件路由列出和下载原文件", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/attachments/attachment%2F3")) {
        return new Response(new Blob(["original bytes"], { type: "text/plain" }), {
          status: 200,
          headers: { "content-type": "text/plain", "content-disposition": "attachment; filename*=UTF-8''interview.txt" },
        });
      }
      return new Response(JSON.stringify(url.endsWith("/attachments") ? { items: [], total: 0 } : {}), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    });
    const file = new File(["source material"], "interview.txt", { type: "text/plain" });

    await api.ingestManagementObservation("project/1", {
      file, observation_kind: "MEETING", title: "访谈原文", occurred_at: "2026-09-12T10:00:00Z",
    });
    await api.listManagementObservationAttachments("project/1", "observation/2");
    const downloaded = await api.downloadManagementObservationAttachment("project/1", "observation/2", "attachment/3");

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/observation-ingestions`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/observations/observation%2F2/attachments`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/observations/observation%2F2/attachments/attachment%2F3`,
    ]);
    const uploadInit = fetchMock.mock.calls[0][1];
    expect(uploadInit).toEqual(expect.objectContaining({ method: "POST" }));
    expect((uploadInit?.headers as Headers).get("content-type")).toBeNull();
    const body = uploadInit?.body as FormData;
    expect(body.get("file")).toBeInstanceOf(File);
    expect((body.get("file") as File).name).toBe("interview.txt");
    expect(body.get("observation_kind")).toBe("MEETING");
    expect(body.get("title")).toBe("访谈原文");
    expect(body.get("occurred_at")).toBe("2026-09-12T10:00:00Z");
    expect(fetchMock.mock.calls[2][1]?.headers).toEqual(expect.objectContaining({ Accept: "application/octet-stream" }));
    expect(downloaded.fileName).toBe("interview.txt");
    expect(await downloaded.blob.text()).toBe("original bytes");
  });
});

describe("现实管理行动 API 客户端", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("访问管理行动各路由，并在所有变更请求中传递当前 revision", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({}), { status: 200, headers: { "content-type": "application/json" } }),
    );
    const projectId = "project/1";
    const actionId = "action/2";
    const event = { expected_revision: 3, message: "已完成首轮核对。", reason: "同步进展", details: { percent: 40 } };

    await api.listManagementActions(projectId, true);
    await api.createManagementAction(projectId, { title: "梳理交付流程", priority: "HIGH", owner: "运营负责人", due_at: "2026-09-30T00:00:00Z", reason: "缩短交付周期", idempotency_key: "submission-key-1" });
    await api.getManagementAction(projectId, actionId);
    await api.updateManagementAction(projectId, actionId, { expected_revision: 1, owner: "新负责人", status: "IN_PROGRESS", due_at: "2026-10-01T00:00:00Z", reason: "重新分工" });
    await api.appendManagementActionProgress(projectId, actionId, event);
    await api.appendManagementActionOutcome(projectId, actionId, { ...event, expected_revision: 4, message: "交付周期下降一天。" });
    await api.reportManagementActionDone(projectId, actionId, { ...event, expected_revision: 5, message: "执行人报告已完成。" });
    await api.verifyManagementActionDone(projectId, actionId, { expected_revision: 6, verification_note: "已检查凭据并确认完成。", reason: "独立核实" });
    await api.cancelManagementAction(projectId, actionId, { expected_revision: 7, reason: "取消原因" });
    await api.getManagementActionHistory(projectId, actionId);

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions?include_cancelled=true&limit=100&offset=0`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions/action%2F2`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions/action%2F2`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions/action%2F2/progress`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions/action%2F2/outcomes`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions/action%2F2/report-done`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions/action%2F2/verify-done`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions/action%2F2/cancel`,
      `${appConfig.apiBaseUrl}/api/v3/projects/project%2F1/management/actions/action%2F2/history?offset=0&limit=100`,
    ]);
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "POST" }));
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual(expect.objectContaining({ title: "梳理交付流程", priority: "HIGH", idempotency_key: "submission-key-1" }));
    expect(fetchMock.mock.calls[3][1]).toEqual(expect.objectContaining({ method: "PATCH" }));
    expect(JSON.parse(String(fetchMock.mock.calls[3][1]?.body))).toEqual(expect.objectContaining({ expected_revision: 1, owner: "新负责人", status: "IN_PROGRESS" }));
    for (const [callIndex, revision] of [[4, 3], [5, 4], [6, 5], [7, 6], [8, 7]] as const) {
      expect(JSON.parse(String(fetchMock.mock.calls[callIndex][1]?.body))).toEqual(expect.objectContaining({ expected_revision: revision }));
    }
  });
});
