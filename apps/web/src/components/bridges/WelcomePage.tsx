/**
 * 启动欢迎页（Landing / Welcome）。
 *
 * 未登录用户访问根路径 `/` 时的第一视觉入口。整屏吸附滚动的六屏叙事：
 * 五屏品牌故事（Connection / Paper Collector / Humanizer / Personalization /
 * Learning）+ 深色结束页（welcome + 注册入口）。仅服务桌面端。
 *
 * 版式参照 lunasol-ts.webflow.io 复刻（.scratch/ 原型经设计验收）：
 * - 固定界面：左上 BridGes 图标、顶部居中字标、左侧竖排章节导航
 *   （点击跳转 + 当前屏高亮），无音频按钮；
 * - 结束页为深色场景，界面元素自动切换浅色；背景视频外加同片源模糊
 *   铺底层，避免画面外纯黑；
 * - 手写字标使用 next/font 自托管的 Caveat（仅 latin 子集），中文标题
 *   沿用 globals.css 的 --font-serif / --font-sans 令牌。
 */
"use client";

import { Caveat } from "next/font/google";
import Link from "next/link";
import { useEffect, useRef, useState, type MouseEvent } from "react";

import styles from "./welcome.module.css";

const caveat = Caveat({
  weight: ["500", "600"],
  subsets: ["latin"],
  display: "swap",
});

const END_VIDEO_URL =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260803_192301_9231ed6b-c55c-4a48-909c-4ebe11cf2e11.mp4";

interface SectionImage {
  cls: "imgL" | "imgR" | "imgTr" | "imgTl";
  src: string;
  alt: string;
  delay?: 1 | 2;
}

interface ContentSection {
  id: string;
  label: string;
  script: string;
  headline: string;
  sub: string;
  cta?: { href: string; label: string };
  images: SectionImage[];
}

const SECTIONS: ContentSection[] = [
  {
    id: "sec1",
    label: "BridGes",
    script: "Connection",
    headline: "连接你与科学知识之间的桥梁",
    sub: "希望每天 BridGes 都能陪伴你飘过万千书海",
    cta: { href: "/login", label: "开始连接" },
    images: [
      { cls: "imgL", src: "/media/welcome/sec1-left.jpg", alt: "捧着书本的男学生" },
      { cls: "imgR", src: "/media/welcome/sec1-right.jpg", alt: "台阶上学习的学生们", delay: 1 },
    ],
  },
  {
    id: "sec2",
    label: "论文抓手",
    script: "Paper Collector",
    headline: "你的私人论文搜集助理",
    sub: "读书破万卷，下笔如有神",
    images: [
      { cls: "imgL", src: "/media/welcome/sec2-left.jpg", alt: "伏案书写论文的研究者" },
      { cls: "imgR", src: "/media/welcome/sec2-right.jpg", alt: "摊开的论文与书籍", delay: 1 },
    ],
  },
  {
    id: "sec3",
    label: "人性化",
    script: "Humanizer",
    headline: "BridGes 与你的每一句交流，都带有满满人味。你解决文章 AI 味道难题",
    sub: "用更人性化的方式带你徜徉知识之海",
    images: [
      { cls: "imgL", src: "/media/welcome/sec3-left.jpg", alt: "拼图构成的人像" },
      { cls: "imgTr", src: "/media/welcome/sec3-tr.jpg", alt: "机械手与人手相触", delay: 1 },
      { cls: "imgR", src: "/media/welcome/sec3-right.jpg", alt: "人群构成的头部剪影", delay: 2 },
    ],
  },
  {
    id: "sec4",
    label: "个性化",
    script: "Personalization",
    headline: "BridGes 提供你的数字分身，在学习中画像逐渐清晰",
    sub: "个性化教学，个性化陪伴",
    images: [
      { cls: "imgTl", src: "/media/welcome/sec4-left.jpg", alt: "拼贴风格的人物肖像" },
      { cls: "imgL", src: "/media/welcome/sec4-mid.jpg", alt: "站在照片墙前的人", delay: 1 },
      { cls: "imgR", src: "/media/welcome/sec4-right.jpg", alt: "镜前对视的青年", delay: 2 },
    ],
  },
  {
    id: "sec5",
    label: "学习模式",
    script: "Learning",
    headline: "BridGes 设置特殊的学习模式，借助搜索引擎搜集海量信息助你学习",
    sub: "因材施教",
    images: [
      { cls: "imgR", src: "/media/welcome/sec5.jpg", alt: "黑板前讲解的教师", delay: 1 },
    ],
  },
];

