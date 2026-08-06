import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type ApiClient, ApiError, useApiMutation, useApiQuery } from "@/lib/data";

/**
 * 内存 fake adapter（测试里注册路由表）：实现 ApiClient seam，
 * 让 hook 生命周期测试完全脱离网络与 fetch。
 */
class MemoryApiClient implements ApiClient {
  private routes = new Map<string, () => unknown>();

  register<T>(path: string, handler: () => T): void {
    this.routes.set(path, handler);
  }

  async request<T>(path: string, _init?: RequestInit): Promise<T> {
    const handler = this.routes.get(path);
    if (!handler) throw new ApiError(`未注册路由：${path}`, 404);
    return handler() as T;
  }
}

/** 冲刷微任务队列，让 in-flight 的 Promise 结算并应用状态更新。 */
async function flush(): Promise<void> {
  await act(async () => {});
}

/** 推进假时钟并冲刷异步链（setTimeout 链内还有 await）。 */
async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useApiQuery 生命周期", () => {
  it("首次加载：loading 由真到假，data 就位且 error 为空", async () => {
    const client = new MemoryApiClient();
    client.register("/items", () => ["a", "b"]);
    const { result } = renderHook(() =>
      useApiQuery("items", () => client.request<string[]>("/items"))
    );

    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBeNull();

    await flush();

    expect(result.current.loading).toBe(false);
    expect(result.current.isFetching).toBe(false);
    expect(result.current.data).toEqual(["a", "b"]);
    expect(result.current.error).toBeNull();
  });

  it("请求失败：error 置为 ApiError，loading 归位", async () => {
    const fetcher = vi.fn().mockRejectedValue(new ApiError("服务不可用", 503));
    const { result } = renderHook(() => useApiQuery("k", fetcher));

    await flush();

    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.error?.message).toBe("服务不可用");
    expect(result.current.loading).toBe(false);
  });

  it("非 ApiError 的异常包装为 ApiError", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useApiQuery("k", fetcher));

    await flush();

    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.error?.message).toBe("boom");
  });

  it("reload 返回的 Promise 在数据就位后 resolve（变更后先等数据再继续）", async () => {
    const client = new MemoryApiClient();
    let value = "v1";
    client.register("/v", () => value);
    const fetcher = () => client.request<string>("/v");
    const { result } = renderHook(() => useApiQuery("k", fetcher));

    await flush();
    expect(result.current.data).toBe("v1");

    let reloaded = false;
    act(() => {
      value = "v2";
      void result.current.reload().then(() => {
        reloaded = true;
      });
    });
    await flush();
    expect(reloaded).toBe(true);
    expect(result.current.data).toBe("v2");
  });

  it("新请求前清空旧 error（reload 成功恢复）", async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new ApiError("服务不可用", 503))
      .mockResolvedValueOnce("ok");
    const { result } = renderHook(() => useApiQuery("k", fetcher));

    await flush();
    expect(result.current.error?.message).toBe("服务不可用");

    act(() => {
      void result.current.reload();
    });
    await flush();

    expect(result.current.error).toBeNull();
    expect(result.current.data).toBe("ok");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("enabled=false 时不发请求，翻转后立即请求（空查询守卫）", async () => {
    const fetcher = vi.fn(async () => "ok");
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => useApiQuery("k", fetcher, { enabled }),
      { initialProps: { enabled: false } }
    );

    await flush();
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);

    rerender({ enabled: true });
    await flush();

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBe("ok");
  });

  it("key 变化触发重新请求，旧数据保留到新数据到达", async () => {
    const client = new MemoryApiClient();
    client.register("/v/a", () => "A");
    client.register("/v/b", () => "B");
    const fetcher = (k: string) => () => client.request<string>(`/v/${k}`);
    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useApiQuery(k, fetcher(k)),
      { initialProps: { k: "a" } }
    );

    await flush();
    expect(result.current.data).toBe("A");

    rerender({ k: "b" });
    await flush();

    expect(result.current.data).toBe("B");
  });
});

