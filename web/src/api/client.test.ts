import { describe, it, expect } from "vitest";
import { formatApiError, normalizeApiError, ApiRequestError } from "./client";

describe("formatApiError", () => {
  it("formats structured error with code and message", () => {
    const error = { detail: { code: "auth_required", message: "请先登录" } };
    const result = formatApiError(error, "出错了");
    expect(result).toBe("[auth_required] 请先登录");
  });

  it("falls back when message is missing but code present", () => {
    const error = { detail: { code: "bad_request" } };
    const result = formatApiError(error, "默认错误");
    expect(result).toBe("[bad_request] 默认错误");
  });

  it("returns message when no code", () => {
    const error = { detail: { message: "网络超时" } };
    const result = formatApiError(error, "出错了");
    expect(result).toBe("网络超时");
  });

  it("uses fallback for null/undefined error", () => {
    expect(formatApiError(null, "兜底文案")).toBe("兜底文案");
    expect(formatApiError(undefined, "兜底文案")).toBe("兜底文案");
  });

  it("handles ApiRequestError instance", () => {
    const err = new ApiRequestError({ code: "turn_in_progress", message: "回合结算中" }, "fallback");
    const result = formatApiError(err, "兜底");
    expect(result).toBe("[turn_in_progress] 回合结算中");
  });
});

describe("normalizeApiError", () => {
  it("extracts code and message from .detail", () => {
    const result = normalizeApiError({ detail: { code: "x", message: "y" } }, "fb");
    expect(result.code).toBe("x");
    expect(result.message).toBe("y");
  });

  it("falls back when detail.message missing", () => {
    const result = normalizeApiError({ detail: { code: "x" } }, "fb");
    expect(result.message).toBe("fb");
  });

  it("handles bare string error", () => {
    const result = normalizeApiError("plain string", "fb");
    expect(result.message).toBe("plain string");
  });
});
