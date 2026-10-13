import { useState } from 'react';
import { useI18n } from './i18n';
import { items, record, records, string } from './model';
import type { Json, Snapshot, Task } from './model';
import {
  authoredText,
  boundedCollection,
  currentCollection,
  currentReference,
  recordedReadiness,
} from './readiness';
import { workloadHref } from './relate';
import { snapshotIndex } from './selectors';
import { goalTaskIds } from './navigation';

export function OriginalRecord({
  value,
  label = 'Original record',
}: {
  readonly value: Json | undefined;
  readonly label?: string;
}) {
  const i18n = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <details className="original-record" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{i18n.t(label)}</summary>
      {open && <pre className="source">{JSON.stringify(value ?? null, null, 2)}</pre>}
    </details>
  );
}

function Omitted({ collection }: { readonly collection: ReturnType<typeof boundedCollection> }) {
  const i18n = useI18n();
  return collection && collection.omitted > 0 ? (
    <p className="muted">
      {i18n.t('{shown} / {total} shown · {omitted} omitted', {
        shown: collection.shown,
        total: collection.total,
        omitted: collection.omitted,
      })}
    </p>
  ) : null;
}

function NoteText({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  const text = authoredText(value);
  return (
    <p className="prose">
      {text.text || i18n.t('Not recorded')}
      {text.truncated && (
        <span className="muted"> … {i18n.t('Read the original record for full text.')}</span>
      )}
    </p>
  );
}

function ShortenedSummary({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  return items(record(value).truncated_fields).length > 0 ? (
    <p className="muted">
      {i18n.t('Summary text is shortened. Inspect the referenced source for full context.')}
    </p>
  ) : null;
}

export function CoordinationNotes({ task }: { readonly task: Task }) {
  const i18n = useI18n();
  const notes = Array.isArray(task.notes) ? task.notes : task.notes == null ? [] : [task.notes];
  const shown = notes.slice(-5).reverse();
  return (
    <details className="coordination-notes">
      <summary>
        {i18n.t('Authored coordination notes')} <span className="muted">{notes.length}</span>
      </summary>
      <p className="muted">
        {i18n.t('Notes preserve authored claims. Peer agreement is not assessed.')}
      </p>
      {!shown.length && <p className="muted">{i18n.t('No notes recorded')}</p>}
      <ol aria-label={i18n.t('Task notes')}>
        {shown.map((note, index) => {
          const entry = record(note);
          const readable = ['actor', 'timestamp', 'reason', 'next_action'].some((key) =>
            string(entry[key]),
          );
          return (
            <li key={index}>
              {typeof note === 'string' ? (
                <NoteText value={note} />
              ) : readable ? (
                <>
                  <p className="note-byline">
                    {string(entry.actor) || i18n.t('Unknown')} · {i18n.date(entry.timestamp)}
                  </p>
                  <NoteText value={entry.reason} />
                  <p className="muted">{i18n.t('Next action')}</p>
                  <NoteText value={entry.next_action} />
                  <ul className="note-evidence">
                    {records(entry.evidence)
                      .slice(0, 5)
                      .map((ref, ordinal) => (
                        <li key={ordinal}>
                          {string(ref.path) && string(ref.sha256) ? (
                            <a href={`#evidence/${encodeURIComponent(string(ref.path))}`}>
                              {string(ref.path)}
                            </a>
                          ) : (
                            <span>{i18n.t('Evidence reference unavailable')}</span>
                          )}
                          <span className="mono muted">{string(ref.sha256)}</span>
                        </li>
                      ))}
                  </ul>
                  {items(entry.evidence).length > 5 && (
                    <p className="muted">
                      {i18n.t('More evidence is available in the original record.')}
                    </p>
                  )}
                </>
              ) : (
                <p className="muted">
                  {i18n.t('Unrecognized note format. Inspect the original record.')}
                </p>
              )}
              <OriginalRecord value={note} label="Original note record" />
            </li>
          );
        })}
      </ol>
      {notes.length > shown.length && (
        <p className="muted">
          {i18n.t('{shown} / {total} shown · {omitted} omitted', {
            shown: shown.length,
            total: notes.length,
            omitted: notes.length - shown.length,
          })}
        </p>
      )}
      <OriginalRecord value={task.notes} label="All original notes" />
    </details>
  );
}

export function ReadinessContext({
  snapshot,
  task,
}: {
  readonly snapshot: Snapshot;
  readonly task: Task;
}) {
  const i18n = useI18n();
  const readiness = recordedReadiness(snapshot, task);
  const collisions = currentCollection(snapshot, readiness?.collisions, task.id);
  const dependencies = currentCollection(snapshot, readiness?.unmet_dependencies, task.id);
  const submission = record(readiness?.submission);
  const peers = snapshotIndex(snapshot).tasks;
  return (
    <details className="readiness-context">
      <summary>
        {i18n.t('Readiness context')}
        {collisions && collisions.total > 0 && (
          <span className="badge status-blocked">
            {i18n.t('{count} scope conflicts', { count: collisions.total })}
          </span>
        )}
      </summary>
      {!readiness ? (
        <p className="muted">{i18n.t('Current readiness context is unavailable.')}</p>
      ) : (
        <>
          <p className="muted">
            {i18n.t(
              'A fresh revalidation and current authority are required before claiming work.',
            )}
          </p>
          <dl className="fields">
            <div>
              <dt>{i18n.t('Claim authorization')}</dt>
              <dd>{i18n.t('Not assessed')}</dd>
            </div>
            <div>
              <dt>{i18n.t('Revalidation')}</dt>
              <dd>
                {i18n.t(
                  readiness.required_revalidation === 'fresh_receipt_required'
                    ? 'Fresh receipt required'
                    : readiness.required_revalidation === 'not_applicable'
                      ? 'Not applicable to this task'
                      : readiness.required_revalidation === 'not_assessed'
                        ? 'Not assessed'
                        : 'Unknown',
                )}
              </dd>
            </div>
            <div>
              <dt>{i18n.t('Peer agreement')}</dt>
              <dd>{i18n.t('Unknown')}</dd>
            </div>
          </dl>
          {submission.status === 'stale' && (
            <p className="notice">
              {i18n.t('Submitted source has changed. Revalidation is required.')}
            </p>
          )}
          {string(submission.reason) && <NoteText value={submission.reason} />}
          {string(submission.next_action) && <NoteText value={submission.next_action} />}
          <h4>{i18n.t('Unmet dependencies')}</h4>
          {dependencies ? (
            dependencies.total ? (
              <ul>
                {dependencies.items.map((value, index) => {
                  const id = string(value);
                  return (
                    <li key={index}>
                      <a href={`#task/${encodeURIComponent(id)}`}>{id}</a>
                      {peers.get(id) && (
                        <span className="badge">{i18n.statusLabel(peers.get(id)!.status)}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="muted">{i18n.t('No unmet dependencies recorded')}</p>
            )
          ) : (
            <p className="muted">{i18n.t('Unknown')}</p>
          )}
          <Omitted collection={dependencies} />
          <h4>{i18n.t('Scope conflicts')}</h4>
          {collisions ? (
            collisions.total ? (
              <ul>
                {collisions.items.map((value, index) => {
                  const row = record(value),
                    id = string(row.task_id),
                    reasons = currentCollection(snapshot, row.reasons, id);
                  return (
                    <li key={index}>
                      <a href={`#task/${encodeURIComponent(id)}`}>{id}</a> ·{' '}
                      <a href={workloadHref('all', string(row.owner))}>
                        {string(row.owner) || i18n.t('Unassigned')}
                      </a>
                      {peers.get(id) && (
                        <span className="badge">{i18n.statusLabel(peers.get(id)!.status)}</span>
                      )}
                      {reasons ? (
                        reasons.items.map((reason, ordinal) => (
                          <div key={ordinal}>
                            <NoteText value={record(reason).reason} />
                            <ShortenedSummary value={reason} />
                          </div>
                        ))
                      ) : (
                        <p>{i18n.t('Unknown')}</p>
                      )}
                      <Omitted collection={reasons} />
                      <OriginalRecord value={row.scope_reference} label="Scope reference" />
                      <OriginalRecord
                        value={row.selected_scope_reference}
                        label="This task scope reference"
                      />
                      <a href={`#task/${encodeURIComponent(id)}`}>
                        {i18n.t('Scope and history for {id}', { id })}
                      </a>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="muted">{i18n.t('No claim-blocking scope conflicts recorded')}</p>
            )
          ) : (
            <p className="muted">{i18n.t('Unknown')}</p>
          )}
          <Omitted collection={collisions} />
        </>
      )}
      <OriginalRecord value={task.readiness} label="Original readiness context" />
    </details>
  );
}

export function GoalCoordination({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n();
  const brief = record(snapshot.brief);
  const goal = goalTaskIds(snapshot);
  const attention =
    brief.brief_version === 1 && currentReference(snapshot, brief.reference)
      ? currentCollection(snapshot, brief.attention, undefined, 50)
      : null;
  const relevant =
    attention?.items
      .map(record)
      .filter(
        (row) =>
          ['collision', 'question', 'transfer'].includes(string(row.kind)) &&
          (row.kind === 'question' || items(row.task_ids).some((id) => goal?.has(string(id)))),
      ) || [];
  const notes = goal
    ? snapshot.tasks.filter((task) => goal.has(task.id) && items(task.notes).length > 0)
    : [];
  return (
    <details className="goal-coordination">
      <summary>{i18n.t('Current goal coordination')}</summary>
      {!goal || !attention ? (
        <p className="muted">{i18n.t('Current coordination summary is unavailable.')}</p>
      ) : (
        <>
          {!relevant.length && (
            <p className="muted">{i18n.t('No coordination items in the supplied summary.')}</p>
          )}
          <ul>
            {relevant.map((row, index) => (
              <li key={index}>
                <p>
                  {i18n.t(
                    row.kind === 'collision'
                      ? 'Scope conflicts'
                      : row.kind === 'question'
                        ? 'Question'
                        : 'Personnel handoffs',
                  )}{' '}
                  · {string(row.entity_id)}
                </p>
                <NoteText value={row.reason} />
                <p>
                  {i18n.t('Responsible actor')}:{' '}
                  {string(row.responsible_actor_id) || i18n.t('Unknown')}
                </p>
                <NoteText value={row.next_action} />
                <ShortenedSummary value={row} />
                <div className="coordination-links">
                  {items(row.task_ids)
                    .map(string)
                    .filter(Boolean)
                    .map((id) => (
                      <a key={id} href={`#task/${encodeURIComponent(id)}`}>
                        {id}
                      </a>
                    ))}
                </div>
                {row.kind === 'question' && (
                  <a href={`#question/${encodeURIComponent(string(row.entity_id))}`}>
                    {i18n.t('Open question')}
                  </a>
                )}
                <OriginalRecord value={row} label="Original coordination context" />
              </li>
            ))}
          </ul>
          {attention.omitted > 0 && (
            <p className="muted">
              {i18n.t(
                'The program summary omits {count} records. Additional coordination may be omitted.',
                { count: attention.omitted },
              )}
            </p>
          )}
        </>
      )}
      {notes.length > 0 && (
        <>
          <h4>{i18n.t('Authored coordination notes')}</h4>
          <ul>
            {notes.slice(0, 20).map((task) => (
              <li key={task.id}>
                <a href={`#task/${encodeURIComponent(task.id)}`}>
                  {task.id} · {task.title}
                </a>
              </li>
            ))}
          </ul>
          {notes.length > 20 && (
            <p className="muted">
              {i18n.t('{shown} / {total} shown · {omitted} omitted', {
                shown: 20,
                total: notes.length,
                omitted: notes.length - 20,
              })}
            </p>
          )}
        </>
      )}
      <OriginalRecord value={brief.attention} label="Original coordination summary" />
    </details>
  );
}
