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
                cache.Remember(i, (ulong)i);
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
                cache.Remember(i, (ulong)i);
            }
            now += 11;
            EditorApplication.Tick();
            for (int i = 0; i < 1000; i += 2)
            {
                alive.Remove((ulong)i);
                Require(cache.Resolve(i) == null, "dead handle resolved");
            }
            cache.Remember(1, 2001);
            alive.Add(2001);
            for (int i = 1000; i < 1250; i++)
                cache.Remember(i, (ulong)i);
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
            cache.Remember(-1, 2001);
            AssemblyReloadEvents.Reload();
            Require(cache.Count == 0 && !cache.IsScheduled, "reload left cache state");
            cache.Remember(0, 2001);
            EditorApplication.Quit();
            Require(cache.Count == 0 && !cache.IsScheduled, "quit left cache state");
        }
        var expected = new Dictionary<int, ulong>();
        var random = new Random(91827);
        using (var cache = new EditorObjectIdCache(id => alive.Contains(id) ? live : null, () => now))
        {
            for (int operation = 0; operation < 50000; operation++)
            {
                int handle = random.Next(-1000, 1000);
                ulong id = (ulong)random.Next(0, 2000);
                switch (random.Next(4))
                {
                    case 0:
                        expected[handle] = id;
                        cache.Remember(handle, id);
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
