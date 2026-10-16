import { useCallback, useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from "react";

export type Route =
  | { name: "episodes" }
  | { name: "episode"; id: string }
  | { name: "editor"; id: string; run: string; clips: string[] }
  | { name: "cuts" }
  | { name: "unknown" };

const NAVIGATE_EVENT = "cortex:navigate";
/** Clip identity across runs is `<runId>~<clipKey>`; the legacy `run` + `clips` URL form is qualified on parse. */
export const KEY_SEPARATOR = "~";

export function parseRoute(pathname: string, search = ""): Route {
  const parts = pathname.split("/").filter(Boolean).map(part => {
    try { return decodeURIComponent(part); } catch { return part; }
  });
  if (!parts.length) return { name: "episodes" };
  if (parts[0] === "cortes" && parts.length === 1) return { name: "cuts" };
  if (parts[0] === "episodios" && parts.length === 2) return { name: "episode", id: parts[1] };
  if (parts[0] === "episodios" && parts.length === 3 && parts[2] === "editor") {
    const query = new URLSearchParams(search);
    const run = query.get("run") ?? "";
    const clips = (query.get("clips") ?? "").split(",").filter(Boolean).map(key => key.includes(KEY_SEPARATOR) || !run ? key : `${run}${KEY_SEPARATOR}${key}`);
    return { name: "editor", id: parts[1], run, clips };
  }
  return { name: "unknown" };
}

export const episodePath = (id: string) => `/episodios/${encodeURIComponent(id)}`;

export function editorPath(id: string, qualifiedKeys: string[]) {
  const query = new URLSearchParams({ clips: qualifiedKeys.join(",") });
  return `${episodePath(id)}/editor?${query}`;
}

export function navigate(to: string, options: { replace?: boolean } = {}) {
  if (options.replace) window.history.replaceState(null, "", to);
  else window.history.pushState(null, "", to);
  window.dispatchEvent(new Event(NAVIGATE_EVENT));
}

function subscribe(listener: () => void) {
  window.addEventListener("popstate", listener);
  window.addEventListener(NAVIGATE_EVENT, listener);
  return () => { window.removeEventListener("popstate", listener); window.removeEventListener(NAVIGATE_EVENT, listener); };
}

const currentLocation = () => window.location.pathname + window.location.search;

export function useLocation() {
  return useSyncExternalStore(subscribe, currentLocation, () => "/");
}

export function useRoute(): Route {
  const location = useLocation();
  const [pathname, search = ""] = location.split("?");
  return parseRoute(pathname, search ? `?${search}` : "");
}

/** Query string as state: `update` merges keys (null or empty removes one) and pushes or replaces the URL. */
export function useSearchParams() {
  const location = useLocation();
  const search = location.split("?")[1] ?? "";
  const update = useCallback((changes: Record<string, string | null>, options: { replace?: boolean } = {}) => {
    const params = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(changes)) {
      if (value) params.set(key, value); else params.delete(key);
    }
    const query = params.toString();
    navigate(window.location.pathname + (query ? `?${query}` : ""), options);
  }, []);
  return [new URLSearchParams(search), update] as const;
}

export function Link({ to, replace, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string; replace?: boolean }) {
  function handle(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || rest.target === "_blank") return;
    event.preventDefault();
    navigate(to, { replace });
  }
  return <a href={to} onClick={handle} {...rest} />;
}
