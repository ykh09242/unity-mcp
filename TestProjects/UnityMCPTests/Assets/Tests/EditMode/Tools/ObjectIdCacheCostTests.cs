using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class ObjectIdCacheCostTests
    {
        private sealed class TransientObject : ScriptableObject { }

#if UNITY_EDITOR_WIN
        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentThread();

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool QueryThreadCycleTime(IntPtr thread, out ulong cycles);
#endif

        private static bool TryThreadCycles(out ulong cycles)
        {
#if UNITY_EDITOR_WIN
            return QueryThreadCycleTime(GetCurrentThread(), out cycles);
#else
            cycles = 0;
            return false;
#endif
        }

        private sealed class Probe : IDisposable
        {
            private readonly object cache;
            private readonly Type type;
            internal readonly Action<ulong> Remember;
            internal readonly Func<int, Object> Resolve;
            internal readonly Action Update;
            internal readonly Action Clear;
            internal double Now;
            internal int Calls;
            internal int Count => (int)type.GetProperty("Count").GetValue(cache);

            internal Probe(bool baseline)
            {
                type = baseline
                    ? typeof(ObjectIdCacheBenchmarkBaseline)
                    : typeof(UnityObjectIdCompat).Assembly.GetType("MCPForUnity.Runtime.Helpers.EditorObjectIdCache");
                cache = Activator.CreateInstance(
                    type,
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                    null,
                    new object[]
                    {
                        (Func<ulong, Object>)(
                            id =>
                            {
                                Calls++;
                                return NativeResolve(id);
                            }
                        ),
                        (Func<double>)(() => Now),
                    },
                    null
                );
                Remember = (Action<ulong>)Delegate.CreateDelegate(typeof(Action<ulong>), cache, type.GetMethod("Remember"));
                Resolve = (Func<int, Object>)Delegate.CreateDelegate(typeof(Func<int, Object>), cache, type.GetMethod("Resolve"));
                Update = (Action)Delegate.CreateDelegate(typeof(Action), cache, type.GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic));
                Clear = (Action)Delegate.CreateDelegate(typeof(Action), cache, type.GetMethod("Clear"));
            }

            internal long BackingPayload()
            {
                object entries = type.GetField("entries", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(cache);
                long bytes = 0;
                int arrays = 0;
                foreach (FieldInfo field in entries.GetType().GetFields(BindingFlags.Instance | BindingFlags.NonPublic))
                {
                    if (!(field.GetValue(entries) is Array array))
                        continue;
                    Assert.Greater(array.Length, 1, "Backing stride measurement needs at least two elements.");
                    bytes += (long)
                        typeof(ObjectIdCacheCostTests)
                            .GetMethod(nameof(ArrayPayload), BindingFlags.Static | BindingFlags.NonPublic)
                            .MakeGenericMethod(array.GetType().GetElementType())
                            .Invoke(null, new object[] { array });
                    arrays++;
                }
                Assert.AreEqual(2, arrays, "Report bucket and entry payload only; do not silently omit a backing array.");
                return bytes;
            }

            public void Dispose() => ((IDisposable)cache).Dispose();
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

        private static long ArrayPayload<T>(T[] array)
        {
            var pinned = GCHandle.Alloc(array, GCHandleType.Pinned);
            try
            {
                long stride = Marshal.UnsafeAddrOfPinnedArrayElement(array, 1).ToInt64() - Marshal.UnsafeAddrOfPinnedArrayElement(array, 0).ToInt64();
                Assert.Greater(stride, 0);
                return stride * array.LongLength;
            }
            finally
            {
                pinned.Free();
            }
        }

        private static ulong FullId(Object value)
        {
#if UNITY_6000_5_OR_NEWER
            return EntityId.ToULong(value.GetEntityId());
#else
            return unchecked((ulong)value.GetInstanceID());
#endif
        }

        [TestCase(32768, false)]
        [TestCase(131072, false)]
        [TestCase(32768, true)]
        [TestCase(131072, true)]
        [Explicit("Paired native CPU, bounded update, allocation and backing-array measurements; transient objects only.")]
        public void BenchmarkPairedMaintenanceCost(int size, bool clustered)
        {
            Assert.AreEqual(12, ArrayPayload(new int[3]));
            Assert.AreEqual(24, ArrayPayload(new ulong[3]));
            long before = GC.GetAllocatedBytesForCurrentThread();
            var calibration = new byte[65536];
            long byteDelta = GC.GetAllocatedBytesForCurrentThread() - before;
            GC.KeepAlive(calibration);
            var arrays = new byte[32][];
            int eventDelta = ManagedAllocation.Count(() =>
            {
                for (int i = 0; i < arrays.Length; i++)
                    arrays[i] = new byte[4096];
            });
            GC.KeepAlive(arrays);
            bool bytesAvailable = byteDelta >= 65536;
            bool eventsAvailable = eventDelta == 32;
            UnityEngine.Debug.Log($"CACHE_COST_CALIBRATION size={size} clustered={clustered} byte_delta={byteDelta} events={eventDelta}");

            var objects = new TransientObject[size];
            var ids = new ulong[size];
            try
            {
                for (int i = 0; i < size; i++)
                {
                    objects[i] = ScriptableObject.CreateInstance<TransientObject>();
                    objects[i].hideFlags = HideFlags.HideAndDontSave;
                    ids[i] = FullId(objects[i]);
                }
                // Warm both implementations, including the native call and JIT, before paired samples.
                Measure(ids, 0, clustered, -1, true, bytesAvailable, eventsAvailable);
                Measure(ids, 0, clustered, -1, false, bytesAvailable, eventsAvailable);
                if (!clustered)
                    MeasureChurn(ids);
                for (int phase = 0; phase < 3; phase++)
                {
                    if (phase > 0)
                        for (int i = 0; i < size; i++)
                            if ((clustered ? i < size / 2 : i % 2 == 0) == (phase == 1))
                                Object.DestroyImmediate(objects[i]);
                    for (int sample = 0; sample < 7; sample++)
                    {
                        Measure(ids, phase, clustered, sample, sample % 2 == 0, bytesAvailable, eventsAvailable);
                        Measure(ids, phase, clustered, sample, sample % 2 != 0, bytesAvailable, eventsAvailable);
                    }
                }
            }
            finally
            {
                foreach (TransientObject value in objects)
                    if (value != null)
                        Object.DestroyImmediate(value);
            }
        }

        private static void Measure(ulong[] ids, int phase, bool clustered, int sample, bool baseline, bool bytesAvailable, bool eventsAvailable)
        {
            var ticks = new double[(ids.Length + 127) / 128];
            using (var probe = new Probe(baseline))
            {
                long started = Stopwatch.GetTimestamp();
                foreach (ulong id in ids)
                    probe.Remember(id);
                double insert = Elapsed(started);
                started = Stopwatch.GetTimestamp();
                foreach (ulong id in ids)
                    probe.Remember(id);
                double overwrite = Elapsed(started);
                // Full-width native IDs, not unrelated sequential surrogate keys.
                if (phase == 0)
                {
                    started = Stopwatch.GetTimestamp();
                    foreach (ulong id in ids)
                        probe.Resolve(unchecked((int)id));
                }
                double lookup = phase == 0 ? Elapsed(started) : 0;
                long payload = probe.BackingPayload();
                probe.Calls = 0;
                probe.Now = 11;
                long bytes = 0;
                int minimumCalls = int.MaxValue;
                int maximumCalls = 0;
                bool cyclesAvailable = TryThreadCycles(out ulong cyclesBefore);
                for (int tick = 0; tick < ticks.Length; tick++)
                {
                    int callsBefore = probe.Calls;
                    long bytesBefore = GC.GetAllocatedBytesForCurrentThread();
                    started = Stopwatch.GetTimestamp();
                    probe.Update();
                    ticks[tick] = Elapsed(started);
                    bytes += GC.GetAllocatedBytesForCurrentThread() - bytesBefore;
                    minimumCalls = Math.Min(minimumCalls, probe.Calls - callsBefore);
                    maximumCalls = Math.Max(maximumCalls, probe.Calls - callsBefore);
                }
                cyclesAvailable = TryThreadCycles(out ulong cyclesAfter) && cyclesAvailable && cyclesAfter > cyclesBefore;
                ulong cycles = cyclesAfter - cyclesBefore;
                Assert.That(minimumCalls, Is.GreaterThan(0));
                Assert.That(maximumCalls, Is.LessThanOrEqualTo(128));
                int expected =
                    phase == 0 ? ids.Length
                    : phase == 1 ? ids.Length / 2
                    : 0;
                Assert.AreEqual(ids.Length, probe.Calls);
                Assert.AreEqual(expected, probe.Count);
                double total = 0;
                foreach (double tick in ticks)
                    total += tick;
                Array.Sort(ticks);
                long afterPayload = probe.BackingPayload();

                // Separate allocation pass: assertions, recorder setup, timing arrays and reflection are excluded.
                probe.Clear();
                foreach (ulong id in ids)
                    probe.Remember(id);
                probe.Calls = 0;
                probe.Now += 11;
                int events = ManagedAllocation.Count(() =>
                {
                    for (int tick = 0; tick < ticks.Length; tick++)
                        probe.Update();
                });
                Assert.AreEqual(ids.Length, probe.Calls);
                Assert.AreEqual(expected, probe.Count);
                UnityEngine.Debug.Log(
                    string.Format(
                        CultureInfo.InvariantCulture,
                        "CACHE_COST strategy={0} size={1} clustered={2} phase={3} sample={4} total_ms={5:F6} p95_ms={6:F6} p99_ms={7:F6} max_ms={8:F6} ticks={9} events={10} allocated_bytes={11} backing_payload={12} after_payload={13} insert_ms={14:F6} overwrite_ms={15:F6} lookup_ms={16:F6} cpu_cycles={17}",
                        baseline ? "beta" : "candidate",
                        ids.Length,
                        clustered,
                        phase,
                        sample,
                        total,
                        ticks[(int)((ticks.Length - 1) * .95)],
                        ticks[(int)((ticks.Length - 1) * .99)],
                        ticks[ticks.Length - 1],
                        ticks.Length,
                        eventsAvailable ? events.ToString() : "unavailable",
                        bytesAvailable ? bytes.ToString() : "unavailable",
                        payload,
                        afterPayload,
                        insert,
                        overwrite,
                        lookup,
                        cyclesAvailable ? cycles.ToString(CultureInfo.InvariantCulture) : "unavailable"
                    )
                );
            }
        }

        private static void MeasureChurn(ulong[] liveIds)
        {
            using (var baseline = new Probe(true))
            using (var candidate = new Probe(false))
            {
                foreach (ulong id in liveIds)
                {
                    baseline.Remember(id);
                    candidate.Remember(id);
                }
                var transient = new TransientObject[4096];
                var ids = new ulong[transient.Length];
                try
                {
                    for (int cycle = 0; cycle < 8; cycle++)
                    {
                        for (int i = 0; i < transient.Length; i++)
                        {
                            transient[i] = ScriptableObject.CreateInstance<TransientObject>();
                            transient[i].hideFlags = HideFlags.HideAndDontSave;
                            ids[i] = FullId(transient[i]);
                        }
                        foreach (ulong id in ids)
                        {
                            baseline.Remember(id);
                            candidate.Remember(id);
                        }
                        foreach (TransientObject value in transient)
                            Object.DestroyImmediate(value);
                        for (int order = 0; order < 2; order++)
                        {
                            bool isBaseline = (cycle + order) % 2 == 0;
                            Probe probe = isBaseline ? baseline : candidate;
                            long started = Stopwatch.GetTimestamp();
                            foreach (ulong id in ids)
                                probe.Resolve(unchecked((int)id));
                            double elapsed = Elapsed(started);
                            Assert.AreEqual(liveIds.Length, probe.Count);
                            UnityEngine.Debug.Log(
                                string.Format(
                                    CultureInfo.InvariantCulture,
                                    "CACHE_CHURN strategy={0} size={1} cycle={2} removal_ms={3:F6} backing_payload={4} retained_live={5}",
                                    isBaseline ? "beta" : "candidate",
                                    liveIds.Length,
                                    cycle,
                                    elapsed,
                                    probe.BackingPayload(),
                                    probe.Count
                                )
                            );
                        }
                    }
                }
                finally
                {
                    foreach (TransientObject value in transient)
                        if (value != null)
                            Object.DestroyImmediate(value);
                }
            }
        }

        private static double Elapsed(long started) => (Stopwatch.GetTimestamp() - started) * 1000.0 / Stopwatch.Frequency;
    }
}
