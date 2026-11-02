"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { KeyRound, LoaderCircle, Send, ShieldCheck } from "lucide-react";
import { type AccessRequest, formatWhen, StatusPill } from "@/components/AccessUi";
import { DOMAIN_LABELS, Shell } from "@/components/Shell";
import { api, switchDomain } from "@/lib/api";

type DomainStatus = {
  id: string;
  label: string;
  authorized: boolean;
  pending_request: boolean;
  requires_tenant: boolean;
};
type DomainOverview = {
  username: string;
  role: string;
  active_domain: string;
  primary_domain: string | null;
  domains: DomainStatus[];
};

export default function AccessPage() {
  const [overview, setOverview] = useState<DomainOverview | null>(null);
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [requestedDomain, setRequestedDomain] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formMessage, setFormMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [domainOverview, history] = await Promise.all([
        api<DomainOverview>("/access/domains"),
        api<AccessRequest[]>("/access/requests"),
      ]);
      setOverview(domainOverview);
      setRequests(history);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load your access.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const requestable = overview?.domains.filter((item) => !item.authorized && !item.pending_request) ?? [];
  const authorized = overview?.domains.filter((item) => item.authorized) ?? [];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!requestedDomain) return;
    setSubmitting(true);
    setFormMessage(null);
    try {
      await api<AccessRequest>("/access/requests", {
        method: "POST",
        body: JSON.stringify({ domain: requestedDomain, reason }),
      });
      setFormMessage({
        tone: "success",
        text: `Request for ${DOMAIN_LABELS[requestedDomain]} submitted. An administrator will review it.`,
      });
      setRequestedDomain("");
      setReason("");
      await load();
    } catch (cause) {
      setFormMessage({ tone: "error", text: cause instanceof Error ? cause.message : "Could not submit the request." });
    } finally {
      setSubmitting(false);
    }
  }

  async function openDomain(domain: string) {
    try {
      await switchDomain(domain);
      window.location.assign("/dashboard");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not open that domain.");
    }
  }

  return (
    <Shell>
      <div className="dashboard-page access-page">
        <div className="access-heading">
          <p className="access-eyebrow"><KeyRound size={14} /> DOMAIN ACCESS</p>
          <h1>My access</h1>
          <p>Your authorized business domains and requests for additional access.</p>
        </div>

        {error ? <div className="access-alert access-alert--error" role="alert">{error}</div> : null}
        {loading ? (
          <div className="access-empty" role="status"><LoaderCircle size={18} className="chat-spin" /> Loading your access…</div>
        ) : overview ? (
          <>
            <section className="access-card" aria-labelledby="authorized-heading">
              <h2 id="authorized-heading">Authorized domains</h2>
              {overview.role === "admin" ? (
                <p className="access-muted">As an administrator you can open every business domain.</p>
              ) : null}
              <div className="access-domain-grid">
                {authorized.map((item) => (
                  <div key={item.id} className={`access-domain${item.id === overview.active_domain ? " access-domain--active" : ""}`}>
                    <ShieldCheck size={18} aria-hidden="true" />
                    <div>
                      <strong>{item.label}</strong>
                      <small>
                        {item.id === overview.active_domain ? "Active now" : item.id === overview.primary_domain ? "Primary domain" : "Approved access"}
                        {item.requires_tenant ? " · tenant provisioning pending" : ""}
                      </small>
                    </div>
                    {item.id !== overview.active_domain && !item.requires_tenant ? (
                      <button type="button" className="access-button access-button--ghost" onClick={() => void openDomain(item.id)}>Open</button>
                    ) : null}
                  </div>
                ))}
              </div>
            </section>

            {overview.role !== "admin" ? (
              <section className="access-card" aria-labelledby="request-heading">
                <h2 id="request-heading">Request access to another domain</h2>
                {requestable.length ? (
                  <form className="access-form" onSubmit={submit}>
                    <label>
                      <span>Business domain</span>
                      <select value={requestedDomain} required onChange={(event) => setRequestedDomain(event.target.value)}>
                        <option value="" disabled>Select a domain</option>
                        {requestable.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                      </select>
                    </label>
                    <label className="access-form__wide">
                      <span>Reason</span>
                      <textarea
                        value={reason}
                        required
                        minLength={10}
                        maxLength={500}
                        rows={3}
                        placeholder="Explain what you need this domain for (at least 10 characters)."
                        onChange={(event) => setReason(event.target.value)}
                      />
                      <small>{reason.trim().length}/500</small>
                    </label>
                    {requestedDomain === "healthcare" ? (
                      <p className="access-muted access-form__wide">
                        Healthcare data is tenant-scoped. After approval, an administrator must also provision your Healthcare tenant.
                      </p>
                    ) : null}
                    {formMessage ? (
                      <div className={`access-alert access-alert--${formMessage.tone} access-form__wide`} role={formMessage.tone === "error" ? "alert" : "status"}>
                        {formMessage.text}
                      </div>
                    ) : null}
                    <div className="access-form__actions">
                      <button type="submit" className="access-button" disabled={submitting || !requestedDomain || reason.trim().length < 10}>
                        {submitting ? <LoaderCircle size={14} className="chat-spin" /> : <Send size={14} />} Submit request
                      </button>
                    </div>
                  </form>
                ) : (
                  <p className="access-muted">
                    {overview.domains.some((item) => item.pending_request)
                      ? "Your remaining domain requests are awaiting an administrator's decision."
                      : "You already have access to every business domain."}
                  </p>
                )}
              </section>
            ) : null}

            <section className="access-card" aria-labelledby="history-heading">
              <h2 id="history-heading">My access requests</h2>
              {requests.length ? (
                <div className="access-table-wrap">
                  <table className="access-table">
                    <thead>
                      <tr><th>Requested domain</th><th>Status</th><th>Submitted</th><th>Reason</th><th>Decision</th></tr>
                    </thead>
                    <tbody>
                      {requests.map((item) => (
                        <tr key={item.id}>
                          <td><strong>{item.requested_domain_label}</strong><small>from {item.current_domain_label}</small></td>
                          <td><StatusPill status={item.status} /></td>
                          <td>{formatWhen(item.created_at)}</td>
                          <td className="access-table__text">{item.reason}</td>
                          <td className="access-table__text">
                            {item.status === "pending" ? "Awaiting review" : (
                              <>
                                {item.decided_by ? `${item.decided_by} · ` : ""}{formatWhen(item.decided_at)}
                                {item.decision_note ? <small>“{item.decision_note}”</small> : null}
                              </>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="access-muted">You have not requested access to any other domain.</p>}
            </section>
          </>
        ) : null}
      </div>
    </Shell>
  );
}
