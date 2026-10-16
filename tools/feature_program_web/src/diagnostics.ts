// Emit-only optional diagnostics. No application state, payloads or runtime controls are exported.
export const timingPrefix = 'WORK_PROGRAM_TIMING_V1';
export const timingPhases = [
  'click',
  'reserved',
  'drain_started',
  'drain_settled',
  'post_started',
  'post_response',
  'receipt_validated',
  'receipt_accepted',
  'receipt_dom_committed',
  'mandatory_get_started',
  'mandatory_get_request_started',
  'mandatory_get_response',
  'mandatory_get_settled',
  'fresh_dom_committed',
  'write_released',
  'abandoned',
] as const;
type Phase = (typeof timingPhases)[number];
export type TimingFields = {
  readonly outcome?: 'success' | 'failure' | 'unknown' | 'not_started' | 'abandoned';
  readonly httpStatus?: number;
  readonly serverTraceId?: string | null;
};
export type DiagnosticSpan = { readonly mark: (phase: Phase, fields?: TimingFields) => void };
export type Diagnostics = { readonly start: () => DiagnosticSpan | undefined };
type Options = {
  readonly enabled?: boolean;
  readonly now?: () => number;
  readonly clockId?: string;
  readonly sink?: (line: string) => void;
};
export function createDiagnostics(options: Options = {}): Diagnostics {
  let enabled =
    options.enabled ??
    Boolean(document.querySelector('meta[name="work-program-diagnostics"][content="1"]'));
  const now = options.now || (() => performance.now());
  const sink = options.sink || ((line: string) => console.info(line));
  const clockId = enabled ? options.clockId || 'client-' + crypto.randomUUID() : '';
  let sequence = 0;
  let truncated = false;
  let bytes = 0;
  return {
    start() {
      if (!enabled) return undefined;
      if (sequence >= 32) {
        if (!truncated) {
          truncated = true;
          try {
            sink(
              timingPrefix +
                ' ' +
                JSON.stringify({
                  version: 1,
                  domain: 'client',
                  clock_id: clockId,
                  truncated: true,
                  events: [],
                }),
            );
          } catch {
            enabled = false;
          } // no-excuse-ok: catch -- diagnostic sink boundary only.
        }
        return undefined;
      }
      const interactionId = 'w' + ++sequence;
      const seen = new Set<Phase>();
      let events: {
        phase: Phase;
        t_ms: number;
        outcome?: string;
        http_status?: number;
        server_trace_id?: string;
      }[] = [];
      const mark: DiagnosticSpan['mark'] = (phase, fields = {}) => {
        if (!enabled || seen.has(phase) || seen.size >= 32 || !timingPhases.includes(phase)) return;
        const timestamp = now();
        if (!Number.isFinite(timestamp) || timestamp < 0) return;
        seen.add(phase);
        const event: (typeof events)[number] = { phase, t_ms: timestamp };
        if (
          fields.outcome &&
          ['success', 'failure', 'unknown', 'not_started', 'abandoned'].includes(fields.outcome)
        )
          event.outcome = fields.outcome;
        if (
          Number.isInteger(fields.httpStatus) &&
          Number(fields.httpStatus) >= 100 &&
          Number(fields.httpStatus) <= 599
        )
          event.http_status = fields.httpStatus;
        if (
          typeof fields.serverTraceId === 'string' &&
          /^[a-f0-9]{16}$/u.test(fields.serverTraceId)
        )
          event.server_trace_id = fields.serverTraceId;
        events.push(event);
        if (
          !['receipt_dom_committed', 'fresh_dom_committed', 'write_released', 'abandoned'].includes(
            phase,
          )
        )
          return;
        const line =
          timingPrefix +
          ' ' +
          JSON.stringify({
            version: 1,
            domain: 'client',
            clock_id: clockId,
            interaction_id: interactionId,
            events,
          });
        events = [];
        bytes += line.length;
        if (bytes > 128 * 1024) {
          enabled = false;
          return;
        }
        try {
          sink(line);
        } catch {
          // no-excuse-ok: catch -- optional diagnostics must never change application behavior.
          enabled = false;
        }
      };
      mark('click');
      return { mark };
    },
  };
}
