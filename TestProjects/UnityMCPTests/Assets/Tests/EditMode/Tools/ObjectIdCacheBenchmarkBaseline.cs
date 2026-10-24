// Frozen beta e0808a32 maintenance algorithm for paired native benchmarks.
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
            // The dictionary key already contains the low 32 bits of the full ID.
            public uint Upper;
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

        public void Remember(ulong full)
        {
            int handle = unchecked((int)full);
            uint upper = (uint)(full >> 32);
            if (entries.TryGetValue(handle, out Entry existing))
            {
                if (existing.Upper != upper)
                {
                    existing.Upper = upper;
                    entries[handle] = existing;
                }
            }
            else if (entries.Count == 0)
            {
                entries.Add(
                    handle,
                    new Entry
                    {
                        Upper = upper,
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
                        Upper = upper,
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
            Object value = resolve(((ulong)entry.Upper << 32) | unchecked((uint)handle));
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
            int deadStart = 0;
            int deadCount = 0;
            Entry deadFirst = default;
            Entry deadLast = default;
            try
            {
                for (int inspected = 0; inspected < LookupsPerUpdate && remaining > 0; inspected++)
                {
                    int handle = cursor;
                    Entry entry = entries[handle];
                    cursor = entry.Next;
                    remaining--;
                    if (resolve(((ulong)entry.Upper << 32) | unchecked((uint)handle)) == null)
                    {
                        if (deadCount == 0)
                        {
                            deadStart = handle;
                            deadFirst = entry;
                        }
                        deadLast = entry;
                        deadCount++;
                        // Public lookups may have removed most of the original sweep
                        // between updates. Do not circle a wholly dead remaining ring.
                        if (deadCount == entries.Count)
                        {
                            RemoveRun(deadStart, deadFirst, deadLast, deadCount);
                            deadCount = 0;
                        }
                    }
                    else if (deadCount > 0)
                    {
                        RemoveRun(deadStart, deadFirst, deadLast, deadCount);
                        deadCount = 0;
                    }
                }
            }
            finally
            {
                // Keep links coherent throughout native resolution and retire each run
                // before returning, including when a native lookup throws.
                if (deadCount > 0)
                    RemoveRun(deadStart, deadFirst, deadLast, deadCount);
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
                entries.Remove(handle);
                StopMaintenance();
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

        private void RemoveRun(int start, Entry head, Entry tail, int count)
        {
            if (count == 1)
            {
                Remove(start, head);
                return;
            }
            int handle = start;
            for (int removed = 0; removed < count; removed++)
            {
                int next = entries[handle].Next;
                entries.Remove(handle);
                if (first == handle)
                    first = tail.Next;
                if (cursor == handle)
                    cursor = tail.Next;
                handle = next;
            }
            if (entries.Count == 0)
            {
                // Remove already cleared the occupied slots. Avoid clearing the entire
                // retained capacity on the final bounded update of a large sweep.
                StopMaintenance();
                return;
            }
            Entry previous = entries[head.Previous];
            previous.Next = tail.Next;
            entries[head.Previous] = previous;
            Entry nextEntry = entries[tail.Next];
            nextEntry.Previous = head.Previous;
            entries[tail.Next] = nextEntry;
        }

        public void Clear()
        {
            entries.Clear();
            StopMaintenance();
        }

        private void StopMaintenance()
        {
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
