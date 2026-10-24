using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
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
                if (entries is Array slots)
                {
                    var buckets = (int[])type.GetField("buckets", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(cache);
                    return MeasureArray(slots) + MeasureArray(buckets);
                }
                long bytes = 0;
                int arrays = 0;
                foreach (FieldInfo field in entries.GetType().GetFields(BindingFlags.Instance | BindingFlags.NonPublic))
                {
                    if (!(field.GetValue(entries) is Array array))
                        continue;
                    bytes += MeasureArray(array);
                    arrays++;
                }
                Assert.AreEqual(2, arrays, "Report bucket and entry payload only; do not silently omit a backing array.");
                return bytes;
            }

            private static long MeasureArray(Array array)
            {
                if (array.Length == 0)
                    return 0;
                Assert.Greater(array.Length, 1, "Backing stride measurement needs at least two elements.");
                return (long)
                    typeof(ObjectIdCacheCostTests)
                        .GetMethod(nameof(ArrayPayload), BindingFlags.Static | BindingFlags.NonPublic)
                        .MakeGenericMethod(array.GetType().GetElementType())
                        .Invoke(null, new object[] { array });
            }

            public void Dispose() => ((IDisposable)cache).Dispose();

            // Experimental zero-live idle release only. Production policy stays unchanged.
            internal void ReleaseEmptyBacking()
            {
                Assert.AreEqual(0, Count);
                foreach (string name in new[] { "entries", "buckets" })
                {
                    FieldInfo field = type.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
                    field.SetValue(cache, Array.CreateInstance(field.FieldType.GetElementType(), 0));
                }
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

        [TestCase(32768, 1)]
        [TestCase(32768, 301)]
        [TestCase(131072, 1)]
        [TestCase(131072, 301)]
        [Explicit("Compare retained indexed storage with a simulated five-minute empty-idle release; no production policy change.")]
        public void BenchmarkEmptyIdleRefill(int size, int idleSeconds)
        {
            var calibration = new byte[32][];
            int calibrationEvents = ManagedAllocation.Count(() =>
            {
                for (int i = 0; i < calibration.Length; i++)
                    calibration[i] = new byte[4096];
            });
            GC.KeepAlive(calibration);
            bool eventsAvailable = calibrationEvents == calibration.Length;
            var ticks = new double[(size + 127) / 128];
            // Sequential full-width keys isolate growth cost from native object creation.
            // Both variants use the exact production indexed storage and identical IDs.
            for (int sample = -1; sample < 7; sample++)
            for (int order = 0; order < 2; order++)
            {
                bool trim = (sample + order) % 2 == 0;
                using (var probe = new Probe(false))
                {
                    for (int i = 0; i < size; i++)
                        probe.Remember(0x1234567800000000UL | (uint)i);
                    long peak = probe.BackingPayload();
                    // Clear and final-dead removal share the same empty storage state.
                    probe.Clear();
                    probe.Now += idleSeconds;
                    if (trim && idleSeconds >= 300)
                        probe.ReleaseEmptyBacking();
                    long idlePayload = probe.BackingPayload();
                    bool cyclesAvailable = TryThreadCycles(out ulong before);
                    for (int batch = 0; batch < ticks.Length; batch++)
                    {
                        long started = Stopwatch.GetTimestamp();
                        for (int i = batch * 128; i < Math.Min(size, (batch + 1) * 128); i++)
                            probe.Remember(0x1234567800000000UL | (uint)i);
                        ticks[batch] = Elapsed(started);
                    }
                    cyclesAvailable = TryThreadCycles(out ulong after) && cyclesAvailable && after > before;
                    Assert.AreEqual(size, probe.Count);
                    Assert.AreEqual(0, probe.Calls);
                    Assert.AreEqual(peak, probe.BackingPayload());
                    double total = 0;
                    foreach (double tick in ticks)
                        total += tick;
                    Array.Sort(ticks);
                    probe.Clear();
                    if (trim && idleSeconds >= 300)
                        probe.ReleaseEmptyBacking();
                    int events = ManagedAllocation.Count(() =>
                    {
                        for (int i = 0; i < size; i++)
                            probe.Remember(0x1234567800000000UL | (uint)i);
                    });
                    UnityEngine.Debug.Log(
                        string.Format(
                            CultureInfo.InvariantCulture,
                            "CACHE_IDLE size={0} idle_seconds={1} sample={2} strategy={3} idle_payload={4} peak_payload={5} refill_ms={6:F6} p99_ms={7:F6} max_ms={8:F6} cpu_cycles={9} events={10}",
                            size,
                            idleSeconds,
                            sample,
                            trim ? "idle_release" : "retain",
                            idlePayload,
                            peak,
                            total,
                            ticks[(int)((ticks.Length - 1) * .99)],
                            ticks[ticks.Length - 1],
                            cyclesAvailable ? (after - before).ToString(CultureInfo.InvariantCulture) : "unavailable",
                            eventsAvailable ? events.ToString(CultureInfo.InvariantCulture) : "unavailable"
                        )
                    );
                }
            }
        }

        private static Object[] warmAssetRoots;

        [UnityTest]
        [Explicit("Native persistent/transient warm/cold identity and loading experiment with real UnloadUnusedAssets.")]
        public IEnumerator BenchmarkNativeColdAssetMaintenance()
        {
            string folder = "Assets/__ObjectIdColdCache_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folder.Substring("Assets/".Length));
            var coldIds = new ulong[16];
            var paths = new string[coldIds.Length];
            var guids = new string[coldIds.Length];
            var localIds = new long[coldIds.Length];
            var transient = ScriptableObject.CreateInstance<TransientObject>();
            transient.hideFlags = HideFlags.HideAndDontSave;
            ulong transientId = FullId(transient);
            var dead = ScriptableObject.CreateInstance<TransientObject>();
            ulong deadId = FullId(dead);
            Object.DestroyImmediate(dead);
            try
            {
                string warmPath = folder + "/warm.asset";
                warmAssetRoots = new Object[] { CreatePersistentTexture(warmPath) };
                ulong warmId = FullId(warmAssetRoots[0]);
                Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(warmAssetRoots[0], out string warmGuid, out long warmLocalId));
                for (int i = 0; i < coldIds.Length; i++)
                {
                    paths[i] = folder + "/cold" + i + ".asset";
                    coldIds[i] = CreatePersistentTextureId(paths[i], out guids[i], out localIds[i]);
                }
                AssetDatabase.SaveAssets();
                yield return UnityEngine.Resources.UnloadUnusedAssets();
                UnityEngine.Debug.Log($"CACHE_COLD_INITIAL loaded={CountLoaded(coldIds)} total={coldIds.Length}");
                for (int sample = 0; sample < 7; sample++)
                {
                    // Force a known cold start independently of Editor bookkeeping roots.
                    UnloadPersistentTextures(paths);
                    yield return UnityEngine.Resources.UnloadUnusedAssets();
                    Assert.AreEqual(0, CountLoaded(coldIds), "Cold rows require verified unloaded native objects.");
                    // Re-establish the warm control explicitly; a managed wrapper alone
                    // does not establish loaded native state across unused-asset collection.
                    warmAssetRoots[0] = AssetDatabase.LoadAssetAtPath<Texture2D>(warmPath);
                    Assert.AreEqual(1, CountLoaded(new[] { warmId }));
                    using (var probe = new Probe(false))
                    {
                        foreach (ulong id in coldIds)
                            probe.Remember(id);
                        probe.Remember(warmId);
                        probe.Remember(transientId);
                        probe.Remember(deadId);
                        probe.Now = 11;
                        bool available = TryThreadCycles(out ulong before);
                        long started = Stopwatch.GetTimestamp();
                        probe.Update();
                        double coldMs = Elapsed(started);
                        available = TryThreadCycles(out ulong after) && available && after > before;
                        Assert.AreEqual(coldIds.Length + 2, probe.Count, "Only the destroyed transient ID may expire.");
                        int loaded = CountLoaded(coldIds);
                        long nativeBytes = 0;
                        for (int i = 0; i < coldIds.Length; i++)
                        {
                            Object value = probe.Resolve(unchecked((int)coldIds[i]));
                            AssertPersistentIdentity(value, coldIds[i], paths[i], guids[i], localIds[i]);
                            nativeBytes += UnityEngine.Profiling.Profiler.GetRuntimeMemorySizeLong(value);
                        }
                        AssertPersistentIdentity(probe.Resolve(unchecked((int)warmId)), warmId, warmPath, warmGuid, warmLocalId);
                        Object resolvedTransient = probe.Resolve(unchecked((int)transientId));
                        Assert.IsTrue(resolvedTransient == transient);
                        Assert.AreEqual(transientId, FullId(resolvedTransient));
                        Assert.IsNull(probe.Resolve(unchecked((int)deadId)));
                        probe.Now += 11;
                        started = Stopwatch.GetTimestamp();
                        probe.Update();
                        double warmMs = Elapsed(started);
                        UnityEngine.Debug.Log(
                            string.Format(
                                CultureInfo.InvariantCulture,
                                "CACHE_COLD sample={0} count={1} loaded_by_maintenance={2} cold_ms={3:F6} warm_ms={4:F6} cpu_cycles={5} loaded_native_bytes={6}",
                                sample,
                                coldIds.Length,
                                loaded,
                                coldMs,
                                warmMs,
                                available ? (after - before).ToString(CultureInfo.InvariantCulture) : "unavailable",
                                nativeBytes
                            )
                        );
                    }
                }
            }
            finally
            {
                warmAssetRoots = null;
                if (transient != null)
                    Object.DestroyImmediate(transient);
                AssetDatabase.DeleteAsset(folder);
            }
        }

        private static Texture2D CreatePersistentTexture(string path)
        {
            var texture = new Texture2D(256, 256, TextureFormat.RGBA32, false);
            texture.name = path;
            AssetDatabase.CreateAsset(texture, path);
            return texture;
        }

        private static ulong CreatePersistentTextureId(string path, out string guid, out long localId)
        {
            Texture2D value = CreatePersistentTexture(path);
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out guid, out localId));
            return FullId(value);
        }

        private static void AssertPersistentIdentity(Object value, ulong id, string path, string guid, long localId)
        {
            Assert.IsNotNull(value);
            Assert.AreEqual(id, FullId(value));
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(value));
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string actualGuid, out long actualLocalId));
            Assert.AreEqual(guid, actualGuid);
            Assert.AreEqual(localId, actualLocalId);
        }

        private static void UnloadPersistentTextures(string[] paths)
        {
            foreach (string path in paths)
                UnityEngine.Resources.UnloadAsset(AssetDatabase.LoadAssetAtPath<Texture2D>(path));
        }

        private static int CountLoaded(ulong[] ids)
        {
            var wanted = new HashSet<ulong>(ids);
            int loaded = 0;
            foreach (Texture2D value in UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>())
                if (wanted.Contains(FullId(value)))
                    loaded++;
            return loaded;
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
                var registered = new HashSet<int>();
                foreach (ulong id in ids)
                    registered.Add(unchecked((int)id));
                var missing = new int[size];
                int missingHandle = int.MinValue;
                for (int i = 0; i < missing.Length; i++)
                {
                    while (registered.Contains(missingHandle))
                        missingHandle++;
                    missing[i] = missingHandle++;
                }
                // Warm both implementations, including the native call and JIT, before paired samples.
                Measure(ids, missing, 0, clustered, -1, true, bytesAvailable, eventsAvailable);
                Measure(ids, missing, 0, clustered, -1, false, bytesAvailable, eventsAvailable);
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
                        Measure(ids, missing, phase, clustered, sample, sample % 2 == 0, bytesAvailable, eventsAvailable);
                        Measure(ids, missing, phase, clustered, sample, sample % 2 != 0, bytesAvailable, eventsAvailable);
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

        private static void Measure(ulong[] ids, int[] missing, int phase, bool clustered, int sample, bool baseline, bool bytesAvailable, bool eventsAvailable)
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
                started = Stopwatch.GetTimestamp();
                foreach (ulong id in ids)
                    probe.Remember(id ^ (1UL << 63));
                double replace = Elapsed(started);
                // Restore actual native identities before any native lookup.
                foreach (ulong id in ids)
                    probe.Remember(id);
                int callsBeforeMiss = probe.Calls;
                started = Stopwatch.GetTimestamp();
                foreach (int handle in missing)
                    probe.Resolve(handle);
                double miss = Elapsed(started);
                Assert.AreEqual(callsBeforeMiss, probe.Calls, "An unregistered handle must not call the native resolver.");
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
                        "CACHE_COST strategy={0} size={1} clustered={2} phase={3} sample={4} total_ms={5:F6} p95_ms={6:F6} p99_ms={7:F6} max_ms={8:F6} ticks={9} events={10} allocated_bytes={11} backing_payload={12} after_payload={13} insert_ms={14:F6} overwrite_ms={15:F6} lookup_ms={16:F6} cpu_cycles={17} replace_ms={18:F6} miss_ms={19:F6}",
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
                        cyclesAvailable ? cycles.ToString(CultureInfo.InvariantCulture) : "unavailable",
                        replace,
                        miss
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
                var insertion = new double[2];
                var insertionCycles = new ulong[2];
                var cyclesSupported = new bool[2];
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
                        for (int order = 0; order < 2; order++)
                        {
                            int index = (cycle + order) % 2;
                            Probe probe = index == 0 ? baseline : candidate;
                            bool available = TryThreadCycles(out ulong beforeCycles);
                            long started = Stopwatch.GetTimestamp();
                            foreach (ulong id in ids)
                                probe.Remember(id);
                            insertion[index] = Elapsed(started);
                            cyclesSupported[index] = TryThreadCycles(out ulong afterCycles) && available && afterCycles > beforeCycles;
                            insertionCycles[index] = afterCycles - beforeCycles;
                        }
                        foreach (TransientObject value in transient)
                            Object.DestroyImmediate(value);
                        for (int order = 0; order < 2; order++)
                        {
                            bool isBaseline = (cycle + order) % 2 == 0;
                            int index = isBaseline ? 0 : 1;
                            Probe probe = isBaseline ? baseline : candidate;
                            bool available = TryThreadCycles(out ulong beforeCycles);
                            long started = Stopwatch.GetTimestamp();
                            foreach (ulong id in ids)
                                probe.Resolve(unchecked((int)id));
                            double elapsed = Elapsed(started);
                            available = TryThreadCycles(out ulong afterCycles) && available && cyclesSupported[index] && afterCycles > beforeCycles;
                            ulong cycles = insertionCycles[index] + afterCycles - beforeCycles;
                            Assert.AreEqual(liveIds.Length, probe.Count);
                            UnityEngine.Debug.Log(
                                string.Format(
                                    CultureInfo.InvariantCulture,
                                    "CACHE_CHURN strategy={0} size={1} cycle={2} removal_ms={3:F6} backing_payload={4} retained_live={5} insert_ms={6:F6} total_ms={7:F6} cpu_cycles={8}",
                                    isBaseline ? "beta" : "candidate",
                                    liveIds.Length,
                                    cycle,
                                    elapsed,
                                    probe.BackingPayload(),
                                    probe.Count,
                                    insertion[index],
                                    insertion[index] + elapsed,
                                    available ? cycles.ToString(CultureInfo.InvariantCulture) : "unavailable"
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
