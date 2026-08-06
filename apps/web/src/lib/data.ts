"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

export { ApiError };

/**
 * 数据获取客户端 seam（窄接口，可替换）：
 * - 真 adapter：api.ts 的 fetch 包装（transport 实现不动），组件以
 *   fetcher 闭包经 useApiQuery/useApiMutation 使用，seam 由测试消费；
 * - 测试 adapter：内存 fake（测试里注册路由表，实现本接口）。
 *
 * 组件不再自管 fetch 生命周期，而是经 useApiQuery/useApiMutation 声明
 * 要什么数据；请求怎么发（transport）与状态怎么管（本模块）分开。
 */
export type ApiClient = {
  request<T>(path: string, init?: RequestInit): Promise<T>;
};

export interface UseApiQueryOptions<T> {
  /** 轮询间隔（毫秒）。设置后按固定间隔重复请求，直到 stopWhen 命中。 */
  pollMs?: number;
  /** 一次性查询的失败重试次数（轮询模式不适用：轮询失败静默，由下一轮兜底）。 */
  retry?: number;
  /** 轮询停止条件：请求结果命中时停止轮询（如任务到达终态）。 */
  stopWhen?: (data: T) => boolean;
  /** 是否启用查询；false 时不发起请求（保持无数据无错误），翻转后立即请求。 */
  enabled?: boolean;
}

export interface ApiQueryResult<T> {
  data: T | null;
  error: ApiError | null;
  /** 首次加载中（有请求在途且尚无数据）；已有数据时后续请求不置 loading。 */
  loading: boolean;
  /** 任意请求在途（含 reload 与轮询每一轮）。 */
  isFetching: boolean;
  /**
   * 立即重新请求；轮询模式同时重启间隔。
   * 返回的 Promise 在该次请求结算（成功或失败）后 resolve，
   * 供「变更后先等数据就位再继续」的调用方 await。
   */
  reload: () => Promise<void>;
}

/** 重试间隔（毫秒）：失败后等待一小段再发起下一次重试，避免紧循环。 */
const RETRY_DELAY_MS = 500;

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  return new ApiError(error instanceof Error ? error.message : "请求失败。", 0);
}

/**
 * 统一查询钩子：一个组件内唯一一份 loading/error/reload 生命周期实现。
 *
 * 语义（与既有任务卡轮询一致，用单测锁死）：
 * - 挂载/key 变化时立即发起请求；key 变化与 reload 会取消在途旧请求的结果；
 * - 每次请求开始前清空 error（杜绝「新请求前忘记清 error」一类 bug）；
 * - pollMs 轮询：首次立即拉取、固定间隔、卸载清理、stopWhen 命中后停轮询；
 *   轮询失败静默（保留旧数据，下一轮重试）；
 * - retry 仅用于一次性查询：连续失败重试，耗尽后置 error。
 */
export function useApiQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  options?: UseApiQueryOptions<T>
): ApiQueryResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  // 初始 true：首帧即视为请求在途，避免「先渲染空内容再闪 loading」。
  const [isFetching, setIsFetching] = useState(true);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  const optionsRef = useRef(options);
  // 是否已成功拿到过数据（区分「首载失败」与「轮询失败」：前者置错误页）。
  const hasDataRef = useRef(false);
  // await reload() 的等待者：下一次请求结算时统一 resolve。
  const pendingReloadsRef = useRef<Set<() => void>>(new Set());
  const enabled = options?.enabled ?? true;

  // fetcher/options 每次渲染都可能是新闭包；只随 key/tick 变化而重新请求。
  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);
  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let retriesUsed = 0;
    // 新一轮请求（key 变化/reload）视作尚未拿到数据。
    hasDataRef.current = false;

    // 本次请求结算（成功或失败）后唤醒所有 await reload() 的调用方。
    const settle = (): void => {
      if (pendingReloadsRef.current.size === 0) return;
      pendingReloadsRef.current.forEach((resolve) => resolve());
      pendingReloadsRef.current.clear();
    };

    if (!enabled) {
      // 查询被禁用（如空查询）：不发请求，保持无数据无错误。
      setIsFetching(false);
      setError(null);
      settle();
      return;
    }

    const run = async (): Promise<void> => {
      if (cancelled) return;
      setIsFetching(true);
      setError(null);
      try {
        const result = await fetcherRef.current();
        if (cancelled) return;
        setData(result);
        hasDataRef.current = true;
        const current = optionsRef.current;
        const polling = current?.pollMs;
        if (polling && !(current?.stopWhen && current.stopWhen(result))) {
          setIsFetching(false);
          timer = setTimeout(() => void run(), polling);
        } else {
          setIsFetching(false);
        }
        settle();
      } catch (err) {
        if (cancelled) return;
        const current = optionsRef.current;
        if (current?.pollMs) {
          // 轮询失败静默：保留旧数据，下一轮重试；首载失败（尚无数据）
          // 置 error 供错误页展示，轮询继续以便自动恢复。
          setIsFetching(false);
          if (!hasDataRef.current) setError(toApiError(err));
          timer = setTimeout(() => void run(), current.pollMs);
          settle();
        } else if (retriesUsed < (current?.retry ?? 0)) {
          retriesUsed += 1;
          timer = setTimeout(() => void run(), RETRY_DELAY_MS);
          settle();
        } else {
          setError(toApiError(err));
          setIsFetching(false);
          settle();
        }
      }
    };

    void run();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
    // fetcher/options 走 ref，刻意不进依赖：只按 key、reload 与 enabled 触发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick, enabled]);

  const reload = useCallback(() => {
    return new Promise<void>((resolve) => {
      pendingReloadsRef.current.add(resolve);
      setTick((t) => t + 1);
    });
  }, []);

  return { data, error, loading: isFetching && data === null, isFetching, reload };
}

/**
 * 统一变更钩子：把「pending/error」从组件里收进来。
 * run 成功返回结果；失败时置 error 并重新抛出（调用方可按需
 * classifyApiError 升级为 reauth/session 流程）。
 */
export function useApiMutation<TArgs = void, TResult = unknown>(
  fetcher: (args: TArgs) => Promise<TResult>
): {
  run: (args: TArgs) => Promise<TResult>;
  pending: boolean;
  error: ApiError | null;
} {
  const fetcherRef = useRef(fetcher);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  const run = useCallback(async (args: TArgs): Promise<TResult> => {
    setPending(true);
    setError(null);
    try {
      const result = await fetcherRef.current(args);
      return result;
    } catch (err) {
      const apiError = toApiError(err);
      setError(apiError);
      throw apiError;
    } finally {
      setPending(false);
    }
  }, []);

  return { run, pending, error };
}
