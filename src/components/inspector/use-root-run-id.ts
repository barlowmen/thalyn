import { useEffect, useState } from "react";

import { subscribeRunStatus } from "@/lib/runs";

/**
 * Track the currently-active root run id by latching on the most
 * recent `run:status` event whose ``parentRunId`` is null. Sub-agent
 * status traffic is ignored so callers stay focused on the run the
 * user is interacting with.
 */
export function useRootRunId(): string | null {
  const [runId, setRunId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const sub = subscribeRunStatus((event) => {
      if (!active) return;
      if (event.parentRunId !== null) return;
      setRunId(event.runId);
    });
    return () => {
      active = false;
      void sub.then((fn) => fn());
    };
  }, []);

  return runId;
}
