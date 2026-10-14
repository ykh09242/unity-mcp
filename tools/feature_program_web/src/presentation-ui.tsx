import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';
import type { Json, Snapshot } from './model';
import { record, string } from './model';
import { actorLabel, presentationFields, taskLabel } from './presentation';
import { useI18n } from './i18n';

const Context = createContext<Snapshot | null>(null);
const actorFields = new Set([
  'Actor',
  'Director',
  'Reviewer',
  'Implementer',
  'Successor',
  'Previous actor',
  'Next actor',
  'Current owner',
  'Assigned actor',
  'Appointed actor',
  'Final director',
  'Owner',
  'Decision owner',
  'Next action owner',
]);
export function PresentationProvider({
  snapshot,
  children,
}: {
  readonly snapshot: Snapshot | null;
  readonly children: ReactNode;
}) {
  return <Context.Provider value={snapshot}>{children}</Context.Provider>;
}
export function usePresentation() {
  const snapshot = useContext(Context);
  const actor = (id: Json | undefined) =>
    snapshot ? actorLabel(snapshot, string(id)) : string(id);
  const task = (id: Json | undefined) => (snapshot ? taskLabel(snapshot, string(id)) : string(id));
  const goal = (id: Json | undefined) => {
    const current = record(snapshot?.objective);
    const goals = [current, ...(Array.isArray(current.history) ? current.history.map(record) : [])];
    const value = goals.find((row) => row.id === id);
    return string(value?.title || value?.goal) || string(id);
  };
  const withId = (label: string, id: Json | undefined) =>
    label === string(id) ? label : label + ' · ' + string(id);
  const workstream = (id: Json | undefined) => {
    const streams = snapshot?.workstreams;
    const stream = Array.isArray(streams)
      ? streams.map(record).find((row) => row.id === id)
      : undefined;
    return string(stream?.name || stream?.title) || string(id);
  };
  return {
    actor,
    field: (label: string, value: Json | undefined): Json | undefined =>
      label === 'Recorded participants' && Array.isArray(value)
        ? value.map(actor)
        : actorFields.has(label) && typeof value === 'string'
          ? actor(value)
          : label === 'Workstream'
            ? workstream(value)
            : label === 'Task'
              ? withId(task(value), value)
              : label === 'Objective'
                ? withId(goal(value), value)
                : value,
    goal,
    task,
    taskReference: (id: Json | undefined) => withId(task(id), id),
    goalReference: (id: Json | undefined) => withId(goal(id), id),
    workstream,
  };
}

