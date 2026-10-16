import { describe, expect, it, vi } from 'vitest';
import { createDiagnostics, timingPrefix } from './diagnostics';

describe('optional emit-only timing', () => {
  it('does nothing when the explicit HTML configuration is absent', () => {
    const sink = vi.fn(),
      now = vi.fn(() => 1);
    expect(createDiagnostics({ sink, now }).start()).toBeUndefined();
    expect(sink).not.toHaveBeenCalled();
    expect(now).not.toHaveBeenCalled();
  });
  it('emits only finite whitelisted fields with an explicit clock and deduplicated phases', () => {
    const lines: string[] = [];
    let clock = 10;
    const span = createDiagnostics({
      enabled: true,
      now: () => clock++,
      clockId: 'test-clock',
      sink: (line) => lines.push(line),
    }).start();
    span?.mark('post_response', { httpStatus: 200, serverTraceId: 'a'.repeat(16) });
    span?.mark('post_response', { httpStatus: 500, serverTraceId: 'SECRET-answer' });
    span?.mark('receipt_dom_committed');
    expect(lines).toHaveLength(1);
    const record = JSON.parse(lines[0]?.slice(timingPrefix.length + 1) || '{}');
    expect(record.clock_id).toBe('test-clock');
    expect(record.events).toEqual([
      { phase: 'click', t_ms: 10 },
      { phase: 'post_response', t_ms: 11, http_status: 200, server_trace_id: 'a'.repeat(16) },
      { phase: 'receipt_dom_committed', t_ms: 12 },
    ]);
    expect(lines.join()).not.toContain('SECRET');
  });
  it('cannot log arbitrary header values or nonfinite status/clock fields', () => {
    const sink = vi.fn();
    const span = createDiagnostics({ enabled: true, clockId: 'clock', now: () => 1, sink }).start();
    span?.mark('post_response', { httpStatus: Infinity, serverTraceId: 'secret-path/answer' });
    span?.mark('write_released');
    expect(sink.mock.calls[0]?.[0]).not.toContain('secret-path');
    expect(sink.mock.calls[0]?.[0]).not.toContain('http_status');
    const bad = createDiagnostics({ enabled: true, now: () => NaN, sink }).start();
    bad?.mark('write_released');
    expect(sink).toHaveBeenCalledTimes(1);
  });
  it('bounds interactions and isolates observer sink failure from application work', () => {
    const sink = vi.fn();
    const observer = createDiagnostics({ enabled: true, now: () => 1, sink });
    for (let i = 0; i < 40; i++) observer.start()?.mark('write_released');
    expect(sink).toHaveBeenCalledTimes(33);
    expect(JSON.parse(sink.mock.calls[32]?.[0].slice(timingPrefix.length + 1)).truncated).toBe(
      true,
    );
    const failed = createDiagnostics({
      enabled: true,
      sink: () => {
        throw new Error('sink failure');
      },
    });
    expect(() => failed.start()?.mark('write_released')).not.toThrow();
    expect(failed.start()).toBeUndefined();
  });
  it('opts in only to the exact inert meta setting and exports no control object on window', () => {
    const meta = document.createElement('meta');
    meta.name = 'work-program-diagnostics';
    meta.content = '1';
    document.head.append(meta);
    try {
      expect(createDiagnostics({ sink: () => {} }).start()).toBeDefined();
    } finally {
      meta.remove();
    }
    expect(Object.hasOwn(window, 'workProgramDiagnostics')).toBe(false);
  });
});
