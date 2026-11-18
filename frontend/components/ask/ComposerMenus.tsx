"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Building2, ChartCandlestick, Check, ChevronDown, HeartPulse, Landmark, Lightbulb, MessageSquareText, ShieldCheck, ShoppingBag, TriangleAlert, type LucideIcon } from "lucide-react";
import { ASK_SUGGESTIONS } from "@/components/ask/AskWelcome";

const DOMAIN_ICONS: Record<string, LucideIcon> = {
  banking: Landmark,
  finance: ChartCandlestick,
  insurance: ShieldCheck,
  retail: ShoppingBag,
  healthcare: HeartPulse,
};

/** Closes on an outside click or Escape. */
function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return { open, setOpen, ref };
}

function MenuSection({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <div className="ask-menu__section">
      <p className="ask-menu__title">{title}</p>
      {children}
    </div>
  );
}

/** The "+" menu: the domain's suggested questions, and the sensitive-data demo prompts. */
export function ComposerPlusMenu({
  domain,
  domainLabel,
  sensitivePrompts,
  onPrefill,
  onSensitivePrompt,
  disabled,
}: {
  domain: string;
  domainLabel: string;
  sensitivePrompts: string[];
  onPrefill: (question: string) => void;
  onSensitivePrompt: (prompt: string) => void;
  disabled?: boolean;
}) {
  const { open, setOpen, ref } = usePopover();
  const suggestions = ASK_SUGGESTIONS[domain];
  const questions = suggestions ? [...suggestions.topics, ...suggestions.questions] : [];
  return (
    <div className="ask-menu" ref={ref}>
      <button
        type="button"
        className="ask-composer__icon-button"
        aria-label="Suggested questions and demo prompts"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        <Lightbulb size={18} />
      </button>
      {open ? (
        <div className="ask-menu__panel ask-menu__panel--wide" role="menu">
          {questions.length ? (
            <MenuSection title={`Suggested ${domainLabel} questions`}>
              {questions.map((item) => (
                <button key={item.question} type="button" role="menuitem" className="ask-menu__item" onClick={() => { onPrefill(item.question); setOpen(false); }}>
                  <MessageSquareText size={14} /> {item.question}
                </button>
              ))}
            </MenuSection>
          ) : null}
          {sensitivePrompts.length ? (
            <MenuSection title={<><TriangleAlert size={12} /> Sensitive-data demo</>}>
              {sensitivePrompts.map((prompt) => (
                <button key={prompt} type="button" role="menuitem" className="ask-menu__item ask-menu__item--warning" onClick={() => { onSensitivePrompt(prompt); setOpen(false); }}>
                  {prompt}
                </button>
              ))}
            </MenuSection>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The active domain. A user permitted several domains can switch here; the API re-issues the
 * session and refuses domains the user is not authorized for. Otherwise it is a label.
 */
export function ComposerDomainPicker({
  domain,
  domains,
  labels,
  onSwitch,
  disabled,
}: {
  domain: string;
  domains: string[];
  labels: Record<string, string>;
  onSwitch: (next: string) => void;
  disabled?: boolean;
}) {
  const { open, setOpen, ref } = usePopover();
  const Icon = DOMAIN_ICONS[domain] ?? Building2;
  const label = labels[domain] ?? domain;
  if (domains.length <= 1) {
    return <span className="ask-composer__domain"><Icon size={17} /> {label}</span>;
  }
  return (
    <div className="ask-menu" ref={ref}>
      <button
        type="button"
        className="ask-composer__domain ask-composer__domain--button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Business domain: ${label}. Change domain`}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon size={17} /> {label} <ChevronDown size={15} />
      </button>
      {open ? (
        <div className="ask-menu__panel" role="menu">
          <MenuSection title="Business domain">
            {domains.map((item) => {
              const ItemIcon = DOMAIN_ICONS[item] ?? Building2;
              return (
                <button
                  key={item}
                  type="button"
                  role="menuitemradio"
                  aria-checked={item === domain}
                  className="ask-menu__item"
                  onClick={() => { setOpen(false); if (item !== domain) onSwitch(item); }}
                >
                  <ItemIcon size={14} /> {labels[item] ?? item}
                  {item === domain ? <Check size={14} className="ask-menu__check" /> : null}
                </button>
              );
            })}
          </MenuSection>
        </div>
      ) : null}
    </div>
  );
}
