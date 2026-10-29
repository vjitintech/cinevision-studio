import { useEffect, useState } from "react";

export function navigate(path: string, replace = false) {
  if (replace) history.replaceState(null, "", path); else history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}

export function usePath() {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const on = () => setPath(location.pathname);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  return path;
}

/** Click handler for in-app links (keeps modifier-click behaviour). */
export function linkTo(path: string) {
  return {
    href: path,
    onClick: (e: React.MouseEvent) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      navigate(path);
    },
  };
}
