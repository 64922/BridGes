"use client";

import { Fragment, useEffect, useState } from "react";

import {
  fetchProfileOverview,
  type ProfileOverviewProjection,
  type ProfileOverviewSegmentProjection,
} from "@/lib/api";

import { emitProfileItemLocate, onProfileDataChanged } from "./profile-events";
import { formatUpdatedTime } from "./profile-format";
import styles from "./ProfileOverviewCard.module.css";

/** 无主题条目的陈述沿用中性标签展示；主题本身仍是开放词汇。 */
const UNTOPICED_LABEL = "其他信息";

const EMPTY_COPY =
  "还没有可以概括的长期信息。你可以在下面添加，或像平常一样聊天——" +
  "整理完成后，这里会出现按主题组织、每句都能点开核对依据的整体概览。";

/**
 * 画像工单 15：整体概览区。
 *
 * 概览由有效记忆原样组成：每条陈述按主题成组，每段都可点击，定位到下方
 * 对应条目的依据与编辑入口。条目、事项或使用开关变化时立即重读——整理中
 * 或部分可用时如实说明，绝不把旧概览当当前内容。
 */
export function ProfileOverviewCard() {
  const [data, setData] = useState<ProfileOverviewProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [locatedId, setLocatedId] = useState<string | null>(null);

  const load = async () => {
    try {
      setData(await fetchProfileOverview());
      setError(null);
    } catch (reason) {
      setData(null);
      setError(
        reason instanceof Error
          ? reason.message
          : "整体概览暂时无法加载，请重试。"
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

  useEffect(
    () =>
      onProfileDataChanged(() => {
        // 变化后旧概览立即失效：先显示整理中，再读取新内容；失败如实报错。
        setLocatedId(null);
        setData(null);
        void load();
      }),
    []
  );

  const locate = (segment: ProfileOverviewSegmentProjection) => {
    setLocatedId(segment.profile_item_id);
    emitProfileItemLocate(segment.profile_item_id);
  };

  const statements = data?.statements ?? [];
  const rebuilding = data !== null && data.state === "rebuilding";

  return (
    <section
      className={styles.card}
      aria-labelledby="profile-overview-title"
      data-testid="overview-card"
    >
      <h2 id="profile-overview-title" className={styles.title}>
        整体概览
      </h2>
      <p className={styles.lead}>
        这是系统目前对你的整体了解，由已有信息按主题原样汇总；点击任意一句，
        可定位到下方对应条目的依据并进入修改。
      </p>

      {data?.notice && !rebuilding ? (
        <p className={styles.notice} role="status" data-testid="overview-notice">
          {data.notice}
        </p>
      ) : null}

      {locatedId ? (
        <p
          className={styles.actionNotice}
          role="status"
          data-testid="overview-located"
        >
          已定位到下方对应条目：可展开「依据」核对，或直接点「修改」。
        </p>
      ) : null}

      {error ? (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={retry}>
            重试
          </button>
        </div>
      ) : null}

      {data === null && !error ? (
        <p className={styles.state} data-testid="overview-loading">
          正在整理整体概览…
        </p>
      ) : null}

      {rebuilding ? (
        <div className={styles.rebuilding} data-testid="overview-rebuilding">
          <p className={styles.state}>
            {data.notice ?? "概览正在整理，请稍后重新加载。"}
          </p>
          <button type="button" onClick={() => void load()}>
            重新加载
          </button>
        </div>
      ) : null}

      {data !== null && data.state === "empty" ? (
        <p className={styles.empty} data-testid="overview-empty">
          {EMPTY_COPY}
        </p>
      ) : null}

      {statements.length > 0 ? (
        <ul className={styles.statements} data-testid="overview-statements">
          {statements.map((statement) => (
            <li
              key={statement.statement_id}
              className={styles.statement}
              data-testid="overview-statement"
            >
              <p className={styles.topic}>
                {statement.topic ?? UNTOPICED_LABEL}
              </p>
              <p className={styles.text}>
                {statement.segments.map((segment, index) => (
                  <Fragment key={segment.profile_item_id}>
                    {index > 0 ? (
                      <span className={styles.separator} aria-hidden="true">
                        ；
                      </span>
                    ) : null}
                    <button
                      type="button"
                      className={styles.segment}
                      data-testid="overview-segment"
                      data-item-id={segment.profile_item_id}
                      aria-label={`定位到条目并查看依据：${segment.text}`}
                      onClick={() => locate(segment)}
                    >
                      {segment.text}
                    </button>
                  </Fragment>
                ))}
              </p>
            </li>
          ))}
        </ul>
      ) : null}

      {data?.generated_at ? (
        <p className={styles.meta} data-testid="overview-generated-at">
          最近整理于 {formatUpdatedTime(data.generated_at)}
        </p>
      ) : null}
    </section>
  );
}
