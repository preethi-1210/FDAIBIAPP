"use client";

import { motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  ArrowRight,
  Building2,
  Check,
  Clock,
  ShieldCheck,
  Store,
  WalletCards,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";

export type OnboardingMode = "select" | "healthcare_pending";

type OnboardingDomain = { id: string; label: string; requires_provisioning: boolean };
type OnboardingStatus = { email: string; display_name: string; domains: OnboardingDomain[] };
type OnboardingResult =
  | { status: "active"; username: string; domain: string; role: string; domains: string[] }
  | { status: "healthcare_pending"; username: string; domain: string };

const DOMAIN_DETAILS: Record<string, { desc: string; icon: typeof Store; color: string }> = {
  banking: { desc: "Accounts, transactions & branches", icon: Building2, color: "text-violet-300" },
  finance: { desc: "Accounts, budgets & revenue", icon: WalletCards, color: "text-sky-300" },
  insurance: { desc: "Policies, claims & premiums", icon: ShieldCheck, color: "text-emerald-300" },
  retail: { desc: "Orders, products & stores", icon: Store, color: "text-cyan-300" },
  healthcare: { desc: "Patients, encounters & quality", icon: Activity, color: "text-rose-300" },
};

function clearLocalSession() {
  localStorage.removeItem("ryze_token");
  localStorage.removeItem("ryze_user");
  localStorage.removeItem("ryze_domain");
  localStorage.removeItem("ryze_role");
}

export function DomainSelectionDialog({
  mode,
  onClose,
}: {
  mode: OnboardingMode;
  onClose: () => void;
}) {
  const router = useRouter();
  const reduceMotion = useReducedMotion();
  const dialogRef = useRef<HTMLElement | null>(null);
  const [profile, setProfile] = useState<OnboardingStatus | null>(null);
  const [selected, setSelected] = useState("");
  const [pending, setPending] = useState(mode === "healthcare_pending");
  const [loading, setLoading] = useState(mode === "select");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (mode === "healthcare_pending") {
      clearLocalSession();
      return;
    }
    let cancelled = false;
    fetch(`${API_URL}/auth/oidc/onboarding`, { credentials: "include" })
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail || `Could not load your sign-up (${response.status})`);
        return body as OnboardingStatus;
      })
      .then((body) => {
        if (!cancelled) setProfile(body);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load your sign-up.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const focusable = () => dialog?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    );
    focusable()?.[0]?.focus();
    const containDialogFocus = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items?.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", containDialogFocus);
    return () => {
      window.removeEventListener("keydown", containDialogFocus);
      previousFocus?.focus();
    };
  }, [onClose]);

  async function confirmDomain() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/auth/oidc/onboarding`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ domain: selected }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Could not save your business domain.");
      const result = body as OnboardingResult;
      // The session lives in the HttpOnly ryze_session cookie; drop any bearer token left
      // by an earlier password sign-in so it cannot override the new account.
      clearLocalSession();
      if (result.status === "healthcare_pending") {
        setPending(true);
        return;
      }
      localStorage.setItem("ryze_user", result.username);
      localStorage.setItem("ryze_domain", result.domain);
      localStorage.setItem("ryze_role", result.role);
      router.replace("/dashboard");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save your business domain.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-[#03060b]/80 p-4 backdrop-blur-md"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.22 }}
      role="presentation"
    >
      <motion.section
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboarding-title"
        ref={dialogRef}
        initial={{ opacity: 0, y: 24, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 16, scale: 0.98 }}
        transition={reduceMotion ? { duration: 0 } : { duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
        // Deliberately not "fd-login-card": globals.css sizes that card's second child as a
        // 46px icon tile, which collapses this dialog's content.
        className="relative my-auto w-full max-w-2xl rounded-3xl border border-white/15 bg-slate-950/95 p-6 text-slate-100 shadow-[0_30px_100px_rgba(0,0,0,.6),0_0_80px_rgba(139,92,246,.12)] backdrop-blur-2xl sm:p-8"
      >
        <button type="button" onClick={onClose} className="absolute right-4 top-4 grid size-9 place-items-center rounded-xl text-slate-400 transition hover:bg-white/[0.07] hover:text-white" aria-label="Close domain selection">
          <X size={18} />
        </button>

        {loading ? (
          <p id="onboarding-title" role="status" className="text-sm text-slate-400">Loading your sign-up…</p>
        ) : pending ? (
          <div>
            <div className="mb-4 grid size-12 place-items-center rounded-2xl border border-amber-300/20 bg-amber-300/10 text-amber-200"><Clock size={21} /></div>
            <h2 id="onboarding-title" className="text-2xl font-semibold tracking-tight text-white">Healthcare access is pending</h2>
            <p className="mt-2 text-sm leading-6 text-slate-400">
              Your account is registered for the Healthcare domain. Healthcare data is tenant-scoped, so an administrator
              must provision your Healthcare tenant before you can open the workspace. Sign in with Google again once
              your administrator confirms access.
            </p>
            <button type="button" onClick={onClose} className="mt-6 inline-flex items-center gap-2 rounded-xl border border-white/15 px-4 py-2.5 text-sm text-slate-200 transition hover:bg-white/[0.06]">
              Close
            </button>
          </div>
        ) : profile ? (
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-violet-300/80">Welcome{profile.display_name ? `, ${profile.display_name}` : ""}</p>
            <h2 id="onboarding-title" className="mt-2 pr-10 text-2xl font-semibold tracking-tight text-white">Choose your business domain</h2>
            <p className="mt-2 text-sm text-slate-400">
              Signed in with Google as <span className="text-slate-200">{profile.email}</span>. Your account will be
              assigned to this domain for every future sign-in. Contact an administrator to change it later.
            </p>

            <div role="radiogroup" aria-labelledby="onboarding-title" className="mt-6 grid gap-3 sm:grid-cols-2">
              {profile.domains.map((item) => {
                const detail = DOMAIN_DETAILS[item.id];
                const Icon = detail?.icon ?? Store;
                const active = selected === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => setSelected(item.id)}
                    className={`flex items-start gap-3 rounded-2xl border p-4 text-left transition ${active ? "border-violet-300/60 bg-violet-300/[0.09]" : "border-white/10 bg-white/[0.025] hover:bg-white/[0.06]"}`}
                  >
                    <span className={`grid size-10 shrink-0 place-items-center rounded-xl bg-white/[0.055] ${detail?.color ?? "text-slate-300"}`}>
                      <Icon size={19} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2 text-sm font-semibold text-white">
                        {item.label}
                        {active && <Check size={16} className="text-violet-200" />}
                      </span>
                      <span className="mt-1 block text-xs text-slate-500">{detail?.desc}</span>
                      {item.requires_provisioning && (
                        <span className="mt-2 block text-[11px] text-amber-200/80">Requires administrator tenant provisioning before access.</span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>

            {error && <p role="alert" className="mt-5 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-3 py-2.5 text-sm text-rose-200">{error}</p>}

            <button
              type="button"
              onClick={confirmDomain}
              disabled={!selected || busy}
              className="group mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-violet-700 to-violet-400 px-4 py-3 text-sm font-semibold text-white shadow-[0_6px_24px_rgba(139,92,246,.17)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy ? "Saving…" : "Continue to workspace"} {!busy && <ArrowRight size={16} className="transition group-hover:translate-x-0.5" />}
            </button>
          </div>
        ) : (
          <div>
            <h2 id="onboarding-title" className="pr-10 text-2xl font-semibold tracking-tight text-white">Sign-up session unavailable</h2>
            <p role="alert" className="mt-3 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-3 py-2.5 text-sm text-rose-200">{error}</p>
            <button type="button" onClick={onClose} className="mt-6 inline-flex items-center gap-2 rounded-xl border border-white/15 px-4 py-2.5 text-sm text-slate-200 transition hover:bg-white/[0.06]">
              Back to sign in
            </button>
          </div>
        )}
      </motion.section>
    </motion.div>
  );
}
