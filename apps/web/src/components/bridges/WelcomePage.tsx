/**
 * 启动欢迎页（Landing / Welcome）。
 *
 * 未登录用户访问根路径 `/` 时的第一视觉入口：全屏品牌视频背景 +
 * 居中品牌字标与简介 + 右上登录/注册入口。仅服务桌面端。
 *
 * 视觉令牌映射自 globals.css 深色主题（页面在视频之上恒为暗色场景，
 * 不随 data-theme 切换）：文字 #f5f2ea、强调色 #e1936b / hover #eaa582、
 * 强调色文字 #241207。视频滤镜与遮罩取值经设计验收（.scratch/welcome-design）。
 */
import Link from "next/link";

import { BrandLogo } from "@/components/bridges/BrandLogo";

import styles from "./welcome.module.css";

export function WelcomePage() {
  return (
    <div className={styles.root}>
      {/* 背景视频：自动播放 / 循环 / 静音 / 内联；对辅助技术隐藏，不拦截交互 */}
      <video
        className={styles.bgVideo}
        autoPlay
        muted
        loop
        playsInline
        aria-hidden="true"
        tabIndex={-1}
        poster="/media/welcome-poster.jpg"
      >
        <source src="/media/welcome.mp4" type="video/mp4" />
      </video>
      <div className={styles.bgOverlay} />

      <header className={styles.topbar}>
        <Link href="/" className={styles.brand} aria-label="BridGes 首页">
          <BrandLogo variant="icon" theme="dark" width={32} />
        </Link>
        <nav className={styles.navActions} aria-label="账户">
          <Link href="/login" className={`${styles.btn} ${styles.btnGhost}`}>
            登录
          </Link>
          <Link href="/register" className={`${styles.btn} ${styles.btnPrimary}`}>
            注册
          </Link>
        </nav>
      </header>

      <main
        id="main-content"
        tabIndex={-1}
        data-testid="main-content"
        className={styles.hero}
      >
        <h1 className={styles.wordmark}>BridGes</h1>
        <p className={styles.tagline}>
          连接你与知识的桥梁，不仅是你的益友，更是一位良师。
          <br />
          在人性化的交流中越来越懂你。
        </p>
      </main>
    </div>
  );
}
