import { useEffect, useState } from "react";

/**
 * Read the current ``window.location.pathname`` and stay subscribed
 * to ``popstate`` so the renderer re-renders when the user uses the
 * browser back/forward buttons (or anything else that mutates
 * history without a full reload).
 *
 * Intentionally minimal — we have exactly two routes during the
 * chat-first transition (``/`` and ``/legacy``) and zero after. A
 * 20-line hook is the right tool for two routes; a router library
 * would be premature abstraction (see ADR-0026 alternatives). When
 * the ``/legacy`` route retires this hook can retire too.
 */
export function usePathname(): string {
  const [pathname, setPathname] = useState<string>(() =>
    typeof window === "undefined" ? "/" : window.location.pathname,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onPop = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  return pathname;
}

/**
 * Push a new path onto history and notify subscribers. The native
 * ``pushState`` API does **not** fire a ``popstate`` event by
 * design; we dispatch one explicitly so ``usePathname`` re-reads.
 */
export function navigateTo(pathname: string): void {
  if (typeof window === "undefined") return;
  if (window.location.pathname === pathname) return;
  window.history.pushState({}, "", pathname);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
