import { useCallback, useEffect, useRef, useState } from 'react';
import { parseSnapshot } from './model';
import type { Snapshot } from './model';

export type SnapshotLoader = (signal: AbortSignal) => Promise<unknown>;
export const loadSnapshot: SnapshotLoader = async (signal) => {
  const response = await fetch('/api/snapshot', {
    signal,
    cache: 'no-store',
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('Snapshot unavailable');
  return response.json();
};
export function useSnapshot(loader: SnapshotLoader = loadSnapshot) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [observed, setObserved] = useState<Date | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const refresh = useCallback(async () => {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    setBusy(true);
    try {
      const result = parseSnapshot(await loader(controller.signal));
      if (!alive.current || controller.signal.aborted) return;
      setSnapshot((previous) =>
        previous && JSON.stringify(previous) === JSON.stringify(result) ? previous : result,
      );
      setObserved(new Date());
      setError(false);
    } catch {
      // This is the API boundary: failed reads retain the last observed data.
      if (alive.current) setError(true);
    } finally {
      clearTimeout(timeout);
      pending.current = null;
      if (alive.current) setBusy(false);
    }
  }, [loader]);
  useEffect(() => {
    alive.current = true;
    void refresh();
    const interval = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 5000);
    const visible = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener('visibilitychange', visible);
    return () => {
      alive.current = false;
      pending.current?.abort();
      clearInterval(interval);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [refresh]);
  return { snapshot, observed, error, busy, refresh };
}
