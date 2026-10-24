using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class ObjectIdCacheMaintenanceTests
    {
        private sealed class CacheBenchmarkObject : ScriptableObject { }

        private static bool byteCounterSupported;
        private static bool allocationEventsSupported;

        private sealed class Probe : IDisposable
        {
            private readonly object cache;
            private readonly Type type = typeof(UnityObjectIdCompat).Assembly.GetType("MCPForUnity.Runtime.Helpers.EditorObjectIdCache");
            public readonly Action<ulong> Remember;
            public readonly Func<int, Object> Resolve;
            public readonly Action Update;
            public readonly Action Clear;
            public double Now;
            public int Count => (int)type.GetProperty("Count").GetValue(cache);

            public Probe(Func<ulong, Object> resolve)
            {
                cache = Activator.CreateInstance(
                    type,
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                    null,
                    new object[] { resolve, (Func<double>)(() => Now) },
                    null
                );
                Remember = (Action<ulong>)Delegate.CreateDelegate(typeof(Action<ulong>), cache, type.GetMethod("Remember"));
                Resolve = (Func<int, Object>)Delegate.CreateDelegate(typeof(Func<int, Object>), cache, type.GetMethod("Resolve"));
                Update = (Action)Delegate.CreateDelegate(typeof(Action), cache, type.GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic));
                Clear = (Action)Delegate.CreateDelegate(typeof(Action), cache, type.GetMethod("Clear"));
            }

            public void Dispose() => ((IDisposable)cache).Dispose();
        }

        [Test]
        public void LookupAndMaintenancePreserveFullWidthIdentityIncludingSignedAndZeroHandles()
        {
            var live = ScriptableObject.CreateInstance<CacheBenchmarkObject>();
            live.hideFlags = HideFlags.HideAndDontSave;
            var seen = new List<ulong>();
            ulong[] ids = { 0xFEDCBA9800000000UL, 0x12345678FFFFFFFFUL, 0xFFFFFFFF80000000UL, 0x876543217FFFFFFFUL };
            int[] handles = { 0, -1, int.MinValue, int.MaxValue };
            try
            {
                using (
                    var probe = new Probe(id =>
                    {
                        seen.Add(id);
                        return live;
                    })
                )
                {
                    foreach (ulong id in ids)
                        probe.Remember(id);
                    for (int i = 0; i < ids.Length; i++)
                    {
                        seen.Clear();
                        Assert.AreSame(live, probe.Resolve(handles[i]));
                        CollectionAssert.AreEqual(new[] { ids[i] }, seen);
                    }
                    probe.Remember(0xABCDEF01FFFFFFFFUL);
                    seen.Clear();
                    Assert.AreSame(live, probe.Resolve(-1));
                    CollectionAssert.AreEqual(new[] { 0xABCDEF01FFFFFFFFUL }, seen);
                    Assert.AreEqual(4, probe.Count);
                    seen.Clear();
                    probe.Now = 11;
                    probe.Update();
                    CollectionAssert.AreEquivalent(new[] { 0xFEDCBA9800000000UL, 0xABCDEF01FFFFFFFFUL, 0xFFFFFFFF80000000UL, 0x876543217FFFFFFFUL }, seen);
                    Assert.AreEqual(4, probe.Count);
                }
            }
            finally
            {
                Object.DestroyImmediate(live);
            }
        }

        [Test]
        public void UpdateBoundsNativeLookupsAndEventuallySweepsEveryDeadHandle()
        {
            int calls = 0;
            using (
                var probe = new Probe(_ =>
                {
                    calls++;
                    return null;
                })
            )
            {
                for (int i = 0; i < 10000; i++)
                    probe.Remember((ulong)i);
                probe.Now = 11;
                probe.Update();
                Assert.That(calls, Is.InRange(1, 128), "One Editor update must not resolve the entire large cache.");
                for (int tick = 0; tick < 100; tick++)
                {
                    int before = calls;
                    probe.Update();
                    Assert.That(calls - before, Is.LessThanOrEqualTo(128));
                }
                Assert.AreEqual(10000, calls);
                Assert.AreEqual(0, probe.Count);
            }
        }

        [Test]
        public void SweepSurvivesOverwriteRemovalAdditionClearAndDispose()
        {
            var live = ScriptableObject.CreateInstance<CacheBenchmarkObject>();
            live.hideFlags = HideFlags.HideAndDontSave;
            var alive = new HashSet<ulong>();
            using (var probe = new Probe(id => alive.Contains(id) ? live : null))
            {
                try
                {
                    for (int i = 0; i < 1000; i++)
                    {
                        alive.Add((ulong)i);
                        probe.Remember((ulong)i);
                    }
                    probe.Now = 11;
                    probe.Update();
                    // Remove both the current cursor and entries before/after it through public lookup.
                    for (int i = 0; i < 1000; i += 2)
                    {
                        alive.Remove((ulong)i);
                        Assert.IsNull(probe.Resolve(i));
                    }
                    for (int i = 1; i < 1000; i += 2)
                        Assert.AreSame(live, probe.Resolve(i));
                    probe.Remember(0x1234567800000001UL);
                    alive.Add(0x1234567800000001UL);
                    for (int i = 1000; i < 1250; i++)
                        probe.Remember((ulong)i); // Add dead handles during the sweep.
                    for (int tick = 0; tick < 50; tick++)
                    {
                        probe.Now += 1;
                        probe.Update();
                    }
                    Assert.AreEqual(500, probe.Count);
                    Assert.AreSame(live, probe.Resolve(1));
                    probe.Clear();
                    Assert.AreEqual(0, probe.Count);
                    probe.Remember(0x12345678FFFFFFFFUL);
                    alive.Add(0x12345678FFFFFFFFUL);
                    probe.Now += 11;
                    probe.Update();
                    Assert.AreSame(live, probe.Resolve(-1));
                    alive.Clear();
                    probe.Now += 11;
                    probe.Update();
                    Assert.AreEqual(0, probe.Count);
                }
                finally
                {
                    Object.DestroyImmediate(live);
                }
            }
        }

        [Test]
        public void PendingSweepDrainsASingletonAfterImmediateRemovalsAndCanRefill()
        {
            var live = ScriptableObject.CreateInstance<CacheBenchmarkObject>();
            live.hideFlags = HideFlags.HideAndDontSave;
            bool alive = true;
            try
            {
                using (var probe = new Probe(_ => alive ? live : null))
                {
                    for (ulong id = 0; id < 1000; id++)
                        probe.Remember(id);
                    probe.Now = 11;
                    probe.Update();
                    alive = false;
                    for (int handle = 0; handle < 999; handle++)
                        Assert.IsNull(probe.Resolve(handle));
                    Assert.AreEqual(1, probe.Count);
                    probe.Update();
                    Assert.AreEqual(0, probe.Count);
                    probe.Remember(0xFFFFFFFFFFFFFFFFUL);
                    probe.Now += 11;
                    probe.Update();
                    Assert.AreEqual(0, probe.Count);
                }
            }
            finally
            {
                Object.DestroyImmediate(live);
            }
        }

        [Test]
        public void NativeLookupFailureRetiresEarlierDeadRunAndPreservesRemainingLinks()
        {
            var live = ScriptableObject.CreateInstance<CacheBenchmarkObject>();
            live.hideFlags = HideFlags.HideAndDontSave;
            bool throwOnce = true;
            try
            {
                using (
                    var probe = new Probe(id =>
                    {
                        if (id == 2 && throwOnce)
                        {
                            throwOnce = false;
                            throw new InvalidOperationException("Owned lookup failure");
                        }
                        return id < 2 ? null : live;
                    })
                )
                {
                    for (ulong id = 0; id < 260; id++)
                        probe.Remember(id);
                    probe.Now = 11;
                    Assert.Throws<InvalidOperationException>(() => probe.Update());
                    Assert.AreEqual(258, probe.Count);
                    for (int tick = 0; tick < 4; tick++)
                        probe.Update();
                    probe.Now += 11;
                    for (int tick = 0; tick < 4; tick++)
                        probe.Update();
                    Assert.AreEqual(258, probe.Count);
                    Assert.AreSame(live, probe.Resolve(2));
                }
            }
            finally
            {
                Object.DestroyImmediate(live);
            }
        }

        // Opt in to this benchmark by its exact filter. It creates no scene or persistent asset.
        [Test, Explicit("Paired native maintenance benchmark; creates 32768 transient ScriptableObjects.")]
        public void BenchmarkPairedNativeLargeCache()
        {
            long counterBefore = GC.GetAllocatedBytesForCurrentThread();
            var calibration = new byte[65536];
            long counterDelta = GC.GetAllocatedBytesForCurrentThread() - counterBefore;
            GC.KeepAlive(calibration);
            byteCounterSupported = counterDelta >= calibration.Length;
            var calibrationArrays = new byte[32][];
            int calibrationEvents = ManagedAllocation.Count(() =>
            {
                for (int i = 0; i < calibrationArrays.Length; i++)
                    calibrationArrays[i] = new byte[4096];
            });
            GC.KeepAlive(calibrationArrays);
            allocationEventsSupported = calibrationEvents == calibrationArrays.Length;
            UnityEngine.Debug.Log(
                "BQR10_CALIBRATION allocated_byte_counter_supported="
                    + byteCounterSupported
                    + " known_payload_bytes=65536 byte_counter_delta="
                    + counterDelta
                    + " allocation_events="
                    + calibrationEvents
            );
            const int size = 32768;
            var objects = new CacheBenchmarkObject[size];
            var ids = new ulong[size];
            try
            {
                for (int i = 0; i < size; i++)
                {
                    objects[i] = ScriptableObject.CreateInstance<CacheBenchmarkObject>();
                    objects[i].hideFlags = HideFlags.HideAndDontSave;
#if UNITY_6000_5_OR_NEWER
                    ids[i] = EntityId.ToULong(objects[i].GetEntityId());
#else
                    ids[i] = unchecked((ulong)objects[i].GetInstanceID());
#endif
                }
                for (int phase = 0; phase < 3; phase++)
                {
                    if (phase > 0)
                        for (int i = phase == 1 ? 0 : 1; i < size; i += 2)
                            Object.DestroyImmediate(objects[i]);
                    for (int sample = 0; sample < 5; sample++)
                    {
                        // Alternate order to avoid consistently crediting the warm native lookup path.
                        if (sample % 2 == 0)
                        {
                            MeasureBaseline(ids, phase, sample);
                            MeasureCandidate(ids, phase, sample);
                        }
                        else
                        {
                            MeasureCandidate(ids, phase, sample);
                            MeasureBaseline(ids, phase, sample);
                        }
                    }
                }
            }
            finally
            {
                foreach (CacheBenchmarkObject value in objects)
                    if (value != null)
                        Object.DestroyImmediate(value);
            }
        }

        private static Object NativeResolve(ulong id)
        {
#if UNITY_6000_5_OR_NEWER
            return EditorUtility.EntityIdToObject(EntityId.FromULong(id));
#elif UNITY_6000_3_OR_NEWER
            return EditorUtility.EntityIdToObject(unchecked((int)id));
#else
            return EditorUtility.InstanceIDToObject(unchecked((int)id));
#endif
        }

        private static void MeasureBaseline(ulong[] ids, int phase, int sample)
        {
            long retainedBefore = GC.GetTotalMemory(true);
            var entries = new Dictionary<int, ulong>();
            for (int i = 0; i < ids.Length; i++)
                entries.Add(unchecked((int)ids[i]), ids[i]);
            long retained = GC.GetTotalMemory(true) - retainedBefore;
            long before = GC.GetAllocatedBytesForCurrentThread();
            long start = Stopwatch.GetTimestamp();
            foreach (int handle in new List<int>(entries.Keys))
                if (NativeResolve(entries[handle]) == null)
                    entries.Remove(handle);
            double ms = (Stopwatch.GetTimestamp() - start) * 1000.0 / Stopwatch.Frequency;
            long allocated = GC.GetAllocatedBytesForCurrentThread() - before;
            int retainedLive = entries.Count;
            // Count GC.Alloc events on a separate identical pass so recorder setup does not affect timings.
            entries.Clear();
            for (int i = 0; i < ids.Length; i++)
                entries.Add(unchecked((int)ids[i]), ids[i]);
            int events = ManagedAllocation.Count(() =>
            {
                foreach (int handle in new List<int>(entries.Keys))
                    if (NativeResolve(entries[handle]) == null)
                        entries.Remove(handle);
            });
            Emit("baseline", phase, sample, ms, ms, ms, ms, 1, allocated, events, retained, retainedLive, ids.Length - retainedLive);
            GC.KeepAlive(entries);
        }

        private static void MeasureCandidate(ulong[] ids, int phase, int sample)
        {
            int calls = 0;
            var times = new List<double>(1024);
            long retainedBefore = GC.GetTotalMemory(true);
            using (
                var probe = new Probe(id =>
                {
                    calls++;
                    return NativeResolve(id);
                })
            )
            {
                for (int i = 0; i < ids.Length; i++)
                    probe.Remember(ids[i]);
                long retained = GC.GetTotalMemory(true) - retainedBefore;
                probe.Now = 11;
                long before = GC.GetAllocatedBytesForCurrentThread();
                while (calls < ids.Length)
                {
                    long start = Stopwatch.GetTimestamp();
                    probe.Update();
                    times.Add((Stopwatch.GetTimestamp() - start) * 1000.0 / Stopwatch.Frequency);
                    if (times.Count >= 1024)
                        throw new InvalidOperationException("The sweep must progress without another ten second delay.");
                }
                long allocated = GC.GetAllocatedBytesForCurrentThread() - before;
                double total = 0;
                foreach (double elapsed in times)
                    total += elapsed;
                times.Sort();
                int retainedLive = probe.Count;
                Assert.AreEqual(ids.Length, calls);
                Assert.AreEqual(
                    phase == 0 ? ids.Length
                        : phase == 1 ? ids.Length / 2
                        : 0,
                    retainedLive
                );
                probe.Clear();
                for (int i = 0; i < ids.Length; i++)
                    probe.Remember(ids[i]);
                probe.Now += 11;
                calls = 0;
                int events = ManagedAllocation.Count(() =>
                {
                    for (int tick = 0; tick < 1024 && calls < ids.Length; tick++)
                        probe.Update();
                });
                Assert.AreEqual(ids.Length, calls);
                Assert.AreEqual(retainedLive, probe.Count);
                Emit(
                    "candidate",
                    phase,
                    sample,
                    times[(int)((times.Count - 1) * .95)],
                    times[(int)((times.Count - 1) * .99)],
                    times[times.Count - 1],
                    total,
                    times.Count,
                    allocated,
                    events,
                    retained,
                    retainedLive,
                    ids.Length - retainedLive
                );
            }
        }

        private static void Emit(
            string strategy,
            int phase,
            int sample,
            double p95,
            double p99,
            double max,
            double total,
            int ticks,
            long allocated,
            int events,
            long retained,
            int live,
            int dead
        )
        {
            UnityEngine.Debug.Log(
                string.Format(
                    System.Globalization.CultureInfo.InvariantCulture,
                    "BQR10 strategy={0} phase={1} sample={2} p95_ms={3:F6} p99_ms={4:F6} max_ms={5:F6} total_ms={6:F6} ticks={7} allocated_bytes={8} allocation_events={9} managed_heap_delta_bytes={10} retained_live={11} removed_dead={12}",
                    strategy,
                    phase,
                    sample,
                    p95,
                    p99,
                    max,
                    total,
                    ticks,
                    byteCounterSupported ? allocated.ToString() : "unavailable",
                    allocationEventsSupported ? events.ToString() : "unavailable",
                    retained,
                    live,
                    dead
                )
            );
        }
    }
}
