import { useState } from 'react';
import { useI18n } from './i18n';
import { items, record, records, string } from './model';
import type { Json, Snapshot } from './model';
import { currentCollection, currentReference } from './readiness';
import { OriginalRecord } from './readiness-ui';
import { usePresentation } from './presentation-ui';
import { CoverageObservations, HistoryReferences } from './history-ui';

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
  integrity: 'Source integrity',
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

function Locator({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  const ref = record(value);
  return (
    <p className="mono muted source-locator">
      {string(ref.path) || i18n.t('Unknown')}
      {string(ref.pointer)}
      {string(ref.revision) && <> · {string(ref.revision)}</>}
      {string(ref.sha256) && <> · {string(ref.sha256)}</>}
    </p>
  );
}

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
    <p className="muted">
      {i18n.t('Summary text is shortened. Inspect the referenced source for full context.')}
    </p>
  ) : null;
}

export function ProgramAttention({ snapshot }: { readonly snapshot: Snapshot }) {
  const i18n = useI18n(),
    presentation = usePresentation();
  const [expanded, setExpanded] = useState(false);
  const brief = currentBrief(snapshot);
  const attention = brief ? currentCollection(snapshot, brief.attention, undefined, 50) : null;
  const shown = attention?.items.slice(0, expanded ? 50 : 5) || [];
  const totals = record(brief?.attention_totals_by_kind);
  return (
    <section className="program-attention">
      <h3>
        {i18n.t('Needs attention')}
        {attention && <span className="muted"> · {i18n.number(attention.total)}</span>}
      </h3>
      {!attention ? (
        <p className="muted">
          {i18n.t('Attention summary is unavailable. Recorded work remains inspectable.')}
        </p>
      ) : (
        <>
          {attention.total === 0 && (
            <p className="muted">{i18n.t('No current attention items recorded.')}</p>
          )}
          <ul className="attention-items">
            {shown.map((value, index) => {
              const row = record(value);
              const source = record(row.reference);
              const recoveryReference =
                row.kind === 'recovery' &&
                Boolean(string(source.path)) &&
                source.revision === undefined &&
                source.pointer === undefined;
              const valid =
                (currentReference(snapshot, row.reference) || recoveryReference) &&
                row.observed_revision === snapshot.revision;
              const impact = currentCollection(snapshot, row.criterion_impact);
              const generatedAction =
                Boolean(systemActions[string(row.kind)]) &&
                systemActions[string(row.kind)] === row.next_action &&
                (row.kind !== 'objective' ||
                  (row.entity_id === record(snapshot.objective).id &&
                    !Object.hasOwn(record(snapshot.objective), 'next_action')));
              return (
                <li key={`${string(row.kind)}:${string(row.entity_id)}:${index}`}>
                  <div className="attention-heading">
                    <strong>
                      {i18n.t(kindLabels[string(row.kind)] || string(row.kind) || 'Unknown')}
                    </strong>
                    <span className="mono">{string(row.entity_id)}</span>
                  </div>
                  {!valid ? (
                    <p>{i18n.t('Current source binding is unavailable.')}</p>
                  ) : (
                    <>
                      <p className="prose">{i18n.warning(row.reason || 'Unknown')}</p>
                      <p className="prose">
                        {generatedAction
                          ? i18n.t(string(row.next_action))
                          : i18n.display(row.next_action, 'No next action recorded')}
                      </p>
                      <p className="muted">
                        {i18n.t('Responsible actor')}:{' '}
                        {presentation.actor(row.responsible_actor_id) || i18n.t('Unknown')}
                      </p>
                      <Shortened value={row} />
                      <div className="coordination-links">
                        {row.kind === 'question' && string(row.entity_id) && (
                          <a href={`#question/${encodeURIComponent(string(row.entity_id))}`}>
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
                  <details>
                    <summary>{i18n.t('Context and source')}</summary>
                    <Locator value={row.reference} />
                    <p className="muted">
                      {i18n.t('Criterion impact includes explicit links only.')}
                    </p>
                    {impact ? (
                      <>
                        <p>
                          {i18n.t('Explicit criterion links')}: {i18n.number(impact.total)}
                        </p>
                        <ul>
                          {impact.items.map((item, n) => (
                            <li key={n}>
                              {i18n.t('Criterion')}{' '}
                              {typeof record(item).ordinal === 'number'
                                ? i18n.number(Number(record(item).ordinal) + 1)
                                : i18n.t('Unknown')}{' '}
                              <span className="mono muted">{string(record(item).text_sha256)}</span>
                            </li>
                          ))}
                        </ul>
                        <Omission shown={impact.shown} total={impact.total} />
                      </>
                    ) : (
                      <p>{i18n.t('Unknown')}</p>
                    )}
                    <OriginalRecord value={row} />
                  </details>
                </li>
              );
            })}
          </ul>
          {attention.items.length > 5 && (
            <button onClick={() => setExpanded((value) => !value)}>
              {i18n.t(
                expanded ? 'Show fewer attention items' : 'Show all supplied attention items',
              )}
            </button>
          )}
          {shown.length < attention.shown && (
            <p className="muted">
              {i18n.t('Showing {visible} of {supplied} supplied items', {
                visible: shown.length,
                supplied: attention.shown,
              })}
            </p>
          )}
          <Omission shown={attention.shown} total={attention.total} />
        </>
      )}
      <details>
        <summary>{i18n.t('Summary scope and source')}</summary>
        <p>{i18n.t('Advisory context does not authorize execution or establish acceptance.')}</p>
        <dl className="content-coverage">
          {Object.entries(totals)
            .filter(
              ([, value]) => typeof value === 'number' && Number.isSafeInteger(value) && value > 0,
            )
            .map(([kind, value]) => (
              <div key={kind}>
                <dt>{i18n.t(kindLabels[kind] || kind)}</dt>
                <dd>{i18n.display(value)}</dd>
              </div>
            ))}
        </dl>
        <Locator value={brief?.reference} />
        <OriginalRecord value={snapshot.brief} label="Original program summary" />
      </details>
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
  const latestAnnotation = compatible ? records(coverage.history).at(-1) : undefined;
  const acceptance = compatible ? record(coverage.recorded_objective_acceptance) : {};
  return (
    <div className="criterion-coverage">
      {latestAnnotation && latestAnnotation.freshness !== 'current' && (
        <p className="notice">
          {i18n.t('Latest criterion annotation')}: {i18n.human(latestAnnotation.freshness)}.{' '}
          {i18n.t('Earlier links are not substituted for the current observation.')}
        </p>
      )}
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
                      <p className="muted">
                        {i18n.t('Linkage')}: {i18n.human(criterion.linkage)} · {i18n.t('Knowledge')}
                        : {i18n.human(criterion.knowledge)}
                      </p>
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
                  <details>
                    <summary>{i18n.t('Evidence and source')}</summary>
                    <Locator value={criterion.reference} />
                    {records(full?.links).map((link, n) => (
                      <div key={n}>
                        <a href={`#task/${encodeURIComponent(string(link.task_id))}`}>
                          {presentation.taskReference(link.task_id)}
                        </a>
                        <ul>
                          {items(link.paths).map((path, k) => (
                            <li className="mono" key={k}>
                              {string(path)}
                            </li>
                          ))}
                        </ul>
                        {records(link.records).map((entry, k) => (
                          <HistoryReferences key={k} value={entry.receipt} />
                        ))}
                      </div>
                    ))}
                    <CoverageObservations value={full?.recorded_observations} />
                    <OriginalRecord value={full || criterion} label="Original criterion context" />
                  </details>
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
      <details>
        <summary>{i18n.t('Coverage source history')}</summary>
        {records(coverage.history).map((entry, index) => (
          <article key={index}>
            <p>
              {i18n.display(entry.id)} · {i18n.human(entry.freshness)} ·{' '}
              {i18n.date(entry.recorded_at)}
            </p>
            <ul>
              {items(entry.reasons).map((reason, n) => (
                <li key={n}>{i18n.display(reason)}</li>
              ))}
            </ul>
            <Locator value={entry.receipt} />
            <OriginalRecord value={entry} />
          </article>
        ))}
        {!records(coverage.history).length && <p>{i18n.t('No coverage annotation recorded.')}</p>}
        <p>
          {i18n.t('Recorded objective acceptance')}: {i18n.human(acceptance.status)}
        </p>
        <p>
          {i18n.t('Source freshness')}: {i18n.human(acceptance.source_freshness)}
        </p>
        <p>
          {i18n.t('Head freshness')}: {i18n.human(acceptance.head_freshness)}
        </p>
        <p>
          {i18n.t('Integrated review')}:{' '}
          {i18n.human(compatible ? record(coverage.integrated_review).status : undefined)}
        </p>
        <OriginalRecord value={snapshot.coverage} label="Original coverage" />
      </details>
    </div>
  );
}
