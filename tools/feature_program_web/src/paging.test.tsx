import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { parseSnapshot } from './model';
import { firstWorkPage, taskPage, taskPageIndex } from './paging';
import { selectTasks } from './selectors';
import { SnapshotResponse } from './useSnapshot';
import { PREFERENCE_KEY } from './i18n';

const fixture = (count = 123) =>
  parseSnapshot({
    snapshot_version: 1,
    revision: 'one',
    program_id: 'paging-test',
    integrity: { status: 'fresh', warnings: [] },
    tasks: Array.from({ length: count }, (_, index) => ({
      id: `T${String(index).padStart(3, '0')}`,
      title: `Task ${index}`,
      status: index % 2 ? 'active' : 'planned',
      owner: index % 2 ? 'Ada' : null,
      workstream: 'work',
      category_id: index % 2 ? 'odd' : 'even',
    })),
    categories: [
      { id: 'odd', title: 'Odd', task_ids: [] },
      { id: 'even', title: 'Even', task_ids: [] },
    ],
  });
const filters = { search: '', status: '', category: '', owner: '', workstream: '', sort: 'id' };
const rows = () => [...document.querySelectorAll('.task-button')];
async function navigate(hash: string) {
  await act(async () => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}
async function work(loader = async () => fixture()) {
  render(
    <App loader={loader} sessionLoader={async () => ({ responsesEnabled: false, nonce: null })} />,
  );
  await screen.findByRole('heading', { name: 'Overview' });
  await navigate('#work');
}

describe('global Work pages', () => {
  it('continues keyboard filtering after removing the focused filter control', async () => {
    await work();
    await navigate('#work?scope=all&status=active');
    const user = userEvent.setup();
    const clear = screen.getByRole('button', { name: 'Clear filters' });
    clear.focus();
    await user.keyboard('{Enter}');
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('searchbox', { name: 'Search tasks' }));
    expect(rows()).toHaveLength(50);
  });
  it('filters and orders full results before slicing and retains original task identity', () => {
    const snapshot = fixture();
    const original = [...snapshot.tasks];
    const matches = selectTasks(snapshot, { ...filters, status: 'active', owner: 'Ada' });
    const page = taskPage(matches, { index: 1, size: 25 });
    expect(page.matching).toBe(61);
    expect(page.tasks[0]?.id).toBe('T051');
    expect(page.tasks[0]).toBe(snapshot.tasks[51]);
    expect(page.start).toBe(26);
    expect(page.end).toBe(50);
    expect(taskPageIndex(matches, 'T121', 25)).toBe(2);
    expect(snapshot.tasks).toEqual(original);
  });
  it('clamps shrinking results and distinguishes empty, exact and short pages', () => {
    expect(taskPage([], { index: 8, size: 50 })).toMatchObject({
      index: 0,
      count: 0,
      start: 0,
      end: 0,
      tasks: [],
    });
    expect(taskPage(fixture(50).tasks, { index: 8, size: 50 })).toMatchObject({
      index: 0,
      count: 1,
      start: 1,
      end: 50,
    });
    expect(taskPage(fixture(51).tasks, { index: 8, size: 50 })).toMatchObject({
      index: 1,
      count: 2,
      start: 51,
      end: 51,
    });
    expect(taskPageIndex(fixture().tasks, 'missing', 100)).toBeNull();
    expect(firstWorkPage).toEqual({ index: 0, size: 50 });
  });
  it('bounds all size choices and keeps global sort ties deterministic across boundaries', () => {
    const snapshot = fixture(2000);
    const matches = selectTasks(
      parseSnapshot({
        ...snapshot,
        tasks: [...snapshot.tasks].reverse().map((task) => ({ ...task, title: 'Same title' })),
      }),
      { ...filters, sort: 'title' },
    );
    for (const size of [25, 50, 100] as const) {
      const page = taskPage(matches, { index: 1, size });
      expect(page.tasks).toHaveLength(size);
      expect(page.tasks[0]?.id).toBe(`T${String(size).padStart(3, '0')}`);
      expect(page.matching).toBe(2000);
    }
  });
});

