import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { parseSnapshot } from './model';
import {
  categoryBreadcrumbs,
  defaultContext,
  defaultPreferences,
  navigationHash,
  navigationSearch,
  parseNavigation,
  parsePreferences,
  readPreferences,
  selectWorkTasks,
  viewPreferenceKey,
  writePreferences,
} from './navigation';
import { SnapshotResponse } from './useSnapshot';
const fixture = () =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'nav-test',
    objective: { title: 'Current objective', task_ids: ['T110', 'T111'] },
    categories: [
      { id: 'parent', title: 'Parent', task_ids: ['T110'] },
      { id: 'child', title: 'Child', parent_id: 'parent', task_ids: ['T110', 'T111'] },
      { id: 'empty', title: 'Empty', task_ids: [] },
    ],
    actors: [{ id: 'Ada', role: 'Reviewer' }],
    questions: [{ id: 'Q', prompt: 'Recorded decision?', status: 'open', version: 1 }],
    reference: { path: 'evidence/one.md', sha256: 'abc', label: 'Verification source' },
    tasks: Array.from({ length: 123 }, (_, index) => ({
      id: `T${String(index).padStart(3, '0')}`,
      title: `Task ${index}`,
      status: index % 2 ? 'active' : 'planned',
      owner: index % 2 ? 'Ada' : null,
      category_id: index >= 110 ? 'child' : '',
      workstream: 'stream',
      next_action: 'Inspect original evidence',
      source: {
        text: '- Goal and expected result: Preserve exact authored context.\n- Source reference: original.md',
      },
    })),
  });
