import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from './App';
import { LanguageProvider, PREFERENCE_KEY, translationTools } from './i18n';
import { StaffingHistory } from './history-ui';
import { parseSnapshot } from './model';

const locales = [
  {
    locale: 'en',
    model: 'Model',
    reasoning: 'Reasoning',
    provenance: 'Provenance',
    observed: 'Last observed',
    absent: 'Not recorded',
    unverified: 'Provenance unverified',
    status: 'Status not verified',
    comparison: 'Comparison unavailable',
    assignment: 'Recorded assignment',
    current: 'Current settings',
  },
  {
    locale: 'ko',
    model: '모델',
    reasoning: '추론 수준',
    provenance: '설정 출처',
    observed: '마지막 관찰',
    absent: '기록 없음',
    unverified: '설정 출처 미확인',
    status: '상태 미확인',
    comparison: '비교 불가',
    assignment: '배정 당시 기록',
    current: '현재 설정',
  },
] as const;

function snapshot() {
  return parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'metadata-truth',
    integrity: { status: 'fresh', warnings: [] },
    actors: [
      {
        id: 'legacy-root',
        role: 'director',
        parent_id: null,
        model_id: null,
        reasoning_effort: null,
        provenance: 'unknown',
        staffing_status: 'active',
        observed_status: 'unknown',
        observed_at: '2026-10-14T00:06:30Z',
        history: [],
      },
      {
        id: 'current-person',
        role: 'worker',
        parent_id: null,
        model_id: 'current-model',
        reasoning_effort: 'high',
        provenance: 'verified_launch',
        staffing_status: 'active',
        observed_status: 'running',
        observed_at: '2026-10-14T00:06:30Z',
        history: [],
      },
    ],
    tasks: [
      {
        id: 'T1',
        title: 'Inspect historical configuration',
        status: 'active',
        owner: 'legacy-root',
        attempts: [
          {
            number: 1,
            owner: 'missing-historical-person',
            assignment: {
              actor_id: 'missing-historical-person',
              model_id: 'archived-model',
              reasoning_effort: null,
              provenance: 'unknown',
              role: 'worker',
            },
          },
        ],
      },
    ],
  });
}

describe('truthful personnel metadata', () => {
  it.each(locales)(
    'distinguishes absent and unverified metadata without translating technical or authored values in $locale',
    (labels) => {
      // Given
      const tools = translationTools(labels.locale);
      // When
      const missing = tools.personnel('model_id', null);
      const provenance = tools.personnel('provenance', 'unknown');
      const status = tools.personnel('observed_status', 'unknown');
      // Then
      expect(missing).toBe(labels.absent);
      expect(provenance).toBe(labels.unverified);
      expect(status).toBe(labels.status);
      expect(tools.personnel('model_id', 'vendor/model_unknown')).toBe('vendor/model_unknown');
      expect(tools.personnel('provenance', 'future_provenance')).toBe('future provenance');
      expect(tools.display('Unknown')).toBe('Unknown');
      expect(tools.display('알 수 없음')).toBe('알 수 없음');
    },
  );
  it.each(locales)(
    'uses consistent settings labels and retains an unknown status observation timestamp in $locale',
    async (labels) => {
      // Given
      localStorage.setItem(PREFERENCE_KEY, labels.locale);
      window.history.replaceState(null, '', '#team');
      render(<App loader={async () => snapshot()} />);
      // When
      const heading = await screen.findByRole('heading', { name: 'legacy-root' });
      const actor = heading.closest('article');
      if (!actor) throw new Error('Actor missing');
      // Then
      expect(within(actor).getByText(labels.model).nextElementSibling?.textContent).toBe(
        labels.absent,
      );
      expect(within(actor).getByText(labels.reasoning).nextElementSibling?.textContent).toBe(
        labels.absent,
      );
      expect(within(actor).queryByText(labels.provenance)).toBeNull();
      const observation = within(actor).getByText(labels.observed).nextElementSibling?.textContent;
      expect(observation).toContain(labels.status);
      expect(observation).toContain('2026');
      const settings = document.querySelector('.allocation-settings');
      expect(settings?.textContent).toContain(labels.absent);
      expect(settings?.textContent).not.toContain(labels.unverified);
    },
  );
  it.each(locales)(
    'keeps frozen historical metadata separate from unavailable current personnel in $locale',
    async (labels) => {
      // Given
      localStorage.setItem(PREFERENCE_KEY, labels.locale);
      window.history.replaceState(null, '', '#task/T1?full=1&section=history');
      render(<App loader={async () => snapshot()} />);
      // When
      const heading = await screen.findByRole('heading', { name: labels.assignment });
      const panel = heading.parentElement;
      if (!panel) throw new Error('Assignment panel missing');
      // Then
      expect(
        within(panel).getByText(labels.model, { selector: 'dt' }).nextElementSibling?.textContent,
      ).toBe('archived-model');
      expect(
        within(panel).getByText(labels.reasoning, { selector: 'dt' }).nextElementSibling
          ?.textContent,
      ).toBe(labels.absent);
      expect(within(panel).queryByText(labels.provenance)).toBeNull();
      expect(within(panel).queryByText('current-model')).toBeNull();
      expect(panel.querySelector('.history-comparison')).toBeNull();
    },
  );
  it.each(locales)(
    'retains recorded staffing history values while exposing absent settings in $locale',
    (labels) => {
      // Given
      localStorage.setItem(PREFERENCE_KEY, labels.locale);
      const actor = {
        model_id: 'current-model',
        history: [
          {
            decision: 'reuse',
            metadata: { model_id: 'archived-model', reasoning_effort: null, provenance: 'unknown' },
          },
        ],
      };
      // When
      const { container } = render(
        <LanguageProvider>
          <StaffingHistory actor={actor} />
        </LanguageProvider>,
      );
      // Then
      const event = container.querySelector<HTMLElement>('.history-events li');
      if (!event) throw new Error('Staffing event missing');
      expect(within(event).getByText(labels.model).nextElementSibling?.textContent).toBe(
        'archived-model',
      );
      expect(within(event).getByText(labels.reasoning).nextElementSibling?.textContent).toBe(
        labels.absent,
      );
      expect(within(event).queryByText(labels.provenance)).toBeNull();
      expect(actor.history[0]?.metadata).toEqual({
        model_id: 'archived-model',
        reasoning_effort: null,
        provenance: 'unknown',
      });
    },
  );
});
