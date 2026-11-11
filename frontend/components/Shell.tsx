"use client";

import Link from "next/link";
import { BarChart3, ChevronDown } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, API_URL, switchDomain } from "@/lib/api";
import { PlanMenuLink } from "@/components/PlanUsage";

const links = [
  { href: "/context", label: "Foresight" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/chat", label: "Ask" },
  { href: "/settings", label: "Connect" },
];

export const DOMAIN_LABELS: Record<string, string> = {
  banking: "Banking",
  finance: "Finance",
  healthcare: "Healthcare",
  insurance: "Insurance",
  retail: "Retail",
};

type Me = {
  username: string;
  domain: string;
  role: string;
  authorized_domains?: string[];
  notifications?: number;
};

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [domain, setDomain] = useState("");
  const [role, setRole] = useState("");
  const [domains, setDomains] = useState<string[]>([]);
  const [notifications, setNotifications] = useState(0);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState("");

  useEffect(() => {
    setUsername(localStorage.getItem("ryze_user") || "");
    setDomain(localStorage.getItem("ryze_domain") || "");
    // Role and authorized domains always come from the API, never from browser storage.
    api<Me>("/me")
      .then((user) => {
        setUsername(user.username);
        setDomain(user.domain);
        setRole(user.role);
        setDomains(user.authorized_domains ?? [user.domain]);
        setNotifications(user.notifications ?? 0);
      })
      .catch(() => {
        setUsername(localStorage.getItem("ryze_user") || "");
        setDomain(localStorage.getItem("ryze_domain") || "");
      });
  }, [pathname]);

  async function changeDomain(next: string) {
    if (!next || next === domain) return;
    setSwitching(true);
    setSwitchError("");
    try {
      await switchDomain(next);
      // A full reload drops every page's cached data from the previous domain.
      window.location.assign(pathname === "/admin" || pathname === "/access" ? pathname : "/dashboard");
    } catch (error) {
      setSwitchError(error instanceof Error ? error.message : "Could not switch domain.");
      setSwitching(false);
    }
  }

  function signOut() {
    fetch(`${API_URL}/auth/logout`, { method: "POST", credentials: "include" })
      .catch((error: unknown) => console.error("Could not close the server session", error))
      .finally(() => {
        localStorage.removeItem("ryze_token");
        localStorage.removeItem("ryze_user");
        localStorage.removeItem("ryze_domain");
        localStorage.removeItem("ryze_role");
        router.push("/login");
      });
  }

  const accessLink = role === "admin"
    ? { href: "/admin", label: "Admin", hint: "pending access requests" }
    : { href: "/access", label: "Access", hint: "new access decisions" };

  return (
    <div className="app-shell" style={{ minHeight: "100vh" }}>
      <header className="app-header">
        <div className="app-brand">
          <span className="app-brand__mark"><BarChart3 size={19} strokeWidth={2.8} /></span>
          <span className="app-brand__copy">
            <span className="app-brand__name">RYZE AI/BI</span>
            <span className="app-brand__tagline">GOVERNED BUSINESS ASSISTANT</span>
          </span>
        </div>
        <nav className="app-navigation">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={`app-navigation__link${pathname === link.href ? " app-navigation__link--active" : ""}`}
            >
              {link.label}
            </Link>
          ))}
          {role ? (
            <Link
              href={accessLink.href}
              className={`app-navigation__link${pathname === accessLink.href ? " app-navigation__link--active" : ""}`}
            >
              {accessLink.label}
              {notifications > 0 ? (
                <span className="access-badge" aria-label={`${notifications} ${accessLink.hint}`}>{notifications}</span>
              ) : null}
            </Link>
          ) : null}
          {domains.length > 1 ? (
            <label className="access-domain-switch" title={switchError || "Switch business domain"}>
              <span className="sr-only">Business domain</span>
              <select value={domain} disabled={switching} onChange={(event) => void changeDomain(event.target.value)}>
                {domains.map((item) => <option key={item} value={item}>{DOMAIN_LABELS[item] ?? item}</option>)}
              </select>
              <ChevronDown size={13} aria-hidden="true" />
            </label>
          ) : null}
          <details className="app-account-menu">
            <summary className="app-account-menu__summary">
              <span className="app-account-menu__avatar">{username.slice(0, 1).toUpperCase() || "U"}</span>
              <span>{username || "User"} · {DOMAIN_LABELS[domain] ?? (domain || "Workspace")}</span>
              <ChevronDown size={14} aria-hidden="true" />
            </summary>
            <div className="app-account-menu__panel">
              <span>{username || "Signed-in user"}</span>
              <small>Role: {role === "admin" ? "Administrator" : "Business user"}</small>
              <small>Active domain: {DOMAIN_LABELS[domain] ?? (domain || "Workspace")}</small>
              <small>Authorized: {domains.map((item) => DOMAIN_LABELS[item] ?? item).join(", ") || "—"}</small>
              <PlanMenuLink />
              {switchError ? <small role="alert" className="access-error-text">{switchError}</small> : null}
              <button type="button" onClick={signOut}>Sign out</button>
            </div>
          </details>
        </nav>
      </header>
      <main className={`app-main${["/dashboard", "/reports", "/admin", "/access"].includes(pathname) ? " app-main--dashboard" : ""}`}>{children}</main>
    </div>
  );
}
