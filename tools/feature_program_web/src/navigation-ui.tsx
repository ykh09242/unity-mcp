import { useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from './i18n';
import type { Snapshot } from './model';
import { categoryBreadcrumbs, columns, navigationHash, navigationSearch } from './navigation';
import type { Route, ViewPreferences, WorkContext } from './navigation';

export function NavigationSearch({
  snapshot,
  context,
  navigate,
}: {
  readonly snapshot: Snapshot;
  readonly context: WorkContext;
  readonly navigate: (route: Route, context?: WorkContext) => void;
}) {
  const i18n = useI18n(),
    [query, setQuery] = useState(''),
    [expanded, setExpanded] = useState(() => window.innerWidth >= 1100),
    input = useRef<HTMLInputElement>(null),
    results = useRef<HTMLDivElement>(null);
  const found = useMemo(() => navigationSearch(snapshot, query), [snapshot, query]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setExpanded(true);
        requestAnimationFrame(() => input.current?.focus());
      }
    };
    const resize = () => setExpanded(window.innerWidth >= 1100);
    window.addEventListener('keydown', key);
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('keydown', key);
      window.removeEventListener('resize', resize);
    };
  }, []);
  return (
    <div className="navigation-search" role="search" aria-label={i18n.t('Local navigation search')}>
      <details
        className="navigation-search-disclosure"
        open={expanded}
        onToggle={(event) => setExpanded(event.currentTarget.open)}
      >
        <summary>
          {i18n.t('Search across records')} <kbd>Ctrl K</kbd>
        </summary>
        <label htmlFor="navigation-query">
          {i18n.t('Find task, person or question')} <kbd>Ctrl K</kbd>
        </label>
        <input
          id="navigation-query"
          ref={input}
          type="search"
          value={query}
          maxLength={300}
          placeholder={i18n.t('Search recorded labels…')}
          aria-controls="navigation-results"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              setQuery('');
              input.current?.focus();
            }
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              results.current?.querySelector<HTMLAnchorElement>('a')?.focus();
            }
          }}
        />
        {query.trim() && (
          <div
            id="navigation-results"
            className="navigation-results"
            ref={results}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault();
                setQuery('');
                input.current?.focus();
              }
              if (event.key === 'ArrowUp' && event.target === results.current?.querySelector('a')) {
                event.preventDefault();
                input.current?.focus();
              }
            }}
          >
            <p role="status">
              {i18n.t('{shown} of {total} recorded matches', {
                shown: found.results.length,
                total: found.total,
              })}
            </p>
            {found.results.map((result) => {
              const next =
                result.kind === 'actor'
                  ? {
                      ...context,
                      filters: { ...context.filters, owner: result.id },
                      paging: { ...context.paging, index: 0 },
                    }
                  : context;
              const route = result.kind === 'actor' ? { view: 'work' as const } : result.route;
              return (
                <a
                  key={result.kind + result.id}
                  href={navigationHash(route, next)}
                  onClick={(event) => {
                    if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
                      event.preventDefault();
                      navigate(route, next);
                      setQuery('');
                      if (result.kind === 'actor') input.current?.focus();
                    }
                  }}
                >
                  <span className="search-kind">
                    {i18n.t(
                      result.kind === 'actor'
                        ? 'Recorded owner'
                        : result.kind === 'evidence'
                          ? 'Evidence'
                          : result.kind === 'question'
                            ? 'Question'
                            : 'Task',
                    )}
                  </span>
                  <strong>{result.label}</strong>
                  {result.label !== result.id && <span className="mono">{result.id}</span>}
                </a>
              );
            })}
            {!found.total && (
              <p className="empty">
                {i18n.t('No recorded labels match. This searches the loaded snapshot only.')}
              </p>
            )}
            {found.total > found.results.length && (
              <p className="muted">{i18n.t('Refine the query to reach omitted matches.')}</p>
            )}
          </div>
        )}
      </details>
    </div>
  );
}
export function ViewControls({
  context,
  preferences,
  onContext,
  onPreferences,
  onPreset,
}: {
  readonly context: WorkContext;
  readonly preferences: ViewPreferences;
  readonly onContext: (context: WorkContext) => void;
  readonly onPreferences: (preferences: ViewPreferences) => void;
  readonly onPreset: (hash: string) => void;
}) {
  const i18n = useI18n(),
    [name, setName] = useState(''),
    [message, setMessage] = useState('');
  return (
    <div className="view-controls">
      <fieldset className="scope-switch">
        <legend>{i18n.t('Work scope')}</legend>
        {(['current', 'all'] as const).map((scope) => (
          <button
            key={scope}
            type="button"
            aria-pressed={context.scope === scope}
            onClick={() =>
              onContext({ ...context, scope, paging: { ...context.paging, index: 0 } })
            }
          >
            {i18n.t(scope === 'current' ? 'Current goal' : 'All work')}
          </button>
        ))}
      </fieldset>
      <div className="recorded-views" aria-label={i18n.t('Recorded work views')}>
        {[
          ['All recorded states', ''],
          ['In progress', 'active'],
          ['Blocked work', 'blocked'],
          ['Review needs', 'review'],
        ].map(([label, status]) => (
          <button
            type="button"
            key={label}
            aria-pressed={
              context.filters.status === status &&
              (status !== 'planned' || context.filters.owner === '__unassigned')
            }
            onClick={() =>
              onContext({
                ...context,
                filters: {
                  ...context.filters,
                  status: status || '',
                  owner: status === 'planned' ? '__unassigned' : '',
                },
                paging: { ...context.paging, index: 0 },
              })
            }
          >
            {i18n.t(label || 'All recorded states')}
          </button>
        ))}
      </div>
      <details className="view-options">
        <summary aria-label={i18n.t('View options and presets')}>{i18n.t('View settings')}</summary>
        <button
          type="button"
          aria-pressed={
            context.filters.status === 'planned' && context.filters.owner === '__unassigned'
          }
          onClick={() =>
            onContext({
              ...context,
              filters: { ...context.filters, status: 'planned', owner: '__unassigned' },
              paging: { ...context.paging, index: 0 },
            })
          }
        >
          {i18n.t('Unassigned planned work')}
        </button>
        <label>
          {i18n.t('Density')}
          <select
            value={preferences.density}
            onChange={(event) =>
              onPreferences({
                ...preferences,
                density: event.target.value === 'compact' ? 'compact' : 'comfortable',
              })
            }
          >
            <option value="comfortable">{i18n.t('Comfortable')}</option>
            <option value="compact">{i18n.t('Compact')}</option>
          </select>
        </label>
        <fieldset>
          <legend>{i18n.t('Optional columns')}</legend>
          {columns.map((column) => (
            <label key={column}>
              <input
                type="checkbox"
                checked={preferences.columns.includes(column)}
                onChange={(event) =>
                  onPreferences({
                    ...preferences,
                    columns: event.target.checked
                      ? [...preferences.columns, column]
                      : preferences.columns.filter((value) => value !== column),
                  })
                }
              />
              {i18n.t('Show {column}', {
                column: i18n.t(
                  column === 'owner' ? 'Owner' : column === 'category' ? 'Category' : 'Workstream',
                ),
              })}
            </label>
          ))}
        </fieldset>
        <label>
          {i18n.t('Local preset')}
          <select
            value={preferences.selectedPreset}
            onChange={(event) => {
              const selected = preferences.presets.find((item) => item.name === event.target.value);
              onPreferences({ ...preferences, selectedPreset: selected?.name || '' });
              if (selected) onPreset(selected.hash);
            }}
          >
            <option value="">{i18n.t('Choose a preset')}</option>
            {preferences.presets.map((item) => (
              <option key={item.name}>{item.name}</option>
            ))}
          </select>
        </label>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const title = name.trim();
            if (!title) return;
            if (
              preferences.presets.length >= 20 &&
              !preferences.presets.some((item) => item.name === title)
            ) {
              setMessage(
                'This browser can keep 20 local presets. Remove one before saving another.',
              );
              return;
            }
            onPreferences({
              ...preferences,
              presets: [
                ...preferences.presets.filter((item) => item.name !== title),
                {
                  name: title,
                  hash: navigationHash({ view: 'work' }, { ...context, selected: null }),
                },
              ],
              selectedPreset: title,
            });
            setName('');
            setMessage('View preset saved in this browser.');
          }}
        >
          <label>
            {i18n.t('Preset name')}
            <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
          </label>
          <button>{i18n.t('Save view preset')}</button>
          <button
            type="button"
            disabled={!preferences.selectedPreset}
            onClick={() =>
              onPreferences({
                ...preferences,
                presets: preferences.presets.filter(
                  (item) => item.name !== preferences.selectedPreset,
                ),
                selectedPreset: '',
              })
            }
          >
            {i18n.t('Remove preset')}
          </button>
        </form>
        {message && <p role="status">{i18n.t(message)}</p>}
        <p className="muted">
          {i18n.t(
            'View settings stay in this browser for this program. They do not change work records or save answers.',
          )}
        </p>
      </details>
    </div>
  );
}
export function Breadcrumbs({
  snapshot,
  category,
  onSelect,
}: {
  readonly snapshot: Snapshot;
  readonly category: string;
  readonly onSelect: (id: string) => void;
}) {
  const i18n = useI18n();
  const path = categoryBreadcrumbs(snapshot, category);
  return path.length ? (
    <nav className="breadcrumbs" aria-label={i18n.t('Category breadcrumb')}>
      <button type="button" onClick={() => onSelect('')}>
        {i18n.t('All categories')}
      </button>
      {path.map((item) => (
        <span key={item.id}>
          {' '}
          /{' '}
          <button type="button" onClick={() => onSelect(item.id)}>
            {item.title}
          </button>
        </span>
      ))}
    </nav>
  ) : null;
}
