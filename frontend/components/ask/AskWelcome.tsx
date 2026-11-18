"use client";

import { motion, useReducedMotion, type Variants } from "framer-motion";
import {
  BarChart3,
  ChevronRight,
  FileText,
  Globe2,
  Pill,
  ShieldCheck,
  ShoppingBag,
  Stethoscope,
  Users,
  Wallet,
  type LucideIcon,
} from "lucide-react";

export type AskSuggestion = { label: string; question: string; icon: LucideIcon };

/**
 * Starting points per domain. Every question is answered by that domain's approved metric
 * registry; each was run against the live API before being listed here.
 */
export const ASK_SUGGESTIONS: Record<string, { topics: AskSuggestion[]; questions: AskSuggestion[] }> = {
  banking: {
    topics: [
      { label: "Transaction activity", question: "What is the total transaction amount?", icon: BarChart3 },
      { label: "Account balances", question: "What is the total account balance?", icon: Wallet },
      { label: "Customer trends", question: "Show new customers onboarded by month", icon: Users },
    ],
    questions: [
      { label: "Show the transaction amount by channel", question: "Show the transaction amount by channel", icon: BarChart3 },
      { label: "How many active accounts do we have?", question: "How many active accounts do we have?", icon: Users },
      { label: "What is the average transaction amount?", question: "What is the average transaction amount?", icon: Wallet },
    ],
  },
  finance: {
    topics: [
      { label: "Cash balances", question: "What is the total cash balance?", icon: Wallet },
      { label: "Trading activity", question: "How many trades do we have?", icon: BarChart3 },
      { label: "Client growth", question: "Show monthly new clients onboarded", icon: Users },
    ],
    questions: [
      { label: "What is the gross trade value?", question: "What is the gross trade value?", icon: BarChart3 },
      { label: "How many clients do we have?", question: "How many clients do we have?", icon: Users },
      { label: "Show the number of accounts by account type", question: "Show the number of accounts by account type", icon: Wallet },
    ],
  },
  insurance: {
    topics: [
      { label: "Premiums", question: "What is the total written premium?", icon: Wallet },
      { label: "Claims", question: "How many claims do we have?", icon: FileText },
      { label: "Collections", question: "What is the premium collection rate?", icon: BarChart3 },
    ],
    questions: [
      { label: "Show monthly premium collected", question: "Show monthly premium collected", icon: BarChart3 },
      { label: "What is the approved claim amount?", question: "What is the approved claim amount?", icon: FileText },
      { label: "How many policies do we have?", question: "How many policies do we have?", icon: ShieldCheck },
    ],
  },
  retail: {
    topics: [
      { label: "Sales", question: "What are total net sales?", icon: BarChart3 },
      { label: "Orders", question: "How many orders do we have?", icon: ShoppingBag },
      { label: "Customers", question: "How many customers do we have?", icon: Users },
    ],
    questions: [
      { label: "Show monthly net sales trend", question: "Show monthly net sales trend", icon: BarChart3 },
      { label: "What is the refund amount?", question: "What is the refund amount?", icon: Wallet },
      { label: "Show net sales by region", question: "Show net sales by region", icon: Globe2 },
    ],
  },
  healthcare: {
    topics: [
      { label: "Encounters", question: "How many encounters do we have?", icon: Stethoscope },
      { label: "Patients", question: "How many patients do we have?", icon: Users },
      { label: "Claims", question: "How many claims do we have?", icon: FileText },
    ],
    questions: [
      { label: "Show monthly number of claims", question: "Show monthly number of claims", icon: BarChart3 },
      { label: "What is the approved claim amount?", question: "What is the approved claim amount?", icon: Wallet },
      { label: "How many prescriptions do we have?", question: "How many prescriptions do we have?", icon: Pill },
    ],
  },
};

const enter: Variants = {
  hidden: { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 260, damping: 26 } },
};

const BARS = [
  { x: 52, height: 30 },
  { x: 84, height: 46 },
  { x: 116, height: 66 },
  { x: 148, height: 44 },
  { x: 180, height: 62 },
  { x: 212, height: 82 },
];
const LINE = "M40 76 L100 52 L136 40 L172 60 L218 34 L250 46";