const narrow = window.innerWidth;
afterEach(() => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: narrow });
});
async function hash(value: string) {
  await act(async () => {
    window.history.pushState(null, '', value);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}
describe('validated navigation context', () => {
  it('shows identical evidence references once and preserves differing supplied metadata', async () => {
    const reference = { path: 'evidence/one.md', sha256: 'abc', label: 'Verification source' };
    const snapshot = {
      ...fixture(),
      references: [
        reference,
        { label: reference.label, sha256: 'abc', path: reference.path },
        { ...reference, origin: 'independent reviewer' },
        { ...reference, sha256: 'new-digest' },
      ],
    };
    const original = JSON.stringify(snapshot);
    await hash('#evidence/evidence%2Fone.md');
    render(<App loader={async () => snapshot} />);
    await screen.findByRole('heading', { name: 'Evidence record' });
    const records = [...document.querySelectorAll('pre.source')].map((node) =>
      JSON.parse(node.textContent || 'null'),
    );
    expect(records).toHaveLength(3);
    expect(records).toContainEqual(reference);
    expect(records).toContainEqual({ ...reference, origin: 'independent reviewer' });
    expect(records).toContainEqual({ ...reference, sha256: 'new-digest' });
    expect(JSON.stringify(snapshot)).toBe(original);
  });
  it('round-trips scope/filters/order/page/size/selection and encoded record identity', () => {
    const context = {
      ...defaultContext,
      scope: 'current' as const,
      filters: {
        search: 'Task 11 & words',
        status: 'active',
        owner: 'Ada',
        workstream: 'stream',
        category: 'child',
        sort: 'title',
      },
      paging: { index: 3, size: 25 as const },
      selected: 'T111',
    };
    const encoded = navigationHash({ view: 'task', taskId: 'A/B?C' }, context);
    const parsed = parseNavigation(encoded, fixture());
    expect(parsed.route.taskId).toBe('A/B?C');
    expect(parsed.filters).toEqual(context.filters);
    expect(parsed.paging).toEqual(context.paging);
    expect(parsed.scope).toBe('current');
    expect(parsed.issues).toEqual([]);
  });
  it('fails safely for invalid/removed filters, duplicate parameters and malformed path/query', () => {
    const parsed = parseNavigation(
      '#work?scope=bad&status=wrong&owner=missing&category=removed&workstream=absent&sort=bogus&page=NaN&size=99&answer=private',
      fixture(),
    );
    expect(parsed).toMatchObject({
      scope: 'all',
      filters: { status: '', owner: '', category: '', workstream: '', sort: 'id' },
      paging: { index: 0, size: 50 },
    });
    expect(parsed.issues).toContain('answer');
    expect(parseNavigation('#task/%E0%A4%A', fixture()).route.view).toBe('overview');
    expect(parseNavigation('#work?q=%E0%A4', fixture()).filters.search).toBe('');
    expect(parseNavigation('#work?status=active&status=blocked', fixture()).filters.status).toBe(
      '',
    );
  });
  it('keeps absent identities explicit and never treats them as an index', () => {
    expect(parseNavigation('#task/missing?task=other', fixture()).selected).toBe('missing');
    expect(parseNavigation('#question/Q', fixture()).route).toEqual({
      view: 'question',
      questionId: 'Q',
    });
    expect(parseNavigation('#evidence/evidence%2Fone.md', fixture()).route.evidencePath).toBe(
      'evidence/one.md',
    );
  });
  it('uses actual goal membership globally and deduplicates nested category task identities', () => {
    const snapshot = fixture();
    expect(
      selectWorkTasks(snapshot, { scope: 'current', filters: defaultContext.filters }).map(
        (task) => task.id,
      ),
    ).toEqual(['T110', 'T111']);
    expect(
      selectWorkTasks(snapshot, {
        scope: 'all',
        filters: { ...defaultContext.filters, category: 'parent', search: 'Task 11' },
      }),
    ).toHaveLength(10);
    expect(
      selectWorkTasks(snapshot, {
        scope: 'all',
        filters: { ...defaultContext.filters, category: 'parent' },
      }),
    ).toHaveLength(13);
    expect(categoryBreadcrumbs(snapshot, 'child').map((item) => item.id)).toEqual([
      'parent',
      'child',
    ]);
    expect(
      selectWorkTasks(
        { ...snapshot, objective: {} },
        { scope: 'current', filters: defaultContext.filters },
      ),
    ).toHaveLength(0);
  });
  it('bounds supplied-label navigation search and exposes full count and typed original routes', () => {
    const found = navigationSearch(fixture(), 'Task');
    expect(found.results).toHaveLength(12);
    expect(found.total).toBe(123);
    expect(navigationSearch(fixture(), 'Reviewer').results[0]?.kind).toBe('actor');
    expect(navigationSearch(fixture(), 'Verification source').results[0]?.route.evidencePath).toBe(
      'evidence/one.md',
    );
    expect(navigationSearch(fixture(), 'Recorded decision').results[0]?.route.questionId).toBe('Q');
    expect(navigationSearch(fixture(), '').total).toBe(0);
  });
  it('parses preferences at storage boundary and omits unknown/private URL payloads from presets', () => {
    const parsed = parsePreferences({
      density: 'compact',
      columns: ['owner', 'invalid'],
      presets: [
        { name: 'Mine', hash: '#question/Q?status=review&answer=private&nonce=secret&task=T111' },
      ],
      selectedPreset: 'Mine',
    });
    expect(parsed.columns).toEqual(['owner']);
    expect(parsed.presets[0]?.hash).not.toMatch(/answer|nonce|private|secret|task=/);
    expect(parsed.presets[0]?.hash).toContain('#work');
    expect(parsePreferences(null)).toEqual(defaultPreferences);
  });
  it('isolates program/browser preferences and reports unavailable storage', () => {
    expect(writePreferences('A', { ...defaultPreferences, density: 'compact' })).toBe(true);
    expect(readPreferences('A').preferences.density).toBe('compact');
    expect(readPreferences('B').preferences.density).toBe('comfortable');
    expect(viewPreferenceKey('A/B')).not.toBe(viewPreferenceKey('A'));
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Unavailable', 'SecurityError');
    });
    expect(readPreferences('A').unavailable).toBe(true);
    get.mockRestore();
  });
});
describe('researched navigation outcomes', () => {
  it('honors an explicit saved preset while contextual task detail is open', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
    writePreferences('nav-test', {
      ...defaultPreferences,
      presets: [{ name: 'All tasks', hash: '#work?scope=all&page=1&size=50' }],
    });
    window.history.replaceState(null, '', '#work?scope=all&status=active');
    const user = userEvent.setup();
    render(<App loader={async () => fixture()} />);
    await user.click(await screen.findByRole('link', { name: /T001 Task 1/ }));
    await screen.findByRole('heading', { name: 'Task 1' });
    await user.selectOptions(screen.getByLabelText('Local preset'), 'All tasks');
    await waitFor(() => expect(screen.getByLabelText('Status')).toHaveProperty('value', ''));
    expect(window.location.hash).not.toContain('status=active');
    expect(document.querySelector('.task-button')?.getAttribute('data-task-id')).toBe('T000');
    expect(screen.getByLabelText('Local preset')).toHaveProperty('value', 'All tasks');
  });
  it('restores nonzero narrow document scroll after consuming return focus exactly once', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 360 });
    const originalScroll = Object.getOwnPropertyDescriptor(window, 'scrollY');
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    const requestFrame = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((callback) => {
        frames.set(++frameId, callback);
        return frameId;
      });
    const cancelFrame = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
      frames.delete(id);
    });
    try {
      window.history.replaceState(null, '', '#work?scope=all');
      const user = userEvent.setup();
      render(<App loader={async () => fixture()} />);
      const row = await screen.findByRole('link', { name: /T010 Task 10/ });
      Object.defineProperty(window, 'scrollY', { configurable: true, value: 640 });
      await user.click(row);
      await screen.findByRole('heading', { name: 'Task 10' });
      Object.defineProperty(window, 'scrollY', { configurable: true, value: 237 });
      await user.click(screen.getByRole('link', { name: 'Back to work' }));
      await waitFor(() =>
        expect(document.activeElement?.getAttribute('data-task-id')).toBe('T010'),
      );
      await act(async () => {
        for (const callback of frames.values()) callback(16);
        frames.clear();
      });
      await waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ top: 640 }));
      expect(scrollTo).toHaveBeenCalledTimes(1);
      const search = screen.getByLabelText('Search tasks');
      await user.click(search);
      expect(document.activeElement).toBe(search);
      expect(scrollTo).toHaveBeenCalledTimes(1);
    } finally {
      scrollTo.mockRestore();
      requestFrame.mockRestore();
      cancelFrame.mockRestore();
      if (originalScroll) Object.defineProperty(window, 'scrollY', originalScroll);
    }
  });
  it('keeps an explicit different history destination when leaving task detail', async () => {
    window.history.replaceState(null, '', '#work?scope=all&status=active&size=25');
    const user = userEvent.setup();
    render(<App loader={async () => fixture()} />);
    await user.click(await screen.findByRole('link', { name: /T001 Task 1/ }));
    await screen.findByRole('heading', { name: 'Task 1' });
    await act(async () => {
      window.history.replaceState(null, '', '#work?scope=all&status=planned&page=2&size=25');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(screen.getByLabelText('Status')).toHaveProperty('value', 'planned');
    expect(document.querySelector('.task-button')?.getAttribute('data-task-id')).toBe('T050');
    expect(window.location.hash).toContain('status=planned&page=2&size=25');
    expect(window.location.hash).not.toContain('task=T001');
    await user.click(screen.getByRole('link', { name: /T050 Task 50/ }));
    await screen.findByRole('heading', { name: 'Task 50' });
    await hash('#work?scope=all&status=planned&page=2&size=25&task=T052');
    expect(window.location.hash).toContain('task=T052');
    await waitFor(() => expect(document.activeElement?.getAttribute('data-task-id')).toBe('T052'));
  });
  it('keeps keyboard focus on the search input after an actor result filters the current Work view', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '#work?scope=all');
    render(<App loader={async () => fixture()} />);
    await screen.findByLabelText('Search tasks');
    await user.keyboard('{Control>}k{/Control}');
    const input = screen.getByLabelText(/Find task, person/);
    await waitFor(() => expect(document.activeElement).toBe(input));
    await user.type(input, 'Ada');
    await user.keyboard('{ArrowDown}{Enter}');
    expect(screen.getByLabelText('Owner')).toHaveProperty('value', 'Ada');
    await waitFor(() => expect(document.activeElement).toBe(input));
    expect(document.querySelector('.navigation-results')).toBeNull();
  });
  it('keeps a legacy deep link when a selected preset supplies otherwise absent view context', async () => {
    writePreferences('nav-test', {
      ...defaultPreferences,
      selectedPreset: 'Active work',
      presets: [{ name: 'Active work', hash: '#work?scope=all&status=active&size=25' }],
    });
    window.history.replaceState(null, '', '#task/T111');
    render(<App loader={async () => fixture()} />);
    await screen.findByRole('heading', { name: 'Task 111' });
    expect(window.location.hash).toBe('#task/T111');
    await waitFor(() =>
      expect(screen.getByRole('link', { name: 'Back to work' }).getAttribute('href')).toContain(
        'status=active',
      ),
    );
  });
  it('restores explicit URL context on initial read and an atomic back/forward event', async () => {
    window.history.replaceState(
      null,
      '',
      '#work?scope=all&status=active&owner=Ada&page=2&size=25&sort=id&task=T111',
    );
    render(<App loader={async () => fixture()} />);
    await screen.findByLabelText('Tasks per page');
    await waitFor(() =>
      expect(document.querySelector('.task-button')?.getAttribute('data-task-id')).toBe('T051'),
    );
    expect(screen.getByLabelText('Status')).toHaveProperty('value', 'active');
    await hash('#work?scope=current&page=1&size=50');
    expect(document.querySelectorAll('.task-button')).toHaveLength(2);
    await act(async () => {
      window.history.replaceState(null, '', '#work?scope=all&status=active&page=2&size=25');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(document.querySelector('.task-button')?.getAttribute('data-task-id')).toBe('T051');
  });
  it('typing replaces history, discrete page changes push history and malformed context is explained', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '#work?scope=all');
    render(<App loader={async () => fixture()} />);
    const search = await screen.findByLabelText('Search tasks');
    const length = window.history.length;
    await user.type(search, 'Task');
    expect(window.history.length).toBe(length);
    expect(window.location.hash).toContain('q=Task');
    await user.click(screen.getByRole('button', { name: 'Next page' }));
    expect(window.history.length).toBe(length + 1);
    await hash('#work?owner=missing&page=bogus');
    expect(screen.getByText(/Some view settings were ignored/)).toBeDefined();
    expect(screen.getByLabelText('Owner')).toHaveProperty('value', '');
  });
  it('keeps wide ledger node/filter/scroll mounted while opening and closing contextual detail', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 });
    const user = userEvent.setup();
    window.history.replaceState(null, '', '#work?scope=all&page=2&size=25');
    render(<App loader={async () => fixture()} />);
    await screen.findByLabelText('Tasks per page');
    const first = document.querySelector('.task-button');
    const ledger = document.querySelector('.task-list');
    if (ledger) ledger.scrollTop = 321;
    await user.click(screen.getByRole('link', { name: /T025 Task 25/ }));
    await screen.findByRole('heading', { name: 'Task 25' });
    expect(document.querySelector('.task-button')).toBe(first);
    expect(ledger?.scrollTop).toBe(321);
    expect(screen.getByRole('link', { name: 'Open full detail' })).toBeDefined();
    await user.click(screen.getByRole('link', { name: 'Back to work' }));
    await waitFor(() => expect(document.activeElement?.getAttribute('data-task-id')).toBe('T025'));
    expect(document.querySelector('.task-button')).toBe(first);
    expect(screen.getByLabelText('Tasks per page')).toHaveProperty('value', '25');
  });
  it('preserves active inputs/page/density through304/equal200/error and avoids a competing answer store', async () => {
    const user = userEvent.setup(),
      original = fixture(),
      loader = vi.fn().mockResolvedValue(new SnapshotResponse(200, 'W/"one"', original));
    window.history.replaceState(null, '', '#work?scope=all&page=2&size=25');
    render(<App loader={loader} />);
    const search = await screen.findByLabelText('Search tasks');
    await user.click(screen.getByLabelText('View options and presets'));
    await user.selectOptions(screen.getByLabelText('Density'), 'compact');
    search.focus();
    for (const response of [
      new SnapshotResponse(304, 'W/"one"'),
      new SnapshotResponse(200, 'W/"one"', { ...original, generated_at: 'later' }),
    ]) {
      loader.mockResolvedValue(response);
      fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Refresh' })).not.toHaveProperty(
          'disabled',
          true,
        ),
      );
      expect(document.activeElement).toBe(search);
      expect(document.querySelector('.work-ledger')?.className).toContain('compact');
      expect(document.querySelector('.task-button')?.getAttribute('data-task-id')).toBe('T025');
    }
    loader.mockRejectedValue(new Error('offline'));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText(/Showing last observed records/);
    expect(document.activeElement).toBe(search);
    expect(localStorage.getItem(viewPreferenceKey(original.program_id))).not.toMatch(
      /answer|nonce|submission/,
    );
  });
  it('saves/restores a validated local preset, preserves explicit URL precedence and essential next action', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '#work?scope=all&status=active&size=25');
    const view = render(<App loader={async () => fixture()} />);
    await screen.findByLabelText('Search tasks');
    await user.click(screen.getByLabelText('View options and presets'));
    await user.type(screen.getByLabelText('Preset name'), 'Review configuration');
    await user.click(screen.getByRole('button', { name: 'Save view preset' }));
    expect(screen.getByLabelText('Local preset')).toHaveProperty('value', 'Review configuration');
    await user.click(screen.getByLabelText('Show Owner'));
    expect(document.querySelectorAll('.task-owner')).toHaveLength(0);
    expect(screen.getAllByText('Inspect original evidence').length).toBe(25);
    view.unmount();
    window.history.replaceState(null, '', '#work?scope=all&status=planned&size=50');
    render(<App loader={async () => fixture()} />);
    await screen.findByLabelText('Search tasks');
    expect(screen.getByLabelText('Status')).toHaveProperty('value', 'planned');
  });
  it('supports keyboard local search entry/exit and records-only evidence routes', async () => {
    const user = userEvent.setup();
    render(<App loader={async () => fixture()} />);
    await screen.findByRole('heading', { name: 'Overview' });
    await user.keyboard('{Control>}k{/Control}');
    const input = screen.getByLabelText(/Find task, person/);
    await waitFor(() => expect(document.activeElement).toBe(input));
    await user.type(input, 'Verification source');
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement?.textContent).toContain('Verification source');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(document.activeElement).toBe(input));
    await user.type(input, 'Verification source');
    await user.keyboard('{ArrowDown}{Enter}');
    await screen.findByRole('heading', { name: 'Evidence record' });
    expect(screen.getByText(/Files are not opened or verified/)).toBeDefined();
  });
});

