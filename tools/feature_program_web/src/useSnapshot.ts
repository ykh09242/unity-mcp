import { useCallback, useEffect, useRef, useState } from 'react';
import { parseSnapshot, semanticSnapshotEqual } from './model';
import type { Snapshot } from './model';
import type { DiagnosticSpan } from './diagnostics';

export type SnapshotLoader = (
  signal: AbortSignal,
  etag?: string,
  diagnostic?: DiagnosticSpan,
) => Promise<unknown>;
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
export const loadSnapshot: SnapshotLoader = async (signal, etag, diagnostic) => {
  diagnostic?.mark('mandatory_get_request_started');
  const response = await fetch('/api/snapshot', {
    signal,
    cache: 'no-store',
    credentials: 'same-origin',
    headers: etag ? { 'If-None-Match': etag } : undefined,
  });
  diagnostic?.mark('mandatory_get_response', {
    httpStatus: response.status,
    serverTraceId: response.headers.get('X-Work-Program-Trace'),
  });
  if (response.status === 304) return new SnapshotResponse(304, response.headers.get('ETag'));
  if (!response.ok) throw new SnapshotReadError('http', response.status);
  return new SnapshotResponse(200, response.headers.get('ETag'), await response.json());
};
export type SnapshotWritePhase = 'waiting-read' | 'writing' | 'reconciling';
export type CoordinatedWrite<T> = {
  readonly outcome:
    | { readonly status: 'fulfilled'; readonly value: T }
    | { readonly status: 'rejected'; readonly reason: unknown };
  readonly snapshot: Snapshot | null;
  readonly refreshed: boolean;
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
  const pendingRead = useRef<Promise<boolean> | null>(null);
  const writeReservation = useRef<object | null>(null);
  const successfulReads = useRef(0);
  const automaticLease = useRef<object | null>(null);
  const missedAutomatic = useRef(false);
  const [writePhase, setWritePhase] = useState<SnapshotWritePhase | null>(null);
  const [diagnosticCommit, setDiagnosticCommit] = useState<{
    readonly span: DiagnosticSpan;
    readonly snapshot: Snapshot;
  } | null>(null);
  const current = useRef<{
    readonly snapshot: Snapshot;
    readonly etag: string | null;
  } | null>(null);
  const refresh = useCallback(
    async function readSnapshot(
      manual = false,
      afterWrite = false,
      reservation?: object,
      diagnostic?: DiagnosticSpan,
    ): Promise<void> {
      // A reserved write owns its one final reconciliation; intermediate refresh intents coalesce.
      if (writeReservation.current && reservation !== writeReservation.current) return;
      if (pending.current) {
        if (afterWrite) queuedAfterWrite.current = true;
        return;
      }
      diagnostic?.mark('mandatory_get_started');
      const controller = new AbortController();
      pending.current = controller;
      let finishRead: (succeeded: boolean) => void = () => {};
      const completion = new Promise<boolean>((resolve) => {
        finishRead = resolve;
      });
      pendingRead.current = completion;
      let succeeded = false;
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
          diagnostic
            ? loader(controller.signal, current.current?.etag || undefined, diagnostic)
            : loader(controller.signal, current.current?.etag || undefined),
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
        missedAutomatic.current = false;
        successfulReads.current += 1;
        succeeded = true;
      } catch {
        // no-excuse-ok: catch -- this hook is the snapshot read boundary.
        // This is the API boundary: failed reads retain the last observed data.
        if (alive.current && pending.current === controller) setError(true);
      } finally {
        clearTimeout(timeout);
        if (aborted) controller.signal.removeEventListener('abort', aborted);
        diagnostic?.mark('mandatory_get_settled', { outcome: succeeded ? 'success' : 'failure' });
        finishRead(succeeded);
        if (pendingRead.current === completion) pendingRead.current = null;
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
  const coordinateWrite = useCallback(
    async <T>(
      operation: () => Promise<T>,
      completed?: (value: T) => void,
      diagnostic?: DiagnosticSpan,
    ): Promise<CoordinatedWrite<T> | null> => {
      if (!alive.current || writeReservation.current) return null;
      const reservation = {};
      const programId = current.current?.snapshot.program_id;
      writeReservation.current = reservation;
      diagnostic?.mark('reserved');
      missedAutomatic.current = false;
      const priorRead = pendingRead.current;
      setWritePhase(priorRead ? 'waiting-read' : 'writing');
      try {
        // A browser abort is not a server cancellation. Do not send after an unsuccessful drain.
        if (priorRead) {
          diagnostic?.mark('drain_started');
          const drained = await priorRead;
          diagnostic?.mark('drain_settled', { outcome: drained ? 'success' : 'failure' });
          if (!drained) return null;
        }
        if (
          !alive.current ||
          writeReservation.current !== reservation ||
          (programId !== undefined && current.current?.snapshot.program_id !== programId)
        )
          return null;
        queuedAfterWrite.current = false;
        setWritePhase('writing');
        let outcome: CoordinatedWrite<T>['outcome'];
        try {
          outcome = { status: 'fulfilled', value: await operation() };
        } catch (reason) {
          // no-excuse-ok: catch -- retain unknown transport outcome while reconciling exact receipt.
          outcome = { status: 'rejected', reason };
        }
        if (
          !alive.current ||
          writeReservation.current !== reservation ||
          (programId !== undefined && current.current?.snapshot.program_id !== programId)
        )
          return null;
        if (outcome.status === 'fulfilled' && completed) {
          try {
            completed(outcome.value);
          } catch (reason) {
            // no-excuse-ok: catch -- local notification failure still requires the real reconciliation.
            outcome = { status: 'rejected', reason };
          }
        }
        setWritePhase('reconciling');
        const beforeRead = successfulReads.current;
        await refresh(false, false, reservation, diagnostic);
        if (
          !alive.current ||
          writeReservation.current !== reservation ||
          (programId !== undefined && current.current?.snapshot.program_id !== programId)
        )
          return null;
        if (diagnostic && successfulReads.current > beforeRead && current.current)
          setDiagnosticCommit({ span: diagnostic, snapshot: current.current.snapshot });
        return {
          outcome,
          snapshot: current.current?.snapshot || null,
          refreshed: successfulReads.current > beforeRead,
        };
      } finally {
        if (writeReservation.current === reservation) {
          writeReservation.current = null;
          diagnostic?.mark('write_released');
          if (alive.current) setWritePhase(null);
        } else diagnostic?.mark('abandoned', { outcome: 'abandoned' });
      }
    },
    [refresh],
  );
  const holdAutomaticReads = useCallback(() => {
    const lease = {};
    automaticLease.current = lease;
    return () => {
      if (automaticLease.current !== lease) return;
      automaticLease.current = null;
      if (missedAutomatic.current) {
        missedAutomatic.current = false;
        // A write already owns one mandatory fresh read; lease cleanup must not add another.
        if (alive.current && !writeReservation.current) void refresh();
      }
    };
  }, [refresh]);
  useEffect(() => {
    alive.current = true;
    setWritePhase(null);
    setDiagnosticCommit(null);
    void refresh();
    const automatic = () => {
      if (document.hidden || writeReservation.current) return;
      if (automaticLease.current) {
        missedAutomatic.current = true;
        return;
      }
      void refresh();
    };
    const interval = setInterval(automatic, 5000);
    const visible = automatic;
    document.addEventListener('visibilitychange', visible);
    return () => {
      alive.current = false;
      writeReservation.current = null;
      automaticLease.current = null;
      missedAutomatic.current = false;
      queuedAfterWrite.current = false;
      pending.current?.abort();
      pending.current = null;
      clearInterval(interval);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [refresh]);
  return {
    snapshot,
    observed,
    error,
    busy,
    announce,
    refresh,
    coordinateWrite,
    writePhase,
    holdAutomaticReads,
    diagnosticCommit,
  };
}