function AnalyticsIllustration({ reduceMotion }: { reduceMotion: boolean }) {
  return (
    <svg className="ask-welcome__art" viewBox="0 0 380 130" aria-hidden="true">
      <defs>
        <linearGradient id="ask-bar" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#8b3cf6" />
          <stop offset="100%" stopColor="#c9a8fb" />
        </linearGradient>
        <linearGradient id="ask-donut" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#a774f8" />
          <stop offset="100%" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      {[{ x: 30, y: 28, r: 4 }, { x: 18, y: 72, r: 2.5 }, { x: 310, y: 22, r: 2.5 }, { x: 352, y: 70, r: 4 }, { x: 370, y: 100, r: 2.5 }, { x: 26, y: 112, r: 2.5 }].map((dot) => (
        <motion.circle
          key={`${dot.x}-${dot.y}`}
          cx={dot.x}
          cy={dot.y}
          r={dot.r}
          fill="#a78bfa"
          animate={reduceMotion ? undefined : { opacity: [0.35, 0.9, 0.35] }}
          transition={{ duration: 3, repeat: Infinity, delay: dot.x / 120 }}
        />
      ))}
      <line x1="38" y1="116" x2="262" y2="116" stroke="#ddd0f3" strokeWidth="2" strokeLinecap="round" />
      {BARS.map((bar, index) => (
        <motion.rect
          key={bar.x}
          x={bar.x}
          width="22"
          rx="3"
          fill="url(#ask-bar)"
          initial={reduceMotion ? false : { y: 116, height: 0 }}
          animate={{ y: 116 - bar.height, height: bar.height }}
          transition={{ duration: 0.6, delay: 0.1 + index * 0.07, ease: "easeOut" }}
        />
      ))}
      <motion.path
        d={LINE}
        fill="none"
        stroke="#7c3aed"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={reduceMotion ? false : { pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 1, delay: 0.4, ease: "easeInOut" }}
      />
      {[{ x: 136, y: 40 }, { x: 172, y: 60 }, { x: 218, y: 34 }].map((point) => (
        <circle key={point.x} cx={point.x} cy={point.y} r="5.5" fill="#7c3aed" stroke="#fff" strokeWidth="2" />
      ))}
      <motion.g
        initial={reduceMotion ? false : { rotate: -90, opacity: 0 }}
        animate={{ rotate: 0, opacity: 1 }}
        transition={{ duration: 0.8, delay: 0.3, ease: "easeOut" }}
        style={{ transformOrigin: "300px 66px" }}
      >
        <circle cx="300" cy="66" r="34" fill="none" stroke="#e9ddfd" strokeWidth="18" />
        <circle cx="300" cy="66" r="34" fill="none" stroke="url(#ask-donut)" strokeWidth="18" strokeDasharray="150 214" transform="rotate(-90 300 66)" />
      </motion.g>
    </svg>
  );
}

/** The Ask start screen. Selecting a topic fills the question box; a question card asks it. */
export function AskWelcome({
  domain,
  onPrefill,
  onAsk,
  disabled,
}: {
  domain: string;
  onPrefill: (question: string) => void;
  onAsk: (question: string) => void;
  disabled?: boolean;
}) {
  const reduceMotion = Boolean(useReducedMotion());
  const suggestions = ASK_SUGGESTIONS[domain] ?? { topics: [], questions: [] };
  return (
    <motion.div className="ask-welcome" initial="hidden" animate="visible" variants={{ visible: { transition: { staggerChildren: 0.06 } } }}>
      <motion.p variants={enter} className="ask-welcome__eyebrow"><span aria-hidden="true" />FD AI/BI ANALYTICS<span aria-hidden="true" /></motion.p>
      <motion.h1 variants={enter}>What would you like to understand today?</motion.h1>
      <motion.p variants={enter} className="ask-welcome__lede">Explore trusted business data and get clear, source-backed answers.</motion.p>

      {suggestions.topics.length ? (
        <motion.div variants={enter} className="ask-welcome__topics" aria-label="Topics">
          {suggestions.topics.map(({ label, question, icon: Icon }) => (
            <button key={label} type="button" className="ask-welcome__topic" disabled={disabled} onClick={() => onPrefill(question)} title={question}>
              <Icon size={17} /> {label}
            </button>
          ))}
        </motion.div>
      ) : null}

      <motion.p variants={enter} className="ask-welcome__protected">
        <ShieldCheck size={17} />
        <span><b>Your data is protected.</b> Access is governed and based on your permissions.</span>
      </motion.p>

      <motion.section variants={enter} className="ask-welcome__start" aria-labelledby="ask-start-heading">
        <AnalyticsIllustration reduceMotion={reduceMotion} />
        <h2 id="ask-start-heading">Start with a business question</h2>
        {suggestions.questions.length ? (
          <div className="ask-welcome__questions">
            {suggestions.questions.map(({ label, question, icon: Icon }) => (
              <motion.button
                key={question}
                type="button"
                className="ask-welcome__question"
                disabled={disabled}
                onClick={() => onAsk(question)}
                whileHover={reduceMotion || disabled ? undefined : { y: -2 }}
                whileTap={reduceMotion || disabled ? undefined : { scale: 0.985 }}
              >
                <Icon size={22} className="ask-welcome__question-icon" />
                <span>{label}</span>
                <ChevronRight size={18} className="ask-welcome__question-arrow" />
              </motion.button>
            ))}
          </div>
        ) : null}
      </motion.section>
    </motion.div>
  );
}
