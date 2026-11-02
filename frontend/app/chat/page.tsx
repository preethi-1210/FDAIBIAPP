"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion, type Variants } from "framer-motion";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Check,
  ChevronDown,
  CircleHelp,
  Clock3,
  History,
  LoaderCircle,
  Menu,
  MessageSquareText,
  MoreHorizontal,
  Pin,
  Plus,
  Send,
  Sparkles,
  SquarePen,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { ResultWorkspace } from "@/components/ResultWorkspace";
import { API_URL, api } from "@/lib/api";
import {
  detectSensitivePrompt,
  sensitivePromptNotice,
  SENSITIVE_DEMO_PROMPTS,
} from "@/lib/sensitive-data";

type Result = Parameters<typeof ResultWorkspace>[0]["result"] & { conversation_id?: string | null };
type HistoryItem = {
  id: number;
  question: string;
  answer: string;
  sql?: string | null;
  result?: Result | null;
  conversation_id?: string | null;
  created_at?: string;
};
type ConversationTurn = {
  question: string;
  answer: string;
  sql?: string | null;
  created_at: string;
  result?: Result;
  sensitiveWarning?: boolean;
};
type Conversation = {
  id: string;
  conversationId?: string;
  question: string;
  answer: string;
  sql?: string | null;
  created_at: string;
  result?: Result;
  turns?: ConversationTurn[];
  sensitiveWarning?: boolean;
};
type ConversationMeta = { title?: string; pinned?: boolean };
type Provider = "auto" | "google" | "openai" | "grok" | "anthropic" | "ollama";

// The API reports how each answer was reached; only "complete" is a successful analysis.
const STATUS_LABELS: Record<string, string> = {
  complete: "Analysis complete",
  no_data: "No matching data",
  insufficient_data: "Insufficient data",
  needs_clarification: "Clarification needed",
  awaiting_review: "Awaiting administrator review",
  error: "Could not complete",
};

const SUGGESTIONS_BY_DOMAIN = {
  banking: [
    { icon: Activity, title: "Transaction activity", question: "Show transaction volume for last week", detail: "Review recent banking transaction totals" },
    { icon: BarChart3, title: "Account balances", question: "Show the latest account balance totals", detail: "See the most recent account balance summary" },
  ],
  finance: [
    { icon: Activity, title: "Monthly balances", question: "Show account balances by month", detail: "Review the monthly account balance trend" },
    { icon: BarChart3, title: "Latest balances", question: "Show the latest account balance totals", detail: "See the most recent account balance summary" },
  ],
  insurance: [
    { icon: Activity, title: "Policy premiums", question: "Show total premium by policy status", detail: "Compare premium totals across policy statuses" },
    { icon: BarChart3, title: "Claims activity", question: "How many claims were recorded last month?", detail: "Review recent claims volume" },
  ],
  retail: [
    { icon: Activity, title: "Weekly orders", question: "How many orders did we get last week?", detail: "Count orders from the previous calendar week" },
    { icon: BarChart3, title: "Revenue by region", question: "What is revenue by region?", detail: "Compare order revenue across regions" },
    { icon: MessageSquareText, title: "Sales trends", question: "Show monthly retail sales trend", detail: "Review monthly order value and volume" },
  ],
  healthcare: [
    { icon: Activity, title: "Common diagnoses", question: "What are the most common diagnosis categories?", detail: "Review aggregate diagnosis counts" },
    { icon: BarChart3, title: "Encounter activity", question: "How many encounters were recorded?", detail: "Review aggregate encounter volume" },
  ],
} satisfies Record<string, { icon: typeof Activity; title: string; question: string; detail: string }[]>;

const messageVariants: Variants = {
  hidden: { opacity: 0, y: 18 },
  visible: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 260, damping: 25 } },
};

