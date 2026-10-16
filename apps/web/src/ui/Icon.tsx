import type { ReactNode } from "react";

const paths = {
  link: <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />,
  upload: <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14v5h14v-5" />,
  spark: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />,
  search: <path d="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4" />,
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" />,
  check: <path d="m5 12 4 4L19 6" />,
  chevronRight: <path d="m9 18 6-6-6-6" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  arrowLeft: <path d="M19 12H5m0 0 6-6m-6 6 6 6" />,
  arrowRight: <path d="M5 12h14m0 0-6-6m6 6-6 6" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  play: <path d="m8 5 11 7-11 7V5Z" />,
  download: <path d="M12 4v12m0 0-4.5-4.5M12 16l4.5-4.5M5 20h14" />,
  refresh: <path d="M20 11a8 8 0 0 0-14.5-3.5M4 4v4h4M4 13a8 8 0 0 0 14.5 3.5M20 20v-4h-4" />,
  archive: <path d="M3 5h18v4H3zM5 9v10h14V9M10 13h4" />,
  mic: <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3" />,
  alert: <path d="M12 4 2.5 20h19L12 4zM12 10v4m0 3h.01" />,
  film: <path d="M4 4h16v16H4zM8 4v16M16 4v16M4 9h4M4 15h4M16 9h4M16 15h4" />,
  clock: <path d="M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" />,
  sliders: <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" />,
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof paths;

export function Icon({ name, small = false }: { name: IconName; small?: boolean }) {
  return <svg className={small ? "ic sm" : "ic"} viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}
