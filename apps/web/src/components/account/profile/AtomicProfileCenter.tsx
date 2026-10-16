"use client";

import { useEffect, useMemo, useState } from "react";

import {
  createAtomicProfileItem,
  listAtomicProfileItems,
  type AtomicProfileItemProjection,
} from "@/lib/api";

import { AtomicProfileItemRow } from "./AtomicProfileItemRow";
import { emitProfileDataChanged, onProfileItemLocate } from "./profile-events";
import { parseTopics } from "./profile-format";
import styles from "./AtomicProfileCenter.module.css";

const EMPTY_COPY =
  "这里还没有长期信息。点「添加一条信息」可以自己记下一条，" +
  "也可以像平常一样聊天——系统只从你明确说过、能引用原话的消息里整理稳定" +
  "事实，不会推断人格或心理状态；聊到相关内容的下一轮，也会按任务需要少量取用。";

type TopicFilter =
  | { kind: "all" }
  | { kind: "topic"; topic: string }
  | { kind: "untopiced" };

type TopicFacet = { topic: string; count: number };

/**
 * 画像工单 15：开放主题筛选的确定性汇总。
 *
 * 主题是开放、多选词汇：多主题条目在每个命中主题下都能找到，但同一次
 * 浏览中每条只出现一次（不复制事实）；没有主题的条目单独成组，不被丢弃。
 */
function buildTopicFacets(items: AtomicProfileItemProjection[]): {
  topics: TopicFacet[];
  untopicedCount: number;
} {
  const counts = new Map<string, number>();
  let untopicedCount = 0;
  for (const item of items) {
    const topics = Array.from(new Set(item.topics ?? []));
    if (topics.length === 0) {
      untopicedCount += 1;
      continue;
    }
    for (const topic of topics) {
      counts.set(topic, (counts.get(topic) ?? 0) + 1);
    }
  }
  const topics = Array.from(counts.entries()).map(([topic, count]) => ({
    topic,
    count,
  }));
  topics.sort(
    (a, b) =>
      b.count - a.count || (a.topic < b.topic ? -1 : a.topic > b.topic ? 1 : 0)
  );
  return { topics, untopicedCount };
}

function matchesFilter(
  item: AtomicProfileItemProjection,
  filter: TopicFilter
): boolean {
  const topics = item.topics ?? [];
  if (filter.kind === "all") return true;
  if (filter.kind === "untopiced") return topics.length === 0;
  return topics.includes(filter.topic);
}

/**
 * V2 Issue 08：无固定类别的原子长期信息列表。
 *
 * 每行始终可见「修改」与「删除」：修改在本行内完成，保存或取消都在原地；
 * 删除先经确认，成功后从列表移除（后台写入删除墓碑，旧消息重放不会复活）。
 * 列表不分组、不展示类别与把握度，空态说明提取边界。
 *
 * 画像工单 15：列表顶部提供开放主题筛选（全部、各主题、无主题），多主题
 * 条目在任一所选主题下都能找到且只出现一次；主题调整不改变事实身份。整体
 * 概览点击某句时定位到对应条目：滚动、聚焦并展开其依据。
 */
