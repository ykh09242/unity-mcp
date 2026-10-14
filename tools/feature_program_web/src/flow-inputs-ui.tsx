import type { Json } from './model';
import { useI18n } from './i18n';
import { inputSummary, inputGroupLabels } from './flow-inputs';
import type { InputCounts } from './flow-inputs';

function Counts({ counts }: { readonly counts: InputCounts }) {
  const i18n = useI18n();
  const labels = [
    ['known', 'Known inputs'],
    ['changed', 'Changed inputs'],
    ['missing', 'Missing inputs'],
    ['unknown', 'Unknown inputs'],
  ] as const;
  return (
    <span className="flow-counts">
      {labels.map(([key, label]) => {
        const count = counts[key];
        return count > 0 ? (
          <span key={key}>
            {i18n.t(label)} · {i18n.number(count)}
          </span>
        ) : null;
      })}
    </span>
  );
}
export function CheckInputs({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  if (value === undefined) return null;
  const summary = inputSummary(value);
  if (!summary)
    return <p className="lane-warning">{i18n.t('Check input summary is unavailable.')}</p>;
  return (
    <section className="flow-summary input-summary" aria-label={i18n.t('Check inputs')}>
      <div className="lane-heading">
        <strong>{i18n.t('Check inputs')}</strong>
        <span className="badge">{i18n.human(summary.status)}</span>
        {summary.applicability !== 'current' && summary.applicability !== summary.status && (
          <span className="lane-warning">{i18n.human(summary.applicability)}</span>
        )}
        {summary.completeness === 'unknown' && (
          <span className="lane-warning">{i18n.t('Input coverage unknown')}</span>
        )}
      </div>
      <Counts counts={summary.counts} />
      {summary.nextAction && <p className="flow-next-action">{i18n.t(summary.nextAction)}</p>}
      {summary.reasons[0] && <p className="lane-warning">{i18n.warning(summary.reasons[0])}</p>}
      <details className="lane-details">
        <summary>{i18n.t('Input details')}</summary>
        <ul className="flow-details-list">
          {summary.groups.map((group) => (
            <li key={group.id}>
              <strong>{i18n.t(inputGroupLabels[group.id] || group.id)}</strong> ·{' '}
              {i18n.human(group.status)}
              <Counts counts={group.counts} />
            </li>
          ))}
        </ul>
        {summary.reasons.length > 1 && (
          <ul>
            {summary.reasons.slice(1).map((reason, index) => (
              <li key={index}>{i18n.warning(reason)}</li>
            ))}
          </ul>
        )}
        {summary.omitted > 0 && (
          <p className="muted">
            {i18n.t('{count} input groups omitted.', { count: summary.omitted })}
          </p>
        )}
      </details>
    </section>
  );
}
