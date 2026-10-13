import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from './App';
import { PREFERENCE_KEY, preferredLanguage, translationTools } from './i18n';
import { parseSnapshot } from './model';

const localizedFixture = () =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'Synthetic language fixture',
    communication_language: 'en',
    integrity: { status: 'fresh', warnings: [] },
    tasks: [
      {
        id: 'A1',
        title: 'Overview',
        status: 'planned',
        owner: null,
        attempts: [],
        source: { text: 'Unknown' },
        delivery: { status: 'unrecorded' },
      },
    ],
    questions: [
      {
        id: 'Q1',
        version: 1,
        prompt: '어떤 검토 범위가 필요한가요?',
        why: '화면과 키보드 흐름을 확인하기 위한 질문입니다.',
        requested_decision: '검토 범위를 선택해주세요.',
        status: 'open',
        answer: null,
        options: ['데스크톱과 키보드', '좁은 화면도 포함'],
      },
    ],
    objective: {
      title: 'Keep authored content unchanged',
      status: 'active',
      criteria: ['Keep local records'],
      decision_owner: 'director',
      next_action_owner: 'director',
    },
  });
const sessionLoader = async () => ({ responsesEnabled: true, nonce: 'synthetic' });

describe('interface language and immutable recorded prose', () => {
  it('localizes known supplied question-state diagnostics without translating unknown source text', () => {
    const ko = translationTools('ko');
    expect(ko.warning('Recorded decision state: answered')).toBe('질문 상태: 답변 저장됨');
    expect(ko.warning('Recorded decision state: acknowledged')).toBe('질문 상태: 디렉터 확인 완료');
    expect(ko.warning('Recorded decision state: future_state')).toContain('future state');
    expect(ko.display('acknowledged')).toBe('acknowledged');
    expect(translationTools('en').warning('Recorded decision state: acknowledged')).toBe(
      'Recorded decision state: acknowledged',
    );
  });
  it('uses saved preference before program language, then browser locale, with English fallback', () => {
    expect(preferredLanguage('en', 'ko', 'ko-KR')).toBe('en');
    expect(preferredLanguage('ko', 'en', 'en-GB')).toBe('ko');
    expect(preferredLanguage(null, 'ko', 'en-GB')).toBe('ko');
    expect(preferredLanguage(null, 'en', 'ko-KR')).toBe('en');
    expect(preferredLanguage(null, null, 'ko-KR')).toBe('ko');
    expect(preferredLanguage('invalid', 'invalid', 'fr-FR')).toBe('en');
  });
  it('localizes only interface fallbacks, dates and numbers while preserving recorded strings', () => {
    const tools = translationTools('ko');
    expect(tools.t('Reviewed complete')).toBe('검토 완료');
    expect(tools.display(null)).toBe('알 수 없음');
    expect(tools.display('Unknown')).toBe('Unknown');
    expect(tools.number(1234)).toBe('1,234');
    expect(tools.date('2026-10-13T00:00:00Z')).toContain('2026');
    expect(tools.warning('Frozen evidence changed: evidence/example.txt')).toContain(
      'evidence/example.txt',
    );
  });
  it('preserves replacement-like characters in recorded identities and warning paths in both languages', () => {
    const identity = "peer$&$$$`$'worker";
    for (const locale of ['en', 'ko'] as const) {
      const tools = translationTools(locale);
      expect(tools.t('Current recorded owner / contact: {owner}', { owner: identity })).toContain(
        identity,
      );
      expect(tools.warning(`Frozen evidence changed: evidence/${identity}.txt`)).toContain(
        `evidence/${identity}.txt`,
      );
      expect(
        tools.t('{title}, {done} of {total} reviewed', { title: '{total}', done: 0, total: 2 }),
      ).toContain('{total}');
    }
  });
  it('switches and persists interface language without translating an authored goal or task title', async () => {
    const user = userEvent.setup();
    const loader = async () => localizedFixture();
    const view = render(<App loader={loader} sessionLoader={sessionLoader} />);
    await screen.findByRole('heading', { name: 'Overview' });
    await user.selectOptions(screen.getByLabelText('Language'), 'ko');
    expect(screen.getByRole('heading', { name: '개요' })).toBeDefined();
    expect(screen.getByText('Keep authored content unchanged')).toBeDefined();
    expect(localStorage.getItem(PREFERENCE_KEY)).toBe('ko');
    expect(document.documentElement.lang).toBe('ko');
    expect(document.title).toBe('작업 프로그램');
    await act(async () => {
      window.location.hash = '#work';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    expect(screen.getByText('Overview')).toBeDefined();
    expect(screen.getAllByText('미배정').length).toBeGreaterThan(0);
    view.unmount();
    render(<App loader={loader} sessionLoader={sessionLoader} />);
    await screen.findByLabelText('언어');
    expect(screen.getByDisplayValue('한국어')).toBeDefined();
  });
  it('keeps a Korean answer draft and question text unchanged when switching languages', async () => {
    const user = userEvent.setup();
    render(<App loader={async () => localizedFixture()} sessionLoader={sessionLoader} />);
    await screen.findByRole('heading', { name: 'Overview' });
    await act(async () => {
      window.location.hash = '#question/Q1';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    await user.type(
      screen.getByLabelText('Your answer'),
      '좁은 화면과 키보드 흐름을 확인해주세요.',
    );
    await user.selectOptions(screen.getByLabelText('Language'), 'ko');
    expect(screen.getByLabelText('내 답변')).toBeDefined();
    expect(screen.getByDisplayValue('좁은 화면과 키보드 흐름을 확인해주세요.')).toBeDefined();
    expect(screen.getByRole('heading', { name: '어떤 검토 범위가 필요한가요?' })).toBeDefined();
    await user.selectOptions(screen.getByLabelText('언어'), 'en');
    expect(screen.getByLabelText('Your answer')).toBeDefined();
    expect(screen.getByDisplayValue('좁은 화면과 키보드 흐름을 확인해주세요.')).toBeDefined();
  });
  it('takes a program communication language when no interface preference is saved', async () => {
    render(
      <App
        loader={async () => ({ ...localizedFixture(), communication_language: 'ko' })}
        sessionLoader={sessionLoader}
      />,
    );
    await screen.findByRole('heading', { name: '개요' });
    expect(screen.getByText('Keep authored content unchanged')).toBeDefined();
    expect(localStorage.getItem(PREFERENCE_KEY)).toBeNull();
  });
});