describe('paging interaction context', () => {
  it('shows exact counts, reaches all boundaries and preserves invoking keyboard control', async () => {
    const user = userEvent.setup();
    await work();
    expect(rows()).toHaveLength(50);
    expect(screen.getByText('123 of 123 tasks')).toBeDefined();
    const next = screen.getByRole('button', { name: 'Next page' });
    next.focus();
    await user.keyboard('{Enter}');
    expect(rows()[0]?.textContent).toContain('T050');
    expect(document.activeElement).toBe(next);
    await user.click(screen.getByRole('button', { name: 'Last page' }));
    expect(rows()).toHaveLength(23);
    expect(screen.getByRole('button', { name: 'Next page' }).getAttribute('aria-disabled')).toBe(
      'true',
    );
    await user.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(rows()[0]?.textContent).toContain('T050');
    await user.click(screen.getByRole('button', { name: 'First page' }));
    expect(rows()[0]?.textContent).toContain('T000');
    for (const size of ['25', '100', '50']) {
      await user.selectOptions(screen.getByLabelText('Tasks per page'), size);
      expect(rows()).toHaveLength(Number(size));
    }
  });
  it('finds off-page global matches, resets page on filters and does not steal search focus', async () => {
    const user = userEvent.setup();
    await work();
    await user.click(screen.getByRole('button', { name: 'Last page' }));
    const search = screen.getByLabelText('Search tasks');
    await user.type(search, 'T010');
    expect(rows()).toHaveLength(1);
    expect(rows()[0]?.textContent).toContain('T010');
    expect(document.activeElement).toBe(search);
    await user.clear(search);
    expect(rows()[0]?.textContent).toContain('T000');
    await user.selectOptions(screen.getByLabelText('Status'), 'active');
    expect(screen.getByText('61 of 123 tasks')).toBeDefined();
    expect(rows()).toHaveLength(50);
    await user.type(search, 'absent');
    expect(rows()).toHaveLength(0);
    expect(screen.getByText(/No tasks match these filters/)).toBeDefined();
    expect(screen.getByRole('button', { name: 'Last page' }).getAttribute('aria-disabled')).toBe(
      'true',
    );
  });
  it('returns to the selected row and preserves selection while browsing another page', async () => {
    const user = userEvent.setup();
    await work();
    await user.click(screen.getByRole('button', { name: 'Next page' }));
    await user.click(screen.getByRole('link', { name: /T051 Task 51/ }));
    await navigate('#task/T051');
    await user.click(screen.getByRole('link', { name: 'Back to work' }));
    await navigate('#work');
    await waitFor(() => expect(document.activeElement?.textContent).toContain('T051'));
    expect(rows()[0]?.textContent).toContain('T050');
    await user.click(screen.getByRole('button', { name: 'Last page' }));
    expect(screen.getByText('Selected task T051 is on another page.')).toBeDefined();
    await user.click(screen.getByRole('button', { name: 'Show selected task' }));
    expect(document.activeElement?.textContent).toContain('T051');
    expect(rows()[0]?.textContent).toContain('T050');
  });
  it('resolves initial off-page deep links and returns with a reachable reveal control', async () => {
    const user = userEvent.setup();
    window.location.hash = '#task/T111';
    render(<App loader={async () => fixture()} />);
    await screen.findByRole('heading', { name: 'Task 111' });
    await user.click(screen.getByRole('link', { name: 'Back to work' }));
    await navigate('#work');
    expect(rows()[0]?.textContent).toContain('T000');
    expect(document.activeElement).toBe(screen.getByLabelText('Tasks per page'));
    await user.click(screen.getByRole('button', { name: 'Show selected task' }));
    expect(document.activeElement?.textContent).toContain('T111');
    expect(rows()).toHaveLength(23);
  });
  it('preserves page, size and focus on304, semantic200 and failed refresh, then clamps removed content', async () => {
    const user = userEvent.setup();
    const original = fixture();
    const loader = vi.fn().mockResolvedValue(new SnapshotResponse(200, 'W/"one"', original));
    await work(loader);
    await user.selectOptions(screen.getByLabelText('Tasks per page'), '25');
    await user.click(screen.getByRole('button', { name: 'Last page' }));
    const search = screen.getByLabelText('Search tasks');
    search.focus();
    for (const response of [
      new SnapshotResponse(304, 'W/"one"'),
      new SnapshotResponse(200, 'W/"one"', { ...original, generated_at: 'new-observation' }),
    ]) {
      loader.mockResolvedValue(response);
      fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Refresh' }).hasAttribute('disabled')).toBe(
          false,
        ),
      );
      expect(rows()[0]?.textContent).toContain('T100');
      expect(document.activeElement).toBe(search);
    }
    loader.mockRejectedValue(new Error('outage'));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText(/Showing last observed records/);
    expect(rows()[0]?.textContent).toContain('T100');
    expect(document.activeElement).toBe(search);
    loader.mockResolvedValue({ ...original, revision: 'two', tasks: original.tasks.slice(0, 35) });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(rows()).toHaveLength(10));
    expect(rows()[0]?.textContent).toContain('T025');
    expect(document.activeElement).toBe(search);
    expect(screen.getByLabelText('Tasks per page')).toHaveProperty('value', '25');
  });
  it('reconciles moved and removed selected identities only during explicit return', async () => {
    const user = userEvent.setup();
    const original = fixture();
    const loader = vi.fn().mockResolvedValue(original);
    await work(loader);
    await user.click(screen.getByRole('button', { name: 'Next page' }));
    await user.click(screen.getByRole('link', { name: /T051 Task 51/ }));
    await navigate('#task/T051');
    loader.mockResolvedValue({
      ...original,
      revision: 'moved',
      tasks: original.tasks.filter((task) => task.id >= 'T050'),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole('link', { name: 'Back to work' }));
    await navigate('#work');
    expect(rows()[0]?.textContent).toContain('T050');
    expect(document.activeElement?.textContent).toContain('T051');
    loader.mockResolvedValue({
      ...original,
      revision: 'removed',
      tasks: original.tasks.filter((task) => task.id !== 'T051'),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText('Selected task T051 is no longer available.');
    expect(screen.queryByRole('button', { name: 'Show selected task' })).toBeNull();
    // The removal notice can commit before the effect restores focus.
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText('Tasks per page')),
    );
  });
  it('keeps the current page when refresh moves selection off page and reveals it only on request', async () => {
    const user = userEvent.setup();
    const original = fixture();
    const loader = vi.fn().mockResolvedValue(original);
    await work(loader);
    await user.click(screen.getByRole('button', { name: 'Next page' }));
    await user.click(screen.getByRole('link', { name: /T051 Task 51/ }));
    await navigate('#task/T051');
    await user.click(screen.getByRole('link', { name: 'Back to work' }));
    await navigate('#work');
    const search = screen.getByLabelText('Search tasks');
    search.focus();
    loader.mockResolvedValue({ ...original, revision: 'moved', tasks: original.tasks.slice(50) });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await screen.findByText('Selected task T051 is on another page.');
    expect(rows()[0]?.textContent).toContain('T100');
    expect(document.activeElement).toBe(search);
    await user.click(screen.getByRole('button', { name: 'Show selected task' }));
    expect(document.activeElement?.textContent).toContain('T051');
  });
  it('exposes Korean paging labels and handles truly empty and unknown task returns honestly', async () => {
    const user = userEvent.setup();
    localStorage.setItem(PREFERENCE_KEY, 'ko');
    window.location.hash = '#task/missing';
    render(<App loader={async () => fixture(0)} />);
    await screen.findByRole('heading', { name: '작업을 볼 수 없음' });
    await user.click(screen.getByRole('link', { name: '목록으로' }));
    await navigate('#work');
    expect(rows()).toHaveLength(0);
    expect(screen.getByRole('button', { name: '다음 페이지' }).getAttribute('aria-disabled')).toBe(
      'true',
    );
    expect(document.activeElement).toBe(screen.getByLabelText('페이지당 작업 수'));
    expect(screen.getByText('더 이상 사용할 수 없는 작업: missing')).toBeDefined();
  });
});
