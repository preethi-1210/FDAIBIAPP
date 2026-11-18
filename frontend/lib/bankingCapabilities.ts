/**
 * Six Banking business capabilities for the Foresight overview.
 *
 * The business descriptions (overview, objectives, activities, responsibilities) are ILLUSTRATIVE:
 * no business owner has approved them, and the page labels them as assumptions. Everything factual
 * shown with them (entities, metrics, rules, glossary terms, availability) is looked up by id in the
 * backend response, which reads banking_context.py and the banking metric registry.
 */
import type { IconType } from "react-icons";
import { HiOutlineUserGroup, HiOutlineWallet, HiOutlineBuildingLibrary, HiOutlineArrowsRightLeft, HiOutlineShieldCheck, HiOutlineBuildingStorefront } from "react-icons/hi2";

export type CapabilityActivity = { text: string; evidence: string | null };

export type BankingCapability = {
  key: string;
  title: string;
  icon: IconType;
  summary: string;
  overview: string;
  objectives: string[];
  responsibilities: string[];
  activities: CapabilityActivity[];
  /** Backend capability ids (banking_context.CAPABILITIES) whose status and gaps apply. */
  contextIds: string[];
  tables: string[];
  metrics: string[];
  terms: string[];
};

export const BANKING_CAPABILITIES: BankingCapability[] = [
  {
    key: "customer",
    title: "Customer Management",
    icon: HiOutlineUserGroup,
    summary: "Who the bank serves: onboarding, identity verification and segmentation.",
    overview: "Manages the customer relationship from onboarding and identity verification through segmentation and servicing.",
    objectives: ["Know every customer and their verification state", "Group customers into segments for servicing", "Track how many customers join over time"],
    responsibilities: ["Customer onboarding and record keeping", "KYC verification status", "Customer segmentation"],
    activities: [
      { text: "Onboard new customers", evidence: "gold_customers.onboarding_date" },
      { text: "Verify customer identity (KYC)", evidence: "gold_customers.kyc_status: VERIFIED, PENDING, REJECTED" },
      { text: "Assign customer segments", evidence: "gold_customers.customer_segment: MASS, AFFLUENT, PREMIUM" },
      { text: "Measure retention and churn", evidence: null },
    ],
    contextIds: ["A"],
    tables: ["gold_customers"],
    metrics: ["customer_count", "new_customers"],
    terms: ["Customer", "New customer", "Customer segment", "KYC status", "Active customer", "Customer growth rate"],
  },
  {
    key: "account",
    title: "Account Management",
    icon: HiOutlineWallet,
    summary: "Current, salary and savings accounts: opening, status and balances.",
    overview: "Covers the accounts customers hold at branches: opening, account type, current status and current balance.",
    objectives: ["Keep an accurate view of accounts by type and status", "Report current balances held", "Track account openings over time"],
    responsibilities: ["Account opening records", "Account status (active, dormant, closed)", "Current balance reporting"],
    activities: [
      { text: "Open accounts", evidence: "gold_accounts.open_date" },
      { text: "Classify account status", evidence: "gold_accounts.status: ACTIVE, DORMANT, CLOSED" },
      { text: "Report current balances", evidence: "gold_accounts.balance (current state only)" },
      { text: "Track account closures over time", evidence: null },
      { text: "Report deposit growth", evidence: null },
    ],
    contextIds: ["B", "D"],
    tables: ["gold_accounts", "agg_accounts_daily", "agg_accounts_monthly"],
    metrics: ["account_count", "new_accounts_opened", "total_account_balance", "average_account_balance"],
    terms: ["Account", "Account type", "Active account", "Dormant account", "Closed account", "Account opening", "Account balance", "Deposit balance"],
  },
  {
    key: "loan",
    title: "Loan Management",
    icon: HiOutlineBuildingLibrary,
    summary: "Loan disbursement, instalments due and repayments collected.",
    overview: "Covers disbursed loans and their instalment schedules: what was lent, what is due and what has been collected.",
    objectives: ["Report loans disbursed by type", "Monitor repayment collection", "Identify missed instalments"],
    responsibilities: ["Loan disbursement records", "Instalment schedule and repayments", "Collection monitoring"],
    activities: [
      { text: "Disburse loans", evidence: "gold_loans.disbursement_date, principal_amount" },
      { text: "Schedule and collect instalments", evidence: "gold_loan_repayments.due_date, emi_due, amount_paid" },
      { text: "Identify missed instalments", evidence: "gold_loan_repayments.amount_paid = 0" },
      { text: "Assess applications and approvals", evidence: null },
      { text: "Track outstanding balances and defaults", evidence: null },
    ],
    contextIds: ["E"],
    tables: ["gold_loans", "gold_loan_repayments", "agg_loans_daily", "agg_loans_monthly", "agg_loan_repayments_daily", "agg_loan_repayments_monthly"],
    metrics: ["loans_disbursed", "average_interest_rate", "repayment_due", "repayment_collected", "collection_rate", "missed_instalments"],
    terms: ["Loan", "Loan disbursement", "Repayment", "Collection rate", "Missed instalment", "Interest rate", "Loan outstanding", "Loan default"],
  },
  {
    key: "payments",
    title: "Payments & Transactions",
    icon: HiOutlineArrowsRightLeft,
    summary: "Debits, credits and transfers across ATM, UPI, NEFT, IMPS, POS and net banking.",
    overview: "Covers money moving through customer accounts: transaction type, channel, status and spend category.",
    objectives: ["Report transaction counts and values", "Monitor transaction failures by channel", "Understand spending by category"],
    responsibilities: ["Transaction recording by channel", "Success and failure monitoring", "Spend categorisation"],
    activities: [
      { text: "Process debits, credits and transfers", evidence: "gold_transactions.txn_type" },
      { text: "Route transactions through channels", evidence: "gold_transactions.channel: ATM, NETBANKING, IMPS, POS, UPI, NEFT" },
      { text: "Monitor failed and pending transactions", evidence: "gold_transactions.status: SUCCESS, FAILED, PENDING" },
      { text: "Categorise spending", evidence: "gold_transactions.merchant_category (a category, not a merchant)" },
      { text: "Settle and reconcile payments", evidence: null },
      { text: "Process RTGS transfers and merchant acquiring", evidence: null },
    ],
    contextIds: ["C", "F", "J"],
    tables: ["gold_transactions", "agg_transactions_daily", "agg_transactions_monthly"],
    metrics: ["transaction_volume", "transaction_amount", "average_transaction_amount", "transaction_failure_rate"],
    terms: ["Transaction", "Transaction volume", "Transaction value", "Average transaction amount", "Transaction type", "Channel", "Successful transaction", "Failed transaction", "Transaction failure rate", "Payment", "Payment settlement", "RTGS", "Merchant", "Merchant category", "Merchant transaction"],
  },
  {
    key: "risk",
    title: "Risk & Compliance",
    icon: HiOutlineShieldCheck,
    summary: "KYC status, data checks and the controls that protect customer data.",
    overview: "Covers identity verification status, data-quality checks and the controls that keep analytics safe. Fraud, AML and credit-risk data are not held in banking.gold.",
    objectives: ["Know which customers are KYC-verified", "Surface data anomalies for review", "Protect sensitive customer data"],
    responsibilities: ["KYC status monitoring", "Data-quality checks", "Sensitive-data protection"],
    activities: [
      { text: "Monitor KYC verification status", evidence: "gold_customers.kyc_status" },
      { text: "Run fixed data checks on transactions", evidence: "Negative amounts and unmatched accounts (registry checks)" },
      { text: "Classify suspicious activity and fraud", evidence: null },
      { text: "Assess credit risk", evidence: null },
      { text: "Report income and profitability", evidence: null },
    ],
    contextIds: ["G", "H", "I"],
    tables: ["gold_customers", "gold_transactions"],
    metrics: [],
    terms: ["KYC status", "Fraud indicator", "Interest income"],
  },
  {
    key: "branch",
    title: "Branch & Banking Products",
    icon: HiOutlineBuildingStorefront,
    summary: "The branch network and card products issued to customers.",
    overview: "Covers the branch network where accounts are held and the credit and debit cards issued to customers.",
    objectives: ["Report accounts and balances by branch region", "Track cards issued and their status", "Report credit limits extended"],
    responsibilities: ["Branch network records", "Card issuance and status", "Credit limit reporting"],
    activities: [
      { text: "Maintain the branch network", evidence: "gold_branches: city, state, region" },
      { text: "Issue credit and debit cards", evidence: "gold_cards.issue_date, card_type" },
      { text: "Manage card status", evidence: "gold_cards.status: ACTIVE, EXPIRED, BLOCKED" },
      { text: "Set credit limits", evidence: "gold_cards.credit_limit (credit cards only)" },
    ],
    contextIds: [],
    tables: ["gold_branches", "gold_cards", "agg_cards_daily", "agg_cards_monthly"],
    metrics: ["card_count", "cards_issued", "total_credit_limit"],
    terms: ["Branch", "Region", "Card", "Credit limit"],
  },
];
