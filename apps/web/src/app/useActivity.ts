import { useEffect, useState } from "react";
import { api, type ApiJob } from "../api";
import { newestActive } from "./humanize";

const BUSY_MS = 3000;
const IDLE_MS = 15000;

/** Polls the job list: quickly while something runs, slowly when idle. */
export function useActivity() {
  const [jobs, setJobs] = useState<ApiJob[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    let timer = 0;
    async function tick() {
      let delay = IDLE_MS;
      try {
        const list = await api.jobs(20, controller.signal);
        if (controller.signal.aborted) return;
        const next = Array.isArray(list) ? list : [];
        setJobs(next);
        if (newestActive(next).active.length) delay = BUSY_MS;
      } catch {
        if (controller.signal.aborted) return;
      }
      timer = window.setTimeout(() => void tick(), delay);
    }
    void tick();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, []);
  return newestActive(jobs);
}
