/**
 * @file Plain-language labels for subscriptions, shared by the list, detail, and wizard.
 */
import type { BadgeTone } from "@/components/ui/Badge";
import type {
  PrivilegePreset,
  SecurableKind,
  SubscriptionPhase,
  SubscriptionResource,
} from "@/api/types";

/** Badge label and tone per phase. */
export const PHASE_LABELS: Record<SubscriptionPhase, { label: string; tone: BadgeTone }> = {
  PENDING_APPROVAL: { label: "Pending approval", tone: "warning" },
  SCHEDULED: { label: "Scheduled", tone: "info" },
  ACTIVE: { label: "Active", tone: "success" },
  EXPIRING: { label: "Expiring soon", tone: "warning" },
  EXPIRED: { label: "Expired", tone: "neutral" },
  DENIED: { label: "Denied", tone: "danger" },
  REVOKED: { label: "Revoked", tone: "neutral" },
};

/** Singular and plural nouns per object type. */
export const KIND_LABELS: Record<SecurableKind, { one: string; many: string }> = {
  CATALOG: { one: "catalog", many: "catalogs" },
  SCHEMA: { one: "schema", many: "schemas" },
  TABLE: { one: "table", many: "tables" },
  VOLUME: { one: "volume", many: "volumes" },
  FUNCTION: { one: "function", many: "functions" },
  MODEL: { one: "model", many: "models" },
  EXTERNAL_LOCATION: { one: "external location", many: "external locations" },
  WORKSPACE_FOLDER: { one: "workspace folder", many: "workspace folders" },
  DASHBOARD: { one: "dashboard", many: "dashboards" },
  GENIE_SPACE: { one: "Genie space", many: "Genie spaces" },
  APP: { one: "app", many: "apps" },
  SQL_WAREHOUSE: { one: "SQL warehouse", many: "SQL warehouses" },
};

/** Kinds inside a catalog: tagged, reached through USE CATALOG / USE SCHEMA, open to approvers. */
export const DATA_KINDS: SecurableKind[] = ["CATALOG", "SCHEMA", "TABLE", "VOLUME", "FUNCTION", "MODEL"];

/** Kinds picked from a flat list by ID or name. */
export const LISTED_KINDS: SecurableKind[] = ["EXTERNAL_LOCATION", "DASHBOARD", "GENIE_SPACE", "APP", "SQL_WAREHOUSE"];

/** Preset names and what they mean. */
export const PRESET_LABELS: Record<PrivilegePreset, { label: string; hint: string }> = {
  READ: { label: "Read", hint: "Query tables, read volumes, run functions and models" },
  BUILD: { label: "Build", hint: "Data Editor: read, modify, apply tags, and create schemas, tables, functions, models and volumes" },
  VOLUME_READ_WRITE: { label: "Volume files", hint: "Read and write files in volumes and external locations" },
  ADMIN: { label: "Admin", hint: "ALL PRIVILEGES and MANAGE (Can manage on workspace objects)" },
  CUSTOM: { label: "Custom", hint: "Pick exactly which privileges to grant" },
};

/** Kinds offered by the object picker, in display order. */
export const PICKABLE_KINDS: SecurableKind[] = [
  "CATALOG",
  "SCHEMA",
  "TABLE",
  "VOLUME",
  "FUNCTION",
  "MODEL",
  "EXTERNAL_LOCATION",
  "WORKSPACE_FOLDER",
  "DASHBOARD",
  "GENIE_SPACE",
  "APP",
  "SQL_WAREHOUSE",
];

/** Privilege groups for the custom picker, in the order Databricks shows them. */
export const PRIVILEGE_GROUPS: { title: string; match: (privilege: string) => boolean }[] = [
  { title: "Prerequisite", match: (p) => p === "USE CATALOG" || p === "USE SCHEMA" },
  { title: "Metadata", match: (p) => ["APPLY TAG", "BROWSE", "READ METADATA"].includes(p) },
  {
    title: "Read",
    match: (p) => p === "SELECT" || p === "EXECUTE" || p.startsWith("READ ") || p === "REFERENCE SECRET" || p === "USE CONNECTION",
  },
  { title: "Edit", match: (p) => p === "MODIFY" || p === "REFRESH" || p.startsWith("WRITE ") },
  { title: "Create", match: (p) => p.startsWith("CREATE ") },
  { title: "Workspace permission", match: (p) => p.startsWith("CAN_") },
  { title: "Other", match: () => true },
];

/** Kinds a tag match can grant on. */
export const TAG_MATCH_KINDS: SecurableKind[] = ["CATALOG", "SCHEMA", "TABLE", "VOLUME"];

/**
 * One-line description of a resource, e.g. "Tables where access_path is under people.ia".
 *
 * @param resource - Resource to describe.
 */
export function describeResource(resource: SubscriptionResource): string {
  if (resource.selector === "OBJECT") {
    const kind = resource.securableKind ? KIND_LABELS[resource.securableKind].one : "object";
    return `${capitalize(kind)} ${resource.label || resource.fullName || ""}`;
  }
  const levels = resource.levels.length ? resource.levels : (["TABLE"] as SecurableKind[]);
  const what = joinWords(levels.map((k) => KIND_LABELS[k].many));
  const rules = resource.conditions.map(
    (c) => `${c.tagKey} ${c.match === "PREFIX" ? "is under" : "is"} ${joinWords(c.values, "or")}`,
  );
  return `${capitalize(what)} where ${rules.join(" and ")}`;
}

/**
 * Privileges a resource grants, as words.
 *
 * @param resource - Resource to describe.
 */
export function describePrivileges(resource: SubscriptionResource): string {
  if (resource.preset === "CUSTOM") return resource.privileges.join(", ");
  return PRESET_LABELS[resource.preset].label;
}

/** Locale date and time for an ISO timestamp. */
export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** "in 12 days", "3 hours ago", … relative to now. */
export function relativeTime(iso: string): string {
  const diff = new Date(iso).getTime() - Date.now();
  const abs = Math.abs(diff);
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [86_400_000, "day"],
    [3_600_000, "hour"],
    [60_000, "minute"],
  ];
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [size, unit] of units) {
    if (abs >= size || unit === "minute") return format.format(Math.round(diff / size), unit);
  }
  return "";
}

/** Value for a `datetime-local` input from an ISO timestamp (local time). */
export function toLocalInput(iso: string | Date): string {
  const date = typeof iso === "string" ? new Date(iso) : iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

/** ISO timestamp from a `datetime-local` input value. */
export function fromLocalInput(value: string): string {
  return new Date(value).toISOString();
}

/**
 * Slug for a subscription name: lowercase letters and digits joined by single underscores.
 *
 * @param text - Free text (usually the display name).
 */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
}

/** Whether a name is a valid subscription slug. */
export function isValidName(name: string): boolean {
  return /^[a-z0-9]+(?:_[a-z0-9]+)*$/.test(name) && name.length <= 64;
}

/** First letter upper-cased. */
export function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function joinWords(items: string[], last = "and"): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} ${last} ${items[items.length - 1]}`;
}