function groupHistory(rows: HistoryItem[]): Conversation[] {
  const grouped = new Map<string, Conversation>();
  for (const row of rows) {
    const conversationId = row.conversation_id || undefined;
    const id = conversationId ? `thread-${conversationId}` : `history-${row.id}`;
    const turn: ConversationTurn = {
      question: row.question,
      answer: row.answer,
      sql: row.sql,
      created_at: row.created_at || new Date().toISOString(),
      result: row.result ?? undefined,
    };
    const existing = grouped.get(id);
    if (existing) {
      existing.turns?.push(turn);
    } else {
      grouped.set(id, {
        id,
        conversationId,
        question: row.question,
        answer: row.answer,
        sql: row.sql,
        created_at: turn.created_at,
        result: turn.result,
        turns: [turn],
      });
    }
  }
  return [...grouped.values()].map((conversation) => {
    const turns = conversation.turns?.reverse() ?? [];
    const first = turns[0];
    const latest = turns[turns.length - 1];
    return {
      ...conversation,
      question: first?.question ?? conversation.question,
      answer: latest?.answer ?? conversation.answer,
      sql: latest?.sql ?? conversation.sql,
      created_at: latest?.created_at ?? conversation.created_at,
      result: latest?.result,
      turns,
    };
  });
}

export default function ChatPage() {
  const router = useRouter();
  const reduceMotion = useReducedMotion();
  const [question, setQuestion] = useState("");
  const [provider, setProvider] = useState<Provider>("auto");
  const [domain, setDomain] = useState("workspace");
  const [username, setUsername] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [meta, setMeta] = useState<Record<string, ConversationMeta>>({});
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [inputFocused, setInputFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const activeConversation = conversations.find((item) => item.id === activeId) ?? null;
  const suggestions = SUGGESTIONS_BY_DOMAIN[domain as keyof typeof SUGGESTIONS_BY_DOMAIN] ?? [];
  const isConversationActive = activeConversation !== null;
  const pinnedItems = useMemo(() => conversations.filter((item) => meta[item.id]?.pinned), [conversations, meta]);
  const recentItems = useMemo(() => conversations.filter((item) => !meta[item.id]?.pinned), [conversations, meta]);

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

  useEffect(() => {
    setUsername(localStorage.getItem("ryze_user") || "Analyst");
    setDomain(localStorage.getItem("ryze_domain") || "workspace");
    try {
      const saved = localStorage.getItem("ryze_chat_meta");
      if (saved) setMeta(JSON.parse(saved) as Record<string, ConversationMeta>);
    } catch {
      localStorage.removeItem("ryze_chat_meta");
    }

    let cancelled = false;
    api<{ username: string; domain: string }>("/me")
      .then((me) => {
        if (cancelled) return;
        setUsername(me.username);
        setDomain(me.domain);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not verify your access role.");
      });

    api<HistoryItem[]>("/history")
      .then((rows) => {
        if (cancelled) return;
        setConversations(groupHistory(rows));
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load your recent analyses.");
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => {
    try {
      localStorage.setItem("ryze_chat_meta", JSON.stringify(meta));
    } catch {
      setError("Conversation preferences could not be saved in this browser.");
    }
  }, [meta]);

  useEffect(() => {
    if (!activeId || !isConversationActive) return;
    messagesEndRef.current?.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "end" });
  }, [activeId, conversations, isConversationActive, reduceMotion]);

  const startNewAnalysis = useCallback(() => {
    setActiveId(null);
    setQuestion("");
    setError("");
    setSidebarOpen(false);
    window.setTimeout(() => textareaRef.current?.focus(), 30);
  }, []);

  async function selectConversation(conversation: Conversation) {
    setActiveId(conversation.id);
    setQuestion("");
    setError("");
    setSidebarOpen(false);
    if (conversation.result || !conversation.id.startsWith("history-")) return;

    const historyId = Number(conversation.id.slice("history-".length));
    if (!Number.isSafeInteger(historyId)) {
      setError("This saved analysis has an invalid history ID.");
      return;
    }
    setRestoringId(conversation.id);
    try {
      const result = await api<Result>(`/history/${historyId}/replay`, { method: "POST" });
      setConversations((current) => current.map((item) => (
        item.id === conversation.id ? { ...item, answer: result.answer, sql: result.sql, result } : item
      )));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The saved analysis could not be restored.");
    } finally {
      setRestoringId((current) => current === conversation.id ? null : current);
    }
  }

  function togglePin(id: string) {
    setMeta((current) => ({
      ...current,
      [id]: { ...current[id], pinned: !current[id]?.pinned },
    }));
  }

  function removeConversation(id: string) {
    setConversations((current) => current.filter((item) => item.id !== id));
    if (activeId === id) setActiveId(null);
    setMeta((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
  }

  function beginRename(item: Conversation) {
    setRenamingId(item.id);
    setRenameValue(meta[item.id]?.title || item.question);
  }

  function saveRename(id: string) {
    const title = renameValue.trim();
    if (title) setMeta((current) => ({ ...current, [id]: { ...current[id], title } }));
    setRenamingId(null);
  }

  function setSuggestion(value: string) {
    setQuestion(value);
    setError("");
    textareaRef.current?.focus();
  }

  async function runQuestion(submitted: string) {
    if (!submitted || busy) return;
    const selectedConversation = activeConversation;
    setBusy(true);
    setError("");
    try {
      const result = await api<Result>("/query", {
        method: "POST",
        body: JSON.stringify({
          question: submitted,
          llm_provider: provider === "auto" ? null : provider,
          conversation_id: selectedConversation?.conversationId ?? null,
        }),
      });
      applyResult(selectedConversation, submitted, result);
      setQuestion("");
      setSidebarOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The analysis could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  /** Continue a prediction an administrator approved, in its original conversation. */
  async function resumeReview(reviewId: number, originalQuestion: string) {
    if (busy) return;
    const selectedConversation = activeConversation;
    setBusy(true);
    setError("");
    try {
      const result = await api<Result>(`/hitl/${reviewId}/resume`, { method: "POST" });
      applyResult(selectedConversation, originalQuestion, result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The approved request could not be run.");
    } finally {
      setBusy(false);
    }
  }

  function applyResult(selectedConversation: Conversation | null, submitted: string, result: Result) {
    const turn: ConversationTurn = {
      question: submitted,
      answer: result.answer,
      sql: result.sql,
      created_at: new Date().toISOString(),
      result,
    };
    const conversationId = result.conversation_id || selectedConversation?.conversationId;
    if (selectedConversation?.conversationId && conversationId === selectedConversation.conversationId) {
      setConversations((current) => current.map((item) => item.id === selectedConversation.id
        ? {
            ...item,
            answer: turn.answer,
            sql: turn.sql,
            created_at: turn.created_at,
            result,
            turns: [...(item.turns ?? [{
              question: item.question,
              answer: item.answer,
              sql: item.sql,
              created_at: item.created_at,
              result: item.result,
            }]), turn],
          }
        : item));
      setActiveId(selectedConversation.id);
    } else {
      const id = conversationId
        ? `thread-${conversationId}`
        : `query-${result.audit_id || Date.now()}`;
      const conversation: Conversation = {
        id,
        conversationId: conversationId ?? undefined,
        ...turn,
        turns: [turn],
      };
      setConversations((current) => [conversation, ...current]);
      setActiveId(id);
    }
  }

  function showSensitiveNotice(submitted: string) {
    const kind = detectSensitivePrompt(submitted, domain);
    if (!kind) return;
    const conversation: Conversation = {
      id: `sensitive-notice-${Date.now()}`,
      question: submitted,
      answer: sensitivePromptNotice(kind),
      created_at: new Date().toISOString(),
      sensitiveWarning: true,
    };
    setConversations((current) => [conversation, ...current]);
    setActiveId(conversation.id);
    setQuestion("");
    setError("");
    setSidebarOpen(false);
  }

  async function submitQuestion(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const submitted = question.trim();
    if (!submitted || busy) return;
    if (detectSensitivePrompt(submitted, domain)) {
      showSensitiveNotice(submitted);
      return;
    }
    await runQuestion(submitted);
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitQuestion();
    }
  }

  return (
    <main className="chat-app">
      <header className="app-header chat-app-header">
        <Link href="/" className="app-brand chat-app-brand">
          <span className="app-brand__name">RYZE AI/BI</span>
          <span className="app-brand__tagline">GOVERNED BUSINESS ASSISTANT</span>
        </Link>
        <nav className="app-navigation chat-app-navigation" aria-label="Main navigation">
          <Link href="/context" className="app-navigation__link">Foresight</Link>
          <Link href="/dashboard" className="app-navigation__link">Dashboard</Link>
          <Link href="/chat" className="app-navigation__link app-navigation__link--active" aria-current="page">Ask</Link>
          <Link href="/settings" className="app-navigation__link">Connect</Link>
          <details className="app-account-menu">
            <summary className="app-account-menu__summary">
              <span className="app-account-menu__avatar">{username.slice(0, 1).toUpperCase() || "U"}</span>
              <span>{username || "User"} · {domain}</span>
              <ChevronDown size={14} aria-hidden="true" />
            </summary>
            <div className="app-account-menu__panel">
              <span>{username || "Signed-in user"}</span>
              <small>Authorized domain: {domain}</small>
              <label className="chat-provider chat-provider--menu">
                <span>Language model provider</span>
                <select value={provider} onChange={(event) => setProvider(event.target.value as Provider)}>
                  <option value="auto">Auto</option>
                  <option value="google">Google Gemini</option>
                  <option value="openai">OpenAI</option>
                  <option value="grok">Grok</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="ollama">Ollama</option>
                </select>
                <ChevronDown size={13} />
              </label>
              <button type="button" onClick={signOut}>Sign out</button>
            </div>
          </details>
        </nav>
      </header>
      <nav className="chat-module-nav" aria-label="Ask workspace">
        <button type="button" className="chat-icon-button chat-menu-button" aria-label="Open sidebar" onClick={() => setSidebarOpen(true)}><Menu size={17} /></button>
        <Link href="/chat" className="chat-module-nav__link chat-module-nav__link--active"><Sparkles size={13} /> Ask</Link>
        <Link href="/monitor" className="chat-module-nav__link"><Activity size={13} /> Monitor</Link>
        <Link href="/benchmarks" className="chat-module-nav__link"><Check size={13} /> Benchmark</Link>
      </nav>

      <div className="chat-workspace">
      <AnimatePresence>
        {sidebarOpen && (
          <motion.button
            type="button"
            aria-label="Close conversation sidebar"
            className="chat-sidebar-scrim"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setSidebarOpen(false)}
          />
        )}
      </AnimatePresence>

      <aside
        className={`chat-sidebar${sidebarOpen ? " chat-sidebar--open" : ""}`}
        aria-label="Conversation navigation"
      >
        <div className="chat-sidebar__top">
          <Link href="/" className="chat-sidebar__brand" aria-label="FD AI/BI home">
            <span className="chat-sidebar__brand-icon"><BarChart3 size={19} /></span>
            <span>FD <b>AI/BI</b></span>
          </Link>
          <button type="button" className="chat-icon-button chat-sidebar__mobile-close" aria-label="Close sidebar" onClick={() => setSidebarOpen(false)}>
            <X size={18} />
          </button>
        </div>

        <motion.button
          type="button"
          className="chat-new-button"
          onClick={startNewAnalysis}
          whileHover={reduceMotion ? undefined : { scale: 1.015, y: -1 }}
          whileTap={reduceMotion ? undefined : { scale: 0.98 }}
        >
          <Plus size={17} /> New analysis <SquarePen size={15} className="chat-new-button__trailing" />
        </motion.button>

        <div className="chat-sidebar__scroll">
          <section className="chat-sidebar-section chat-history-section" aria-labelledby="recent-history-heading">
            <div className="chat-sidebar-section__heading">
              <span id="recent-history-heading">Recent history</span>
              <History size={14} />
            </div>
            {pinnedItems.length > 0 && (
              <div className="chat-history-group">
                <div className="chat-history-group__label"><Pin size={11} /> Pinned</div>
                <AnimatePresence initial={false}>
                  {pinnedItems.map((item) => (
                    <HistoryRow key={item.id} item={item} active={activeId === item.id} pinned={Boolean(meta[item.id]?.pinned)} title={meta[item.id]?.title} editing={renamingId === item.id} renameValue={renameValue} onRenameChange={setRenameValue} onSelect={selectConversation} onTogglePin={togglePin} onDelete={removeConversation} onStartRename={beginRename} onSaveRename={saveRename} onCancelRename={() => setRenamingId(null)} reduceMotion={reduceMotion} />
                  ))}
                </AnimatePresence>
              </div>
            )}
            {recentItems.length > 0 && (
              <div className="chat-history-group">
                {pinnedItems.length > 0 && <div className="chat-history-group__label"><Clock3 size={11} /> Earlier</div>}
                <AnimatePresence initial={false}>
                  {recentItems.map((item) => (
                    <HistoryRow key={item.id} item={item} active={activeId === item.id} pinned={Boolean(meta[item.id]?.pinned)} title={meta[item.id]?.title} editing={renamingId === item.id} renameValue={renameValue} onRenameChange={setRenameValue} onSelect={selectConversation} onTogglePin={togglePin} onDelete={removeConversation} onStartRename={beginRename} onSaveRename={saveRename} onCancelRename={() => setRenamingId(null)} reduceMotion={reduceMotion} />
                  ))}
                </AnimatePresence>
              </div>
            )}
            {historyLoading && <div className="chat-sidebar-empty"><LoaderCircle size={14} className="chat-spin" /> Loading analyses…</div>}
            {!historyLoading && conversations.length === 0 && (
              <div className="chat-sidebar-empty">Your recent analyses will appear here.</div>
            )}
          </section>
        </div>

        <div className="chat-sidebar__footer">
          <div className="chat-user-avatar">{username.slice(0, 1).toUpperCase() || "A"}</div>
          <div className="chat-user-details"><b>{username || "Analyst"}</b><span>{domain}</span></div>
          <button type="button" className="chat-icon-button" aria-label="Sign out" title="Sign out" onClick={signOut}>
            <ArrowLeft size={16} />
          </button>
        </div>
      </aside>

      <section className="chat-main">
        <div className={`chat-scroll-area${isConversationActive ? " chat-scroll-area--conversation" : ""}${busy ? " chat-scroll-area--busy" : ""}`}>
          {!isConversationActive ? (
            <motion.div className="chat-welcome" initial="hidden" animate="visible" variants={{ visible: { transition: { staggerChildren: 0.07 } } }}>
              <motion.div variants={messageVariants} className="chat-welcome__icon"><Sparkles size={23} /></motion.div>
              <motion.p variants={messageVariants} className="chat-welcome__eyebrow">FD AI/BI ANALYTICS</motion.p>
              <motion.h1 variants={messageVariants}>Natural Language to Insights</motion.h1>
              <motion.p variants={messageVariants} className="chat-welcome__description">
                Ask a business question. Explore trusted data, discover patterns, and get an answer with the analysis behind it.
              </motion.p>
              <motion.div variants={messageVariants} className="chat-suggestions" aria-label="Suggested business questions">
                {suggestions.map(({ icon: Icon, title, question: prompt, detail }) => (
                  <motion.button
                    key={prompt}
                    type="button"
                    className="chat-suggestion"
                    onClick={() => setSuggestion(prompt)}
                    whileHover={reduceMotion ? undefined : { y: -2 }}
                    whileTap={reduceMotion ? undefined : { scale: 0.985 }}
                  >
                    <span className="chat-suggestion__icon"><Icon size={16} /></span>
                    <span className="chat-suggestion__copy"><b>{title}</b><small>{detail}</small></span>
                    <ArrowRight size={14} className="chat-suggestion__arrow" />
                  </motion.button>
                ))}
              </motion.div>
              <motion.section
                variants={messageVariants}
                className="chat-sensitive-demo"
                aria-label={`${domain} sensitive data demo prompts`}
              >
                <div className="chat-sensitive-demo__heading">
                  <TriangleAlert size={14} />
                  <span>Sensitive-data demo · {domain}</span>
                </div>
                <div className="chat-sensitive-demo__prompts">
                  {(SENSITIVE_DEMO_PROMPTS[domain] ?? SENSITIVE_DEMO_PROMPTS.retail).map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      className="chat-sensitive-demo__prompt"
                      onClick={() => showSensitiveNotice(prompt)}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </motion.section>
            </motion.div>
          ) : (
            <div className="chat-thread">
              <AnimatePresence mode="popLayout">
                <motion.article key={activeConversation.id} className="chat-exchange" variants={messageVariants} initial="hidden" animate="visible" exit={{ opacity: 0, y: -8 }} layout>
                  {(activeConversation.turns?.length ? activeConversation.turns : [activeConversation]).map((turn, index) => (
                    <div key={`${activeConversation.id}-${index}`}>
                      <div className="chat-user-message">
                        <div className="chat-message-avatar">{username.slice(0, 1).toUpperCase() || "A"}</div>
                        <div><span className="chat-message-label">You</span><p>{turn.question}</p></div>
                      </div>
                      <div className={`chat-assistant-message${turn.sensitiveWarning ? " chat-assistant-message--warning" : ""}`}>
                        <div className="chat-assistant-avatar">
                          {turn.sensitiveWarning ? <TriangleAlert size={16} /> : <Sparkles size={16} />}
                        </div>
                        <div className="chat-assistant-content">
                          <div className="chat-assistant-heading">
                            <span>{turn.sensitiveWarning ? "Sensitive-data notice" : "FD AI/BI"}</span>
                            {turn.sensitiveWarning
                              ? <small className="chat-sensitive-warning__label">Protected information</small>
                              : (
                                <small className={`chat-status chat-status--${turn.result?.status ?? "complete"}`}>
                                  {(turn.result?.status ?? "complete") === "complete" ? <Check size={12} /> : <TriangleAlert size={12} />}{" "}
                                  {STATUS_LABELS[turn.result?.status ?? "complete"] ?? "Analysis complete"}
                                </small>
                              )}
                          </div>
                          {turn.sensitiveWarning ? (
                            <p className="chat-answer-text chat-answer-text--warning" role="alert">
                              <TriangleAlert size={16} aria-hidden="true" />
                              <span>{turn.answer}</span>
                            </p>
                          ) : (
                            <p className={`chat-answer-text${turn.result?.status && turn.result.status !== "complete" ? ` chat-answer-text--${turn.result.status}` : ""}`}>
                              {turn.answer}
                            </p>
                          )}
                          {turn.result?.status === "awaiting_review" && turn.result.hitl?.review_id ? (
                            <button
                              type="button"
                              className="chat-resume-button"
                              disabled={busy}
                              onClick={() => void resumeReview(turn.result!.hitl!.review_id!, turn.question)}
                            >
                              Run approved prediction
                            </button>
                          ) : null}
                          {!turn.sensitiveWarning && restoringId === activeConversation.id ? (
                            <div className="chat-sidebar-empty"><LoaderCircle size={14} className="chat-spin" /> Restoring charts and analysis details…</div>
                          ) : !turn.sensitiveWarning && turn.result ? (
                            <div className="chat-result-surface"><ResultWorkspace result={turn.result} /></div>
                          ) : !turn.sensitiveWarning && turn.sql ? (
                            <details className="chat-sql-details">
                              <summary>View generated SQL</summary>
                              <pre>{turn.sql}</pre>
                            </details>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  ))}
                </motion.article>
              </AnimatePresence>
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        <AnimatePresence>
          {busy && (
            <motion.div
              className="chat-processing-overlay"
              role="status"
              aria-live="polite"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              <div className="chat-processing-card">
                <LoaderCircle size={26} className="chat-spin" />
                <strong>Analyzing your question</strong>
                <span>Checking governed data and building your results…</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="chat-composer-zone">
          <AnimatePresence>
            {error && (
              <motion.div className="chat-error" role="alert" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}>
                <CircleHelp size={15} /> <span>{error}</span><button type="button" onClick={() => setError("")} aria-label="Dismiss error"><X size={14} /></button>
              </motion.div>
            )}
          </AnimatePresence>
          <motion.form
            className="chat-composer"
            onSubmit={(event) => void submitQuestion(event)}
            animate={inputFocused ? { scale: 1.008 } : { scale: 1 }}
            transition={{ duration: 0.2, ease: "easeInOut" }}
          >
            <motion.div className="chat-composer__glow" animate={{ opacity: inputFocused ? 1 : 0.45 }} transition={{ duration: 0.2 }} />
            <textarea
              ref={textareaRef}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onFocus={() => setInputFocused(true)}
              onBlur={() => setInputFocused(false)}
              onKeyDown={handleInputKeyDown}
              placeholder="Ask anything about your business…"
              rows={1}
              maxLength={4000}
              aria-label="Ask a business analytics question"
              disabled={busy}
            />
            <div className="chat-composer__bottom">
              <span className="chat-composer__hint"><span>↵</span> to ask <span className="chat-composer__hint-divider">·</span> Shift + ↵ for a new line</span>
              <div className="chat-composer__controls">
                <span className="chat-composer__domain">{domain}</span>
                <motion.button type="submit" className="chat-submit" disabled={busy || !question.trim()} whileHover={reduceMotion ? undefined : { scale: 1.04 }} whileTap={reduceMotion ? undefined : { scale: 0.95 }} aria-label={busy ? "Analyzing" : "Ask"}>
                  {busy ? <LoaderCircle size={17} className="chat-spin" /> : <Send size={16} />}
                  <span>{busy ? "Analyzing" : "Ask"}</span>
                </motion.button>
              </div>
            </div>
          </motion.form>
          <p className="chat-composer-disclaimer">AI-generated analysis can be wrong. Verify important business decisions against your source data.</p>
        </div>
      </section>
      </div>
      <footer className="chat-footer">
        <span>RYZE AI/BI</span>
        <span>Governed business intelligence</span>
        <span>{domain} · Databricks Genie not used</span>
      </footer>
    </main>
  );
}

function HistoryRow({
  item,
  active,
  pinned,
  title,
  editing,
  renameValue,
  onRenameChange,
  onSelect,
  onTogglePin,
  onDelete,
  onStartRename,
  onSaveRename,
  onCancelRename,
  reduceMotion,
}: {
  item: Conversation;
  active: boolean;
  pinned: boolean;
  title?: string;
  editing: boolean;
  renameValue: string;
  onRenameChange: (value: string) => void;
  onSelect: (item: Conversation) => void;
  onTogglePin: (id: string) => void;
  onDelete: (id: string) => void;
  onStartRename: (item: Conversation) => void;
  onSaveRename: (id: string) => void;
  onCancelRename: () => void;
  reduceMotion: boolean | null;
}) {
  const [actionsOpen, setActionsOpen] = useState(false);
  return (
    <motion.div
      className={`chat-history-row${active ? " chat-history-row--active" : ""}`}
      layout
      initial={reduceMotion ? false : { opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      exit={reduceMotion ? undefined : { opacity: 0, x: -12, height: 0, margin: 0 }}
      transition={{ duration: 0.2, ease: "easeInOut" }}
    >
      {editing ? (
        <input
          className="chat-history-row__rename"
          value={renameValue}
          autoFocus
          maxLength={100}
          aria-label="Rename conversation"
          onChange={(event) => onRenameChange(event.target.value)}
          onBlur={() => onSaveRename(item.id)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSaveRename(item.id);
            if (event.key === "Escape") onCancelRename();
          }}
        />
      ) : (
        <button type="button" className="chat-history-row__select" onClick={() => onSelect(item)} title={title || item.question}>
          <MessageSquareText size={14} />
          <span>{title || item.question}</span>
        </button>
      )}
      <button type="button" className="chat-history-row__more" aria-label={`Actions for ${title || item.question}`} aria-expanded={actionsOpen} onClick={() => setActionsOpen((open) => !open)}>
        <MoreHorizontal size={16} />
      </button>
      <AnimatePresence>
        {actionsOpen && (
          <motion.div className="chat-history-menu" initial={{ opacity: 0, scale: 0.96, y: 4 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.96, y: 4 }} transition={{ duration: 0.14 }}>
            <button type="button" onClick={() => { onStartRename(item); setActionsOpen(false); }}><SquarePen size={13} /> Rename</button>
            <button type="button" onClick={() => { onTogglePin(item.id); setActionsOpen(false); }}><Pin size={13} /> {pinned ? "Unpin" : "Pin"}</button>
            <button type="button" className="chat-history-menu__delete" onClick={() => { onDelete(item.id); setActionsOpen(false); }}><Trash2 size={13} /> Remove</button>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
