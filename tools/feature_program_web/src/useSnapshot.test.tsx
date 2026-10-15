import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadSnapshot, SnapshotResponse, useSnapshot } from './useSnapshot';

const fixture = (generated_at = 'first') => ({
  snapshot_version: 1,
  revision: 'same',
  program_id: 'test',
  generated_at,
  integrity: { status: 'fresh', warnings: [] },
  tasks: [{ id: 'A', title: 'Task', status: 'planned' }],
});

describe('snapshot refresh lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  });
  afterEach(() => vi.useRealTimers());

  it('coalesces write invalidations behind a pending read and fetches the saved state', async () => {
    let finish: ((value: unknown) => void) | undefined;
    const loader = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValue({
        ...fixture(),
        tasks: [{ id: 'A', title: 'Saved task', status: 'planned' }],
      });
    const hook = renderHook(() => useSnapshot(loader));
    await act(async () => {
      await hook.result.current.refresh(false, true);
      await hook.result.current.refresh(false, true);
      await hook.result.current.refresh(true);
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(loader).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish?.(fixture());
    });
    expect(loader).toHaveBeenCalledTimes(2);
    expect(hook.result.current.snapshot?.tasks[0]?.title).toBe('Saved task');
    expect(hook.result.current.busy).toBe(false);
  });

  it('discards queued invalidation and aborts the old request on loader replacement', async () => {
    let finish: ((value: unknown) => void) | undefined;
    let oldSignal: AbortSignal | undefined;
    const oldLoader = vi.fn((signal: AbortSignal) => {
      oldSignal = signal;
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    const nextLoader = vi.fn().mockResolvedValue(fixture('new source'));
    const hook = renderHook(({ loader }) => useSnapshot(loader), {
      initialProps: { loader: oldLoader },
    });
    await act(async () => {
      await hook.result.current.refresh(false, true);
    });
    hook.rerender({ loader: nextLoader });
    await act(async () => {
      finish?.(fixture('late old source'));
    });
    expect(oldSignal?.aborted).toBe(true);
    expect(oldLoader).toHaveBeenCalledTimes(1);
    expect(nextLoader).toHaveBeenCalledTimes(1);
    expect(hook.result.current.snapshot?.generated_at).toBe('new source');
  });

  it('retains exact model when only top-level observation generation changes', async () => {
    // Given
    const loader = vi.fn().mockResolvedValue(fixture());
    const hook = renderHook(() => useSnapshot(loader));
    await act(async () => {});
    const previous = hook.result.current.snapshot;
    // When
    loader.mockResolvedValue(fixture('second'));
    await act(async () => {
      await hook.result.current.refresh();
    });
    // Then
    expect(hook.result.current.snapshot).toBe(previous);
    expect(hook.result.current.error).toBe(false);
  });

  it('replaces model when integrity changes without registry revision changing', async () => {
    // Given
    const loader = vi.fn().mockResolvedValue(fixture());
    const hook = renderHook(() => useSnapshot(loader));
    await act(async () => {});
    const previous = hook.result.current.snapshot;
    // When
    loader.mockResolvedValue({ ...fixture(), integrity: { status: 'stale', warnings: ['drift'] } });
    await act(async () => {
      await hook.result.current.refresh();
    });
    // Then
    expect(hook.result.current.snapshot).not.toBe(previous);
    expect(hook.result.current.snapshot?.integrity).toEqual({
      status: 'stale',
      warnings: ['drift'],
    });
  });

  it('suppresses hidden polling and refreshes immediately on foreground', async () => {
    // Given
    const loader = vi.fn().mockResolvedValue(fixture());
    renderHook(() => useSnapshot(loader));
    await act(async () => {});
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    // When
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    // Then
    expect(loader).toHaveBeenCalledTimes(1);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('times out even a loader that ignores abort without overlapping reads', async () => {
    // Given
    const signals: AbortSignal[] = [];
    const loader = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<unknown>(() => {});
    });
    const hook = renderHook(() => useSnapshot(loader));
    // When
    await act(async () => {
      await vi.advanceTimersByTimeAsync(12000);
    });
    // Then
    expect(loader).toHaveBeenCalledTimes(1);
    expect(signals[0]?.aborted).toBe(true);
    expect(hook.result.current.error).toBe(true);
    expect(hook.result.current.busy).toBe(false);
  });

  it('retains last good snapshot and observed time after failure', async () => {
    // Given
    const loader = vi.fn().mockResolvedValue(fixture());
    const hook = renderHook(() => useSnapshot(loader));
    await act(async () => {});
    const previous = hook.result.current.snapshot;
    const observed = hook.result.current.observed;
    // When
    loader.mockRejectedValue(new Error('outage'));
    await act(async () => {
      await hook.result.current.refresh();
    });
    // Then
    expect(hook.result.current.snapshot).toBe(previous);
    expect(hook.result.current.observed).toBe(observed);
    expect(hook.result.current.error).toBe(true);
  });

  it('retains exact model and advances observation on an unchanged conditional response', async () => {
    // Given
    const loader = vi.fn().mockResolvedValue(new SnapshotResponse(200, 'W/"one"', fixture()));
    const hook = renderHook(() => useSnapshot(loader));
    await act(async () => {});
    const previous = hook.result.current.snapshot;
    const observed = hook.result.current.observed;
    loader.mockResolvedValue(new SnapshotResponse(304, 'W/"one"'));
    // When
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    // Then
    expect(loader.mock.calls[1]?.[1]).toBe('W/"one"');
    expect(hook.result.current.snapshot).toBe(previous);
    expect(hook.result.current.observed).not.toBe(observed);
    expect(hook.result.current.error).toBe(false);
  });

  it('reports an initial304 without inventing a current model', async () => {
    // Given
    const loader = vi.fn().mockResolvedValue(new SnapshotResponse(304, 'W/"one"'));
    const hook = renderHook(() => useSnapshot(loader));
    // When
    await act(async () => {});
    // Then
    expect(loader.mock.calls[0]?.[1]).toBeUndefined();
    expect(hook.result.current.snapshot).toBeNull();
    expect(hook.result.current.observed).toBeNull();
    expect(hook.result.current.error).toBe(true);
  });

  it('validates the full changed200 even if its validator matches the current one', async () => {
    // Given
    const loader = vi.fn().mockResolvedValue(new SnapshotResponse(200, 'W/"one"', fixture()));
    const hook = renderHook(() => useSnapshot(loader));
    await act(async () => {});
    const previous = hook.result.current.snapshot;
    loader.mockResolvedValue(
      new SnapshotResponse(200, 'W/"one"', { ...fixture(), tasks: [{ id: 'bad' }] }),
    );
    // When
    await act(async () => {
      await hook.result.current.refresh();
    });
    // Then
    expect(hook.result.current.snapshot).toBe(previous);
    expect(hook.result.current.error).toBe(true);
  });

  it('ignores late results from a cancelled previous loader after replacement', async () => {
    // Given
    let resolveOld: ((value: unknown) => void) | undefined;
    const oldLoader = () =>
      new Promise<unknown>((resolve) => {
        resolveOld = resolve;
      });
    const newLoader = async () => ({ ...fixture(), program_id: 'new-program' });
    const hook = renderHook(({ loader }) => useSnapshot(loader), {
      initialProps: { loader: oldLoader },
    });
    hook.rerender({ loader: newLoader });
    await act(async () => {});
    const current = hook.result.current.snapshot;
    // When
    await act(async () => {
      resolveOld?.(fixture());
    });
    // Then
    expect(hook.result.current.snapshot).toBe(current);
    expect(hook.result.current.snapshot?.program_id).toBe('new-program');
  });

  it('keeps nested generation timestamps and unknown future projections semantic', async () => {
    // Given
    const loader = vi
      .fn()
      .mockResolvedValue({ ...fixture(), extension: { generated_at: 'first' } });
    const hook = renderHook(() => useSnapshot(loader));
    await act(async () => {});
    const previous = hook.result.current.snapshot;
    loader.mockResolvedValue({ ...fixture(), extension: { generated_at: 'second' } });
    // When
    await act(async () => {
      await hook.result.current.refresh();
    });
    // Then
    expect(hook.result.current.snapshot).not.toBe(previous);
  });

  it('keeps no-store and same-origin safeguards and handles304 without reading a body', async () => {
    // Given
    const json = vi.fn();
    const fetcher = vi.fn().mockResolvedValue({
      status: 304,
      ok: false,
      headers: new Headers({ ETag: 'W/"one"' }),
      json,
    });
    vi.stubGlobal('fetch', fetcher);
    const controller = new AbortController();
    // When
    const response = await loadSnapshot(controller.signal, 'W/"one"');
    // Then
    expect(response).toEqual(new SnapshotResponse(304, 'W/"one"'));
    expect(json).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledWith('/api/snapshot', {
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { 'If-None-Match': 'W/"one"' },
    });
    vi.unstubAllGlobals();
  });

  it('continues reaching loopback while browser reports offline', async () => {
    // Given
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    const loader = vi.fn().mockResolvedValue(fixture());
    renderHook(() => useSnapshot(loader));
    // When
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    // Then
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('cancels the pending request and removes schedules on unmount', async () => {
    // Given
    const signals: AbortSignal[] = [];
    const loader = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<unknown>(() => {});
    });
    const hook = renderHook(() => useSnapshot(loader));
    // When
    hook.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    // Then
    expect(signals[0]?.aborted).toBe(true);
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('retains the coherent timing object across 304 and replaces it only for a new retained observation', async () => {
    // Given: nested timing contains retained facts, not a projection-time age.
    const timing = {
      schema_version: 1,
      source_binding: { input_digest: 'retained-one' },
      current: { items: [{ id: 'stable-review', age: null }], total: 1, shown: 1, omitted: 0 },
    };
    const loader = vi
      .fn()
      .mockResolvedValue(new SnapshotResponse(200, 'W/"retained-one"', { ...fixture(), timing }));
    const hook = renderHook(() => useSnapshot(loader));
    await act(async () => {});
    const previous = hook.result.current.snapshot;
    // When: the server confirms identical retained observations.
    loader.mockResolvedValue(new SnapshotResponse(304, 'W/"retained-one"'));
    await act(async () => {
      await hook.result.current.refresh();
    });
    // Then: the model and nested timing retain their identity.
    expect(hook.result.current.snapshot).toBe(previous);
    expect(hook.result.current.snapshot?.timing).toBe(previous?.timing);
    // When: a real newly retained observation arrives with another validator.
    loader.mockResolvedValue(
      new SnapshotResponse(200, 'W/"retained-two"', {
        ...fixture('later'),
        timing: {
          ...timing,
          source_binding: { input_digest: 'retained-two' },
          current: {
            ...timing.current,
            items: [{ id: 'stable-review', age: { seconds: 3, as_of: '2026-10-15T01:00:03Z' } }],
          },
        },
      }),
    );
    await act(async () => {
      await hook.result.current.refresh();
    });
    // Then: the coherent projection changes, with its stable logical row ID retained.
    expect(hook.result.current.snapshot).not.toBe(previous);
    expect(hook.result.current.snapshot?.timing).not.toBe(previous?.timing);
  });
});

describe('answer write coordination', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  });
  afterEach(() => vi.useRealTimers());
  function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<T>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    return { promise, resolve, reject };
  }
  it('drains an existing read, blocks duplicate writes and coalesces refreshes into one post-write read', async () => {
    const prior = deferred<unknown>(),
      posted = deferred<string>();
    const loader = vi
      .fn()
      .mockReturnValueOnce(prior.promise)
      .mockResolvedValue({ ...fixture('after'), revision: 'after' });
    const operation = vi.fn(() => posted.promise);
    const hook = renderHook(() => useSnapshot(loader));
    let result!: ReturnType<typeof hook.result.current.coordinateWrite<string>>;
    await act(async () => {
      result = hook.result.current.coordinateWrite(operation);
      expect(await hook.result.current.coordinateWrite(operation)).toBeNull();
      await hook.result.current.refresh(true);
      await hook.result.current.refresh(false, true);
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(hook.result.current.writePhase).toBe('waiting-read');
    expect(operation).not.toHaveBeenCalled();
    expect(loader).toHaveBeenCalledTimes(1);
    await act(async () => {
      prior.resolve(fixture());
    });
    expect(operation).toHaveBeenCalledTimes(1);
    expect(hook.result.current.writePhase).toBe('writing');
    await act(async () => {
      await hook.result.current.refresh(true);
      await vi.advanceTimersByTimeAsync(5000);
      posted.resolve('post-result');
      await result;
    });
    expect(loader).toHaveBeenCalledTimes(2);
    expect(await result).toMatchObject({
      outcome: { status: 'fulfilled', value: 'post-result' },
      refreshed: true,
    });
    expect(hook.result.current.writePhase).toBeNull();
    expect(hook.result.current.snapshot?.generated_at).toBe('after');
  });
  it.each(['rejected', 'conflict'])(
    'reconciles exactly once after a %s POST outcome',
    async (outcome) => {
      const loader = vi.fn().mockResolvedValue(fixture());
      const hook = renderHook(() => useSnapshot(loader));
      await act(async () => {});
      let result: unknown;
      await act(async () => {
        result = await hook.result.current.coordinateWrite(async () => {
          if (outcome === 'rejected') throw new Error('transport unknown');
          return { ok: false, conflict: true };
        });
      });
      expect(loader).toHaveBeenCalledTimes(2);
      expect(result).toMatchObject({
        refreshed: true,
        outcome: { status: outcome === 'rejected' ? 'rejected' : 'fulfilled' },
      });
      expect(hook.result.current.writePhase).toBeNull();
    },
  );
  it('does not send after an unsuccessful drain and permits a later explicit retry', async () => {
    const prior = deferred<unknown>();
    const loader = vi.fn().mockReturnValueOnce(prior.promise).mockResolvedValue(fixture());
    const operation = vi.fn().mockResolvedValue('sent');
    const hook = renderHook(() => useSnapshot(loader));
    let result!: ReturnType<typeof hook.result.current.coordinateWrite<string>>;
    await act(async () => {
      result = hook.result.current.coordinateWrite(operation);
      prior.reject(new Error('read failed'));
      await result;
    });
    expect(await result).toBeNull();
    expect(operation).not.toHaveBeenCalled();
    expect(hook.result.current.error).toBe(true);
    expect(hook.result.current.writePhase).toBeNull();
    await act(async () => {
      await hook.result.current.coordinateWrite(operation);
    });
    expect(operation).toHaveBeenCalledTimes(1);
    expect(loader).toHaveBeenCalledTimes(2);
  });
  it('retains failed reconciliation as unavailable and resumes polling without another POST', async () => {
    const loader = vi
      .fn()
      .mockResolvedValueOnce(fixture())
      .mockRejectedValueOnce(new Error('read failed'))
      .mockResolvedValue(fixture('recovered'));
    const operation = vi.fn().mockResolvedValue('sent');
    const hook = renderHook(() => useSnapshot(loader));
    await act(async () => {});
    const previous = hook.result.current.snapshot;
    let result: unknown;
    await act(async () => {
      result = await hook.result.current.coordinateWrite(operation);
    });
    expect(result).toMatchObject({ refreshed: false });
    expect(hook.result.current.snapshot).toBe(previous);
    expect(hook.result.current.error).toBe(true);
    expect(hook.result.current.writePhase).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(hook.result.current.error).toBe(false);
    expect(operation).toHaveBeenCalledTimes(1);
  });
  it.each(['unmount', 'replace'])(
    'does not send a deferred old-program write after %s',
    async (change) => {
      const prior = deferred<unknown>();
      const loader = vi.fn(() => prior.promise),
        next = vi.fn().mockResolvedValue({ ...fixture(), program_id: 'replacement' });
      const operation = vi.fn().mockResolvedValue('must-not-send');
      const hook = renderHook(({ input }) => useSnapshot(input), {
        initialProps: { input: loader },
      });
      let result!: ReturnType<typeof hook.result.current.coordinateWrite<string>>;
      await act(async () => {
        result = hook.result.current.coordinateWrite(operation);
      });
      await act(async () => {
        if (change === 'unmount') hook.unmount();
        else hook.rerender({ input: next });
      });
      await act(async () => {
        await result;
      });
      expect(operation).not.toHaveBeenCalled();
      expect(await result).toBeNull();
    },
  );
  it('does not apply an in-flight old-program result or release a replacement write barrier', async () => {
    const post = deferred<string>(),
      replacementPost = deferred<string>();
    const loader = vi.fn().mockResolvedValue(fixture()),
      next = vi.fn().mockResolvedValue({ ...fixture(), program_id: 'replacement' });
    const hook = renderHook(({ input }) => useSnapshot(input), { initialProps: { input: loader } });
    await act(async () => {});
    let old!: ReturnType<typeof hook.result.current.coordinateWrite<string>>;
    await act(async () => {
      old = hook.result.current.coordinateWrite(() => post.promise);
    });
    hook.rerender({ input: next });
    await act(async () => {});
    let current!: ReturnType<typeof hook.result.current.coordinateWrite<string>>;
    await act(async () => {
      current = hook.result.current.coordinateWrite(() => replacementPost.promise);
      post.resolve('old');
      await old;
    });
    expect(hook.result.current.writePhase).toBe('writing');
    expect(next).toHaveBeenCalledTimes(1);
    await act(async () => {
      replacementPost.resolve('current');
      await current;
    });
    expect(next).toHaveBeenCalledTimes(2);
    expect(hook.result.current.snapshot?.program_id).toBe('replacement');
  });
});

it('does not send an old-program write when the same loader observes a replacement program during drain', async () => {
  let finish!: (value: unknown) => void;
  const loader = vi
    .fn()
    .mockResolvedValueOnce(fixture())
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
  const hook = renderHook(() => useSnapshot(loader));
  await act(async () => {});
  let result!: ReturnType<typeof hook.result.current.coordinateWrite<string>>;
  const operation = vi.fn().mockResolvedValue('must-not-send');
  await act(async () => {
    void hook.result.current.refresh();
    result = hook.result.current.coordinateWrite(operation);
    finish({ ...fixture(), program_id: 'replacement' });
    await result;
  });
  expect(await result).toBeNull();
  expect(operation).not.toHaveBeenCalled();
  expect(hook.result.current.writePhase).toBeNull();
});
it('does not return an old-program receipt when reconciliation changes program with the same loader', async () => {
  const loader = vi
    .fn()
    .mockResolvedValueOnce(fixture())
    .mockResolvedValue({ ...fixture(), program_id: 'replacement' });
  const hook = renderHook(() => useSnapshot(loader));
  await act(async () => {});
  let result: unknown;
  await act(async () => {
    result = await hook.result.current.coordinateWrite(async () => 'sent-once');
  });
  expect(result).toBeNull();
  expect(loader).toHaveBeenCalledTimes(2);
  expect(hook.result.current.snapshot?.program_id).toBe('replacement');
  expect(hook.result.current.writePhase).toBeNull();
});

it('publishes the completed operation callback before GET but keeps reservation until that GET settles', async () => {
  let finishRead!: (value: unknown) => void;
  const loader = vi
    .fn()
    .mockResolvedValueOnce(fixture())
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishRead = resolve;
        }),
    );
  const hook = renderHook(() => useSnapshot(loader));
  await act(async () => {});
  const received = vi.fn(),
    second = vi.fn().mockResolvedValue('second');
  let pending!: ReturnType<typeof hook.result.current.coordinateWrite<string>>;
  await act(async () => {
    pending = hook.result.current.coordinateWrite(async () => 'durably-complete', received);
  });
  expect(received).toHaveBeenCalledExactlyOnceWith('durably-complete');
  expect(hook.result.current.writePhase).toBe('reconciling');
  await act(async () => {
    expect(await hook.result.current.coordinateWrite(second)).toBeNull();
    await hook.result.current.refresh(true);
  });
  expect(second).not.toHaveBeenCalled();
  expect(loader).toHaveBeenCalledTimes(2);
  await act(async () => {
    finishRead(fixture());
    await pending;
  });
  expect(hook.result.current.writePhase).toBeNull();
});
it.each(['unmount', 'replace'])(
  'does not notify an old operation callback after %s',
  async (change) => {
    let finishPost!: (value: string) => void;
    const loader = vi.fn().mockResolvedValue(fixture()),
      replacement = vi.fn().mockResolvedValue({ ...fixture(), program_id: 'replacement' });
    const hook = renderHook(({ input }) => useSnapshot(input), { initialProps: { input: loader } });
    await act(async () => {});
    const received = vi.fn();
    let pending!: ReturnType<typeof hook.result.current.coordinateWrite<string>>;
    await act(async () => {
      pending = hook.result.current.coordinateWrite(
        () =>
          new Promise((resolve) => {
            finishPost = resolve;
          }),
        received,
      );
    });
    if (change === 'unmount') hook.unmount();
    else hook.rerender({ input: replacement });
    await act(async () => {});
    await act(async () => {
      finishPost('old-receipt');
      await pending;
    });
    expect(received).not.toHaveBeenCalled();
    expect(replacement).toHaveBeenCalledTimes(change === 'unmount' ? 0 : 1);
  },
);
