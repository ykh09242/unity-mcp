import { useI18n } from './i18n';
import type { Json } from './model';
import { capturedExecution, processResultLabels, publicationLabels } from './flow-execution';
import type { CapturedExecution } from './flow-execution';

export function ExecutionFacts({ execution }: { readonly execution: CapturedExecution }) {
  const i18n = useI18n();
  return (
    <dl className="flow-facts">
      <div>
        <dt>{i18n.t('Run')}</dt>
        <dd className="mono">{execution.runId}</dd>
      </div>
      <div>
        <dt>{i18n.t('Execution phase')}</dt>
        <dd>{i18n.human(execution.phase)}</dd>
      </div>
      {execution.startedAt && (
        <div>
          <dt>{i18n.t('Started')}</dt>
          <dd>{i18n.date(execution.startedAt)}</dd>
        </div>
      )}
      {execution.endedAt && (
        <div>
          <dt>{i18n.t('Ended')}</dt>
          <dd>{i18n.date(execution.endedAt)}</dd>
        </div>
      )}
      {execution.outputs.map((output) => (
        <div key={output.stream}>
          <dt>{output.stream}</dt>
          <dd>
            {i18n.t('{retained} / {seen} bytes retained', {
              retained: output.retained,
              seen: output.seen,
            })}
            {output.truncated && <> · {i18n.t('Output truncated')}</>}
          </dd>
        </div>
      ))}
    </dl>
  );
}
export function LocalExecution({ value }: { readonly value: Json | undefined }) {
  const i18n = useI18n();
  if (value === undefined) return null;
  const execution = capturedExecution(value);
  if (!execution)
    return <p className="lane-warning">{i18n.t('Execution capture is unavailable.')}</p>;
  return (
    <section className="flow-summary execution-summary" aria-label={i18n.t('Captured execution')}>
      <div className="lane-heading">
        <strong>
          {i18n.t(execution.reused ? 'Reused execution evidence' : 'Captured execution')}
        </strong>
        <span>
          {i18n.t(processResultLabels[execution.processResult] || execution.processResult)}
        </span>
        <span className="badge">{i18n.human(execution.reportedResult)}</span>
        <span className={execution.captureStatus === 'complete' ? 'muted' : 'lane-warning'}>
          {i18n.t(
            execution.captureStatus === 'complete' ? 'Capture complete' : 'Capture incomplete',
          )}
        </span>
      </div>
      <p className={execution.publication === 'recorded' ? 'muted' : 'lane-warning'}>
        {i18n.t(publicationLabels[execution.publication] || execution.publication)}
      </p>
      {execution.observationDraft === 'unavailable' && (
        <p className="lane-warning">{i18n.t('Recording preparation needs attention')}</p>
      )}
      {execution.nextAction && <p className="flow-next-action">{i18n.t(execution.nextAction)}</p>}
      {execution.reasons[0] && <p className="lane-warning">{i18n.warning(execution.reasons[0])}</p>}
      <p className={execution.artifacts.missing > 0 ? 'lane-warning' : 'muted'}>
        {i18n.t('Evidence: {captured} / {expected} artifacts captured', execution.artifacts)}
        {execution.artifacts.missing > 0 && (
          <> · {i18n.t('{count} missing artifacts', { count: execution.artifacts.missing })}</>
        )}
        {execution.outputs.some((output) => output.truncated) && (
          <> · {i18n.t('Output truncated')}</>
        )}
      </p>
      <details className="lane-details">
        <summary>{i18n.t('Execution details')}</summary>
        <ExecutionFacts execution={execution} />
        {execution.reasons.length > 1 && (
          <ul>
            {execution.reasons.slice(1).map((reason, index) => (
              <li key={index}>{i18n.warning(reason)}</li>
            ))}
          </ul>
        )}
      </details>
    </section>
  );
}