export function AtomicProfileCenter() {
  const [items, setItems] = useState<AtomicProfileItemProjection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<TopicFilter>({ kind: "all" });
  const [locatedId, setLocatedId] = useState<string | null>(null);
  const [locateNonce, setLocateNonce] = useState(0);
  const [adding, setAdding] = useState(false);
  const [addDraft, setAddDraft] = useState("");
  const [addTopics, setAddTopics] = useState("");
  const [addPurpose, setAddPurpose] = useState("");
  const [addBusy, setAddBusy] = useState(false);

  const load = async () => {
    try {
      setItems(await listAtomicProfileItems());
      setError(null);
    } catch (reason) {
      setItems(null);
      setError(
        reason instanceof Error ? reason.message : "长期信息暂时无法加载，请重试。"
      );
    }
  };

  const retry = () => {
    setError(null);
    void load();
  };

  useEffect(() => {
    void load();
  }, []);

  // 概览点击定位：切回「全部」保证目标可见，再由对应行滚动、聚焦并展开依据。
  useEffect(
    () =>
      onProfileItemLocate((itemId) => {
        setFilter({ kind: "all" });
        setLocatedId(itemId);
        setLocateNonce((value) => value + 1);
      }),
    []
  );

  const facets = useMemo(() => buildTopicFacets(items ?? []), [items]);
  // 选中主题在条目变化后不存在时，如实回到「全部」，不留一个空列表。
  const activeFilter = useMemo<TopicFilter>(() => {
    if (
      filter.kind === "topic" &&
      !facets.topics.some((facet) => facet.topic === filter.topic)
    ) {
      return { kind: "all" };
    }
    if (filter.kind === "untopiced" && facets.untopicedCount === 0) {
      return { kind: "all" };
    }
    return filter;
  }, [filter, facets]);
  const visibleItems = useMemo(
    () => (items ?? []).filter((item) => matchesFilter(item, activeFilter)),
    [items, activeFilter]
  );

  const handleItemUpdated = (updated: AtomicProfileItemProjection) => {
    setItems(
      (previous) =>
        previous?.map((item) =>
          item.profile_item_id === updated.profile_item_id ? updated : item
        ) ?? previous
    );
    // 条目变化让服务端概览立即失效；概览区据此重读，不继续展示旧内容。
    emitProfileDataChanged();
  };

  const handleItemDeleted = (itemId: string) => {
    setItems(
      (previous) =>
        previous?.filter((item) => item.profile_item_id !== itemId) ?? previous
    );
    if (locatedId === itemId) setLocatedId(null);
    emitProfileDataChanged();
  };

  const startAdd = () => {
    setError(null);
    setAdding(true);
  };

  const cancelAdd = () => {
    setAdding(false);
    setAddDraft("");
    setAddTopics("");
    setAddPurpose("");
  };

  const submitAdd = async () => {
    const text = addDraft.trim();
    if (!text) return;
    try {
      setAddBusy(true);
      // 手动添加与聊天「记住」同一条用户权威路径：重复内容合并到既有
      // 条目，服务端返回落库后的条目（可能是被合并的旧条目）。
      const created = await createAtomicProfileItem({
        text,
        topics: parseTopics(addTopics),
        usage_purpose: addPurpose.trim(),
      });
      setItems((previous) => {
        if (previous === null) return [created];
        const exists = previous.some(
          (item) => item.profile_item_id === created.profile_item_id
        );
        return exists
          ? previous.map((item) =>
              item.profile_item_id === created.profile_item_id ? created : item
            )
          : [...previous, created];
      });
      cancelAdd();
      emitProfileDataChanged();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "长期信息添加失败，请重试。"
      );
    } finally {
      setAddBusy(false);
    }
  };

  return (
    <section className={styles.page} aria-labelledby="atomic-profile-title">
      <header className={styles.header}>
        <p className={styles.eyebrow}>关于你的信息</p>
        <h1 id="atomic-profile-title">你的信息</h1>
        <p className={styles.lead}>
          这些是系统从对话里整理出的长期信息，按条列出。你可以逐条修改或删除，
          你的修改优先于自动整理；展开某条可以查看它的原话依据、来源与有效期，
          也可以按主题筛选查看。
        </p>
      </header>

      <div className={styles.addSection}>
        {adding ? (
          <div className={styles.addForm}>
            <textarea
              className={styles.editor}
              value={addDraft}
              maxLength={1000}
              rows={3}
              aria-label="要添加的长期信息"
              placeholder="例如：我养了一只猫叫团子"
              onChange={(event) => setAddDraft(event.target.value)}
            />
            <input
              className={styles.topicsInput}
              value={addTopics}
              maxLength={200}
              aria-label="长期信息主题"
              placeholder="主题（可选，用逗号分隔，如：兴趣与喜好）"
              onChange={(event) => setAddTopics(event.target.value)}
            />
            <input
              className={styles.topicsInput}
              value={addPurpose}
              maxLength={60}
              aria-label="长期信息用途"
              placeholder="用途（可选；健康等敏感信息需说明用途，如：户外建议）"
              onChange={(event) => setAddPurpose(event.target.value)}
            />
            <div className={styles.actions}>
              <button type="button" onClick={cancelAdd} disabled={addBusy}>
                取消
              </button>
              <button
                type="button"
                className={styles.primary}
                disabled={!addDraft.trim() || addBusy}
                onClick={() => void submitAdd()}
              >
                添加
              </button>
            </div>
          </div>
        ) : (
          <button type="button" onClick={startAdd}>
            添加一条信息
          </button>
        )}
      </div>

      {error ? (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={retry}>
            重试
          </button>
        </div>
      ) : null}

      {items === null && !error ? (
        <p className={styles.state} data-testid="atomic-loading">
          正在加载信息…
        </p>
      ) : null}

      {items !== null && items.length === 0 ? (
        <p className={styles.empty} data-testid="atomic-empty">
          {EMPTY_COPY}
        </p>
      ) : null}

      {items !== null && items.length > 0 ? (
        <div
          className={styles.topicFilter}
          role="group"
          aria-label="按主题筛选信息"
          data-testid="atomic-topic-filter"
        >
          <button
            type="button"
            aria-pressed={activeFilter.kind === "all"}
            onClick={() => setFilter({ kind: "all" })}
          >
            全部（{items.length} 条）
          </button>
          {facets.topics.map((facet) => (
            <button
              key={facet.topic}
              type="button"
              aria-pressed={
                activeFilter.kind === "topic" &&
                activeFilter.topic === facet.topic
              }
              aria-label={`${facet.topic}，共 ${facet.count} 条`}
              data-testid="atomic-topic-facet"
              onClick={() => setFilter({ kind: "topic", topic: facet.topic })}
            >
              {facet.topic}
            </button>
          ))}
          {facets.untopicedCount > 0 ? (
            <button
              type="button"
              aria-pressed={activeFilter.kind === "untopiced"}
              aria-label={`无主题，共 ${facets.untopicedCount} 条`}
              data-testid="atomic-untopiced-filter"
              onClick={() => setFilter({ kind: "untopiced" })}
            >
              无主题
            </button>
          ) : null}
        </div>
      ) : null}

      {items !== null && items.length > 0 ? (
        <ul className={styles.items}>
          {visibleItems.map((item) => (
            <AtomicProfileItemRow
              key={item.profile_item_id}
              item={item}
              located={locatedId === item.profile_item_id}
              locateNonce={locateNonce}
              onItemUpdated={handleItemUpdated}
              onItemDeleted={handleItemDeleted}
              onRefresh={load}
              onError={setError}
            />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
