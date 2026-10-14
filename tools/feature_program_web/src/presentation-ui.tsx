import { createContext, useContext } from 'react';
import type { ReactNode } from 'react';
import type { Json, Snapshot } from './model';
import { record, string } from './model';
import { actorLabel, taskLabel } from './presentation';

const Context = createContext<Snapshot | null>(null);
const actorFields = new Set([
  'Actor',
  'Director',
  'Reviewer',
  'Implementer',
  'Successor',
  'Previous actor',
  'Next actor',
  'Current owner',
  'Assigned actor',
  'Appointed actor',
  'Final director',
  'Owner',
  'Decision owner',
  'Next action owner',
]);
export function PresentationProvider({
  snapshot,
  children,
}: {
  readonly snapshot: Snapshot | null;
  readonly children: ReactNode;
}) {
  return <Context.Provider value={snapshot}>{children}</Context.Provider>;
}
export function usePresentation() {
  const snapshot = useContext(Context);
  const actor = (id: Json | undefined) =>
    snapshot ? actorLabel(snapshot, string(id)) : string(id);
  const task = (id: Json | undefined) => (snapshot ? taskLabel(snapshot, string(id)) : string(id));
  const goal = (id: Json | undefined) => {
    const current = record(snapshot?.objective);
    const goals = [current, ...(Array.isArray(current.history) ? current.history.map(record) : [])];
    const value = goals.find((row) => row.id === id);
    return string(value?.title || value?.goal) || string(id);
  };
  const withId = (label: string, id: Json | undefined) =>
    label === string(id) ? label : label + ' · ' + string(id);
  const workstream = (id: Json | undefined) => {
    const streams = snapshot?.workstreams;
    const stream = Array.isArray(streams)
      ? streams.map(record).find((row) => row.id === id)
      : undefined;
    return string(stream?.name || stream?.title) || string(id);
  };
  return {
    actor,
    field: (label: string, value: Json | undefined): Json | undefined =>
      label === 'Recorded participants' && Array.isArray(value)
        ? value.map(actor)
        : actorFields.has(label) && typeof value === 'string'
          ? actor(value)
          : label === 'Workstream'
            ? workstream(value)
            : label === 'Task'
              ? withId(task(value), value)
              : label === 'Objective'
                ? withId(goal(value), value)
                : value,
    goal,
    task,
    taskReference: (id: Json | undefined) => withId(task(id), id),
    goalReference: (id: Json | undefined) => withId(goal(id), id),
    workstream,
  };
}
