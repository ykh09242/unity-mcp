import { useCallback, useEffect, useRef, useState } from 'react';
import { parseSnapshot, semanticSnapshotEqual } from './model';
import type { Snapshot } from './model';

export type SnapshotLoader = (signal: AbortSignal, etag?: string) => Promise<unknown>;
export class SnapshotResponse {
  constructor(
    readonly status: 200 | 304,
    readonly etag: string | null,
    readonly data?: unknown,
  ) {}
}
export class SnapshotReadError extends Error {
  readonly name = 'SnapshotReadError';
  constructor(
    readonly reason: 'http' | 'missing-model' | 'aborted',
    readonly status?: number,
  ) {
    super(`Snapshot unavailable: ${reason}`);
  }
}
export const loadSnapshot: SnapshotLoader = async (signal, etag) => {
  const response = await fetch('/api/snapshot', {
    signal,
    cache: 'no-store',
    credentials: 'same-origin',
    headers: etag ? { 'If-None-Match': etag } : undefined,
  });
  if (response.status === 304) return new SnapshotResponse(304, response.headers.get('ETag'));
  if (!response.ok) throw new SnapshotReadError('http', response.status);
  return new SnapshotResponse(200, response.headers.get('ETag'), await response.json());
};
export function useSnapshot(loader: SnapshotLoader = loadSnapshot) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [observed, setObserved] = useState<Date | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [announce, setAnnounce] = useState(true);
  const pending = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const queuedAfterWrite = useRef(false);
  const current = useRef<{
    readonly snapshot: Snapshot;
    readonly etag: string | null;
  } | null>(null);
  const refresh = useCallback(
    async function readSnapshot(manual = false, afterWrite = false): Promise<void> {
      if (pending.current) {
        if (afterWrite) queuedAfterWrite.current = true;
        return;
      }
      const controller = new AbortController();
      pending.current = controller;
      let aborted: (() => void) | undefined;
      const cancellation = new Promise<never>((_, reject) => {
        aborted = () => reject(new SnapshotReadError('aborted'));
        controller.signal.addEventListener('abort', aborted, { once: true });
      });
      const timeout = setTimeout(() => controller.abort(), 12000);
      setAnnounce(manual || !current.current);
      setBusy(true);
      try {
        const response = await Promise.race([
          loader(controller.signal, current.current?.etag || undefined),
          cancellation,
        ]);
        if (!alive.current || controller.signal.aborted || pending.current !== controller) return;
        if (response instanceof SnapshotResponse && response.status === 304) {
          if (!current.current) throw new SnapshotReadError('missing-model');
          current.current = { ...current.current, etag: response.etag || current.current.etag };
        } else {
          const result = parseSnapshot(
            response instanceof SnapshotResponse ? response.data : response,
          );
          const model =
            current.current && semanticSnapshotEqual(current.current.snapshot, result)
              ? current.current.snapshot
              : result;
          current.current = {
            snapshot: model,
            etag: response instanceof SnapshotResponse ? response.etag : null,
          };
          setSnapshot(model);
        }
        setObserved(new Date());
        setError(false);
      } catch {
        // no-excuse-ok: catch -- this hook is the snapshot read boundary.
        // This is the API boundary: failed reads retain the last observed data.
        if (alive.current && pending.current === controller) setError(true);
      } finally {
        clearTimeout(timeout);
        if (aborted) controller.signal.removeEventListener('abort', aborted);
        if (pending.current === controller) {
          pending.current = null;
          if (alive.current) setBusy(false);
          if (alive.current && queuedAfterWrite.current) {
            queuedAfterWrite.current = false;
            void readSnapshot(false, true);
          }
        }
      }
    },
    [loader],
  );
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
      queuedAfterWrite.current = false;
      pending.current?.abort();
      pending.current = null;
      clearInterval(interval);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [refresh]);
  return { snapshot, observed, error, busy, announce, refresh };
}
