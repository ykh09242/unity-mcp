import { items, record, records, string } from './model';
import type { JsonRecord, Snapshot } from './model';
import { originalValue } from './presentation';

export const questionStages = [
  'open',
  'answered',
  'acknowledged',
  'applied',
  'withdrawn',
  'unknown',
] as const;
export type QuestionStage = (typeof questionStages)[number];
export type QuestionContext = {
  readonly view: 'current' | 'history' | 'all';
  readonly state: string;
  readonly search: string;
  readonly goal: string;
  readonly task: string;
  readonly selected: string;
};
export const defaultQuestions: QuestionContext = {
  view: 'current',
  state: '',
  search: '',
  goal: '',
  task: '',
  selected: '',
};
export const questionListIdentity = (context: QuestionContext = defaultQuestions) =>
  JSON.stringify([context.view, context.state, context.search, context.goal, context.task]);
export function questionStage(question: JsonRecord): QuestionStage {
  if (question.status === 'open') return 'open';
  if (question.status === 'answered') return 'answered';
  if (question.status === 'acknowledged') return 'acknowledged';
  if (question.status === 'resolved') {
    if (record(question.outcome).kind === 'applied') return 'applied';
    if (record(question.outcome).kind === 'withdrawn') return 'withdrawn';
  }
  return 'unknown';
}
export const questionStageLabel: Readonly<Record<QuestionStage, string>> = {
  open: 'Needs your answer',
  answered: 'Answer saved',
  acknowledged: 'Director acknowledged',
  applied: 'Applied',
  withdrawn: 'Withdrawn',
  unknown: 'Unknown question state',
};
export function questionMatches(question: JsonRecord, context: QuestionContext): boolean {
  const historical = question.status === 'resolved';
  if (context.view === 'current' && historical) return false;
  if (context.view === 'history' && !historical) return false;
  if (context.state && questionStage(question) !== context.state) return false;
  if (context.goal && string(question.objective_id) !== context.goal) return false;
  if (context.task && !items(question.affected_task_ids).some((id) => id === context.task))
    return false;
  const label = [question, originalValue(question)]
    .flatMap((value) => [
      value.id,
      value.prompt,
      value.requested_decision,
      value.why,
      ...items(value.options),
      record(value.outcome).summary,
    ])
    .map(string)
    .join(' ')
    .toLocaleLowerCase();
  return label.includes(context.search.trim().toLocaleLowerCase());
}
export function questionGoals(snapshot: Snapshot): readonly string[] {
  return [
    ...new Set(
      [
        string(record(snapshot.objective).id),
        ...records(snapshot.questions).map((q) => string(q.objective_id)),
      ].filter(Boolean),
    ),
  ];
}
