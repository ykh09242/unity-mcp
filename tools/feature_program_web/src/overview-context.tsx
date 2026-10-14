import { useState } from 'react';
import { recordedCriterionResult } from './criterion-results';
import { useI18n } from './i18n';
import { items, record, records, string } from './model';
import type { Json, Snapshot } from './model';
import { currentCollection, currentReference } from './readiness';
import { usePresentation } from './presentation-ui';
import { CoverageObservations } from './history-ui';

const systemActions: Readonly<Record<string, string>> = {
  objective: 'Inspect the objective-completion prerequisites',
  integrity: 'Inspect original records and artifacts',
  recovery:
    'Inspect recovery-readiness; lock existence does not establish liveness or takeover authority',
  acceptance: 'Inspect the exact submission/review binding',
  delivery: 'Inspect frozen delivery inputs and provider facts',
  question: 'Inspect the saved answer, acknowledgement and application separately',
  transfer: 'Follow the recorded succession workflow',
  coverage: 'Inspect exact criterion and current membership/attempt bindings',
};

export function currentBrief(snapshot: Snapshot) {
  const brief = record(snapshot.brief);
  return brief.brief_version === 1 &&
    brief.revision === snapshot.revision &&
    brief.program_id === snapshot.program_id &&
    currentReference(snapshot, brief.reference)
    ? brief
    : null;
}

const kindLabels: Readonly<Record<string, string>> = {
  integrity: 'Data integrity',
  recovery: 'Recovery',
  question: 'Question',
  objective: 'Current objective',
  blocked: 'Blocked',
  collision: 'Scope conflicts',
  acceptance: 'Acceptance',
  review: 'Review',
  delivery: 'Delivery',
  dependency: 'Dependencies',
  revalidation: 'Revalidation',
  coverage: 'Criterion links',
  transfer: 'Personnel handoffs',
};

function Omission({ shown, total }: { readonly shown: number; readonly total: number }) {
  const i18n = useI18n();
  return shown < total ? (
    <p className="muted">
      {i18n.t('{shown} / {total} shown · {omitted} omitted', {
        shown,
        total,
        omitted: total - shown,
      })}
    </p>
  ) : null;
}

