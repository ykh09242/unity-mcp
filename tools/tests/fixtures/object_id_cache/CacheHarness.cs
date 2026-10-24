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
        CheckSmallRings(live);
        CheckReentrantMutations(live);
        Console.WriteLine("PASS: bounded sweep; complete expiry; mutation/overwrite; live preservation; clear/reload/quit/dispose");
    }

    private static void CheckSmallRings(Object live)
    {
        foreach (int dead in new[] { 0, 1, 127, 128, 129 })
        {
            int calls = 0;
            double now = 0;
            using (
                var cache = new EditorObjectIdCache(
                    id =>
                    {
                        calls++;
                        return id == (ulong)dead ? null : live;
                    },
                    () => now
                )
            )
            {
                for (ulong id = 0; id < 260; id++)
                    cache.Remember(id);
                now = 11;
                for (int tick = 0; tick < 4; tick++)
                {
                    int before = calls;
                    EditorApplication.Tick();
                    Require(calls - before <= 128, "live/dead paths exceeded their shared update budget");
                }
                Require(calls == 260 && cache.Count == 259, "live/dead paths lost sweep coverage");
            }
        }
        for (int survivor = 129; survivor <= 130; survivor++)
        {
            double now = 0;
            bool allAlive = true;
            bool pairAlive = true;
            using (
                var cache = new EditorObjectIdCache(id => allAlive || id == (ulong)survivor || (pairAlive && (id == 129 || id == 130)) ? live : null, () => now)
            )
            {
                for (ulong id = 0; id < 260; id++)
                    cache.Remember(id);
                now += 11;
                EditorApplication.Tick();
                allAlive = false;
                for (int id = 0; id < 260; id++)
                    if (id != 129 && id != 130)
                        Require(cache.Resolve(id) == null, "pending pair setup retained a dead handle");
                pairAlive = false;
                EditorApplication.Tick();
                EditorApplication.Tick();
                Require(cache.Count == 1 && cache.Resolve(survivor) == live, "pending pair lost its survivor");
            }
        }
        // Exhaust the small-ring deletion patterns, including an aliased predecessor
        // and successor, then append and empty the ring to exercise both link directions.
        for (int size = 2; size <= 8; size++)
        {
            for (int mask = 0; mask < (1 << size); mask++)
            {
                double now = 0;
                bool empty = false;
                int expected = 1;
                using (var cache = new EditorObjectIdCache(id => !empty && (id == 100 || (mask & (1 << (int)id)) != 0) ? live : null, () => now))
                {
                    for (int id = 0; id < size; id++)
                    {
                        cache.Remember((ulong)id);
                        if ((mask & (1 << id)) != 0)
                            expected++;
                    }
                    now += 11;
                    EditorApplication.Tick();
                    Require(cache.Count == expected - 1, "small-ring sweep count changed");
                    // Revisit surviving links before an append can repair a bad tail.
                    now += 11;
                    EditorApplication.Tick();
                    EditorApplication.Transition();
                    EditorApplication.Tick();
                    Require(cache.Count == expected - 1, "small-ring repeat lost a survivor");
                    cache.Remember(100);
                    now += 11;
                    EditorApplication.Tick();
                    Require(cache.Count == expected && cache.Resolve(100) == live, "small-ring append corrupted links");
                    for (int id = 0; id < size; id++)
                        Require((cache.Resolve(id) != null) == ((mask & (1 << id)) != 0), "small-ring survivor lost");
                    empty = true;
                    now += 11;
                    EditorApplication.Tick();
                    Require(cache.Count == 0 && !cache.IsScheduled, "small-ring refill did not drain");
                }
            }
        }
        Require(EditorApplication.Subscribers == 0 && AssemblyReloadEvents.Subscribers == 0, "small-ring checks leaked callbacks");
    }

    private static void CheckReentrantMutations(Object live)
    {
        int loaded = 0;
        int calls = 0;
        double loadingNow = 0;
        EditorObjectIdCache loading = null;
        using (
            loading = new EditorObjectIdCache(
                id =>
                {
                    calls++;
                    if (id == (ulong)loaded && loaded < 1024)
                    {
                        loading.Remember((ulong)(10000 + loaded));
                        loaded++;
                    }
                    return live;
                },
                () => loadingNow
            )
        )
        {
            for (ulong id = 0; id < 1024; id++)
                loading.Remember(id);
            loadingNow = 11;
            for (int tick = 0; tick < 1100; tick++)
                EditorApplication.Tick();
            Require(loaded == 1024 && loading.Count == 2048, "reentrant loading did not reach every original entry");
            Require(calls <= 4096, "reentrant loading repeatedly rescanned the prefix");
        }
        EditorObjectIdCache inserting = null;
        using (
            inserting = new EditorObjectIdCache(
                id =>
                {
                    if (id == 0)
                    {
                        inserting.Remember(1);
                        return null;
                    }
                    return live;
                },
                () => 0
            )
        )
        {
            inserting.Remember(0);
            Require(inserting.Resolve(0) == null, "inserting lookup changed its native result");
            Require(inserting.Count == 1 && inserting.Resolve(1) == live, "unrelated insertion delayed dead lookup removal");
        }
        for (int clear = 0; clear < 2; clear++)
        {
            EditorObjectIdCache cache = null;
            bool alive = true;
            using (
                cache = new EditorObjectIdCache(
                    id =>
                    {
                        if (id == 0)
                        {
                            if (clear != 0)
                                cache.Clear();
                            cache.Remember(0xFFFFFFFF00000000UL);
                            return null;
                        }
                        return alive ? live : null;
                    },
                    () => 0
                )
            )
            {
                cache.Remember(0);
                Require(cache.Resolve(0) == null, "lookup changed its native result");
                Require(cache.Count == 1 && cache.Resolve(0) == live, "lookup removed a reentrant replacement");
                alive = false;
                Require(cache.Resolve(0) == null && cache.Count == 0, "ordinary lookup stopped removing dead entries immediately");
            }
        }
        for (int kind = 0; kind < 5; kind++)
        {
            for (int fail = 0; fail < 2; fail++)
            {
                double now = 0;
                bool injected = false;
                var alive = new HashSet<ulong> { 0, 2 };
                EditorObjectIdCache cache = null;
                using (
                    cache = new EditorObjectIdCache(
                        id =>
                        {
                            if (id == (kind == 4 ? 0UL : 2UL) && !injected)
                            {
                                injected = true;
                                if (kind == 0)
                                {
                                    alive.Add(3);
                                    cache.Remember(3);
                                }
                                else if (kind == 1)
                                {
                                    alive.Remove(0);
                                    alive.Add(0xFFFFFFFF00000000UL);
                                    cache.Remember(0xFFFFFFFF00000000UL);
                                }
                                else if (kind == 2 || kind == 4)
                                {
                                    cache.Clear();
                                    alive.Clear();
                                    alive.Add(3);
                                    cache.Remember(3);
                                }
                                else
                                {
                                    alive.Remove(0);
                                    cache.Resolve(0);
                                }
                                if (fail != 0)
                                    throw new InvalidOperationException("owned reentrant failure");
                            }
                            return alive.Contains(id) ? live : null;
                        },
                        () => now
                    )
                )
                {
                    cache.Remember(0);
                    cache.Remember(1);
                    cache.Remember(2);
                    now += 11;
                    try
                    {
                        EditorApplication.Tick();
                        Require(fail == 0, "expected reentrant failure");
                    }
                    catch (InvalidOperationException error)
                    {
                        Require(fail != 0 && error.Message == "owned reentrant failure", "unexpected reentrant failure");
                    }
                    EditorApplication.Tick();
                    now += 11;
                    EditorApplication.Tick();
                    Require(cache.Count == alive.Count, "reentrant mutation lost cache membership");
                    foreach (ulong id in alive)
                        Require(cache.Resolve(unchecked((int)id)) == live, "reentrant mutation lost full identity");
                    alive.Clear();
                    now += 11;
                    EditorApplication.Tick();
                    Require(cache.Count == 0 && !cache.IsScheduled, "reentrant mutation left unreachable entries");
                }
            }
        }
        Require(EditorApplication.Subscribers == 0 && AssemblyReloadEvents.Subscribers == 0, "reentrant checks leaked callbacks");
    }
}
