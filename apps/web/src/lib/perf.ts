/** Lightweight in-browser timing records (graph load, layout, selection), exposed on window.__cfsPerf. */
type Rec = { name: string; ms: number; at: number; meta?: Record<string, unknown> };
const records: Rec[] = [];

export const perf = {
  record(name: string, ms: number, meta?: Record<string, unknown>) {
    records.push({ name, ms, at: Date.now(), meta });
    if (records.length > 500) records.shift();
  },
  async time<T>(name: string, fn: () => Promise<T>, meta?: Record<string, unknown>): Promise<T> {
    const t0 = performance.now();
    try {
      return await fn();
    } finally {
      perf.record(name, performance.now() - t0, meta);
    }
  },
  all: () => records.slice(),
};
(globalThis as unknown as { __cfsPerf: typeof perf }).__cfsPerf = perf;