function Shortened({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  return items(record(value).truncated_fields).length ? (
    <p className="muted">{i18n.t('Summary text is shortened.')}</p>
  ) : null;
}

export function ProgramAttention({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n(),
    presentation = usePresentation();
  const [expanded, setExpanded] = useState(false);
  const brief = currentBrief(snapshot);
  const attention = brief ? currentCollection(snapshot, brief.attention, undefined, 50) : null;
  const relevant =
    attention?.items.filter((value) => {
      const row = record(value);
      return !(row.attention_kind === 'completion_reminder' && row.actionability === 'routine');
    }) || [];
  const shown = relevant.slice(0, expanded ? 50 : 5);
  if (attention && relevant.length === 0 && attention.omitted === 0)
    return (
      <p className="attention-empty muted">{i18n.t('No current attention items recorded.')}</p>
    );
  return (
    <section className="program-attention">
      <h3>
        {i18n.t('Needs attention')}
        {attention && (
          <span className="muted"> · {i18n.number(relevant.length + attention.omitted)}</span>
        )}
      </h3>
      {!attention ? (
        <p className="muted">
          {i18n.t('Attention summary is unavailable. Recorded work remains inspectable.')}
        </p>
      ) : (
        <>
          <ul className="attention-items">
            {shown.map((value, index) => {
              const row = record(value),
                source = record(row.reference),
                kind = string(row.kind),
                entityId = string(row.entity_id);
              const recoveryReference =
                kind === 'recovery' &&
                Boolean(string(source.path)) &&
                source.revision === undefined &&
                source.pointer === undefined;
              const valid =
                (currentReference(snapshot, row.reference) || recoveryReference) &&
                row.observed_revision === snapshot.revision;
              const generatedAction =
                Boolean(systemActions[kind]) &&
                systemActions[kind] === row.next_action &&
                (kind !== 'objective' ||
                  (row.entity_id === record(snapshot.objective).id &&
                    !Object.hasOwn(record(snapshot.objective), 'next_action')));
              const question =
                kind === 'question'
                  ? records(snapshot.questions).find((item) => item.id === entityId)
                  : undefined;
              const decisionStatus =
                generatedAction &&
                question &&
                row.reason === 'Recorded decision state: ' + string(question.status)
                  ? string(question.status)
                  : '';
              const meaningfulAction =
                string(row.next_action).trim() &&
                !(generatedAction && (kind === 'integrity' || kind === 'question')) &&
                row.next_action !== row.reason;
              return (
                <li key={`${kind}:${entityId}:${index}`}>
                  <div className="attention-heading">
                    <strong>{i18n.t(kindLabels[kind] || kind || 'Unknown')}</strong>
                    {entityId && !(kind === 'integrity' && /^\d+$/u.test(entityId)) && (
                      <span className="mono attention-entity">{entityId}</span>
                    )}
                    {valid &&
                      (decisionStatus ? (
                        <span className="badge">{i18n.human(decisionStatus)}</span>
                      ) : (
                        <span className="attention-reason">
                          {i18n.warning(row.reason || 'Unknown')}
                        </span>
                      ))}
                  </div>
                  {!valid ? (
                    <p>{i18n.t('Current source binding is unavailable.')}</p>
                  ) : (
                    <>
                      {meaningfulAction && (
                        <p className="attention-action">
                          {generatedAction
                            ? i18n.t(string(row.next_action))
                            : i18n.display(row.next_action)}
                        </p>
                      )}
                      <div className="attention-meta muted">
                        {string(row.responsible_actor_id) && (
                          <span>
                            {i18n.t('Responsible actor')}:{' '}
                            {presentation.actor(row.responsible_actor_id)}
                          </span>
                        )}
                        <div className="coordination-links">
                          {kind === 'question' && entityId && (
                            <a href={`#question/${encodeURIComponent(entityId)}`}>
                              {i18n.t('Open question')}
                            </a>
                          )}
                          {items(row.task_ids)
                            .map(string)
                            .filter(Boolean)
                            .slice(0, 5)
                            .map((id) => (
                              <a key={id} href={`#task/${encodeURIComponent(id)}`}>
                                {presentation.taskReference(id)}
                              </a>
                            ))}
                        </div>
                      </div>
                      <Shortened value={row} />
                      {items(row.task_ids).length > 5 && (
                        <details>
                          <summary>
                            {i18n.t('All related tasks')} ·{' '}
                            {i18n.number(items(row.task_ids).length)}
                          </summary>
                          <ul>
                            {items(row.task_ids)
                              .map(string)
                              .filter(Boolean)
                              .map((id) => (
                                <li key={id}>
                                  <a href={`#task/${encodeURIComponent(id)}`}>
                                    {presentation.taskReference(id)}
                                  </a>
                                </li>
                              ))}
                          </ul>
                        </details>
                      )}
                    </>
                  )}
                </li>
              );
            })}
          </ul>
          {relevant.length > 5 && (
            <button onClick={() => setExpanded((value) => !value)}>
              {i18n.t(
                expanded ? 'Show fewer attention items' : 'Show all supplied attention items',
              )}
            </button>
          )}
          {shown.length < relevant.length && (
            <p className="muted">
              {i18n.t('Showing {visible} of {supplied} supplied items', {
                visible: shown.length,
                supplied: relevant.length,
              })}
            </p>
          )}
          <Omission shown={relevant.length} total={relevant.length + attention.omitted} />
        </>
      )}
    </section>
  );
}

export function CriterionCoverage({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n(),
    presentation = usePresentation();
  const brief = currentBrief(snapshot);
  const criteria = brief
    ? currentCollection(snapshot, record(brief.objective).criteria, undefined, 50)
    : null;
  const coverage = record(snapshot.coverage);
  const compatible = coverage.objective_id === record(snapshot.objective).id;
  return (
    <div className="criterion-coverage">
      {!criteria ? (
        <>
          <p className="muted">{i18n.t('Criterion coverage is unavailable.')}</p>
          <ol>
            {items(record(snapshot.objective).criteria).map((text, index) => (
              <li key={index} className="prose">
                {i18n.display(text)}
              </li>
            ))}
          </ol>
        </>
      ) : (
        <>
          <p className="muted">{i18n.t('Declared links do not establish criterion acceptance.')}</p>
          {compatible && record(snapshot.objective).completion != null && (
            <p className="muted">
              {i18n.t('Historical completion record; current acceptance is not inferred.')}
            </p>
          )}
          <ol>
            {criteria.items.map((value, index) => {
              const criterion = record(value);
              const valid =
                currentReference(snapshot, criterion.reference) &&
                Number.isSafeInteger(criterion.ordinal) &&
                typeof criterion.text_sha256 === 'string';
              const contributors = valid
                ? currentCollection(snapshot, criterion.contributors)
                : null;
              const uncovered = valid ? currentCollection(snapshot, criterion.uncovered) : null;
              const full =
                compatible && valid
                  ? records(coverage.criteria).find(
                      (row) =>
                        record(row.criterion).ordinal === criterion.ordinal &&
                        record(row.criterion).text_sha256 === criterion.text_sha256,
                    )
                  : undefined;
              const completion =
                compatible && valid ? recordedCriterionResult(snapshot, criterion) : null;
              const completionSupplied =
                compatible && record(snapshot.objective).completion != null;
              const annotationAbsent =
                compatible &&
                coverage.legacy_absence === true &&
                criterion.linkage === 'unknown' &&
                criterion.knowledge === 'unknown';
              return (
                <li key={index}>
                  <p className="prose">
                    {valid
                      ? i18n.display(criterion.text)
                      : i18n.t('Criterion context is unavailable.')}
                  </p>
                  {!valid ? (
                    <p>{i18n.t('Current source binding is unavailable.')}</p>
                  ) : (
                    <>
                      {completion && (
                        <p className="recorded-criterion-result">
                          {i18n.t('Recorded completion result')}:{' '}
                          {i18n.human(completion.result.status)}
                        </p>
                      )}
                      {!completion && completionSupplied && (
                        <p className="muted">
                          {i18n.t('Recorded completion result is unavailable.')}
                        </p>
                      )}
                      {annotationAbsent ? (
                        !completion && (
                          <p className="muted">
                            {i18n.t('Explicit task links')}:{' '}
                            {i18n.t('No coverage annotation recorded.')}
                          </p>
                        )
                      ) : (
                        <p className="muted">
                          {i18n.t('Linkage')}: {i18n.human(criterion.linkage)} ·{' '}
                          {i18n.t('Knowledge')}: {i18n.human(criterion.knowledge)}
                        </p>
                      )}
                      {contributors ? (
                        <>
                          <div className="coordination-links">
                            {contributors.items.map(string).map((id) => (
                              <a key={id} href={`#task/${encodeURIComponent(id)}`}>
                                {presentation.taskReference(id)}
                              </a>
                            ))}
                          </div>
                          <Omission shown={contributors.shown} total={contributors.total} />
                        </>
                      ) : (
                        <p>{i18n.t('Contributor context is unavailable.')}</p>
                      )}
                      {uncovered ? (
                        <>
                          <ul>
                            {uncovered.items.map((entry, n) => (
                              <li key={n} className="prose">
                                {i18n.display(
                                  typeof entry === 'string' ? entry : record(entry).reason,
                                )}
                              </li>
                            ))}
                          </ul>
                          <Omission shown={uncovered.shown} total={uncovered.total} />
                        </>
                      ) : (
                        <p>{i18n.t('Uncovered context is unavailable.')}</p>
                      )}
                      <Shortened value={criterion} />
                    </>
                  )}
                  {records(full?.recorded_observations).length > 0 && (
                    <details>
                      <summary>{i18n.t('Recorded review findings')}</summary>
                      <CoverageObservations value={full?.recorded_observations} />
                    </details>
                  )}
                </li>
              );
            })}
          </ol>
          <Omission shown={criteria.shown} total={criteria.total} />
          {criteria.omitted > 0 && (
            <details>
              <summary>{i18n.t('All criteria')}</summary>
              <ol>
                {items(record(snapshot.objective).criteria).map((text, index) => (
                  <li key={index} className="prose">
                    {i18n.display(text)}
                  </li>
                ))}
              </ol>
            </details>
          )}
        </>
      )}
    </div>
  );
}
