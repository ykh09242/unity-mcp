// Frozen beta dae3f01d maintenance algorithm for paired native benchmarks.
// Keep this test-only baseline unchanged when optimizing production.
using System.Collections.Generic;
using UnityEditor;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    internal sealed class ObjectIdCacheBenchmarkBaseline : System.IDisposable
    {
        private struct Entry
        {
            public ulong Full;
            public int Previous;
            public int Next;
        }

        // Intrusive circular links keep the cursor valid across additions/removals without
        // allocating a key snapshot or one managed linked-list node per live handle.
        private readonly Dictionary<int, Entry> entries = new Dictionary<int, Entry>();
        private const int LookupsPerUpdate = 128;
        private readonly System.Func<ulong, Object> resolve;
        private readonly System.Func<double> clock;
        private double nextPrune;
        private int first;
        private int cursor;
        private int remaining;
        public int Count => entries.Count;
        public bool IsScheduled { get; private set; }

        public ObjectIdCacheBenchmarkBaseline(System.Func<ulong, Object> resolve, System.Func<double> clock)
        {
            this.resolve = resolve;
            this.clock = clock;
            AssemblyReloadEvents.beforeAssemblyReload += Clear;
            EditorApplication.quitting += Clear;
            EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
        }

        // Match the candidate's call shape; the frozen maintenance/storage algorithm is unchanged.
        public void Remember(ulong full) => Remember(unchecked((int)full), full);

        private void Remember(int handle, ulong full)
        {
            if (entries.TryGetValue(handle, out Entry existing))
            {
                existing.Full = full;
                entries[handle] = existing;
            }
            else if (entries.Count == 0)
            {
                entries.Add(
                    handle,
                    new Entry
                    {
                        Full = full,
                        Previous = handle,
                        Next = handle,
                    }
                );
                first = handle;
                cursor = handle;
            }
            else
            {
                int last = entries[first].Previous;
                Entry tail = entries[last];
                tail.Next = handle;
                entries[last] = tail;
                Entry head = entries[first];
                head.Previous = handle;
                entries[first] = head;
                entries.Add(
                    handle,
                    new Entry
                    {
                        Full = full,
                        Previous = last,
                        Next = first,
                    }
                );
            }
            if (IsScheduled)
                return;
            nextPrune = clock() + 10;
            IsScheduled = true;
            EditorApplication.update += Update;
        }

        public Object Resolve(int handle)
        {
            if (!entries.TryGetValue(handle, out Entry entry))
                return null;
            Object value = resolve(entry.Full);
            if (value == null)
                Remove(handle, entry);
            return value;
        }

        private void Update()
        {
            if (remaining == 0)
            {
                if (clock() < nextPrune)
                    return;
                BeginSweep();
            }
            for (int inspected = 0; inspected < LookupsPerUpdate && remaining > 0; inspected++)
            {
                int handle = cursor;
                cursor = entries[handle].Next;
                remaining--;
                Resolve(handle);
            }
            if (remaining == 0)
                nextPrune = clock() + 10;
        }

        private void OnPlayModeStateChanged(PlayModeStateChange _) => BeginSweep();

        private void BeginSweep()
        {
            // Do not restart a sweep during repeated transitions. New handles join the
            // circular list and at the latest participate in the next complete sweep.
            if (remaining > 0 || entries.Count == 0)
                return;
            cursor = first;
            remaining = entries.Count;
        }

        private void Remove(int handle, Entry entry)
        {
            if (entries.Count == 1)
            {
                Clear();
                return;
            }
            Entry previous = entries[entry.Previous];
            previous.Next = entry.Next;
            entries[entry.Previous] = previous;
            Entry next = entries[entry.Next];
            next.Previous = entry.Previous;
            entries[entry.Next] = next;
            if (first == handle)
                first = entry.Next;
            if (cursor == handle)
                cursor = entry.Next;
            entries.Remove(handle);
        }

        public void Clear()
        {
            entries.Clear();
            first = 0;
            cursor = 0;
            remaining = 0;
            EditorApplication.update -= Update;
            IsScheduled = false;
        }

        public void Dispose()
        {
            Clear();
            AssemblyReloadEvents.beforeAssemblyReload -= Clear;
            EditorApplication.quitting -= Clear;
            EditorApplication.playModeStateChanged -= OnPlayModeStateChanged;
        }
    }
}
