import type { Publication, PublicationInput, PublicationPlatform } from "./api";

export type PublicationFormState = {
  published: boolean;
  platforms: PublicationPlatform[];
  published_on: string;
  url: string;
  views: string; likes: string; comments: string; shares: string; saves: string;
  retention_3s_pct: string; avg_watch_seconds: string;
  rating: "" | "-1" | "0" | "1";
  notes: string;
};

export const emptyPublicationForm: PublicationFormState = {
  published: false, platforms: [], published_on: "", url: "",
  views: "", likes: "", comments: "", shares: "", saves: "",
  retention_3s_pct: "", avg_watch_seconds: "", rating: "", notes: "",
};

const text = (value: number | null | undefined) => (value == null ? "" : String(value).replace(".", ","));

export function formFromPublication(item: Publication | null | undefined): PublicationFormState {
  if (!item) return emptyPublicationForm;
  return {
    published: item.published, platforms: [...item.platforms],
    published_on: item.published_on ?? "", url: item.url ?? "",
    views: text(item.views), likes: text(item.likes), comments: text(item.comments),
    shares: text(item.shares), saves: text(item.saves),
    retention_3s_pct: text(item.retention_3s_pct), avg_watch_seconds: text(item.avg_watch_seconds),
    rating: item.rating == null ? "" : (String(item.rating) as PublicationFormState["rating"]),
    notes: item.notes,
  };
}

const COUNT_FIELDS = [
  ["views", "Views"], ["likes", "Curtidas"], ["comments", "Comentários"],
  ["shares", "Compartilhamentos"], ["saves", "Salvamentos"],
] as const;

function parseCount(raw: string): number | null | undefined {
  const value = raw.trim();
  if (!value) return null;
  return /^\d{1,12}$/.test(value) ? Number(value) : undefined;
}

function parseDecimal(raw: string, max?: number): number | null | undefined {
  const value = raw.trim().replace(",", ".");
  if (!value) return null;
  if (!/^\d+(\.\d+)?$/.test(value)) return undefined;
  const number = Number(value);
  return Number.isFinite(number) && (max === undefined || number <= max) ? number : undefined;
}

export type PublicationParse =
  | { ok: true; payload: PublicationInput }
  | { ok: false; errors: string[] };

export function buildPublicationPayload(
  form: PublicationFormState, hookText?: string,
): PublicationParse {
  const errors: string[] = [];
  const counts: Record<string, number | null> = {};
  for (const [key, label] of COUNT_FIELDS) {
    const parsed = parseCount(form[key]);
    if (parsed === undefined) errors.push(`${label}: use um número inteiro maior ou igual a zero.`);
    else counts[key] = parsed;
  }
  const retention = parseDecimal(form.retention_3s_pct, 100);
  if (retention === undefined) errors.push("Retenção 3 s: use um número de 0 a 100.");
  const watch = parseDecimal(form.avg_watch_seconds);
  if (watch === undefined) errors.push("Tempo médio: use um número de segundos maior ou igual a zero.");
  const url = form.url.trim();
  if (url && !/^https?:\/\/\S+$/.test(url)) errors.push("Link: use um endereço começando com http:// ou https://.");
  if (url.length > 500) errors.push("Link: no máximo 500 caracteres.");
  if (form.notes.length > 300) errors.push("Notas: no máximo 300 caracteres.");
  if (errors.length) return { ok: false, errors };
  return {
    ok: true,
    payload: {
      published: form.published, platforms: form.platforms,
      published_on: form.published_on || null, url: url || null,
      views: counts.views, likes: counts.likes, comments: counts.comments,
      shares: counts.shares, saves: counts.saves,
      retention_3s_pct: retention ?? null, avg_watch_seconds: watch ?? null,
      rating: form.rating === "" ? null : (Number(form.rating) as -1 | 0 | 1),
      notes: form.notes,
      ...(hookText ? { hook_text: hookText.slice(0, 200) } : {}),
    },
  };
}

export function formatViews(views: number): string {
  const format = (value: number, suffix: string) => `${value.toFixed(1).replace(".", ",")} ${suffix}`;
  if (views >= 1_000_000) return format(views / 1_000_000, "mi");
  if (views >= 1_000) return format(views / 1_000, "mil");
  return String(views);
}

export const RATING_LABEL = { "-1": "ruim", "0": "ok", "1": "ótimo" } as const;