describe("useApiQuery 轮询", () => {
  it("pollMs：首次立即拉取，固定间隔轮询，data 随轮更新", async () => {
    vi.useFakeTimers();
    const client = new MemoryApiClient();
    let count = 0;
    client.register("/task", () => {
      count += 1;
      return { status: count <= 1 ? "queued" : "running" };
    });
    const fetcher = vi.fn(() => client.request<{ status: string }>("/task"));
    const { result } = renderHook(() =>
      useApiQuery("t", fetcher, { pollMs: 5000 })
    );

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.loading).toBe(false);

    await advance(5000);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.data?.status).toBe("running");

    await advance(5000);
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(result.current.data?.status).toBe("running");
  });

  it("stopWhen 命中后停止轮询（成功停轮询）", async () => {
    vi.useFakeTimers();
    const client = new MemoryApiClient();
    let status = "queued";
    client.register("/task", () => ({ status }));
    const fetcher = vi.fn(() => client.request<{ status: string }>("/task"));
    const { result } = renderHook(() =>
      useApiQuery("t", fetcher, {
        pollMs: 5000,
        stopWhen: (data) => data.status !== "queued",
      })
    );

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);

    status = "succeeded";
    await advance(5000);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.data?.status).toBe("succeeded");

    await advance(5000);
    await advance(5000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("轮询失败静默：保留旧数据不置 error，下一轮继续", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("v1")
      .mockRejectedValueOnce(new ApiError("网络错误", 0));
    const { result } = renderHook(() =>
      useApiQuery("t", fetcher, { pollMs: 5000 })
    );

    await flush();
    expect(result.current.data).toBe("v1");

    await advance(5000);
    expect(result.current.data).toBe("v1");
    expect(result.current.error).toBeNull();

    await advance(5000);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("首载失败（轮询模式）：置 error 供错误页展示，轮询继续可自动恢复", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new ApiError("服务不可用", 503))
      .mockResolvedValueOnce("v1");
    const { result } = renderHook(() =>
      useApiQuery("t", fetcher, { pollMs: 5000 })
    );

    await flush();
    expect(result.current.error?.message).toBe("服务不可用");
    expect(result.current.loading).toBe(false);

    await advance(5000);
    expect(result.current.error).toBeNull();
    expect(result.current.data).toBe("v1");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("轮询间隙 isFetching 为 false，仅在请求在途为 true", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => "ok");
    const { result } = renderHook(() =>
      useApiQuery("t", fetcher, { pollMs: 5000 })
    );

    await flush();
    expect(result.current.isFetching).toBe(false);

    await advance(5000);
    expect(result.current.isFetching).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("卸载后停止轮询（stop-on-unmount）", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => "ok");
    const { unmount } = renderHook(() =>
      useApiQuery("t", fetcher, { pollMs: 5000 })
    );

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);

    unmount();
    await advance(5000);
    await advance(5000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("useApiQuery 重试", () => {
  it("retry：连续失败按间隔重试，成功后数据就位", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new ApiError("e1", 500))
      .mockRejectedValueOnce(new ApiError("e2", 500))
      .mockResolvedValueOnce("ok");
    const { result } = renderHook(() =>
      useApiQuery("k", fetcher, { retry: 2 })
    );

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);

    await advance(500);
    expect(fetcher).toHaveBeenCalledTimes(2);

    await advance(500);
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(result.current.data).toBe("ok");
    expect(result.current.error).toBeNull();
  });

  it("retry 耗尽后置 error", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockRejectedValue(new ApiError("失败", 500));
    const { result } = renderHook(() =>
      useApiQuery("k", fetcher, { retry: 1 })
    );

    await flush();
    await advance(500);

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.error?.message).toBe("失败");
    expect(result.current.loading).toBe(false);
  });
});

describe("useApiMutation", () => {
  it("成功：返回结果，pending 归位，error 为空", async () => {
    const fetcher = vi.fn(async (n: number) => n * 2);
    const { result } = renderHook(() => useApiMutation<number, number>(fetcher));

    let returned: number | undefined;
    await act(async () => {
      returned = await result.current.run(21);
    });

    expect(returned).toBe(42);
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("在途：pending 为 true", async () => {
    let resolve!: (value: string) => void;
    const fetcher = vi.fn(
      () => new Promise<string>((r) => { resolve = r; })
    );
    const { result } = renderHook(() => useApiMutation<string, string>(fetcher));

    let promise!: Promise<string>;
    act(() => {
      promise = result.current.run("x");
    });
    expect(result.current.pending).toBe(true);

    await act(async () => {
      resolve("ok");
      await promise;
    });
    expect(result.current.pending).toBe(false);
  });

  it("失败：置 error 并重新抛出（调用方可 classify 升级）", async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValue(new ApiError("需要重新认证", 403, "reauth_required"));
    const { result } = renderHook(() => useApiMutation<unknown, unknown>(fetcher));

    await act(async () => {
      await expect(result.current.run({})).rejects.toMatchObject({
        code: "reauth_required",
      });
    });

    expect(result.current.error?.code).toBe("reauth_required");
    expect(result.current.pending).toBe(false);
  });

  it("新请求前清空旧 error", async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new ApiError("旧错误", 500))
      .mockResolvedValueOnce("ok");
    const { result } = renderHook(() => useApiMutation<string, string>(fetcher));

    await act(async () => {
      await expect(result.current.run("a")).rejects.toBeInstanceOf(ApiError);
    });
    expect(result.current.error?.message).toBe("旧错误");

    await act(async () => {
      await result.current.run("b");
    });
    expect(result.current.error).toBeNull();
  });
});
