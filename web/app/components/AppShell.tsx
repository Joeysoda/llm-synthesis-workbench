"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navigation = [
  { href: "/", label: "首页" },
];

const toolNavigation = [
  { href: "/synthetic", label: "Synthetic Data Kit" },
  { href: "/easy-dataset", label: "Easy Dataset" },
  { href: "/synlogic", label: "SynLogic" },
  { href: "/kaqg", label: "KAQG" },
  { href: "/cleanlab", label: "Cleanlab" },
];

const medicalNavigation = [
  { href: "/medical", label: "医疗数据生成" },
  { href: "/datasets", label: "医疗数据资产" },
];

const systemNavigation = [
  { href: "/tasks", label: "任务记录" },
  { href: "/settings", label: "设置" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <strong>合成数据平台</strong>
          <span>本机工作区</span>
        </div>
        <nav aria-label="主导航">
          {navigation.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            return (
              <Link
                className={active ? "nav-link active" : "nav-link"}
                href={item.href}
                key={item.href}
              >
                {item.label}
              </Link>
            );
          })}
          <div className="nav-group-label">工具中心</div>
          {toolNavigation.map((item) => {
            const active = pathname.startsWith(item.href);
            return (
              <Link className={active ? "nav-link nav-child active" : "nav-link nav-child"} href={item.href} key={item.href}>
                {item.label}
              </Link>
            );
          })}
          <div className="nav-group-label">医疗数据</div>
          {medicalNavigation.map((item) => {
            const active = pathname.startsWith(item.href);
            return (
              <Link className={active ? "nav-link active" : "nav-link"} href={item.href} key={item.href}>
                {item.label}
              </Link>
            );
          })}
          <div className="nav-group-label">运行与系统</div>
          {systemNavigation.map((item) => {
            const active = pathname.startsWith(item.href);
            return (
              <Link className={active ? "nav-link active" : "nav-link"} href={item.href} key={item.href}>
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          <span className="status-dot online" />
          仅限本机访问
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
