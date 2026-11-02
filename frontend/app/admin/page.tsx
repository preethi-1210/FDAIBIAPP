"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Check,
  ClipboardList,
  Inbox,
  LayoutDashboard,
  LoaderCircle,
  ShieldCheck,
  UserCog,
  UsersRound,
  X,
} from "lucide-react";
import { type AccessRequest, formatWhen, StatusPill } from "@/components/AccessUi";
import { DOMAIN_LABELS, Shell } from "@/components/Shell";
import { api, switchDomain } from "@/lib/api";

type Tab = "overview" | "requests" | "users" | "audit";
type RequestFilter = "pending" | "approved" | "rejected" | "all";
type AuditEvent = {
  id: number;
  actor: string;
  action: string;
  target_username: string;
  domain: string | null;
  domain_label: string | null;
  request_id: number | null;
  detail: string | null;
  created_at: string;
};
type Overview = {
  users: number;
  admins: number;
  business_users: number;
  pending_requests: number;
  domains: { id: string; label: string; business_users: number }[];
  recent_activity: AuditEvent[];
};
type ManagedUser = {
  username: string;
  display_name: string;
  role: "admin" | "analyst";
  primary_domain: string | null;
  authorized_domains: string[];
  grants: { domain: string; label: string; granted_by: string; granted_at: string }[];
  healthcare_tenant: boolean;
  google_linked: boolean;
  pending_requests: number;
};

const DOMAINS = Object.keys(DOMAIN_LABELS);
const ACTION_LABELS: Record<string, string> = {
  request_submitted: "Requested access",
  request_approved: "Approved request",
  request_rejected: "Rejected request",
  domain_granted: "Granted domain",
  domain_revoked: "Revoked domain",
  role_changed: "Changed role",
};

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [activeDomain, setActiveDomain] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyKey, setBusyKey] = useState("");

  const load = useCallback(async () => {
    try {
      const [nextOverview, nextUsers, nextRequests, nextAudit, me] = await Promise.all([
        api<Overview>("/admin/overview"),
        api<ManagedUser[]>("/admin/users"),
        api<AccessRequest[]>("/admin/access-requests"),
        api<AuditEvent[]>("/admin/audit"),
        api<{ domain: string }>("/me"),
      ]);
      setOverview(nextOverview);
      setUsers(nextUsers);
      setRequests(nextRequests);
      setAudit(nextAudit);
      setActiveDomain(me.domain);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the admin console.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("tab");
    if (requested === "requests" || requested === "users" || requested === "audit") setTab(requested);
    void load();
  }, [load]);

  async function run(key: string, action: () => Promise<unknown>, success: string) {
    setBusyKey(key);
    setNotice("");
    setError("");
    try {
      await action();
      setNotice(success);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The action failed.");
    } finally {
      setBusyKey("");
    }
  }

  async function openDomain(domain: string) {
    setBusyKey(`open-${domain}`);
    try {
      await switchDomain(domain);
      window.location.assign("/dashboard");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not open that domain.");
      setBusyKey("");
    }
  }

  const pendingCount = requests.filter((item) => item.status === "pending").length;
  const tabs: { id: Tab; label: string; icon: typeof Inbox; count?: number }[] = [
    { id: "overview", label: "Overview", icon: LayoutDashboard },
    { id: "requests", label: "Access requests", icon: Inbox, count: pendingCount },
    { id: "users", label: "Users & permissions", icon: UsersRound },
    { id: "audit", label: "Audit history", icon: ClipboardList },
  ];

  return (
    <Shell>
      <div className="dashboard-page access-page">
        <div className="access-heading">
          <p className="access-eyebrow"><ShieldCheck size={14} /> ADMINISTRATION</p>
          <h1>Admin console</h1>
          <p>Manage business users, their domain permissions and access requests across all five domains.</p>
        </div>

        <div className="access-tabs" role="tablist" aria-label="Admin sections">
          {tabs.map(({ id, label, icon: Icon, count }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              className={`access-tab${tab === id ? " access-tab--active" : ""}`}
              onClick={() => setTab(id)}
            >
              <Icon size={15} aria-hidden="true" /> {label}
              {count ? <span className="access-badge">{count}</span> : null}
            </button>
          ))}
        </div>

        {error ? <div className="access-alert access-alert--error" role="alert">{error}</div> : null}
        {notice ? <div className="access-alert access-alert--success" role="status">{notice}</div> : null}

        {loading ? (
          <div className="access-empty" role="status"><LoaderCircle size={18} className="chat-spin" /> Loading admin console…</div>
        ) : !overview ? null : tab === "overview" ? (
          <>
            <div className="access-stats">
              <Stat label="Users" value={overview.users} />
              <Stat label="Administrators" value={overview.admins} />
              <Stat label="Business users" value={overview.business_users} />
              <Stat label="Pending requests" value={overview.pending_requests} highlight={overview.pending_requests > 0} />
            </div>
            <section className="access-card" aria-labelledby="domains-heading">
              <h2 id="domains-heading">Business domains</h2>
              <p className="access-muted">Open any domain&apos;s dashboard, reports and Ask AI as an administrator.</p>
              <div className="access-domain-grid">
                {overview.domains.map((domain) => (
                  <div key={domain.id} className={`access-domain${domain.id === activeDomain ? " access-domain--active" : ""}`}>
                    <ShieldCheck size={18} aria-hidden="true" />
                    <div>
                      <strong>{domain.label}</strong>
                      <small>
                        {domain.business_users} business user{domain.business_users === 1 ? "" : "s"}
                        {domain.id === activeDomain ? " · active" : ""}
                      </small>
                    </div>
                    <button
                      type="button"
                      className="access-button access-button--ghost"
                      disabled={busyKey === `open-${domain.id}`}
                      onClick={() => void openDomain(domain.id)}
                    >
                      Open
                    </button>
                  </div>
                ))}
              </div>
            </section>
            <section className="access-card" aria-labelledby="activity-heading">
              <h2 id="activity-heading">Recent activity</h2>
              <AuditTable rows={overview.recent_activity} />
            </section>
          </>
        ) : tab === "requests" ? (
          <RequestsPanel requests={requests} busyKey={busyKey} run={run} />
        ) : tab === "users" ? (
          <UsersPanel users={users} busyKey={busyKey} run={run} />
        ) : (
          <section className="access-card" aria-labelledby="audit-heading">
            <h2 id="audit-heading">Audit history</h2>
            <AuditTable rows={audit} />
          </section>
        )}
      </div>
    </Shell>
  );
}

