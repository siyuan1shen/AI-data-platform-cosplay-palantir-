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
