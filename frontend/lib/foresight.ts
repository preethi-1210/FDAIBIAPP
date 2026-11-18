// Response of GET /context/foresight (backend/app/services/foresight.py).

export type ForesightColumn = { name: string; type: string | null; key: string | null; meaning: string | null; technical: boolean };

export type ForesightEntity = {
  table: string;
  name: string;
  kind: "entity" | "aggregate";
  source: string;
  description: string | null;
  notes: string[];
  rows: number | null;
  coverage: string | null;
  columns: ForesightColumn[];
  hidden_sensitive_columns: number;
  metadata_available: boolean;
  metrics: string[];
  relationships: string[];
  validation: string;
};

export type ForesightRelationship = {
  child: string;
  child_column: string;
  parent: string;
  parent_column: string;
  child_label: string;
  parent_label: string;
  join: string;
  cardinality: string;
  unmatched_children: number;
  status: "configured" | "verified";
  evidence: string;
  explanation: string;
  verified_on: string;
};

export type ForesightMetric = {
  id: string;
  name: string;
  description: string;
  formula: string;
  aggregation: string;
  source_catalog?: string;
  source_schema?: string;
  source_tables: string[];
  source_columns: string[];
  date_semantics: string;
  supports_time: boolean;
  units: string[];
  dimensions: { id: string; label: string }[];
  status: "registry" | "secondary";
  used_by: string[];
  business_owner: string | null;
  approval: string;
  validation_note: string;
  pending_rules: string[];
  data_quality: string[];
  version: string | null;
};

export type ForesightRule = {
  id: string;
  name: string;
  kind: "enforced" | "shadow" | "definition" | "pending" | "data note" | "check";
  status: string;
  description: string;
  implementation?: string;
  while_pending?: string | null;
  applies_to?: string[];
  expression?: string;
  columns?: string[];
  violations?: number;
  measured_on?: string;
  domain: string;
};

export type ForesightTerm = { term: string; definition: string; status: string; maps_to: string | null; source: string | null };

export type ForesightCapability = {
  id: string;
  name: string;
  status: string;
  metrics: string[];
  pending_rules: string[];
  unavailable: { topic: string; reason: string }[];
  note: string;
};

export type ForesightContext = {
  domain: string;
  domain_label: string;
  available: boolean;
  registry_version: string;
  overview: {
    analytical_source: string;
    registry_source: string;
    application_source: string;
    synchronized_at: string | null;
    metadata_available: boolean;
    entities: number;
    aggregates: number;
    technical_tables: number;
    relationships: Record<string, number>;
    metrics: Record<string, number>;
    rules: Record<string, number>;
    documented_entities: number;
    verified_on: string;
  };
  entities: ForesightEntity[];
  technical_tables: { table: string; kind: string }[];
  relationships: ForesightRelationship[];
  metrics: ForesightMetric[];
  rules: ForesightRule[];
  glossary: ForesightTerm[] | null;
  capabilities: ForesightCapability[] | null;
};

export function total(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, value) => sum + value, 0);
}