function Stat({ label, value, highlight = false }: { label: string; value: number; highlight?: boolean }) {
  return (
    <div className={`access-stat${highlight ? " access-stat--highlight" : ""}`}>
      <span>{label}</span>
      <strong>{value.toLocaleString()}</strong>
    </div>
  );
}

type Run = (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;

function RequestsPanel({ requests, busyKey, run }: { requests: AccessRequest[]; busyKey: string; run: Run }) {
  const [filter, setFilter] = useState<RequestFilter>("pending");
  const [notes, setNotes] = useState<Record<number, string>>({});
  const visible = filter === "all" ? requests : requests.filter((item) => item.status === filter);

  function decide(item: AccessRequest, decision: "approve" | "reject") {
    void run(
      `${decision}-${item.id}`,
      () => api(`/admin/access-requests/${item.id}/${decision}`, {
        method: "POST",
        body: JSON.stringify({ note: notes[item.id]?.trim() || null }),
      }),
      `${decision === "approve" ? "Approved" : "Rejected"} ${item.username}'s request for ${item.requested_domain_label}.`,
    );
  }

  return (
    <section className="access-card" aria-labelledby="requests-heading">
      <div className="access-card__heading">
        <h2 id="requests-heading">Access requests</h2>
        <div className="access-segmented" role="group" aria-label="Filter requests">
          {(["pending", "approved", "rejected", "all"] as RequestFilter[]).map((value) => (
            <button key={value} type="button" aria-pressed={filter === value} className={filter === value ? "is-active" : ""} onClick={() => setFilter(value)}>
              {value[0].toUpperCase() + value.slice(1)}
            </button>
          ))}
        </div>
      </div>
      {visible.length ? (
        <div className="access-request-list">
          {visible.map((item) => (
            <article key={item.id} className="access-request">
              <div className="access-request__main">
                <div className="access-request__title">
                  <strong>{item.display_name}</strong>
                  <span className="access-muted">@{item.username}</span>
                  <StatusPill status={item.status} />
                </div>
                <p className="access-request__domains">
                  {item.current_domain_label} → <b>{item.requested_domain_label}</b>
                  <span className="access-muted"> · submitted {formatWhen(item.created_at)}</span>
                </p>
                <p className="access-request__reason">{item.reason}</p>
                {item.status !== "pending" ? (
                  <p className="access-muted">
                    {item.status === "approved" ? "Approved" : "Rejected"} by {item.decided_by} · {formatWhen(item.decided_at)}
                    {item.decision_note ? ` · “${item.decision_note}”` : ""}
                  </p>
                ) : null}
                {item.status === "pending" && item.requested_domain === "healthcare" ? (
                  <p className="access-muted">Healthcare also requires a provisioned tenant before the user can open it.</p>
                ) : null}
              </div>
              {item.status === "pending" ? (
                <div className="access-request__actions">
                  <input
                    aria-label={`Decision note for request ${item.id}`}
                    placeholder="Optional note to the user"
                    maxLength={500}
                    value={notes[item.id] ?? ""}
                    onChange={(event) => setNotes((current) => ({ ...current, [item.id]: event.target.value }))}
                  />
                  <div>
                    <button type="button" className="access-button" disabled={Boolean(busyKey)} onClick={() => decide(item, "approve")}>
                      <Check size={14} /> Approve
                    </button>
                    <button type="button" className="access-button access-button--danger" disabled={Boolean(busyKey)} onClick={() => decide(item, "reject")}>
                      <X size={14} /> Reject
                    </button>
                  </div>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      ) : (
        <p className="access-muted">{filter === "pending" ? "No pending access requests." : "No requests in this view."}</p>
      )}
    </section>
  );
}

function UsersPanel({ users, busyKey, run }: { users: ManagedUser[]; busyKey: string; run: Run }) {
  const [managing, setManaging] = useState<string | null>(null);
  const [grantDomain, setGrantDomain] = useState("");
  const [primaryDomain, setPrimaryDomain] = useState("");
  const [query, setQuery] = useState("");
  const visible = users.filter((user) =>
    `${user.username} ${user.display_name}`.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <section className="access-card" aria-labelledby="users-heading">
      <div className="access-card__heading">
        <h2 id="users-heading">Users &amp; permissions</h2>
        <input className="access-search" placeholder="Search users" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Search users" />
      </div>
      <div className="access-table-wrap">
        <table className="access-table">
          <thead>
            <tr><th>User</th><th>Role</th><th>Primary domain</th><th>Authorized domains</th><th /></tr>
          </thead>
          <tbody>
            {visible.map((user) => {
              const isManaging = managing === user.username;
              const grantable = DOMAINS.filter((domain) => !user.authorized_domains.includes(domain));
              return (
                <tr key={user.username} className={isManaging ? "is-managing" : ""}>
                  <td>
                    <strong>{user.display_name}</strong>
                    <small>@{user.username}{user.google_linked ? " · Google" : ""}{user.pending_requests ? ` · ${user.pending_requests} pending` : ""}</small>
                  </td>
                  <td><span className={`access-role access-role--${user.role}`}>{user.role === "admin" ? "Administrator" : "Business user"}</span></td>
                  <td>{user.primary_domain ? DOMAIN_LABELS[user.primary_domain] : "All domains"}</td>
                  <td>
                    <div className="access-chips">
                      {user.role === "admin" ? <span className="access-chip">All five domains</span> : user.authorized_domains.map((domain) => {
                        const grant = user.grants.find((item) => item.domain === domain);
                        return (
                          <span key={domain} className="access-chip" title={grant ? `Granted by ${grant.granted_by} on ${formatWhen(grant.granted_at)}` : "Primary domain"}>
                            {DOMAIN_LABELS[domain]}
                            {domain === "healthcare" && !user.healthcare_tenant ? " (no tenant)" : ""}
                            {grant && isManaging ? (
                              <button
                                type="button"
                                aria-label={`Revoke ${DOMAIN_LABELS[domain]} from ${user.username}`}
                                disabled={Boolean(busyKey)}
                                onClick={() => void run(
                                  `revoke-${user.username}-${domain}`,
                                  () => api(`/admin/users/${encodeURIComponent(user.username)}/domains/${domain}`, { method: "DELETE" }),
                                  `Revoked ${DOMAIN_LABELS[domain]} from ${user.username}.`,
                                )}
                              >
                                <X size={11} />
                              </button>
                            ) : null}
                          </span>
                        );
                      })}
                    </div>
                    {isManaging ? (
                      <div className="access-manage">
                        {user.role === "analyst" && grantable.length ? (
                          <div className="access-manage__row">
                            <select value={grantDomain} onChange={(event) => setGrantDomain(event.target.value)} aria-label="Domain to grant">
                              <option value="">Grant a domain…</option>
                              {grantable.map((domain) => <option key={domain} value={domain}>{DOMAIN_LABELS[domain]}</option>)}
                            </select>
                            <button
                              type="button"
                              className="access-button"
                              disabled={!grantDomain || Boolean(busyKey)}
                              onClick={() => void run(
                                `grant-${user.username}`,
                                () => api(`/admin/users/${encodeURIComponent(user.username)}/domains`, {
                                  method: "POST",
                                  body: JSON.stringify({ domain: grantDomain }),
                                }).then(() => setGrantDomain("")),
                                `Granted ${DOMAIN_LABELS[grantDomain]} to ${user.username}.`,
                              )}
                            >
                              Grant
                            </button>
                          </div>
                        ) : null}
                        <div className="access-manage__row">
                          {user.role === "analyst" ? (
                            <>
                              <select value={primaryDomain} onChange={(event) => setPrimaryDomain(event.target.value)} aria-label="New primary domain">
                                <option value="">Change primary domain…</option>
                                {DOMAINS.filter((domain) => domain !== user.primary_domain).map((domain) => (
                                  <option key={domain} value={domain}>{DOMAIN_LABELS[domain]}</option>
                                ))}
                              </select>
                              <button
                                type="button"
                                className="access-button access-button--ghost"
                                disabled={!primaryDomain || Boolean(busyKey)}
                                onClick={() => void run(
                                  `primary-${user.username}`,
                                  () => api(`/admin/users/${encodeURIComponent(user.username)}/role`, {
                                    method: "PATCH",
                                    body: JSON.stringify({ role: "analyst", primary_domain: primaryDomain }),
                                  }).then(() => setPrimaryDomain("")),
                                  `${user.username}'s primary domain is now ${DOMAIN_LABELS[primaryDomain]}.`,
                                )}
                              >
                                Set primary
                              </button>
                              <button
                                type="button"
                                className="access-button access-button--danger"
                                disabled={Boolean(busyKey)}
                                onClick={() => {
                                  if (!window.confirm(`Make ${user.username} an administrator with access to all domains?`)) return;
                                  void run(
                                    `role-${user.username}`,
                                    () => api(`/admin/users/${encodeURIComponent(user.username)}/role`, {
                                      method: "PATCH",
                                      body: JSON.stringify({ role: "admin" }),
                                    }),
                                    `${user.username} is now an administrator.`,
                                  );
                                }}
                              >
                                Make administrator
                              </button>
                            </>
                          ) : (
                            <>
                              <select value={primaryDomain} onChange={(event) => setPrimaryDomain(event.target.value)} aria-label="Primary domain for business user">
                                <option value="">Primary domain…</option>
                                {DOMAINS.map((domain) => <option key={domain} value={domain}>{DOMAIN_LABELS[domain]}</option>)}
                              </select>
                              <button
                                type="button"
                                className="access-button access-button--danger"
                                disabled={!primaryDomain || Boolean(busyKey)}
                                onClick={() => void run(
                                  `role-${user.username}`,
                                  () => api(`/admin/users/${encodeURIComponent(user.username)}/role`, {
                                    method: "PATCH",
                                    body: JSON.stringify({ role: "analyst", primary_domain: primaryDomain }),
                                  }).then(() => setPrimaryDomain("")),
                                  `${user.username} is now a business user.`,
                                )}
                              >
                                Change to business user
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                    ) : null}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="access-button access-button--ghost"
                      onClick={() => {
                        setManaging(isManaging ? null : user.username);
                        setGrantDomain("");
                        setPrimaryDomain("");
                      }}
                    >
                      <UserCog size={14} /> {isManaging ? "Done" : "Manage"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!visible.length ? <p className="access-muted">No users match your search.</p> : null}
    </section>
  );
}

function AuditTable({ rows }: { rows: AuditEvent[] }) {
  if (!rows.length) return <p className="access-muted">No access changes have been recorded yet.</p>;
  return (
    <div className="access-table-wrap">
      <table className="access-table">
        <thead>
          <tr><th>When</th><th>Actor</th><th>Action</th><th>User</th><th>Domain</th><th>Detail</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{formatWhen(row.created_at)}</td>
              <td>{row.actor}</td>
              <td>{ACTION_LABELS[row.action] ?? row.action}</td>
              <td>{row.target_username}</td>
              <td>{row.domain_label ?? "—"}</td>
              <td className="access-table__text">{row.detail ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
