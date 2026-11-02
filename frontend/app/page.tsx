"use client";

import {
  AnimatePresence,
  motion,
  useReducedMotion,
} from "framer-motion";
import {
  Activity,
  ArrowDown,
  ArrowRight,
  BarChart3,
  Building2,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Eye,
  EyeOff,
  LockKeyhole,
  Menu,
  Pause,
  Play,
  ShieldCheck,
  Sparkles,
  Store,
  WalletCards,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ContextArchitecture } from "@/components/ContextArchitecture";
import { DomainSelectionDialog, type OnboardingMode } from "@/components/DomainSelectionDialog";
import { API_URL } from "@/lib/api";

type Business = "Banking" | "Finance" | "Healthcare" | "Insurance" | "Retail";
type Domain = { id: string; label: string };
type DemoUser = { username: string; domain: string; password_hint: string };

const DEMO_SLIDES = [
  {
    title: "Multi-Channel Credit Risk & Underwriting Assessment",
    segment: "Banking",
    icon: Building2,
    accent: "violet",
    problem: "Processing instant credit applications over dynamic cross-channel lending feeds while keeping computing overhead low.",
    application: "TabPFN accepts historical repayment datasets pulled directly from databricks.catalog.banking.credit_history as an in-context learning window. It generates an immediate default risk projection within milliseconds.",
    context: "Ollama processes the predictive risk scores along with macro-economic indicators, generating tailored loan modification letters and credit terms suited for compliance review.",
  },
  {
    title: "Fraudulent Claims Vector Detection & Policy Optimization",
    segment: "Insurance",
    icon: ShieldCheck,
    accent: "emerald",
    problem: "Sifting out complex, high-dimensional anomaly footprints inside submitted loss records.",
    application: "Claims attributes are synchronized into Pandas frames. TabPFN runs out-of-the-box multi-class anomaly categorization on the feature distribution to compute fraud probability metrics.",
    context: "If the fraud metric exceeds 75%, Ollama flags the policy, structures a detailed investigative report parsing the anomaly variables, and auto-generates investigative sub-tasks for claims adjusters.",
  },
  {
    title: "Portfolio Drift & Algorithmic Allocation",
    segment: "Wealth Management & Capital Markets",
    icon: WalletCards,
    accent: "sky",
    problem: "Real-time rebalancing of complex financial portfolios amidst high volatility regimes without complex retraining latency.",
    application: "Historical volatility tables from the Databricks Warehouse are pulled to construct predictive baseline regimes via NumPy vector mechanics.",
    context: "Ollama acts as an autonomous investment co-pilot. It ingests the statistical variance profiles, reconciles them with standard client investment mandates, and outputs formatted portfolio adjustment proposals.",
  },
] as const;

const DEMO_SLIDE_INTERVAL_MS = 6000;

const BUSINESS_METRICS: Record<Business, { label: string; value: string; change: string; points: string }> = {
  Banking: { label: "Deposit balances", value: "$8.42M", change: "+9.2%", points: "8,124 72,106 138,85 205,97 271,69 338,77 404,48 470,63 536,31 602,12" },
  Finance: { label: "Assets under management", value: "$8.42M", change: "+9.2%", points: "8,124 72,106 138,85 205,97 271,69 338,77 404,48 470,63 536,31 602,12" },
  Healthcare: { label: "Approved claim amount", value: "$684K", change: "+6.8%", points: "8,118 72,110 138,82 205,91 271,57 338,68 404,44 470,51 536,24 602,18" },
  Insurance: { label: "Claims paid", value: "$2.8M", change: "+5.3%", points: "8,118 72,96 138,105 205,74 271,88 338,55 404,63 470,36 536,48 602,18" },
  Retail: { label: "Gross merchandise value", value: "$248,390", change: "+18.6%", points: "8,128 72,102 138,116 205,66 271,79 338,43 404,58 470,22 536,38 602,12" },
};

const AI_INSIGHT =
  "Online revenue is up 18.6% this month. Repeat customers are driving the strongest growth in the West region.";

const riseIn = {
  hidden: { opacity: 0, y: 24 },
  visible: { opacity: 1, y: 0 },
};

const staggerIn = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.11, delayChildren: 0.12 } },
};

