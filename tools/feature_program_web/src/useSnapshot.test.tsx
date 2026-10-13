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
});
