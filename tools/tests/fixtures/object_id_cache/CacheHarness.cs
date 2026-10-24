using System;
using System.Collections.Generic;
using MCPForUnity.Runtime.Helpers;
using UnityEditor;
using Object = UnityEngine.Object;

internal static class CacheHarness
{
    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new Exception(message);
    }

    private static void Main()
    {
        double now = 0;
        int calls = 0;
        using (
            var cache = new EditorObjectIdCache(
                _ =>
                {
                    calls++;
                    return null;
                },
                () => now
            )
        )
        {
            for (int i = 0; i < 10000; i++)
                cache.Remember((ulong)i);
            now = 11;
            EditorApplication.Tick();
            Require(calls > 0 && calls <= 128, "maintenance native lookup budget exceeded: " + calls);
            for (int i = 0; i < 100; i++)
                EditorApplication.Tick();
            Require(calls == 10000 && cache.Count == 0 && !cache.IsScheduled, "dead handles did not drain");
        }

        var alive = new HashSet<ulong>();
        var live = new Object();
        using (var cache = new EditorObjectIdCache(id => alive.Contains(id) ? live : null, () => now))
        {
            for (int i = 0; i < 1000; i++)
            {
                alive.Add((ulong)i);
                cache.Remember((ulong)i);
            }
            now += 11;
            EditorApplication.Tick();
            for (int i = 0; i < 1000; i += 2)
            {
                alive.Remove((ulong)i);
                Require(cache.Resolve(i) == null, "dead handle resolved");
            }
            cache.Remember(0x1234567800000001UL);
            alive.Add(0x1234567800000001UL);
            for (int i = 1000; i < 1250; i++)
                cache.Remember((ulong)i);
            for (int i = 0; i < 50; i++)
            {
                now += 1;
                EditorApplication.Tick();
            }
            Require(cache.Count == 500 && cache.Resolve(1) == live, "mutation skipped handles or lost live entries");
            calls = 0;
            EditorApplication.Transition();
            Require(cache.Count == 500, "play transition lost live handles");
            cache.Clear();
            Require(cache.Count == 0 && !cache.IsScheduled, "clear left work scheduled");
            cache.Remember(0x23456789FFFFFFFFUL);
            AssemblyReloadEvents.Reload();
            Require(cache.Count == 0 && !cache.IsScheduled, "reload left cache state");
            cache.Remember(0x3456789000000000UL);
            EditorApplication.Quit();
            Require(cache.Count == 0 && !cache.IsScheduled, "quit left cache state");
        }
        // The wire handle is the low 32 bits, including its sign; all upper bits must survive.
        var identities = new Dictionary<int, ulong>
        {
            { 0, 0xFEDCBA9800000000UL },
            { -1, 0x12345678FFFFFFFFUL },
            { int.MinValue, 0xFFFFFFFF80000000UL },
            { int.MaxValue, 0x876543217FFFFFFFUL },
        };
        var seen = new List<ulong>();
        using (
            var cache = new EditorObjectIdCache(
                id =>
                {
                    seen.Add(id);
                    return live;
                },
                () => now
            )
        )
        {
            foreach (var pair in identities)
                cache.Remember(pair.Value);
            foreach (var pair in identities)
            {
                seen.Clear();
                Require(cache.Resolve(pair.Key) == live && seen.Count == 1 && seen[0] == pair.Value, "full-width lookup identity changed");
            }
            cache.Remember(0xABCDEF01FFFFFFFFUL);
            seen.Clear();
            cache.Resolve(-1);
            Require(cache.Count == 4 && seen[0] == 0xABCDEF01FFFFFFFFUL, "same-handle overwrite lost the new full identity");
            seen.Clear();
            now += 11;
            EditorApplication.Tick();
            Require(
                seen.Count == 4
                    && seen.Contains(0xFEDCBA9800000000UL)
                    && seen.Contains(0xABCDEF01FFFFFFFFUL)
                    && seen.Contains(0xFFFFFFFF80000000UL)
                    && seen.Contains(0x876543217FFFFFFFUL),
                "maintenance lost full-width identity"
            );
            Require(cache.Count == 4, "maintenance evicted live full-width identities");
        }
        bool allAlive = true;
        using (var cache = new EditorObjectIdCache(_ => allAlive ? live : null, () => now))
        {
            for (ulong id = 0; id < 1000; id++)
                cache.Remember(id);
            now += 11;
            EditorApplication.Tick();
            allAlive = false;
            for (int handle = 0; handle < 999; handle++)
                cache.Resolve(handle);
            Require(cache.Count == 1, "immediate removals did not leave the pending cursor singleton");
            EditorApplication.Tick();
            Require(cache.Count == 0 && !cache.IsScheduled, "pending sweep revisited a removed singleton");
            cache.Remember(0xFFFFFFFFFFFFFFFFUL);
            now += 11;
            EditorApplication.Tick();
            Require(cache.Count == 0 && !cache.IsScheduled, "retained capacity did not support an empty-cache refill");
        }
        bool throwOnce = true;
        using (
            var cache = new EditorObjectIdCache(
                id =>
                {
                    if (id == 2 && throwOnce)
                    {
                        throwOnce = false;
                        throw new InvalidOperationException("owned lookup failure");
                    }
                    return id < 2 ? null : live;
                },
                () => now
            )
        )
        {
            for (ulong id = 0; id < 260; id++)
                cache.Remember(id);
            now += 11;
            try
            {
                EditorApplication.Tick();
                throw new Exception("expected native lookup failure");
            }
            catch (InvalidOperationException error)
            {
                Require(error.Message == "owned lookup failure", "unexpected failure");
            }
            Require(cache.Count == 258, "lookup failure retained earlier dead handles");
            for (int tick = 0; tick < 4; tick++)
                EditorApplication.Tick();
            now += 11;
            for (int tick = 0; tick < 4; tick++)
                EditorApplication.Tick();
            Require(cache.Count == 258 && cache.Resolve(2) == live, "lookup failure corrupted the remaining ring");
        }
        var expected = new Dictionary<int, ulong>();
        var random = new Random(91827);
        using (var cache = new EditorObjectIdCache(id => alive.Contains(id) ? live : null, () => now))
        {
            for (int operation = 0; operation < 50000; operation++)
            {
                int handle = random.Next(-1000, 1000);
                ulong id = ((ulong)random.Next(0, 4) << 32) | unchecked((uint)handle);
                switch (random.Next(4))
                {
                    case 0:
                        expected[handle] = id;
                        cache.Remember(id);
                        break;
                    case 1:
                        alive.Add(id);
                        break;
                    case 2:
                        alive.Remove(id);
                        break;
                    default:
                        bool found = expected.TryGetValue(handle, out ulong full) && alive.Contains(full);
                        Require((cache.Resolve(handle) != null) == found, "random lookup parity failed");
                        if (!found)
                            expected.Remove(handle);
                        break;
                }
                if (operation % 100 == 0)
                    EditorApplication.Transition();
                if (operation % 1000 == 0)
                {
                    // Finish an in-flight sweep, then include entries added after its start.
                    for (int sweep = 0; sweep < 2; sweep++)
                    {
                        now += 11;
                        for (int tick = 0; tick < 32; tick++)
                            EditorApplication.Tick();
                    }
                    foreach (int key in new List<int>(expected.Keys))
                        if (!alive.Contains(expected[key]))
                            expected.Remove(key);
                    Require(cache.Count == expected.Count, "random full-sweep parity failed");
                    foreach (int key in expected.Keys)
                        Require(cache.Resolve(key) == live, "random sweep evicted a live handle");
                }
            }
        }
        Require(EditorApplication.Subscribers == 0 && AssemblyReloadEvents.Subscribers == 0, "dispose leaked Editor callbacks");
        Console.WriteLine("PASS: bounded sweep; complete expiry; mutation/overwrite; live preservation; clear/reload/quit/dispose");
    }
}
