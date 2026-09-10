import { appConfig } from "../app/config";
import type { ErrorResponse } from "./types";

export class ApiError extends Error {
  readonly code: string;
  readonly details: unknown;
  readonly traceId: string;
  readonly status: number;

  constructor(status: number, payload: ErrorResponse) {
    const message = formatErrorMessage(payload.error.message, payload.error.details);
    const traceSuffix = payload.error.trace_id ? `（追踪 ID：${payload.error.trace_id}）` : "";
    super(`${message}${traceSuffix}`);
    this.name = "ApiError";
    this.status = status;
    this.code = payload.error.code;
    this.details = payload.error.details;
    this.traceId = payload.error.trace_id ?? "";
  }
}

function formatErrorMessage(message: string, details: unknown): string {
  const detailLines = Array.isArray(details)
    ? details
        .map((detail) => {
          if (typeof detail === "string") return detail;
          if (!detail || typeof detail !== "object") return String(detail);
          const item = detail as { path?: unknown; message?: unknown };
          const path = typeof item.path === "string" ? item.path : "";
          const serialized = JSON.stringify(detail);
          const detailMessage = typeof item.message === "string"
            ? item.message
            : serialized ?? "输入无效";
          return path && detailMessage ? `${path}：${detailMessage}` : detailMessage;
        })
        .filter((detail): detail is string => Boolean(detail))
    : [];

  return [message, ...detailLines].filter(Boolean).join(" ");
}

const isErrorResponse = (value: unknown): value is ErrorResponse => {
  if (!value || typeof value !== "object" || !("error" in value)) return false;
  const error = (value as { error?: unknown }).error;
  return Boolean(
    error &&
      typeof error === "object" &&
      "code" in error &&
      typeof (error as { code?: unknown }).code === "string" &&
      "message" in error &&
      typeof (error as { message?: unknown }).message === "string",
  );
};

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("Accept", "application/json");

  let response: Response;
  try {
    response = await fetch(`${appConfig.apiBaseUrl}${path}`, { ...init, headers });
  } catch {
    throw new Error(`无法连接后端服务（${appConfig.apiBaseUrl}）。请确认后端已经启动。`);
  }

  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    if (isErrorResponse(payload)) throw new ApiError(response.status, payload);
    throw new Error(`服务请求失败（HTTP ${response.status}）`);
  }

  return payload as T;
}

export interface DownloadedFile {
  blob: Blob;
  fileName: string;
}

export async function download(path: string): Promise<DownloadedFile> {
  let response: Response;
  try {
    response = await fetch(`${appConfig.apiBaseUrl}${path}`, {
      headers: { Accept: "application/octet-stream" },
    });
  } catch {
    throw new Error(`无法连接后端服务（${appConfig.apiBaseUrl}）。请确认后端已经启动。`);
  }

  if (!response.ok) {
    const contentType = response.headers.get("content-type") ?? "";
    const payload: unknown = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (isErrorResponse(payload)) throw new ApiError(response.status, payload);
    throw new Error(`文件下载失败（HTTP ${response.status}）`);
  }

  const disposition = response.headers.get("content-disposition") ?? "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  return {
    blob: await response.blob(),
    fileName: encoded ? decodeURIComponent(encoded) : plain ?? "enterprise-insight-export",
  };
}