export function ContentLanguage({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n();
  const presentation = record(snapshot.presentation),
    totals = record(presentation.totals);
  const locale = string(snapshot.communication_language);
  const compatible =
    presentation.schema_version === 1 &&
    presentation.revision === snapshot.revision &&
    presentation.locale === locale;
  const name = locale === 'ko' ? i18n.t('Korean') : locale === 'en' ? i18n.t('English') : locale;
  const count = (value: Json | undefined): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  const available =
    compatible &&
    count(totals.total) &&
    count(totals.current_translated) &&
    count(totals.current_original);
  const states = [
    ['current_translated', 'Translated or aliased'],
    ['current_original', 'Original retained'],
    ['original_only', 'Original only'],
    ['missing', 'Missing review'],
    ['stale', 'Stale review'],
    ['withdrawn', 'Withdrawn review'],
    ['unavailable', 'Source unavailable'],
  ] as const;
  const periods = record(presentation.periods);
  return (
    <details className="content-language">
      <summary>
        {i18n.t('Content language')}: {name || i18n.t('Unknown')}
      </summary>
      <p>
        {i18n.t(
          'Session language is separate from the interface. Originals and technical IDs are preserved.',
        )}
      </p>
      <p>
        {available
          ? i18n.t('{current} / {total} reviewed content fields', {
              current:
                Number(totals.current_translated || 0) + Number(totals.current_original || 0),
              total: Number(totals.total),
            })
          : i18n.t('Content coverage is unavailable.')}
      </p>
      {available && (
        <dl className="content-coverage">
          {states.map(([key, label]) => {
            const value = totals[key];
            return count(value) && value > 0 ? (
              <div key={key}>
                <dt>{i18n.t(label)}</dt>
                <dd>{i18n.number(value)}</dd>
              </div>
            ) : null;
          })}
          {(
            [
              ['current', 'Current records'],
              ['historical', 'Retained history'],
            ] as const
          ).map(([key, label]) => {
            const value = record(periods[key]).total;
            return count(value) ? (
              <div key={key}>
                <dt>{i18n.t(label)}</dt>
                <dd>{i18n.number(value)}</dd>
              </div>
            ) : null;
          })}
        </dl>
      )}
      <p className="muted">
        {i18n.t('Exact language tag')}: <code>{locale || i18n.t('Unknown')}</code>
      </p>
    </details>
  );
}

const kinds: Readonly<Record<string, readonly string[]>> = {
  task: [
    'task_title',
    'task_content',
    'task_action',
    'task_note',
    'task_history',
    'receipt_narrative',
    'delivery',
  ],
  question: ['question', 'question_outcome'],
  goal: ['objective', 'objective_criterion', 'objective_record'],
  actor: ['actor_alias', 'staffing'],
  category: ['category', 'workstream'],
  handoff: ['handoff'],
};
const fieldLabels: Readonly<Record<string, string>> = {
  title: 'Title',
  name: 'Name',
  label: 'Display name',
  source_body: 'Task description',
  expected_result: 'Expected result',
  acceptance_criteria: 'Acceptance criteria',
  text: 'Criterion',
  goal: 'Goal',
  next_action: 'Next action',
  planning_note: 'Planning note',
  reason: 'Recorded reason',
  summary: 'Summary',
  note: 'Note',
  constraints: 'Constraints',
  decisions: 'Decisions',
  findings: 'Findings',
  unresolved_findings: 'Unresolved findings',
  unresolved_acceptance: 'Unresolved acceptance',
  unresolved_blockers: 'Unresolved blockers',
  limitations: 'Limitations',
  description: 'Description',
  basis: 'Authority basis',
  focus: 'Focus',
  team_state: 'Team state',
  criterion: 'Criterion',
  prompt: 'Question',
  why: 'Why this question',
  requested_decision: 'Requested decision',
  option: 'Option',
};
export function ContentOriginals({
  entity,
  id,
}: {
  readonly entity: keyof typeof kinds;
  readonly id: string;
}) {
  const snapshot = useContext(Context),
    i18n = useI18n();
  const [open, setOpen] = useState(false);
  const fields = snapshot
    ? presentationFields(snapshot)
        .filter(
          (field) =>
            field.target.entity_id === id && kinds[entity]?.includes(string(field.target.kind)),
        )
        .sort(
          (a, b) =>
            string(a.origin.path).localeCompare(string(b.origin.path), 'en') ||
            string(a.origin.pointer).localeCompare(string(b.origin.pointer), 'en', {
              numeric: true,
            }),
        )
    : [];
  if (!fields.length) return null;
  return (
    <details
      className="content-originals"
      onToggle={(event) => {
        if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
      }}
    >
      <summary>
        {i18n.t('Original text and sources')} ({fields.length})
      </summary>
      {open &&
        fields.map((field) => (
          <details key={JSON.stringify(field.origin)}>
            <summary>
              {i18n.t(fieldLabels[string(field.target.field)] || string(field.target.field))}{' '}
              <span className="mono muted">{string(field.origin.pointer)}</span>
            </summary>
            <p className="prose">{field.original}</p>
            <dl className="fields">
              <div>
                <dt>{i18n.t('ID')}</dt>
                <dd className="mono">{id}</dd>
              </div>
              <div>
                <dt>{i18n.t('Path')}</dt>
                <dd className="mono">{string(field.origin.path)}</dd>
              </div>
              <div>
                <dt>{i18n.t('Record location')}</dt>
                <dd className="mono">{string(field.origin.pointer)}</dd>
              </div>
              <div>
                <dt>{i18n.t('Source identity')}</dt>
                <dd className="mono">{string(field.origin.sha256 || field.origin.revision)}</dd>
              </div>
            </dl>
          </details>
        ))}
    </details>
  );
}
