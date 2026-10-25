// Frozen beta 356e2462 indexed storage for paired native benchmarks.
// Keep this test-only baseline unchanged when optimizing production.
using UnityEditor;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    internal sealed class ObjectIdCacheBenchmarkBaseline : System.IDisposable
    {
        private struct Entry
        {
            public int Handle;
            public uint Upper;
            public int Previous;
            public int Next;
            public int HashNext;
        }

        // Slot indices stay stable across growth, so traversal and ring repair do
        // not hash handles. HashNext belongs to a bucket OR the free list, never both.
        private Entry[] entries = System.Array.Empty<Entry>();
        private int[] buckets = System.Array.Empty<int>();
        private int used;
        private int free;
        private int count;
        private const int LookupsPerUpdate = 128;

        // Follow Dictionary's doubling/prime growth trajectory on supported Mono
        // without depending on private runtime APIs or allocating a probe dictionary.
        private static readonly int[] Capacities =
        {
            3,
            7,
            17,
            37,
            89,
            197,
            431,
            919,
            1931,
            4049,
            8419,
            17519,
            36353,
            75431,
            156437,
            324449,
            672827,
            1395263,
            2893249,
            5999471,
        };
        private readonly System.Func<ulong, Object> resolve;
        private readonly System.Func<double> clock;
        private double nextPrune;
        private int first;
        private int cursor;
        private int remaining;
        private uint mutationVersion;
        public int Count => count;
        public bool IsScheduled { get; private set; }

        public ObjectIdCacheBenchmarkBaseline(System.Func<ulong, Object> resolve, System.Func<double> clock)
        {
            this.resolve = resolve;
            this.clock = clock;
            AssemblyReloadEvents.beforeAssemblyReload += Clear;
            EditorApplication.quitting += Clear;
            EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
        }

        private int Find(int handle)
        {
            if (count == 0)
                return -1;
            for (int slot = buckets[(handle & int.MaxValue) % buckets.Length] - 1; slot >= 0; slot = entries[slot].HashNext - 1)
                if (entries[slot].Handle == handle)
                    return slot;
            return -1;
        }

        public void Remember(ulong full)
        {
            int handle = unchecked((int)full);
            uint upper = (uint)(full >> 32);
            int slot = Find(handle);
            if (slot >= 0)
            {
                if (entries[slot].Upper != upper)
                {
                    mutationVersion++;
                    entries[slot].Upper = upper;
                }
            }
            else
            {
                if (free == 0 && used == entries.Length)
                    Grow();
                if (free != 0)
                {
                    slot = free - 1;
                    free = entries[slot].HashNext;
                }
                else
                    slot = used++;
                int bucket = (handle & int.MaxValue) % buckets.Length;
                int last = count == 0 ? slot : entries[first].Previous;
                entries[slot] = new Entry
                {
                    Handle = handle,
                    Upper = upper,
                    Previous = last,
                    Next = count == 0 ? slot : first,
                    HashNext = buckets[bucket],
                };
                buckets[bucket] = slot + 1;
                if (count == 0)
                    first = cursor = slot;
                else
                {
                    entries[last].Next = slot;
                    entries[first].Previous = slot;
                }
                count++;
                mutationVersion++;
            }
            if (IsScheduled)
                return;
            nextPrune = clock() + 10;
            IsScheduled = true;
            EditorApplication.update += Update;
        }

        private void Grow()
        {
            int capacity = NextCapacity(entries.Length);
            var newEntries = new Entry[capacity];
            var newBuckets = new int[capacity];
            System.Array.Copy(entries, newEntries, used);
            // Free slots reuse HashNext. Only live ring members can be rehashed.
            int slot = first;
            for (int copied = 0; copied < count; copied++)
            {
                int bucket = (newEntries[slot].Handle & int.MaxValue) % capacity;
                newEntries[slot].HashNext = newBuckets[bucket];
                newBuckets[bucket] = slot + 1;
                slot = newEntries[slot].Next;
            }
            // Publish only after both allocations and the rebuild succeed.
            entries = newEntries;
            buckets = newBuckets;
        }

        private static int NextCapacity(int current)
        {
            foreach (int capacity in Capacities)
                if (capacity > current)
                    return capacity;
            const int maxArrayLength = 0x7FEFFFFD;
            if (current >= maxArrayLength)
                throw new System.OutOfMemoryException();
            long desired = (long)current * 2;
            if (desired >= maxArrayLength)
                return maxArrayLength;
            // Continue growing beyond the table; no project-size limit is imposed.
            for (int candidate = (int)desired | 1; candidate < maxArrayLength; candidate += 2)
            {
                if ((candidate - 1) % 101 == 0)
                    continue;
                bool prime = true;
                for (int divisor = 3; (long)divisor * divisor <= candidate; divisor += 2)
                    if (candidate % divisor == 0)
                    {
                        prime = false;
                        break;
                    }
                if (prime)
                    return candidate;
            }
            return maxArrayLength;
        }

        public Object Resolve(int handle)
        {
            int slot = Find(handle);
            if (slot < 0)
                return null;
            uint upper = entries[slot].Upper;
            uint version = mutationVersion;
            Object value = resolve(((ulong)upper << 32) | unchecked((uint)handle));
            if (value == null)
            {
                if (version != mutationVersion)
                {
                    // A callback may have cleared, grown or reused this slot.
                    slot = Find(handle);
                    if (slot < 0 || entries[slot].Upper != upper)
                        return value;
                }
                mutationVersion++;
                Remove(slot);
            }
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
            uint version = mutationVersion;
            try
            {
                for (int inspected = 0; inspected < LookupsPerUpdate && remaining > 0; inspected++)
                {
                    int slot = cursor;
                    Entry entry = entries[slot];
                    cursor = entry.Next;
                    remaining--;
                    // Never keep an array/ref across the native callback: it can grow
                    // or replace storage. Reacquire the current field each iteration.
                    if (resolve(((ulong)entry.Upper << 32) | unchecked((uint)entry.Handle)) == null)
                    {
                        if (version != mutationVersion)
                            break;
                        Remove(slot);
                    }
                }
            }
            finally
            {
                // Resume from the public-mutation-maintained cursor, not a loaded prefix.
                if (version != mutationVersion)
                    remaining = count;
            }
            if (remaining == 0)
                nextPrune = clock() + 10;
        }

        private void OnPlayModeStateChanged(PlayModeStateChange _) => BeginSweep();

        private void BeginSweep()
        {
            if (remaining > 0 || count == 0)
                return;
            cursor = first;
            remaining = count;
        }

        private void Remove(int slot)
        {
            Entry entry = entries[slot];
            int bucket = (entry.Handle & int.MaxValue) % buckets.Length;
            int previous = -1;
            for (int current = buckets[bucket] - 1; current != slot; current = entries[current].HashNext - 1)
                previous = current;
            if (previous < 0)
                buckets[bucket] = entry.HashNext;
            else
                entries[previous].HashNext = entry.HashNext;
            count--;
            if (count == 0)
            {
                // Every bucket was unlinked individually. No capacity-wide clear is
                // needed on the final bounded update; these entries contain no refs.
                used = free = 0;
                StopMaintenance();
                return;
            }
            entries[entry.Previous].Next = entry.Next;
            entries[entry.Next].Previous = entry.Previous;
            if (first == slot)
                first = entry.Next;
            if (cursor == slot)
                cursor = entry.Next;
            entries[slot].HashNext = free;
            free = slot + 1;
        }

        public void Clear()
        {
            mutationVersion++;
            if (count != 0)
                System.Array.Clear(buckets, 0, buckets.Length);
            count = used = free = 0;
            StopMaintenance();
        }

        private void StopMaintenance()
        {
            first = cursor = remaining = 0;
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
