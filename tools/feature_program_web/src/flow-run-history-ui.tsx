import { useI18n } from './i18n';
import type { Json } from './model';
import { runHistory } from './flow-run-history';
import { processResultLabels, publicationLabels } from './flow-execution';
import { ExecutionFacts } from './flow-execution-ui';

export function RunHistory({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  if (value === undefined) return null;
  const history = runHistory(value);
  if (!history) return <p className="lane-warning">{i18n.t('Run history is unavailable.')}</p>;
  if (!history.total) return null;
  const failed = history.rows.filter((row) => row.execution?.reportedResult === 'failed').length;
  const unknown = history.rows.filter(
    (row) => !row.execution || row.execution.reportedResult === 'unknown',
  ).length;
  const passed = history.rows.filter((row) => row.execution?.reportedResult === 'passed').length;
  return (
    <details className="lane-details local-run-history">
      <summary>
        {i18n.t('Run history')} · {i18n.number(history.total)}
        {failed > 0 && <> · {i18n.t('{count} failed results', { count: failed })}</>}
        {unknown > 0 && <> · {i18n.t('{count} unknown results', { count: unknown })}</>}
        {passed > 0 && <> · {i18n.t('{count} passed results', { count: passed })}</>}
      </summary>
      <ol className="flow-details-list">
        {history.rows.map((row) => (
          <li key={row.id}>
            {row.execution ? (
              <>
                <div className="lane-heading">
                  <strong className="mono">{row.execution.runId}</strong>
                  <span>
                    {i18n.t(
                      processResultLabels[row.execution.processResult] ||
                        row.execution.processResult,
                    )}
                  </span>
                  <span className="badge">{i18n.human(row.execution.reportedResult)}</span>
                  {row.execution.reused && (
                    <span className="muted">{i18n.t('Reused execution evidence')}</span>
                  )}
                  {row.execution.captureStatus !== 'complete' && (
                    <span className="lane-warning">{i18n.t('Capture incomplete')}</span>
                  )}
                </div>
                <p className="muted">
                  {i18n.t(
                    publicationLabels[row.execution.publication] || row.execution.publication,
                  )}
                </p>
                <ExecutionFacts execution={row.execution} />
              </>
            ) : (
              <p className="lane-warning">{i18n.t('Execution capture is unavailable.')}</p>
            )}
            {row.nextAction && <p>{i18n.t(row.nextAction)}</p>}
            {row.reasons.map((reason, index) => (
              <p className="lane-warning" key={index}>
                {i18n.warning(reason)}
              </p>
            ))}
          </li>
        ))}
      </ol>
      {history.omitted > 0 && (
        <p className="muted">
          {i18n.t('{count} runs omitted; outcome counts are partial.', { count: history.omitted })}
        </p>
      )}
    </details>
  );
}
