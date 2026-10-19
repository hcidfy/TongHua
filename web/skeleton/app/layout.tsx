import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "投资 X Buddy",
  description: "一个研究目标 → 一份带完整证据链、可复核、区分事实与推断的研究结论",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="topbar">
          <span className="brand">投资 X Buddy</span>
          <Link className="nav-link" href="/console">
            研究控制台
          </Link>
          <span className="tag">M5 动态 UI</span>
        </header>
        <main className="page">{children}</main>
      </body>
    </html>
  );
}