describe('compact navigation accessibility', () => {
  it('shows equal search labels and identifiers once while retaining distinct labels and routes', async () => {
    const user = userEvent.setup();
    const snapshot = parseSnapshot({
      ...fixture(),
      references: [
        { path: 'evidence/equal.md', label: 'evidence/equal.md', sha256: 'equal' },
        { path: 'evidence/distinct.md', label: 'Distinct evidence label', sha256: 'distinct' },
      ],
    });
    render(<App loader={async () => snapshot} />);
    await screen.findByRole('heading', { name: 'Overview' });
    await user.keyboard('{Control>}k{/Control}');
    const input = screen.getByLabelText(/Find task, person/);
    await waitFor(() => expect(document.activeElement).toBe(input));
    await user.type(input, 'evidence');
    const equal = screen.getByRole('link', { name: /Evidence\s*evidence\/equal\.md/ });
    expect(equal.textContent?.match(/evidence\/equal\.md/g)).toHaveLength(1);
    expect(equal.getAttribute('href')).toContain('#evidence/evidence%2Fequal.md');
    const distinct = screen.getByRole('link', {
      name: /Evidence\s*Distinct evidence label\s*evidence\/distinct\.md/,
    });
    expect(distinct.getAttribute('href')).toContain('#evidence/evidence%2Fdistinct.md');
    await user.click(equal);
    await screen.findByRole('heading', { name: 'Evidence record' });
    expect(window.location.hash).toContain('#evidence/evidence%2Fequal.md');
  });
  it('opens collapsed mobile search from the keyboard and exposes it after a wide resize', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 360 });
    const user = userEvent.setup();
    render(<App loader={async () => fixture()} />);
    await screen.findByRole('heading', { name: 'Overview' });
    const disclosure = document.querySelector('.navigation-search-disclosure');
    expect(disclosure?.hasAttribute('open')).toBe(false);
    await user.keyboard('{Control>}k{/Control}');
    const input = screen.getByLabelText(/Find task, person/);
    await waitFor(() => expect(document.activeElement).toBe(input));
    expect(disclosure?.hasAttribute('open')).toBe(true);
    await user.type(input, 'Verification source');
    await user.keyboard('{ArrowDown}{Escape}');
    expect(document.activeElement).toBe(input);
    await user.click(
      screen.getByText('Search across records', { exact: false, selector: 'summary' }),
    );
    expect(disclosure?.hasAttribute('open')).toBe(false);
    await act(async () => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
      window.dispatchEvent(new Event('resize'));
    });
    expect(disclosure?.hasAttribute('open')).toBe(true);
  });
  it('makes in-progress work an explicit URL filter and keeps unassigned planned under view settings', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '#work?scope=all');
    render(<App loader={async () => fixture()} />);
    await screen.findByLabelText('Search tasks');
    await user.click(screen.getByRole('button', { name: 'In progress' }));
    expect(window.location.hash).toContain('status=active');
    expect(document.querySelectorAll('.task-button')).toHaveLength(50);
    expect(screen.getByLabelText('Status')).toHaveProperty('value', 'active');
    await user.click(screen.getByLabelText('View options and presets'));
    await user.click(screen.getByRole('button', { name: 'Unassigned planned work' }));
    expect(window.location.hash).toContain('status=planned');
    expect(window.location.hash).toContain('owner=__unassigned');
    expect(screen.getByLabelText('Owner')).toHaveProperty('value', '__unassigned');
  });
  it('shows exact authored expected result before closed source and planning disclosures', async () => {
    window.history.replaceState(null, '', '#task/T111?scope=all');
    render(<App loader={async () => fixture()} />);
    await screen.findByRole('heading', { name: 'Task 111' });
    const result = screen.getByText('Preserve exact authored context.', { exact: true });
    expect(result.closest('details')).toBeNull();
    expect(screen.getByText('Inspect original evidence', { exact: true })).toBeDefined();
    expect(document.querySelector('.expected-result')?.hasAttribute('open')).toBe(false);
  });
});