export default function LandingPage() {
  const router = useRouter();
  const reduceMotion = useReducedMotion();
  const [business, setBusiness] = useState<Business>("Retail");
  const [showLogin, setShowLogin] = useState(false);
  const [onboarding, setOnboarding] = useState<OnboardingMode | null>(null);
  const [showDemo, setShowDemo] = useState(false);
  const [activeDemoSlide, setActiveDemoSlide] = useState(0);
  const [isDemoPaused, setIsDemoPaused] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [typedInsight, setTypedInsight] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [domain, setDomain] = useState("retail");
  const [authMode, setAuthMode] = useState<"login" | "signup">("login");
  const [domains, setDomains] = useState<Domain[]>([]);
  const [users, setUsers] = useState<DemoUser[]>([]);
  const [oidcEnabled, setOidcEnabled] = useState(false);
  const [error, setError] = useState("");
  const [ssoError, setSsoError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const loginDialogRef = useRef<HTMLElement | null>(null);
  const demoDialogRef = useRef<HTMLElement | null>(null);
  const metrics = BUSINESS_METRICS[business];
  const transition = reduceMotion ? { duration: 0 } : { duration: 1.55, ease: [0.22, 1, 0.36, 1] as const };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const onboardingParam = params.get("onboarding");
    if (onboardingParam === "select" || onboardingParam === "healthcare_pending") {
      setOnboarding(onboardingParam);
    } else if (params.has("login")) {
      setShowLogin(true);
      // The API sends the browser back here when Google sign-in could not start.
      const sso = params.get("sso_error");
      if (sso) {
        setSsoError(sso.slice(0, 500));
        window.history.replaceState(null, "", window.location.pathname + "?login=1");
      }
    }
  }, []);

  const closeOnboarding = useCallback(() => {
    setOnboarding(null);
    window.history.replaceState(null, "", window.location.pathname);
    setShowLogin(true);
  }, []);

  useEffect(() => {
    if (reduceMotion) {
      setTypedInsight(AI_INSIGHT);
      return;
    }
    let index = 0;
    const timer = window.setInterval(() => {
      index += 2;
      setTypedInsight(AI_INSIGHT.slice(0, index));
      if (index >= AI_INSIGHT.length) window.clearInterval(timer);
    }, 28);
    return () => window.clearInterval(timer);
  }, [reduceMotion]);

  useEffect(() => {
    if (!showLogin) return;
    let cancelled = false;
    fetch(`${API_URL}/auth/domains`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Could not load sign-in options (${response.status})`);
        return response.json();
      })
      .then((body: { domains?: Domain[]; users?: DemoUser[]; oidc_enabled?: boolean }) => {
        if (cancelled) return;
        setDomains(body.domains ?? []);
        setUsers(body.users ?? []);
        setOidcEnabled(body.oidc_enabled ?? false);
        setError("");
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Could not connect to the sign-in service.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [showLogin]);

  useEffect(() => {
    if (!showDemo || isDemoPaused) return;
    const timer = window.setInterval(() => {
      setActiveDemoSlide((current) => (current + 1) % DEMO_SLIDES.length);
    }, DEMO_SLIDE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [showDemo, isDemoPaused, activeDemoSlide]);

  useEffect(() => {
    if (!showDemo) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = demoDialogRef.current;
    const focusable = () => dialog?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    );
    focusable()?.[0]?.focus();
    const containDialogFocus = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowDemo(false);
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
  }, [showDemo]);

  useEffect(() => {
    if (!showLogin) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = loginDialogRef.current;
    const focusable = () => dialog?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    );
    focusable()?.[0]?.focus();
    const containDialogFocus = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowLogin(false);
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
  }, [showLogin]);

  async function onLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSsoError("");
    try {
      const signup = authMode === "signup";
      const response = await fetch(`${API_URL}${signup ? "/auth/signup" : "/auth/login"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(signup
          ? { username, password, domain, display_name: displayName }
          : { username, password, domain }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Sign-in failed");
      localStorage.setItem("ryze_token", body.access_token);
      localStorage.setItem("ryze_user", body.username);
      localStorage.setItem("ryze_domain", body.domain);
      localStorage.setItem("ryze_role", body.role);
      router.push("/dashboard");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sign-in failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  function openLogin() {
    setMenuOpen(false);
    setShowLogin(true);
  }

  function openDemo() {
    setMenuOpen(false);
    setActiveDemoSlide(0);
    setIsDemoPaused(false);
    setShowDemo(true);
  }

  return (
    <main className="fd-landing relative min-h-screen overflow-hidden bg-[#070b14] text-slate-100 selection:bg-cyan-300/30">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="landing-orb landing-orb--cyan" />
        <div className="landing-orb landing-orb--violet" />
        <div className="landing-grid absolute inset-0 opacity-30" />
      </div>

      <header className="fd-header relative z-20 mx-auto flex w-full max-w-7xl items-center justify-between px-5 py-5 sm:px-8">
        <a href="#" className="fd-brand flex items-center gap-3" aria-label="FD AI/BI home">
          <span className="grid size-10 place-items-center rounded-xl border border-violet-300/20 bg-violet-300/10 text-violet-200 shadow-[0_0_28px_rgba(139,92,246,0.12)]">
            <BarChart3 size={21} />
          </span>
          <span className="text-sm font-semibold tracking-[0.16em] text-white">FD <span className="text-violet-300">AI/BI</span></span>
        </a>
        <nav className="hidden items-center gap-8 text-sm text-slate-400 md:flex" aria-label="Main navigation">
          <a className="transition hover:text-white" href="#platform">Platform</a>
          <a className="transition hover:text-white" href="#businesses">Businesses</a>
          <a className="transition hover:text-white" href="#demo">Live demo</a>
        </nav>
        <div className="hidden items-center gap-3 md:flex">
          <button onClick={openLogin} className="rounded-lg px-4 py-2 text-sm text-slate-300 transition hover:text-white">Log in</button>
          <button onClick={openLogin} className="rounded-lg border border-violet-200/25 bg-violet-300/10 px-4 py-2 text-sm font-medium text-violet-100 transition hover:border-violet-200/50 hover:bg-violet-300/15">
            Get started <ArrowRight className="ml-1 inline" size={15} />
          </button>
        </div>
        <button
          type="button"
          className="grid size-10 place-items-center rounded-lg border border-white/10 text-slate-200 md:hidden"
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          onClick={() => setMenuOpen((open) => !open)}
        >
          {menuOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
        <AnimatePresence>
          {menuOpen && (
            <motion.nav
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="absolute left-4 right-4 top-[72px] grid gap-1 rounded-2xl border border-white/10 bg-slate-950/95 p-3 shadow-2xl backdrop-blur-xl md:hidden"
              aria-label="Mobile navigation"
            >
              <a onClick={() => setMenuOpen(false)} className="rounded-lg px-3 py-3 text-sm text-slate-300 hover:bg-white/5" href="#platform">Platform</a>
              <a onClick={() => setMenuOpen(false)} className="rounded-lg px-3 py-3 text-sm text-slate-300 hover:bg-white/5" href="#businesses">Businesses</a>
              <a onClick={() => setMenuOpen(false)} className="rounded-lg px-3 py-3 text-sm text-slate-300 hover:bg-white/5" href="#demo">Live demo</a>
              <button onClick={openLogin} className="rounded-lg px-3 py-3 text-left text-sm text-violet-200 hover:bg-white/5">Login / Get Started</button>
            </motion.nav>
          )}
        </AnimatePresence>
      </header>

      <section id="platform" className="fd-hero-section relative z-10 mx-auto grid max-w-7xl items-center gap-14 px-5 pb-20 pt-12 sm:px-8 md:min-h-[670px] md:grid-cols-[0.92fr_1.08fr] md:gap-10 md:pb-28 md:pt-16">
        <motion.div variants={staggerIn} initial="hidden" animate="visible" className="fd-hero-copy relative z-10">
          <motion.div variants={riseIn} transition={transition} className="mb-6 inline-flex items-center gap-2 rounded-full border border-violet-300/20 bg-violet-300/[0.06] px-3 py-1.5 text-xs font-medium text-violet-200">
            <Sparkles size={14} /> One intelligent view across your business
          </motion.div>
          <motion.h1 variants={riseIn} transition={transition} className="fd-hero-title max-w-2xl text-5xl font-semibold leading-[1.04] tracking-[-0.055em] text-white sm:text-6xl lg:text-[4.5rem]">
            FD AI/BI
            <span className="mt-2 block bg-gradient-to-r from-violet-500 via-violet-400 to-purple-300 bg-clip-text pb-2 text-transparent">analytics platform</span>
          </motion.h1>
          <motion.p variants={riseIn} transition={transition} className="mt-6 max-w-xl text-base leading-7 text-slate-400 sm:text-lg sm:leading-8">
            A multi-tenant, multi-business AI analytics platform that turns trusted data into clear answers for every team, domain, and decision.
          </motion.p>
          <motion.div variants={riseIn} transition={transition} className="mt-8 flex flex-wrap gap-3">
            <button type="button" onClick={openDemo} className="group inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-violet-700 to-violet-400 px-5 py-3 text-sm font-semibold text-white shadow-[0_8px_30px_rgba(139,92,246,.2)] transition hover:-translate-y-0.5 hover:shadow-[0_12px_38px_rgba(139,92,246,.28)]">
              Workflow <ArrowDown size={16} className="transition group-hover:translate-y-0.5" />
            </button>
            <button onClick={openLogin} className="inline-flex items-center gap-2 rounded-xl border border-violet-300/20 bg-white/[0.035] px-5 py-3 text-sm font-semibold text-slate-100 transition hover:border-violet-200/40 hover:bg-white/[0.07]">
              Login / Get Started <ArrowRight size={16} />
            </button>
          </motion.div>
          <motion.div variants={riseIn} transition={transition} className="mt-9 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1.5"><Check size={14} className="text-emerald-300" /> Governed data access</span>
            <span className="inline-flex items-center gap-1.5"><Check size={14} className="text-emerald-300" /> Built for multiple domains</span>
          </motion.div>
        </motion.div>

        <DashboardDemo business={business} setBusiness={setBusiness} metrics={metrics} typedInsight={typedInsight} />
      </section>

      <section id="businesses" className="fd-business-section relative z-10 border-y border-white/[0.07] bg-white/[0.018]">
        <div className="mx-auto grid max-w-7xl gap-8 px-5 py-12 sm:px-8 md:grid-cols-[0.7fr_1.3fr] md:items-center md:py-14">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-violet-300/75">One platform. Every business.</p>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight text-white sm:text-3xl">Switch contexts, not tools.</h2>
            <p className="mt-3 max-w-lg text-sm leading-6 text-slate-400">Keep business-specific metrics and access in view while your analytics workflow stays consistent.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
            {([
              { title: "Retail", desc: "Orders, products & stores", icon: Store, color: "text-cyan-300", glow: "border-cyan-300/15" },
              { title: "Banking", desc: "Accounts, transactions & branches", icon: Building2, color: "text-violet-300", glow: "border-violet-300/15" },
              { title: "Insurance", desc: "Policies, claims & premiums", icon: ShieldCheck, color: "text-emerald-300", glow: "border-emerald-300/15" },
              { title: "Finance", desc: "Accounts, budgets & revenue", icon: WalletCards, color: "text-sky-300", glow: "border-sky-300/15" },
              { title: "Healthcare", desc: "Patients, encounters & quality", icon: Activity, color: "text-rose-300", glow: "border-rose-300/15" },
            ] as const).map(({ title, desc, icon: Icon, color, glow }, index) => (
              <motion.button
                key={title}
                type="button"
                onClick={() => setBusiness(title)}
                whileHover={reduceMotion ? undefined : { y: -4 }}
                whileTap={reduceMotion ? undefined : { scale: 0.98 }}
                className={`fd-business-card rounded-2xl border ${glow} ${business === title ? "bg-white/[0.065]" : "bg-white/[0.025]"} p-4 text-left transition hover:bg-white/[0.07]`}
                aria-pressed={business === title}
              >
                <motion.span animate={reduceMotion ? undefined : { y: [0, -3, 0] }} transition={{ duration: 3 + index * 0.4, repeat: Infinity, ease: "easeInOut" }} className={`mb-4 grid size-10 place-items-center rounded-xl bg-white/[0.055] ${color}`}>
                  <Icon size={19} />
                </motion.span>
                <span className="block text-sm font-semibold text-white">{title}</span>
                <span className="mt-1 block text-xs text-slate-500">{desc}</span>
              </motion.button>
            ))}
          </div>
        </div>
      </section>

      <section id="context-layer" className="relative z-10 mx-auto max-w-7xl scroll-mt-8 px-5 py-14 sm:px-8 md:py-20">
        <div className="mb-7 max-w-2xl">
          <p className="text-xs font-medium uppercase tracking-[0.2em] text-indigo-300/80">Context intelligence · {business}</p>
          <h2 className="mt-3 text-2xl font-semibold tracking-tight text-white sm:text-3xl">See how business context shapes every answer.</h2>
          <p className="mt-3 text-sm leading-6 text-slate-400">
            Follow the path from source metadata through business meaning and access controls to evidence-backed insights.
          </p>
        </div>
        <div className="rounded-3xl border border-white/[0.09] bg-[#0c1320]/85 p-4 shadow-[0_22px_70px_rgba(0,0,0,.24)] sm:p-6">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-100">Interactive context architecture</h3>
              <p className="mt-1 text-xs text-slate-500">Choose a layer to inspect how it contributes.</p>
            </div>
            <span className="rounded-full border border-amber-200/15 bg-amber-200/[0.06] px-3 py-1.5 text-[10px] text-amber-100/80">
              Sample walkthrough · not live data
            </span>
          </div>
          <ContextArchitecture sampleMode domainLabel={business} />
        </div>
      </section>

      <section className="fd-features relative z-10 mx-auto grid max-w-7xl gap-4 px-5 py-14 sm:px-8 md:grid-cols-3 md:py-20">
        <Feature title="Ask in plain language" detail="Let teams explore governed business data without starting with a blank SQL editor." />
        <Feature title="Insights with context" detail="Connect metrics, trends, and business definitions in one clear analytics workflow." />
        <Feature title="Designed for each tenant" detail="Keep domain-specific views and access patterns organized as your business grows." />
      </section>

      <footer className="fd-footer relative z-10 border-t border-white/[0.07] px-5 py-7 text-center text-xs text-slate-600">
        FD AI/BI Analytics Platform · One view for every business.
      </footer>

      <AnimatePresence>
        {showDemo && (
          <motion.div
            className="fd-login-backdrop fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-[#03060b]/80 p-3 backdrop-blur-md sm:p-6"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.22 }}
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) setShowDemo(false);
            }}
            role="presentation"
          >
            <motion.section
              role="dialog"
              aria-modal="true"
              aria-labelledby="demo-dialog-title"
              aria-describedby="demo-dialog-description"
              ref={demoDialogRef}
              initial={{ opacity: 0, y: 24, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 16, scale: 0.98 }}
              transition={transition}
              className="relative my-auto w-full max-w-4xl overflow-hidden rounded-3xl border border-violet-200/15 bg-[#0b1020]/95 text-slate-100 shadow-[0_30px_100px_rgba(0,0,0,.65),0_0_80px_rgba(139,92,246,.12)] backdrop-blur-2xl"
            >
              <div className="flex items-center justify-between border-b border-white/[0.08] px-5 py-4 sm:px-7">
                <div className="flex items-center gap-3">
                  <span className="grid size-9 place-items-center rounded-xl border border-violet-200/15 bg-violet-300/[0.08] text-violet-200"><Sparkles size={17} /></span>
                  <div>
                    <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-violet-200/70">FD AI/BI · Use case walkthrough</p>
                    <p className="mt-1 text-xs text-slate-400">Illustrative workflows · not live data</p>
                  </div>
                </div>
                <button type="button" onClick={() => setShowDemo(false)} className="grid size-9 place-items-center rounded-xl border border-white/[0.08] text-slate-400 transition hover:border-violet-200/20 hover:bg-white/[0.06] hover:text-white" aria-label="Exit demo">
                  <X size={18} />
                </button>
              </div>

              <div className="px-5 pt-5 sm:px-7 sm:pt-7">
                <div className="flex gap-2" aria-label={`Slide ${activeDemoSlide + 1} of ${DEMO_SLIDES.length}`}>
                  {DEMO_SLIDES.map((slide, index) => (
                    <div key={slide.segment} className="h-1 flex-1 overflow-hidden rounded-full bg-white/[0.09]">
                      {index === activeDemoSlide && (
                        <div
                          key={activeDemoSlide}
                          className="demo-slide-progress h-full origin-left rounded-full bg-gradient-to-r from-violet-500 to-cyan-300"
                          style={{
                            animationDuration: `${DEMO_SLIDE_INTERVAL_MS}ms`,
                            animationPlayState: isDemoPaused ? "paused" : "running",
                          }}
                        />
                      )}
                    </div>
                  ))}
                </div>
              </div>

              <AnimatePresence mode="wait">
                <DemoUseCaseSlide
                  key={activeDemoSlide}
                  slide={DEMO_SLIDES[activeDemoSlide]}
                  index={activeDemoSlide}
                  reduceMotion={Boolean(reduceMotion)}
                />
              </AnimatePresence>

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.08] px-5 py-4 sm:px-7">
                <div className="flex items-center gap-2" aria-label="Choose demo slide">
                  {DEMO_SLIDES.map((slide, index) => (
                    <button
                      key={slide.segment}
                      type="button"
                      onClick={() => setActiveDemoSlide(index)}
                      aria-label={`Show slide ${index + 1}: ${slide.segment}`}
                      aria-current={index === activeDemoSlide ? "step" : undefined}
                      className={`size-2.5 rounded-full transition ${index === activeDemoSlide ? "bg-violet-300 ring-4 ring-violet-300/10" : "bg-slate-600 hover:bg-slate-400"}`}
                    />
                  ))}
                  <span className="ml-2 text-[11px] text-slate-500">{activeDemoSlide + 1} / {DEMO_SLIDES.length} · {isDemoPaused ? "paused" : "advances every 6 seconds"}</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setIsDemoPaused((paused) => !paused)}
                    className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-300 transition hover:border-violet-200/25 hover:bg-white/[0.05]"
                    aria-label={isDemoPaused ? "Resume automatic slide advance" : "Pause automatic slide advance"}
                    aria-pressed={isDemoPaused}
                  >
                    {isDemoPaused ? <Play size={15} /> : <Pause size={15} />}
                    <span>{isDemoPaused ? "Resume" : "Pause"}</span>
                  </button>
                  <button type="button" onClick={() => setActiveDemoSlide((activeDemoSlide + DEMO_SLIDES.length - 1) % DEMO_SLIDES.length)} className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-300 transition hover:border-violet-200/25 hover:bg-white/[0.05]" aria-label="Previous slide">
                    <ChevronLeft size={15} /> <span className="hidden sm:inline">Previous</span>
                  </button>
                  <button type="button" onClick={() => setActiveDemoSlide((activeDemoSlide + 1) % DEMO_SLIDES.length)} className="inline-flex items-center gap-1 rounded-lg border border-violet-200/20 bg-violet-300/[0.08] px-3 py-2 text-xs text-violet-100 transition hover:bg-violet-300/[0.14]" aria-label="Next slide">
                    <span className="hidden sm:inline">Next</span> <ChevronRight size={15} />
                  </button>
                  <button type="button" onClick={() => setShowDemo(false)} className="rounded-lg bg-gradient-to-r from-violet-700 to-violet-500 px-4 py-2 text-xs font-semibold text-white shadow-[0_5px_20px_rgba(139,92,246,.18)] transition hover:brightness-110">
                    Exit demo
                  </button>
                </div>
              </div>
            </motion.section>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {onboarding && <DomainSelectionDialog mode={onboarding} onClose={closeOnboarding} />}
      </AnimatePresence>

      <AnimatePresence>
        {showLogin && (
          <motion.div
            className="fd-login-backdrop fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-[#03060b]/75 p-4 backdrop-blur-md"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.22 }}
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) setShowLogin(false);
            }}
            role="presentation"
          >
            <motion.section
              role="dialog"
              aria-modal="true"
              aria-labelledby="login-title"
              aria-describedby="login-description"
              ref={loginDialogRef}
              initial={{ opacity: 0, x: 48, scale: 0.97 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 32, scale: 0.98 }}
              transition={transition}
              className="fd-login-card relative my-auto w-full max-w-md rounded-3xl border border-white/15 bg-slate-950/80 p-6 shadow-[0_0_80px_rgba(34,211,238,.11)] backdrop-blur-2xl sm:p-8"
            >
              <button type="button" onClick={() => setShowLogin(false)} className="absolute right-4 top-4 grid size-9 place-items-center rounded-xl text-slate-400 transition hover:bg-white/[0.07] hover:text-white" aria-label="Close login dialog">
                <X size={18} />
              </button>
              <div className="mb-6 grid size-12 place-items-center rounded-2xl border border-violet-300/20 bg-violet-300/10 text-violet-200"><LockKeyhole size={21} /></div>
              <p className="text-xs font-medium uppercase tracking-[0.18em] text-violet-300/80">Welcome to FD AI/BI</p>
              <h2 id="login-title" className="mt-2 text-2xl font-semibold tracking-tight text-white">
                {authMode === "signup" ? "Create your workspace account" : "Sign in to your workspace"}
              </h2>
              <p id="login-description" className="mt-2 text-sm text-slate-400">
                {authMode === "signup"
                  ? "Create an analyst account for one business domain."
                  : "Choose your business and continue to your analytics workspace."}
              </p>

              <form onSubmit={onLogin} className="fd-login-form mt-7 space-y-4">
                {authMode === "signup" && (
                  <label className="block text-sm text-slate-300">
                    Display name
                    <input
                      autoComplete="name"
                      maxLength={120}
                      minLength={1}
                      required
                      value={displayName}
                      onChange={(event) => setDisplayName(event.target.value)}
                      className="fd-login-field login-field mt-2 w-full"
                      placeholder="Your name"
                    />
                  </label>
                )}
                <label className="block text-sm text-slate-300">
                  Business domain
                  <span className="relative mt-2 block">
                    <select
                      value={domain}
                      onChange={(event) => setDomain(event.target.value)}
                      className="fd-login-field login-field w-full appearance-none"
                      required
                    >
                      {domains.filter((item) => authMode === "login" || item.id !== "healthcare").map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                    </select>
                    <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-500" />
                  </span>
                </label>
                <label className="block text-sm text-slate-300">
                  Username
                  <input autoComplete="username" required minLength={3} maxLength={40} pattern="[A-Za-z0-9_.\x2d]+" value={username} onChange={(event) => setUsername(event.target.value)} className="fd-login-field login-field mt-2 w-full" placeholder="Enter your username" />
                </label>
                <label className="block text-sm text-slate-300">
                  Password
                  <span className="relative mt-2 block">
                    <input type={showPassword ? "text" : "password"} autoComplete={authMode === "signup" ? "new-password" : "current-password"} required minLength={authMode === "signup" ? 12 : 1} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} className="fd-login-field login-field w-full pr-11" placeholder={authMode === "signup" ? "Use at least 12 characters" : "Enter your password"} />
                    <button type="button" className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 transition hover:text-slate-200" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"}>
                      {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
                    </button>
                  </span>
                </label>
                {ssoError && <p role="alert" className="rounded-xl border border-amber-300/25 bg-amber-300/[0.08] px-3 py-2.5 text-sm text-amber-100">{ssoError}</p>}
                {error && <p role="alert" className="rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-3 py-2.5 text-sm text-rose-200">{error}</p>}
                <button type="submit" disabled={busy} className="group flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-violet-700 to-violet-400 px-4 py-3 text-sm font-semibold text-white shadow-[0_6px_24px_rgba(139,92,246,.17)] transition hover:brightness-110 disabled:cursor-wait disabled:opacity-60">
                  {busy ? (authMode === "signup" ? "Creating account…" : "Signing in…") : (authMode === "signup" ? "Create account" : "Continue to workspace")} {!busy && <ArrowRight size={16} className="transition group-hover:translate-x-0.5" />}
                </button>
              </form>
              <div className="mt-5">
                  <div className="mb-4 flex items-center gap-3" aria-hidden="true">
                    <span className="h-px flex-1 bg-white/[0.08]" />
                    <span className="text-[10px] uppercase tracking-[0.16em] text-slate-500">
                      {authMode === "signup" ? "or sign up with" : "or continue with"}
                    </span>
                    <span className="h-px flex-1 bg-white/[0.08]" />
                  </div>
                  <button
                    type="button"
                    disabled={!oidcEnabled}
                    onClick={() => {
                      const params = new URLSearchParams({ domain });
                      if (authMode === "signup") {
                        const normalizedUsername = username.trim().toLowerCase();
                        if (!/^[a-z0-9_.-]{3,40}$/.test(normalizedUsername)) {
                          setError("Choose a 3-40 character username using letters, numbers, dots, underscores, or hyphens.");
                          return;
                        }
                        if (domain === "healthcare") {
                          setError("Choose a supported business domain. Healthcare accounts require administrator provisioning.");
                          return;
                        }
                        params.set("intent", "signup");
                        params.set("username", normalizedUsername);
                      }
                      // Google sign-in uses the HttpOnly session cookie; a leftover bearer token
                      // from a password sign-in would otherwise take precedence over it.
                      localStorage.removeItem("ryze_token");
                      localStorage.removeItem("ryze_user");
                      localStorage.removeItem("ryze_domain");
                      localStorage.removeItem("ryze_role");
                      setSsoError("");
                      window.location.assign(`${API_URL}/auth/oidc/login?${params.toString()}`);
                    }}
                    className="flex w-full items-center justify-center gap-3 rounded-xl border border-white/15 bg-white px-4 py-3 text-sm font-medium text-slate-800 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
                    aria-describedby={!oidcEnabled ? "google-signin-status" : undefined}
                  >
                    <svg aria-hidden="true" viewBox="0 0 48 48" className="size-5">
                      <path fill="#4285F4" d="M43.6 24.5c0-1.4-.1-2.8-.4-4.1H24v7.8h11a9.4 9.4 0 0 1-4.1 6.2v5.1h6.6c3.9-3.6 6.1-8.8 6.1-15Z" />
                      <path fill="#34A853" d="M24 44c5.5 0 10.1-1.8 13.5-4.9l-6.6-5.1c-1.8 1.2-4.1 2-6.9 2-5.3 0-9.8-3.6-11.4-8.4H5.8v5.3A20 20 0 0 0 24 44Z" />
                      <path fill="#FBBC05" d="M12.6 27.6a12 12 0 0 1 0-7.2v-5.3H5.8a20 20 0 0 0 0 17.8l6.8-5.3Z" />
                      <path fill="#EA4335" d="M24 12c3 0 5.7 1 7.8 3l5.8-5.8A19.5 19.5 0 0 0 24 4 20 20 0 0 0 5.8 15.1l6.8 5.3C14.2 15.6 18.7 12 24 12Z" />
                    </svg>
                    <span>{authMode === "signup" ? "Sign up with Google" : "Sign in with Google"}</span>
                  </button>
                  {authMode === "signup" && (
                    <p className="mt-2 text-center text-xs text-slate-500">
                      Creates a new analyst account in the selected domain. Choose a username above; no password is needed.
                    </p>
                  )}
                  {!oidcEnabled && (
                    <p id="google-signin-status" role="status" className="mt-2 text-center text-xs text-slate-500">
                      Google sign-in is not configured. Set OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, and OIDC_REDIRECT_URI in the backend environment.
                    </p>
                  )}
              </div>
              <p className="mt-4 text-center text-sm text-slate-400">
                {authMode === "signup" ? "Already have an account?" : "New to FD AI/BI?"}{" "}
                <button
                  type="button"
                  className="font-medium text-violet-200 transition hover:text-white"
                  onClick={() => {
                    const nextMode = authMode === "signup" ? "login" : "signup";
                    setAuthMode(nextMode);
                    if (nextMode === "signup" && domain === "healthcare") setDomain("retail");
                    setError("");
                  }}
                >
                  {authMode === "signup" ? "Sign in" : "Create an account"}
                </button>
              </p>
              {authMode === "login" && users.length > 0 && (
                <div className="mt-5 border-t border-white/[0.08] pt-4">
                  <p className="mb-2 text-xs text-slate-500">Demo accounts</p>
                  <div className="flex flex-wrap gap-2">
                    {users.map((user) => (
                      <button
                        type="button"
                        key={user.username}
                        onClick={() => {
                          setUsername(user.username);
                          setPassword(user.password_hint);
                          if (user.domain !== "all") setDomain(user.domain);
                        }}
                        className="rounded-lg border border-white/10 bg-white/[0.035] px-2.5 py-1.5 text-xs text-slate-400 transition hover:border-cyan-200/25 hover:text-cyan-100"
                      >
                        {user.username}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <p className="mt-5 flex items-start gap-2 text-[11px] leading-5 text-slate-500">
                <CircleHelp size={14} className="mt-0.5 shrink-0" /> Sign-in credentials are sent only to this workspace&apos;s configured API.
              </p>
            </motion.section>
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  );
}

function DemoUseCaseSlide({
  slide,
  index,
  reduceMotion,
}: {
  slide: (typeof DEMO_SLIDES)[number];
  index: number;
  reduceMotion: boolean;
}) {
  const Icon = slide.icon;
  const accentStyles = {
    violet: "border-violet-200/20 bg-violet-300/[0.08] text-violet-200",
    emerald: "border-emerald-200/20 bg-emerald-300/[0.08] text-emerald-200",
    sky: "border-sky-200/20 bg-sky-300/[0.08] text-sky-200",
  }[slide.accent];

  return (
    <motion.div
      initial={{ opacity: 0, x: reduceMotion ? 0 : 18 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: reduceMotion ? 0 : -18 }}
      transition={{ duration: reduceMotion ? 0 : 0.24 }}
      className="max-h-[min(70vh,620px)] overflow-y-auto px-5 py-6 sm:px-7 sm:py-8"
      aria-live="polite"
    >
      <div className="mb-6 flex items-start gap-4">
        <span className={`grid size-12 shrink-0 place-items-center rounded-2xl border ${accentStyles}`}><Icon size={22} /></span>
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-violet-200/70">{slide.segment}</p>
          <h2 id="demo-dialog-title" className="mt-2 text-xl font-semibold leading-tight tracking-tight text-white sm:text-2xl">{slide.title}</h2>
          <p id="demo-dialog-description" className="mt-2 text-xs text-slate-500">Financial AI workflow · scenario {index + 1}</p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <article className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4 sm:p-5">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-amber-200/80">The problem</p>
          <p className="text-sm leading-6 text-slate-300">{slide.problem}</p>
        </article>
        <article className="rounded-2xl border border-violet-200/[0.12] bg-violet-300/[0.035] p-4 sm:p-5">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-200/80">Application design</p>
          <p className="text-sm leading-6 text-slate-300">{slide.application}</p>
        </article>
        <article className="rounded-2xl border border-cyan-200/[0.12] bg-cyan-300/[0.035] p-4 sm:p-5">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-cyan-200/80">GenAI contextual layer</p>
          <p className="text-sm leading-6 text-slate-300">{slide.context}</p>
        </article>
      </div>
    </motion.div>
  );
}

function DashboardDemo({
  business,
  setBusiness,
  metrics,
  typedInsight,
}: {
  business: Business;
  setBusiness: (business: Business) => void;
  metrics: (typeof BUSINESS_METRICS)[Business];
  typedInsight: string;
}) {
  const reduceMotion = useReducedMotion();
  const metricValues: Record<Business, [string, string, string]> = {
    Banking: ["12.8k", "4.6%", "3.2 days"],
    Finance: ["8.4M", "2.3%", "1.8 days"],
    Healthcare: ["12.8k", "94.2%", "3.2 days"],
    Insurance: ["3.4k", "7.1%", "2.4 days"],
    Retail: ["12.8k", "4.6%", "3.2 days"],
  };

  return (
    <motion.div
      id="demo"
      initial={{ opacity: 0, y: 24, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: reduceMotion ? 0 : 0.8, delay: reduceMotion ? 0 : 0.18, ease: [0.22, 1, 0.36, 1] }}
      className="relative mx-auto w-full max-w-[660px] scroll-mt-8"
    >
      <motion.div aria-hidden="true" animate={reduceMotion ? undefined : { y: [0, -9, 0] }} transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }} className="absolute -right-3 -top-7 z-20 hidden rounded-xl border border-cyan-200/20 bg-slate-900/90 px-3 py-2 shadow-[0_10px_35px_rgba(34,211,238,.12)] backdrop-blur-xl sm:block">
        <div className="flex items-center gap-2 text-xs text-slate-300"><span className="size-1.5 rounded-full bg-amber-300" /> Sample preview</div>
      </motion.div>
      <motion.div aria-hidden="true" animate={reduceMotion ? undefined : { y: [0, 8, 0] }} transition={{ duration: 5.2, repeat: Infinity, ease: "easeInOut", delay: 0.4 }} className="absolute -bottom-7 -left-4 z-20 hidden rounded-xl border border-violet-200/15 bg-slate-900/90 px-3 py-2 shadow-[0_10px_35px_rgba(167,139,250,.12)] backdrop-blur-xl sm:block">
        <div className="flex items-center gap-2 text-xs text-slate-300"><Sparkles size={13} className="text-violet-300" /> AI analysis complete</div>
      </motion.div>

      <div className="fd-demo-window overflow-hidden rounded-[26px] border border-white/[0.13] bg-[#0d1421]/95 shadow-[0_28px_100px_rgba(0,0,0,.45),0_0_70px_rgba(79,70,229,.12)]">
        <div className="flex items-center justify-between border-b border-white/[0.08] px-4 py-3.5 sm:px-5">
          <div className="flex items-center gap-2">
            <span className="flex gap-1.5"><i className="size-2 rounded-full bg-rose-400/80" /><i className="size-2 rounded-full bg-amber-300/80" /><i className="size-2 rounded-full bg-emerald-300/80" /></span>
            <span className="ml-2 text-xs text-slate-400">Sample analytics preview</span>
          </div>
          <span className="hidden items-center gap-1.5 text-[10px] text-slate-500 sm:flex">Illustrative data · not live</span>
        </div>

        <div className="p-4 sm:p-6">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Business overview · sample</p>
              <h2 className="mt-1 text-lg font-semibold text-white">Your business overview</h2>
            </div>
            <label className="relative inline-flex items-center">
              <span className="sr-only">Choose demo business</span>
              <select value={business} onChange={(event) => setBusiness(event.target.value as Business)} className="appearance-none rounded-lg border border-white/10 bg-white/[0.045] py-2 pl-3 pr-9 text-xs text-slate-200 outline-none transition hover:border-cyan-200/30 focus:border-cyan-200/50">
                {(["Retail", "Banking", "Insurance", "Finance", "Healthcare"] as const).map((item) => <option className="bg-slate-900" key={item}>{item}</option>)}
              </select>
              <ChevronDown size={14} className="pointer-events-none absolute right-3 text-slate-500" />
            </label>
          </div>

          <div className="grid grid-cols-3 gap-2.5 sm:gap-3">
            <MetricCard label={metrics.label} value={metrics.value} change={metrics.change} accent="violet" />
            <MetricCard label="Active customers" value={metricValues[business][0]} change="+8.2%" accent="violet" />
            <MetricCard label="Conversion rate" value={metricValues[business][1]} change="+1.4%" accent="emerald" />
          </div>

          <div className="mt-4 rounded-2xl border border-white/[0.07] bg-white/[0.025] p-3.5 sm:p-4">
            <div className="mb-3 flex items-center justify-between">
              <div><p className="text-xs font-medium text-slate-200">Illustrative performance trend</p><p className="mt-0.5 text-[10px] text-slate-500">Sample values · last 30 days</p></div>
              <span className="rounded-md bg-emerald-300/10 px-2 py-1 text-[10px] font-medium text-emerald-200">{metrics.change}</span>
            </div>
            <div className="relative h-32 w-full">
              <svg viewBox="0 0 610 145" preserveAspectRatio="none" className="h-full w-full overflow-visible" role="img" aria-label={`${business} performance trend line chart`}>
                <defs>
                  <linearGradient id="chartArea" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#8b5cf6" stopOpacity=".24" /><stop offset="100%" stopColor="#8b5cf6" stopOpacity="0" /></linearGradient>
                  <linearGradient id="chartStroke" x1="0" x2="1" y1="0" y2="0"><stop offset="0%" stopColor="#7c3aed" /><stop offset="100%" stopColor="#c4b5fd" /></linearGradient>
                </defs>
                {[25, 62, 99, 136].map((y) => <line key={y} x1="0" x2="610" y1={y} y2={y} stroke="rgba(148,163,184,.12)" strokeDasharray="3 6" />)}
                <motion.path
                  d={`M ${metrics.points} L 602 145 L 8 145 Z`}
                  fill="url(#chartArea)"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 1.3, delay: 0.7 }}
                />
                <motion.polyline
                  points={metrics.points}
                  fill="none"
                  stroke="url(#chartStroke)"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  initial={{ pathLength: 0, opacity: 0.6 }}
                  animate={{ pathLength: 1, opacity: 1 }}
                  transition={{ duration: 1.7, delay: 0.55, ease: "easeInOut" }}
                />
                <motion.circle cx="602" cy={business === "Insurance" ? "18" : "12"} r="4.5" fill="#c4b5fd" initial={{ scale: 0 }} animate={{ scale: [0.8, 1.2, 1] }} transition={{ delay: 2, duration: 0.45 }} />
              </svg>
            </div>
            <div className="mt-1 flex justify-between text-[9px] text-slate-600"><span>01 JUN</span><span>08 JUN</span><span>15 JUN</span><span>22 JUN</span><span>30 JUN</span></div>
          </div>

          <motion.div layout className="mt-3 flex gap-3 rounded-2xl border border-cyan-200/15 bg-gradient-to-r from-indigo-400/[0.08] to-cyan-300/[0.055] p-3.5 sm:p-4">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-cyan-200/15 bg-cyan-200/[0.08] text-cyan-200"><Sparkles size={15} /></span>
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-xs font-medium text-cyan-100">
                Example insight
                <span className="inline-flex items-center gap-1 text-[9px] font-normal text-slate-500">illustrative</span>
              </div>
              <p className="mt-1.5 min-h-9 text-[11px] leading-5 text-slate-300 sm:text-xs">
                {business === "Retail" ? typedInsight : business === "Banking"
                  ? "Deposit balances are trending upward across the selected business context."
                  : business === "Insurance"
                    ? "Claims activity is summarized using the selected insurance business context."
                    : business === "Healthcare"
                      ? "Synthetic patient, encounter, claims, and quality metrics are summarized within the selected tenant."
                      : "Assets under management are summarized using the selected finance context."}
                {business === "Retail" && typedInsight.length < AI_INSIGHT.length && <span className="ml-0.5 inline-block h-3.5 w-px animate-pulse bg-cyan-300 align-middle" />}
              </p>
            </div>
          </motion.div>
        </div>
      </div>
    </motion.div>
  );
}