export function WelcomePage() {
  const deckRef = useRef<HTMLElement>(null);
  const [active, setActive] = useState(SECTIONS[0].id);
  const [revealed, setRevealed] = useState<ReadonlySet<string>>(
    () => new Set([SECTIONS[0].id]),
  );

  useEffect(() => {
    const deck = deckRef.current;
    if (!deck) return;

    // 滚动驱动：当前屏高亮 + 入场动画 + 结束页切换浅色界面
    const update = () => {
      const vh = deck.clientHeight;
      const mid = deck.scrollTop + vh / 2;
      const screens = Array.from(deck.querySelectorAll<HTMLElement>("section[data-sec]"));
      let current = screens[0];
      screens.forEach((s) => {
        if (s.offsetTop <= mid) current = s;
      });
      const id = current.dataset.sec ?? SECTIONS[0].id;
      setActive(id);

      setRevealed((prev) => {
        let changed = false;
        const next = new Set(prev);
        screens.forEach((s) => {
          const sid = s.dataset.sec ?? "";
          if (next.has(sid)) return;
          const r = s.getBoundingClientRect();
          if (r.top < vh * 0.75 && r.bottom > 0) {
            next.add(sid);
            changed = true;
          }
        });
        return changed ? next : prev;
      });
    };

    deck.scrollTop = 0;
    update();
    deck.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      deck.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, []);

  const scrollTo = (id: string) => (e: MouseEvent) => {
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  };

  const onDark = active === "end";

  return (
    <div className={styles.page}>
      {/* 固定界面：左上 Logo + 顶部字标 + 左侧章节导航（无音频按钮） */}
      <div className={`${styles.chrome} ${onDark ? styles.onDark : ""}`}>
        <a className={styles.logoCorner} href="#sec1" onClick={scrollTo("sec1")} aria-label="BridGes 首页">
          {/* eslint-disable @next/next/no-img-element */}
          <img className={styles.logoLight} src="/brand/bridges-logo-icon.svg" alt="BridGes" width={40} height={40} />
          <img className={styles.logoDark} src="/brand/bridges-logo-icon-dark.svg" alt="" width={40} height={40} />
          {/* eslint-enable @next/next/no-img-element */}
        </a>
        <div className={styles.brandWord}>BridGes</div>
        <nav className={styles.sideNav} aria-label="章节导航">
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              onClick={scrollTo(s.id)}
              className={active === s.id ? styles.navActive : ""}
              aria-current={active === s.id ? "true" : undefined}
            >
              {s.label}
            </a>
          ))}
        </nav>
      </div>

      <main
        id="main-content"
        tabIndex={-1}
        data-testid="main-content"
        className={styles.deck}
        ref={deckRef}
      >
        {SECTIONS.map((s, i) => {
          const Heading = i === 0 ? "h1" : "h2";
          const inView = revealed.has(s.id);
          return (
            <section key={s.id} id={s.id} data-sec={s.id} className={`${styles.screen} ${styles[s.id]}`}>
              {s.images.map((img) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={img.cls}
                  className={[
                    styles.ph,
                    styles[img.cls],
                    styles.reveal,
                    img.delay ? styles[`d${img.delay}`] : "",
                    inView ? styles.in : "",
                  ].join(" ")}
                  src={img.src}
                  alt={img.alt}
                />
              ))}
              <div className={styles.heroText}>
                <div className={`${styles.script} ${caveat.className} ${styles.reveal} ${inView ? styles.in : ""}`}>
                  {s.script}
                </div>
                <Heading className={`${styles.headline} ${styles.reveal} ${styles.d1} ${inView ? styles.in : ""}`}>
                  {s.headline}
                </Heading>
                <p className={`${styles.sub} ${styles.reveal} ${styles.d2} ${inView ? styles.in : ""}`}>
                  {s.sub}
                </p>
                {s.cta && (
                  <Link
                    href={s.cta.href}
                    className={`${styles.cta} ${styles.reveal} ${styles.d3} ${inView ? styles.in : ""}`}
                  >
                    {s.cta.label}
                  </Link>
                )}
              </div>
            </section>
          );
        })}

        {/* 结束页：背景视频 + 注册入口 */}
        <section id="end" data-sec="end" className={`${styles.screen} ${styles.end}`}>
          <div className={styles.bgVideo} aria-hidden="true">
            <video src={END_VIDEO_URL} autoPlay muted loop playsInline tabIndex={-1} />
          </div>
          <div className={styles.videoFrame}>
            <video src={END_VIDEO_URL} autoPlay muted loop playsInline aria-label="森林小径上独行的人" />
          </div>
          <div className={styles.heroText}>
            <div
              className={`${styles.script} ${caveat.className} ${styles.reveal} ${revealed.has("end") ? styles.in : ""}`}
            >
              welcome
            </div>
            <Link
              href="/register"
              className={`${styles.cta} ${styles.reveal} ${styles.d2} ${revealed.has("end") ? styles.in : ""}`}
            >
              注册
            </Link>
          </div>
        </section>
      </main>
    </div>
  );
}
