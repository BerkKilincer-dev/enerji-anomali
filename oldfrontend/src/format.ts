import type { Building } from "./api";
import type { T } from "./i18n";

const tl = new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY", maximumFractionDigits: 0 });
const kwh = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });

export const formatTl = (value: number | null) => (value == null ? "-" : tl.format(value));
export const formatKwh = (value: number | null) => (value == null ? "-" : `${kwh.format(value)} kWh`);
export const buildingLabel = (b: Pick<Building, "name" | "external_id">) => b.name || b.external_id;

export const formatTs = (ts: string | null) => (ts ? ts.replace("T", " ").slice(0, 16) : "-");
// Timestamps carry no offset; treating them as UTC keeps the arithmetic free of DST shifts.
export const hourAfter = (ts: string) => new Date(Date.parse(`${ts}Z`) + 3_600_000).toISOString().slice(0, 19);

const RULES = ["night_base_load", "weekend_usage", "spike", "dropout", "flatline"] as const;

export const anomalyLabel = (t: T, source: string | null, ruleType: string | null) => {
  const rule = RULES.find((r) => r === ruleType);
  if (rule) return t(`rule.${rule}`);
  return t(source === "rule" ? "rule.rule" : "rule.model");
};
