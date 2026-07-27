"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navigation = [
  { href: "/", label: "总览" },
  { href: "/synthetic", label: "Synthetic Data Kit" },
  { href: "/easy-dataset", label: "Easy Dataset" },
  { href: "/synlogic", label: "SynLogic" },
  { href: "/tasks", label: "已完成任务" },
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
