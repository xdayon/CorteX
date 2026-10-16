import type { ReactNode } from "react";

export type ChipTone = "neutral" | "ac" | "ok" | "warn" | "bad";

export function Chip({ tone = "neutral", children, className }: { tone?: ChipTone; children: ReactNode; className?: string }) {
  return <span className={["chip", tone === "neutral" ? "" : tone, className].filter(Boolean).join(" ")}>{children}</span>;
}
