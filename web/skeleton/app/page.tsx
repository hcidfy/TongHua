import { redirect } from "next/navigation";

/**
 * 云端交付入口直接落到研究控制台，避免首页仍显示 M0 占位壳导致误判为“未完成部署”。
 */
export default function Home() {
  redirect("/console");
}