function MetricCard({ label, value, change, accent }: { label: string; value: string; change: string; accent: "cyan" | "violet" | "emerald" }) {
  const accentClass = {
    cyan: "text-cyan-200 bg-cyan-300/10",
    violet: "text-violet-200 bg-violet-300/10",
    emerald: "text-emerald-200 bg-emerald-300/10",
  }[accent];

  return (
    <motion.div layout className="min-w-0 rounded-xl border border-white/[0.075] bg-white/[0.025] p-2.5 sm:p-3">
      <p className="truncate text-[9px] leading-4 text-slate-500 sm:text-[10px]">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold tracking-tight text-white sm:text-lg">{value}</p>
      <span className={`mt-1 inline-flex rounded px-1.5 py-0.5 text-[9px] ${accentClass}`}>{change}</span>
    </motion.div>
  );
}

function Feature({ title, detail }: { title: string; detail: string }) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.article
      whileHover={reduceMotion ? undefined : { y: -4 }}
      className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-5 transition-colors hover:border-cyan-200/20 hover:bg-white/[0.04]"
    >
      <span className="mb-4 grid size-9 place-items-center rounded-xl border border-cyan-200/15 bg-cyan-200/[0.07] text-cyan-200"><Check size={17} /></span>
      <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-slate-400">{detail}</p>
    </motion.article>
  );
}
