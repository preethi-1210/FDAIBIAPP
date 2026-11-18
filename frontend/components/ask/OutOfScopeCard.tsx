"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Lightbulb, MessageSquareText } from "lucide-react";

/** Example questions answered by each domain's approved metric registry. */
export const DOMAIN_EXAMPLE_QUESTIONS: Record<string, string[]> = {
  banking: [
    "What is the total transaction amount?",
    "How many active accounts do we have?",
    "What is the transaction failure rate?",
    "Show monthly transaction trends",
  ],
  finance: [
    "What is the total cash balance?",
    "How many trades do we have?",
    "What is the gross trade value?",
    "Show monthly new clients onboarded",
  ],
  insurance: [
    "What is the total written premium?",
    "How many claims do we have?",
    "What is the approved claim amount?",
    "Show monthly premium collected",
  ],
  retail: [
    "What are total net sales?",
    "How many orders do we have?",
    "What is the refund amount?",
    "Show monthly net sales trend",
  ],
  healthcare: [
    "How many encounters do we have?",
    "How many patients do we have?",
    "What is the approved claim amount?",
    "Show monthly number of claims",
  ],
};

const PARTICLES = [
  { left: "12%", top: "22%", size: 6, delay: 0 },
  { left: "82%", top: "18%", size: 5, delay: 0.8 },
  { left: "88%", top: "70%", size: 7, delay: 1.6 },
  { left: "8%", top: "74%", size: 5, delay: 2.2 },
  { left: "50%", top: "8%", size: 4, delay: 1.2 },
];

function AssistantIllustration({ reduceMotion }: { reduceMotion: boolean }) {
  return (
    <div className="ask-scope__art" aria-hidden="true">
      {PARTICLES.map((particle) => (
        <motion.span
          key={`${particle.left}-${particle.top}`}
          className="ask-scope__particle"
          style={{ left: particle.left, top: particle.top, width: particle.size, height: particle.size }}
          animate={reduceMotion ? undefined : { opacity: [0.25, 0.85, 0.25], y: [0, -8, 0] }}
          transition={{ duration: 3.2, repeat: Infinity, delay: particle.delay, ease: "easeInOut" }}
        />
      ))}
      <motion.svg
        viewBox="0 0 120 120"
        className="ask-scope__robot"
        animate={reduceMotion ? undefined : { y: [0, -7, 0] }}
        transition={{ duration: 3.6, repeat: Infinity, ease: "easeInOut" }}
      >
        <line x1="60" y1="20" x2="60" y2="31" stroke="#8b5cf6" strokeWidth="3" strokeLinecap="round" />
        <circle cx="60" cy="16" r="5" fill="#a78bfa" />
        <rect x="26" y="31" width="68" height="54" rx="24" fill="#ffffff" stroke="#e4d7fb" strokeWidth="2" />
        <rect x="36" y="44" width="48" height="27" rx="13.5" fill="#2e1065" />
        <path d="M47 55 q4 -5 8 0" stroke="#c4b5fd" strokeWidth="3" fill="none" strokeLinecap="round" />
        <path d="M65 55 q4 -5 8 0" stroke="#c4b5fd" strokeWidth="3" fill="none" strokeLinecap="round" />
        <path d="M54 63 q6 4 12 0" stroke="#c4b5fd" strokeWidth="2.5" fill="none" strokeLinecap="round" />
        <rect x="18" y="50" width="9" height="18" rx="4.5" fill="#ddd0fb" />
        <rect x="93" y="50" width="9" height="18" rx="4.5" fill="#ddd0fb" />
        <rect x="44" y="86" width="32" height="18" rx="9" fill="#f3edff" stroke="#e4d7fb" strokeWidth="2" />
        <circle cx="94" cy="92" r="15" fill="#ffffff" stroke="#7c3aed" strokeWidth="4" />
        <line x1="84" y1="102" x2="104" y2="82" stroke="#7c3aed" strokeWidth="4" strokeLinecap="round" />
      </motion.svg>
    </div>
  );
}

/** Shown when the API classifies a question as unrelated to the business. No data was queried. */
export function OutOfScopeCard({
  domain,
  domainLabel,
  onAsk,
  disabled,
}: {
  domain: string;
  domainLabel: string;
  onAsk: (question: string) => void;
  disabled?: boolean;
}) {
  const reduceMotion = Boolean(useReducedMotion());
  const examples = DOMAIN_EXAMPLE_QUESTIONS[domain] ?? [];
  return (
    <motion.section
      className="ask-scope"
      aria-label="Not a business question"
      initial={reduceMotion ? false : { opacity: 0, scale: 0.96, y: 8 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
    >
      <AssistantIllustration reduceMotion={reduceMotion} />
      <div className="ask-scope__copy">
        <h3>This is not a business-oriented question</h3>
        <p>I&apos;m here to help you with business insights, analytics, and information related to your permitted business domain.</p>
        <p className="ask-scope__domain">Your current domain is <b>{domainLabel}</b>. No data was queried for this question.</p>
        <span className="ask-scope__hint"><Lightbulb size={14} /> Try asking something related to your business domain.</span>
        {examples.length ? (
          <div className="ask-scope__chips" aria-label={`Example ${domainLabel} questions`}>
            {examples.map((example) => (
              <button key={example} type="button" className="ask-scope__chip" disabled={disabled} onClick={() => onAsk(example)}>
                <MessageSquareText size={12} /> {example}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </motion.section>
  );
}
