"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const links = [
  { href: "/dashboard", label: "Analyze" },
  { href: "/chat", label: "Ask" },
  { href: "/settings", label: "Connect" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [domain, setDomain] = useState("");

  useEffect(() => {
    setUsername(localStorage.getItem("ryze_user") || "");
    setDomain(localStorage.getItem("ryze_domain") || "");
  }, []);

  return (
    <div style={{ minHeight: "100vh" }}>
      <header className="app-header">
        <div className="app-brand">
          <div className="app-brand__name">AI/BI</div>
          <div className="app-brand__tagline">
            {domain ? domain.toUpperCase() : "WORKSPACE"} · natural language → SQL → insights
          </div>
        </div>
        <nav className="app-navigation">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={`app-navigation__link${pathname === l.href ? " app-navigation__link--active" : ""}`}
            >
              {l.label}
            </Link>
          ))}
          <span className="app-navigation__user">{username} · {domain}</span>
          <button
            onClick={() => {
              localStorage.clear();
              router.push("/login");
            }}
            className="app-navigation__logout"
          >
            Sign out
          </button>
        </nav>
      </header>
      <main className="app-main">
        {children}
      </main>
    </div>
  );
}
