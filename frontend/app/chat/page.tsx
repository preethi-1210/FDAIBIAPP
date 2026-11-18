"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion, type Variants } from "framer-motion";
import {
  Activity,
  ArrowLeft,
  BarChart3,
  Check,
  ChevronDown,
  CircleHelp,
  Clock3,
  Info,
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
  Square,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { ResultWorkspace } from "@/components/ResultWorkspace";
import { DOMAIN_LABELS } from "@/components/Shell";
import { ExecutionProgress } from "@/components/ask/ExecutionProgress";
import { OutOfScopeCard } from "@/components/ask/OutOfScopeCard";
import { AnswerCard, UnsupportedNotice, type InsightResult } from "@/components/ask/AnswerCards";
import { API_URL, ApiError, api, switchDomain } from "@/lib/api";
import { AskWelcome } from "@/components/ask/AskWelcome";
import { ComposerDomainPicker, ComposerPlusMenu } from "@/components/ask/ComposerMenus";
import { askStream } from "@/lib/askStream";
import { applyStreamEvent, failJourney, startJourney, type Journey } from "@/lib/askJourney";
import { PlanMenuLink, PlanNotices, PlanUsageCard, UpgradePrompt, usePlanUsage } from "@/components/PlanUsage";
import type { PlanUsage } from "@/lib/subscription";
import {
  detectSensitivePrompt,
  sensitivePromptNotice,
  SENSITIVE_DEMO_PROMPTS,
} from "@/lib/sensitive-data";

type Result = Parameters<typeof ResultWorkspace>[0]["result"] & {
  conversation_id?: string | null;
  usage?: PlanUsage | null;
  domain?: string | null;
  evidence?: InsightResult["evidence"];
  presentation?: InsightResult["presentation"];
};
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
  // How long the analysis took, when it ran in this session (history rows do not record it).
  elapsedMs?: number;
};
/** A question still running, or one that failed and can be retried. Not yet part of history. */
type PendingQuestion = {
  question: string;
  conversation: Conversation | null;
  journey: Journey;
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
  elapsedMs?: number;
};
type ConversationMeta = { title?: string; pinned?: boolean };

