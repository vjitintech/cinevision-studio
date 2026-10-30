import { useEffect, useState } from "react";

// Server edition uses normal paths; the browser edition (GitHub Pages, served from a sub-folder with no
// server-side routing) keeps the route in the URL hash so deep links and reloads always work.
const HASH = __EDITION__ === "browser";
const current = () => (HASH ? location.hash.replace(/^#/, "") || "/" : location.pathname);

export function navigate(path: string, replace = false) {
  const url = HASH ? "#" + path : path;
  if (replace) history.replaceState(null, "", url); else history.pushState(null, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}

/** Current route plus a counter that changes on every navigation (used to reset pages re-opened from the menu). */
export function usePath(): string {
  return useRoute()[0];
}

export function useRoute(): [string, number] {
  const [state, setState] = useState<[string, number]>([current(), 0]);
  useEffect(() => {
    const on = () => setState(([, n]) => [current(), n + 1]);
    window.addEventListener("popstate", on);
    window.addEventListener("hashchange", on);
    return () => { window.removeEventListener("popstate", on); window.removeEventListener("hashchange", on); };
  }, []);
  return state;
}

/** Click handler for in-app links (keeps modifier-click behaviour). */
export function linkTo(path: string) {
  return {
    href: HASH ? "#" + path : path,
    onClick: (e: React.MouseEvent) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      navigate(path);
    },
  };
}
