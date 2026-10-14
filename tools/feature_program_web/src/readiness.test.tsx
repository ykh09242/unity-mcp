import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import peerWire from './fixtures/relate-peer.json?raw';
import readinessWire from './fixtures/relate-readiness.json?raw';
import blockedReviewWire from './fixtures/relate-blocked-review.json?raw';
import { parseSnapshot, record } from './model';
import { LanguageProvider, PREFERENCE_KEY } from './i18n';
import { authoredText, boundedCollection, currentCollection, recordedReadiness } from './readiness';
import { CoordinationNotes, GoalCoordination, ReadinessContext } from './readiness-ui';

// Actual producer read_snapshot outputs from the synthetic peer-wire fixture.
// Prose claims are preserved; no agreement status is inferred from their wording.
const fixture = JSON.parse(peerWire);
const readinessFixture = JSON.parse(readinessWire);
const blockedReviewFixture = JSON.parse(blockedReviewWire);

describe('canonical readiness and authored coordination context', () => {
  it('localizes generated blocked-review diagnostics while preserving authored coordination prose', async () => {
    localStorage.setItem(PREFERENCE_KEY, 'ko');
    const snapshot = parseSnapshot(blockedReviewFixture.review_changed);
    const task = snapshot.tasks.find((entry) => entry.id === 'S01')!;
    render(
      <LanguageProvider>
        <ReadinessContext snapshot={snapshot} task={task} />
        <CoordinationNotes
          task={{
            ...task,
            notes: [
              {
                actor: 'recorded-worker',
                reason: 'Pending submitted source changed',
                next_action: 'Authored exact next action',
              },
            ],
          }}
        />
      </LanguageProvider>,
    );
    await userEvent.click(screen.getByText('작업 인수 전 확인'));
    expect(screen.getByText('검토 중인 제출 소스가 변경됐어요')).toBeTruthy();
    await userEvent.click(screen.getByText(/협업 메모/));
    expect(screen.getByText('Pending submitted source changed')).toBeTruthy();
    expect(screen.getByText('Authored exact next action')).toBeTruthy();
  });
  it('uses actual blocked-review source observation without warning on unchanged review or active origin', async () => {
    const changed = parseSnapshot(blockedReviewFixture.review_changed);
    const task = changed.tasks.find((entry) => entry.id === 'S01')!;
    const view = render(<ReadinessContext snapshot={changed} task={task} />);
    await userEvent.click(screen.getByText('Readiness context'));
    expect(screen.getByText('Pending submitted source changed')).toBeDefined();
    expect(
      screen.getByText(
        'Resolve the recorded block through resume; changed source requires rework and a new submission.',
      ),
    ).toBeDefined();
    for (const name of ['review_unchanged', 'active_changed']) {
      const snapshot = parseSnapshot(blockedReviewFixture[name]);
      view.rerender(
        <ReadinessContext
          snapshot={snapshot}
          task={snapshot.tasks.find((entry) => entry.id === 'S01')!}
        />,
      );
      expect(
        screen.queryByText('Submitted source has changed. Revalidation is required.'),
      ).toBeNull();
      expect(screen.queryByText('Pending submitted source changed')).toBeNull();
      expect(screen.queryByText('Ready to claim')).toBeNull();
    }
  });
  it('retains canonical current-goal question attention without inventing task links', async () => {
    const snapshot = parseSnapshot(fixture.snapshot);
    const brief = record(snapshot.brief);
    const attention = record(brief.attention);
    const supplied = {
      ...snapshot,
      brief: {
        ...brief,
        attention: {
          ...attention,
          total: 1,
          shown: 1,
          omitted: 0,
          items: [
            {
              kind: 'question',
              entity_id: 'Q-goal',
              task_ids: [],
              reason: 'Exact canonical current-goal question',
              responsible_actor_id: 'director',
              next_action: 'Inspect saved answer',
              reference: attention.reference ?? null,
              truncated_fields: ['reason'],
            },
          ],
        },
      },
    };
    render(<GoalCoordination snapshot={supplied} />);
    await userEvent.click(screen.getByText('Current goal coordination'));
    expect(screen.getByText('Exact canonical current-goal question')).toBeDefined();
    expect(
      screen.getByText(
        'Summary text is shortened. Inspect the referenced source for full context.',
      ),
    ).toBeDefined();
    expect(screen.getByRole('link', { name: 'Open question' }).getAttribute('href')).toBe(
      '#question/Q-goal',
    );
  });
  it('rejects a stale nested collection without claiming that no conflicts exist', async () => {
    const snapshot = parseSnapshot(readinessFixture.blocked_owner_collision);
    const task = snapshot.tasks.find((entry) => entry.id === 'S02')!;
    const readiness = record(task.readiness);
    const collisions = record(readiness.collisions);
    const stale = {
      ...collisions,
      reference: { ...record(collisions.reference), revision: 'old' },
    };
    expect(currentCollection(snapshot, stale, task.id)).toBeNull();
    render(
      <ReadinessContext
        snapshot={snapshot}
        task={{ ...task, readiness: { ...readiness, collisions: stale } }}
      />,
    );
    await userEvent.click(screen.getByText('Readiness context'));
    expect(screen.queryByText('No claim-blocking scope conflicts recorded')).toBeNull();
    expect(screen.queryByText('scope: src/S01.py / src/S01.py')).toBeNull();
  });
  it('retains supplied non-applicability and stale-source warnings without deriving permission', async () => {
    const snapshot = parseSnapshot(fixture.snapshot);
    const task = snapshot.tasks[0]!;
    render(
      <ReadinessContext
        snapshot={snapshot}
        task={{
          ...task,
          readiness: {
            ...record(task.readiness),
            required_revalidation: 'not_applicable',
            submission: { status: 'stale', reason: 'Exact supplied source diagnostic' },
          },
        }}
      />,
    );
    await userEvent.click(screen.getByText('Readiness context'));
    expect(screen.getByText('Not applicable to this task')).toBeDefined();
    expect(
      screen.getByText('Submitted source has changed. Revalidation is required.'),
    ).toBeDefined();
    expect(screen.getByText('Exact supplied source diagnostic')).toBeDefined();
    expect(screen.queryByText('Ready to claim')).toBeNull();
  });
  it('distinguishes permitted planned overlap from actual canonical blocked-owner collision', async () => {
    const permitted = parseSnapshot(readinessFixture.planned_overlap_permitted);
    const blocked = parseSnapshot(readinessFixture.blocked_owner_collision);
    const before = permitted.tasks.find((task) => task.id === 'S02')!;
    const after = blocked.tasks.find((task) => task.id === 'S02')!;
    const view = render(<ReadinessContext snapshot={permitted} task={before} />);
    await userEvent.click(screen.getByText('Readiness context'));
    expect(screen.getByText('No claim-blocking scope conflicts recorded')).toBeDefined();
    expect(screen.queryByText('scope: src/S01.py / src/S01.py')).toBeNull();
    view.rerender(<ReadinessContext snapshot={blocked} task={after} />);
    expect(screen.getByText('scope: src/S01.py / src/S01.py')).toBeDefined();
    expect(screen.getByRole('link', { name: 'worker' }).getAttribute('href')).toContain(
      'owner=worker',
    );
    expect(
      screen.getByRole('link', { name: 'Scope and history for S01' }).getAttribute('href'),
    ).toBe('#task/S01');
    expect(screen.getByText('Fresh receipt required')).toBeDefined();
    expect(screen.getByText('Claim authorization').nextElementSibling?.textContent).toBe(
      'Not assessed',
    );
    await userEvent.click(screen.getByText('This task scope reference'));
    expect(screen.getByText(/\/tasks\/1\/candidate_paths/)).toBeDefined();
  });
  it('shows actual current-goal collision and responsible director without interpreting other prose', async () => {
    const snapshot = parseSnapshot(readinessFixture.blocked_owner_collision);
    render(<GoalCoordination snapshot={snapshot} />);
    await userEvent.click(screen.getByText('Current goal coordination'));
    expect(screen.getByText('scope: src/S01.py / src/S01.py')).toBeDefined();
    expect(screen.getByText('Responsible actor: director')).toBeDefined();
    expect(screen.getByRole('link', { name: 'S01' }).getAttribute('href')).toBe('#task/S01');
    expect(screen.queryByText('Revalidation is approved')).toBeNull();
  });
  it('accepts the actual versioned current reference and rejects stale or absent projection', () => {
    const snapshot = parseSnapshot(fixture.snapshot);
    const task = snapshot.tasks[0]!;
    expect(recordedReadiness(snapshot, task)?.claim).toMatchObject({ status: 'not_assessed' });
    expect(recordedReadiness(snapshot, { ...task, readiness: null })).toBeNull();
    expect(recordedReadiness({ ...snapshot, revision: 'changed' }, task)).toBeNull();
    expect(recordedReadiness({ ...snapshot, brief: { brief_version: 99 } }, task)).toBeNull();
  });
  it('does not turn missing, malformed or omitted collections into a zero-conflict assurance', () => {
    expect(boundedCollection(undefined)).toBeNull();
    expect(boundedCollection({ total: 1, shown: 0, omitted: 0, items: [] })).toBeNull();
    expect(
      boundedCollection({ total: 4, shown: 3, omitted: 1, items: ['A', 'B', 'C'] }, 2),
    ).toMatchObject({ total: 4, shown: 2, omitted: 2, items: ['A', 'B'] });
    expect(authoredText('가😀나', 2)).toEqual({ text: '가😀', truncated: true });
  });
  it('shows actual empty canonical factors while keeping claim and peer authority unassessed', async () => {
    const snapshot = parseSnapshot(fixture.snapshot);
    render(<ReadinessContext snapshot={snapshot} task={snapshot.tasks[0]!} />);
    await userEvent.click(screen.getByText('Readiness context'));
    expect(screen.getByText('No claim-blocking scope conflicts recorded')).toBeDefined();
    expect(screen.getByText('No unmet dependencies recorded')).toBeDefined();
    expect(screen.getAllByText('Not assessed')).toHaveLength(3);
    expect(screen.queryByText('Unknown')).toBeNull();
    expect(screen.queryByText('Ready to claim')).toBeNull();
  });
  it('retains unknown agreement when the supplied reason does not establish non-assessment', async () => {
    // Given
    const snapshot = parseSnapshot(fixture.snapshot);
    const original = snapshot.tasks[0];
    if (!original) throw new Error('Task missing');
    const task = {
      ...original,
      readiness: {
        ...record(original.readiness),
        peer_agreement: { status: 'unknown', reason: 'Not yet verified by an observer' },
      },
    };
    // When
    render(<ReadinessContext snapshot={snapshot} task={task} />);
    await userEvent.click(screen.getByText('Readiness context'));
    // Then
    expect(screen.getByText('Peer agreement').nextElementSibling?.textContent).toBe('Unknown');
    expect(screen.getAllByText('Not assessed')).toHaveLength(2);
  });
  it('preserves all three authored peer claims and task-specific evidence without computing acceptance', async () => {
    const snapshot = parseSnapshot(fixture.snapshot);
    const task = snapshot.tasks[0]!;
    render(<CoordinationNotes task={task} />);
    await userEvent.click(screen.getByText('Authored coordination notes'));
    const notes = screen.getByRole('list', { name: 'Task notes' });
    for (const stage of ['proposed', 'acknowledged', 'accepted']) {
      expect(
        within(notes).getByText(new RegExp(`^${stage}: Synthetic authored contract:`)),
      ).toBeDefined();
      expect(
        within(notes)
          .getByRole('link', { name: `evidence/S01/attempt-1/peer-${stage}.json` })
          .getAttribute('href'),
      ).toContain(encodeURIComponent(`evidence/S01/attempt-1/peer-${stage}.json`));
    }
    expect(notes.querySelector('.badge')).toBeNull();
    expect(
      screen.getByText('Notes preserve authored claims. Peer agreement is not assessed.'),
    ).toBeDefined();
    await userEvent.click(screen.getByText('All original notes'));
    expect(document.querySelector('pre')?.textContent).toBe(JSON.stringify(task.notes, null, 2));
  });
  it('bounds visible notes and retains all original records behind explicit disclosure', async () => {
    const snapshot = parseSnapshot(fixture.absent_snapshot);
    const task = {
      ...snapshot.tasks[0]!,
      notes: Array.from({ length: 8 }, (_, i) => ({
        actor: 'writer',
        reason: `Note ${i}`,
        next_action: 'Inspect original context',
      })),
    };
    render(<CoordinationNotes task={task} />);
    await userEvent.click(screen.getByText('Authored coordination notes'));
    expect(screen.getByText('5 / 8 shown · 3 omitted')).toBeDefined();
    expect(screen.queryByText('Note 0')).toBeNull();
    await userEvent.click(screen.getByText('All original notes'));
    expect(screen.getByText(/"reason": "Note 0"/)).toBeDefined();
  });
  it('keeps absent coordination unknown and does not reinterpret note prose as unresolved attention', async () => {
    const snapshot = parseSnapshot(fixture.snapshot);
    expect(record(record(snapshot.brief).attention).items).toEqual([
      expect.objectContaining({ kind: 'objective' }),
    ]);
    render(<GoalCoordination snapshot={snapshot} />);
    await userEvent.click(screen.getByText('Current goal coordination'));
    expect(screen.getByText('No coordination items in the supplied summary.')).toBeDefined();
    expect(screen.queryByText('Accepted agreement')).toBeNull();
    expect(screen.getByRole('link', { name: 'S01 · First unit' })).toBeDefined();
  });
});
