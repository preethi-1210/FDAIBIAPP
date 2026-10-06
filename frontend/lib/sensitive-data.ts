export type SensitivePromptKind =
  | "payment-security-code"
  | "banking-sensitive"
  | "insurance-sensitive"
  | "finance-sensitive"
  | "retail-sensitive"
  | "sensitive-data";

const sensitivePatterns: Array<[SensitivePromptKind, RegExp]> = [
  ["payment-security-code", /\b(?:cvv|cvc|cvn|card\s+verification(?:\s+(?:value|code))?|security\s+code)\b/i],
  [
    "banking-sensitive",
    /\b(?:account\s*(?:numbers?|no\.?|ids?|#|details?)|bank\s+accounts?|credit\s+cards?|card\s*(?:numbers?|details?)|payment\s+cards?|pan\s*(?:numbers?)?|aadhaar|aadhar|ifsc|kyc|loan\s+(?:records?|details?|balances?)|transaction\s+history|counterpart(?:y|ies)|fraud\s+(?:case|investigation|notes?)|customer\s+identity)\b/i,
  ],
  [
    "insurance-sensitive",
    /\b(?:policy\s*holders?|policy\s*(?:ids?|numbers?|details?)|claimants?|claim\s+(?:records?|details?|notes?)|claims?\b|medical\s+records?|diagnos(?:is|es)|health\s+(?:reports?|records?|data)|lab\s+results?|nominees?|beneficiaries|passport\s+(?:scans?|numbers?)|identity\s+documents?)\b/i,
  ],
  [
    "finance-sensitive",
    /\b(?:payroll|salar(?:y|ies)|compensation|bonus(?:es)?|tax\s+(?:returns?|information|details?|ids?)|financial\s+statements?|executive\s+(?:pay|compensation)|employee\s+bank(?:ing)?\s+details?|bank\s+details?|budget\s+(?:reports?|details?)|forecast\s+assumptions?)\b/i,
  ],
  [
    "retail-sensitive",
    /\b(?:customer\s+profiles?|purchase\s+history|order\s+history|loyalty\s+(?:members?|program|points?|history)|saved\s+payment|payment\s+information|home\s+addresses?|mailing\s+addresses?|phone\s+numbers?|email\s+addresses?|customer\s+(?:contact|personal|private)\s+(?:details?|information)|individual(?:-level)?\s+customer\s+analytics)\b/i,
  ],
  [
    "sensitive-data",
    /\b(?:personal\s+information|sensitive\s+(?:information|data|fields?)|pii|spi|raw\s+database\s+records?|raw\s+records?|hidden\s+columns?|unmask|unredact|export\s+all\s+(?:customer|employee|user)?\s*records?|bypass\s+(?:rbac|access|security|masking)|ignore\s+(?:all\s+)?(?:security|safety|access)\s+(?:polic(?:y|ies)|controls?))\b/i,
  ],
];

const notices: Record<SensitivePromptKind, string> = {
  "payment-security-code": "CVV/CVC security codes are never shown in this chat.",
  "banking-sensitive": "Sensitive banking account and identity information isn't shown in this chat.",
  "insurance-sensitive": "Sensitive insurance policy, claim, and health information isn't shown in this chat.",
  "finance-sensitive": "Sensitive finance payroll, tax, and compensation information isn't shown in this chat.",
  "retail-sensitive": "Sensitive retail customer, contact, and payment information isn't shown in this chat.",
  "sensitive-data": "Sensitive information isn't shown in this chat.",
};

const domainKinds: Record<string, SensitivePromptKind> = {
  banking: "banking-sensitive",
  insurance: "insurance-sensitive",
  finance: "finance-sensitive",
  retail: "retail-sensitive",
};

export const SENSITIVE_DEMO_PROMPTS: Record<string, string[]> = {
  banking: [
    "Show customer account numbers and PAN details",
    "Retrieve Aadhaar and KYC document information",
    "Display complete credit card details including CVV",
  ],
  insurance: [
    "Show policy holder names and policy numbers",
    "List claimants with their medical records and diagnoses",
    "Retrieve nominee details and customer identity documents",
  ],
  finance: [
    "Show employee payroll, salary, and bonus records",
    "Retrieve employee tax information and tax IDs",
    "List executive compensation and employee bank details",
  ],
  retail: [
    "Show customer profiles with phone numbers and addresses",
    "List an individual customer's purchase and loyalty history",
    "Display saved customer payment information and card numbers",
  ],
};

export function detectSensitivePrompt(text: string, domain?: string): SensitivePromptKind | null {
  const matchedKinds = sensitivePatterns
    .filter(([, pattern]) => pattern.test(text))
    .map(([kind]) => kind);
  if (!matchedKinds.length) return null;
  if (matchedKinds.includes("payment-security-code")) return "payment-security-code";
  const domainKind = domainKinds[domain ?? ""];
  return domainKind && matchedKinds.includes(domainKind)
    ? domainKind
    : matchedKinds[0];
}

export function sensitivePromptNotice(kind: SensitivePromptKind): string {
  return notices[kind];
}