// The API reports how each answer was reached; only "complete" is a successful analysis.
const STATUS_LABELS: Record<string, string> = {
  complete: "Analysis complete",
  no_data: "No matching data",
  insufficient_data: "Insufficient data",
  insufficient_history: "Insufficient history",
  needs_clarification: "Clarification needed",
  awaiting_review: "Awaiting administrator review",
  out_of_scope: "Not a business question",
  error: "Could not complete",
};

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
  const [domain, setDomain] = useState("workspace");
  // Domains this user may work in, as the API reports them; never from browser storage.
  const [domains, setDomains] = useState<string[]>([]);
  const [username, setUsername] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [meta, setMeta] = useState<Record<string, ConversationMeta>>({});
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [planLimit, setPlanLimit] = useState("");
  const planUsage = usePlanUsage();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingQuestion | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const latestTurnRef = useRef<HTMLDivElement>(null);

  const domainLabel = DOMAIN_LABELS[domain] ?? domain;
  const activeConversation = conversations.find((item) => item.id === activeId) ?? null;
  const isConversationActive = activeConversation !== null;
  // A running question belongs to the conversation it was asked in.
  const showPending = pending !== null && (pending.conversation?.id ?? null) === (activeConversation?.id ?? null);
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
    api<{ username: string; domain: string; authorized_domains?: string[] }>("/me")
      .then((me) => {
        if (cancelled) return;
        setUsername(me.username);
        setDomain(me.domain);
        setDomains(me.authorized_domains ?? [me.domain]);
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
    if ((!activeId || !isConversationActive) && !pending) return;
    const behavior = reduceMotion ? "auto" : "smooth";
    // While a question runs, follow its progress; once answered, show the answer from its top.
    if (pending) messagesEndRef.current?.scrollIntoView({ behavior, block: "end" });
    else latestTurnRef.current?.scrollIntoView({ behavior, block: "start" });
  }, [activeId, conversations, isConversationActive, reduceMotion, pending]);

  // Leaving the page cancels a request still streaming.
  useEffect(() => () => abortRef.current?.abort(), []);

  const startNewAnalysis = useCallback(() => {
    if (!abortRef.current) setPending(null);
    setActiveId(null);
    setQuestion("");
    setError("");
    setSidebarOpen(false);
    window.setTimeout(() => textareaRef.current?.focus(), 30);
  }, []);

  async function selectConversation(conversation: Conversation) {
    if (!abortRef.current) setPending(null);
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

  async function runQuestion(submitted: string, conversation: Conversation | null = activeConversation) {
    if (!submitted || busy) return;
    const selectedConversation = conversation;
    const controller = new AbortController();
    const startedAt = performance.now();
    abortRef.current = controller;
    setBusy(true);
    setError("");
    setPlanLimit("");
    setQuestion("");
    setSidebarOpen(false);
    setPending({ question: submitted, conversation: selectedConversation, journey: startJourney() });
    try {
      // The progress shown is the server's own record of each stage as it happens.
      const result = await askStream<Result>(
        {
          question: submitted,
          conversation_id: selectedConversation?.conversationId ?? null,
        },
        (event) => setPending((current) => current && { ...current, journey: applyStreamEvent(current.journey, event, domainLabel) }),
        controller.signal,
      );
      setPending(null);
      applyResult(selectedConversation, submitted, result, Math.round(performance.now() - startedAt));
      planUsage.update(result.usage);
      setPlanLimit(result.usage?.limited_by_plan ?? "");
    } catch (cause) {
      if (controller.signal.aborted) {
        setPending((current) => current && {
          ...current,
          journey: failJourney(
            current.journey,
            "You cancelled this request. If the analysis had already started on the server, it may still finish and appear in Recent history.",
          ),
        });
      } else if (cause instanceof ApiError && cause.status === 402) {
        setPending(null);
        setPlanLimit(cause.message);
        void planUsage.refresh();
      } else {
        const message = cause instanceof Error ? cause.message : "The analysis could not be completed.";
        // A refusal (for example another domain's data) will not change on retry; an outage may.
        const retryable = !(cause instanceof ApiError) || cause.status === 0 || cause.status >= 500 || cause.status === 429;
        setPending((current) => current && { ...current, journey: failJourney(current.journey, message, retryable) });
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
    }
  }

  function cancelQuestion() {
    abortRef.current?.abort();
  }

  function retryPending() {
    if (!pending || busy) return;
    void runQuestion(pending.question, pending.conversation);
  }

  /** Asks an example question from the out-of-scope card in the same conversation. */
  function askExample(example: string) {
    if (busy) return;
    void runQuestion(example, activeConversation);
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

  function applyResult(selectedConversation: Conversation | null, submitted: string, result: Result, elapsedMs?: number) {
    const turn: ConversationTurn = {
      question: submitted,
      answer: result.answer,
      sql: result.sql,
      created_at: new Date().toISOString(),
      result,
      elapsedMs,
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

  async function changeDomain(next: string) {
    if (busy) return;
    try {
      await switchDomain(next);
      // A full reload drops conversations and history that belong to the previous domain.
      window.location.assign("/chat");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not switch domain.");
    }
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Ctrl + Enter (Cmd + Enter on a Mac) sends; Enter alone also sends, Shift + Enter adds a line.
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey || !event.shiftKey)) {
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
              <small>Authorized domain: {domainLabel}</small>
              <PlanMenuLink usage={planUsage.usage} />
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

        <PlanUsageCard usage={planUsage.usage} />
        <div className="chat-sidebar__footer">
          <div className="chat-user-avatar">{username.slice(0, 1).toUpperCase() || "A"}</div>
          <div className="chat-user-details"><b>{username || "Analyst"}</b><span>{domain}</span></div>
          <button type="button" className="chat-icon-button" aria-label="Sign out" title="Sign out" onClick={signOut}>
            <ArrowLeft size={16} />
          </button>
        </div>
      </aside>

      <section className="chat-main">
        <div className={`chat-scroll-area${isConversationActive || showPending ? " chat-scroll-area--conversation" : ""}`}>
          {!isConversationActive && !showPending ? (
            <AskWelcome
              domain={domain}
              onPrefill={setSuggestion}
              onAsk={(prompt) => void runQuestion(prompt, null)}
              disabled={busy}
            />
          ) : (
            <div className="chat-thread">
              {activeConversation ? (
              <AnimatePresence mode="popLayout">
                <motion.article key={activeConversation.id} className="chat-exchange" variants={messageVariants} initial="hidden" animate="visible" exit={{ opacity: 0, y: -8 }} layout>
                  {(activeConversation.turns?.length ? activeConversation.turns : [activeConversation]).map((turn, index, turns) => (
                    <div key={`${activeConversation.id}-${index}`} ref={index === turns.length - 1 ? latestTurnRef : undefined} className="chat-turn">
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
                                  {(turn.result?.status ?? "complete") === "complete" ? <Check size={12} />
                                    : turn.result?.status === "out_of_scope" ? <CircleHelp size={12} /> : <TriangleAlert size={12} />}{" "}
                                  {STATUS_LABELS[turn.result?.status ?? "complete"] ?? "Analysis complete"}
                                </small>
                              )}
                          </div>
                          {turn.sensitiveWarning ? (
                            <p className="chat-answer-text chat-answer-text--warning" role="alert">
                              <TriangleAlert size={16} aria-hidden="true" />
                              <span>{turn.answer}</span>
                            </p>
                          ) : turn.result?.status === "out_of_scope" ? (
                            <OutOfScopeCard
                              domain={turn.result.domain ?? domain}
                              domainLabel={DOMAIN_LABELS[turn.result.domain ?? domain] ?? domainLabel}
                              onAsk={askExample}
                              disabled={busy}
                            />
                          ) : turn.result?.status === "insufficient_data" && ["registry", "metric"].includes(turn.result.evidence?.kind ?? "") ? (
                            <UnsupportedNotice answer={turn.answer} domainLabel={DOMAIN_LABELS[turn.result.domain ?? domain] ?? domainLabel} />
                          ) : turn.result?.presentation
                            && ["complete", "no_data", "insufficient_history"].includes(turn.result.status ?? "complete")
                            && ["metric", "warehouse", "prediction"].includes(turn.result.evidence?.kind ?? "") ? (
                            <AnswerCard result={turn.result} elapsedMs={turn.elapsedMs} />
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
                          ) : !turn.sensitiveWarning && turn.result && turn.result.status !== "out_of_scope" ? (
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
              ) : null}
              {showPending && pending ? (
                <motion.div className="chat-exchange chat-pending-turn" variants={messageVariants} initial="hidden" animate="visible">
                  <div className="chat-user-message">
                    <div className="chat-message-avatar">{username.slice(0, 1).toUpperCase() || "A"}</div>
                    <div><span className="chat-message-label">You</span><p>{pending.question}</p></div>
                  </div>
                  <div className="chat-assistant-message">
                    <div className="chat-assistant-avatar"><Sparkles size={16} /></div>
                    <div className="chat-assistant-content">
                      <div className="chat-assistant-heading">
                        <span>FD AI/BI</span>
                        <small className={`chat-status${busy ? "" : " chat-status--error"}`}>
                          {busy ? <><LoaderCircle size={12} className="chat-spin" /> Working</> : <><TriangleAlert size={12} /> Stopped</>}
                        </small>
                      </div>
                      <ExecutionProgress
                        journey={pending.journey}
                        domainLabel={domainLabel}
                        onCancel={busy ? cancelQuestion : undefined}
                        onRetry={busy ? undefined : retryPending}
                        onDismiss={busy ? undefined : () => setPending(null)}
                      />
                    </div>
                  </div>
                </motion.div>
              ) : null}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        <div className="chat-composer-zone">
          <PlanNotices notices={planUsage.usage?.notices ?? []} onDismiss={planUsage.dismiss} />
          {planLimit ? <UpgradePrompt message={planLimit} onClose={() => setPlanLimit("")} /> : null}
          <AnimatePresence>
            {error && (
              <motion.div className="chat-error" role="alert" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}>
                <CircleHelp size={15} /> <span>{error}</span><button type="button" onClick={() => setError("")} aria-label="Dismiss error"><X size={14} /></button>
              </motion.div>
            )}
          </AnimatePresence>
          <form className="chat-composer ask-composer" onSubmit={(event) => void submitQuestion(event)}>
            <textarea
              ref={textareaRef}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder="Ask a question about your business data…"
              rows={1}
              maxLength={4000}
              aria-label="Ask a business analytics question"
              disabled={busy}
            />
            <div className="ask-composer__bar">
              <ComposerPlusMenu
                domain={domain}
                domainLabel={domainLabel}
                sensitivePrompts={SENSITIVE_DEMO_PROMPTS[domain] ?? SENSITIVE_DEMO_PROMPTS.retail}
                onPrefill={setSuggestion}
                onSensitivePrompt={showSensitiveNotice}
                disabled={busy}
              />
              <span className="ask-composer__divider" aria-hidden="true" />
              <ComposerDomainPicker domain={domain} domains={domains} labels={DOMAIN_LABELS} onSwitch={(next) => void changeDomain(next)} disabled={busy} />
              <span className="ask-composer__spacer" />
              {busy ? (
                <motion.button type="button" className="chat-submit chat-submit--stop ask-composer__send" onClick={cancelQuestion} whileTap={reduceMotion ? undefined : { scale: 0.95 }} aria-label="Stop the running analysis">
                  <Square size={13} fill="currentColor" />
                  <span>Stop</span>
                </motion.button>
              ) : (
                <motion.button type="submit" className="chat-submit ask-composer__send" disabled={!question.trim()} whileHover={reduceMotion ? undefined : { scale: 1.03 }} whileTap={reduceMotion ? undefined : { scale: 0.96 }} aria-label="Ask">
                  <Send size={17} />
                  <span>Ask</span>
                </motion.button>
              )}
            </div>
          </form>
          <p className="ask-composer__note">
            <Info size={15} />
            <span>Answers are grounded in approved source data. Press <b>Ctrl + Enter</b> to send.</span>
          </p>
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
