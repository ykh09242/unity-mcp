import { items, record, string } from './model';
import type { Json } from './model';

export type CapturedExecution = {
  readonly runId: string;
  readonly phase: string;
  readonly processResult: string;
  readonly reportedResult: string;
  readonly captureStatus: string;
  readonly publication: string;
  readonly status: string;
  readonly reused: boolean;
  readonly nextAction: string;
  readonly reasons: readonly string[];
  readonly startedAt: string;
  readonly endedAt: string;
  readonly artifacts: {
    readonly expected: number;
    readonly captured: number;
    readonly missing: number;
  };
  readonly outputs: readonly {
    readonly stream: string;
    readonly seen: number;
    readonly retained: number;
    readonly truncated: boolean;
  }[];
};
const processResults = [
  'not_started',
  'exited',
  'spawn_failed',
  'timed_out',
  'canceled',
  'interrupted',
  'outcome_unknown',
] as const;
const count = (value: Json | undefined): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
export function capturedExecution(value: Json | undefined): CapturedExecution | null {
  const data = record(value),
    artifacts = record(data.artifacts),
    publication = record(data.publication);
  if (
    data.schema_version !== 1 ||
    !string(data.run_id) ||
    !['finished', 'interrupted'].includes(string(data.phase)) ||
    !processResults.some((result) => result === data.process_result) ||
    !['passed', 'failed', 'pending', 'not_run', 'unknown', 'not_applicable'].includes(
      string(data.reported_check_result),
    ) ||
    !['complete', 'incomplete'].includes(string(data.capture_status)) ||
    !['captured', 'unavailable'].includes(string(data.status)) ||
    !['pending', 'recorded', 'conflict', 'committed_views_failed'].includes(
      string(publication.status),
    ) ||
    !count(artifacts.expected) ||
    !count(artifacts.captured) ||
    !count(artifacts.missing)
  )
    return null;
  const outputs: CapturedExecution['outputs'][number][] = [];
  for (const stream of ['stdout', 'stderr']) {
    const output = record(record(data.output_counts)[stream]);
    if (
      !count(output.bytes_seen) ||
      !count(output.bytes_retained) ||
      output.bytes_retained > output.bytes_seen ||
      typeof output.truncated !== 'boolean'
    )
      return null;
    outputs.push({
      stream,
      seen: output.bytes_seen,
      retained: output.bytes_retained,
      truncated: output.truncated,
    });
  }
  return {
    runId: string(data.run_id),
    phase: string(data.phase),
    processResult: string(data.process_result),
    reportedResult: string(data.reported_check_result),
    captureStatus: string(data.capture_status),
    publication: string(publication.status),
    status: string(data.status),
    reused: data.reused === true,
    nextAction: string(data.next_action),
    reasons: items(data.reasons).map(string).filter(Boolean),
    startedAt: string(data.started_at),
    endedAt: string(data.ended_at),
    artifacts: {
      expected: artifacts.expected,
      captured: artifacts.captured,
      missing: artifacts.missing,
    },
    outputs,
  };
}
export const processResultLabels: Readonly<Record<string, string>> = {
  not_started: 'Process not started',
  exited: 'Process exited',
  spawn_failed: 'Process could not start',
  timed_out: 'Process timed out',
  canceled: 'Process canceled',
  interrupted: 'Process interrupted',
  outcome_unknown: 'Process outcome unknown',
};
export const publicationLabels: Readonly<Record<string, string>> = {
  pending: 'Evidence publication pending',
  recorded: 'Evidence recorded',
  conflict: 'Evidence publication conflict',
  committed_views_failed: 'Recorded; views need recovery',
};
