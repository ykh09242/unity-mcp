using System;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.CompilerServices;
using MCPForUnity.Editor.Tools;

// Compile with ReferenceGraphInventories.cs as documented in reference-graph-native-performance.md.
// Measures the real retention policy with synthetic, separately allocated 32-character GUIDs.
public static class ReferenceGraphInventoryRetentionExperiment
{
    [MethodImpl(MethodImplOptions.NoInlining)]
    private static void FirstPage(ReferenceGraphInventories cache, int count, int revision)
    {
        var items = new string[count];
        for (int i = 0; i < count; i++)
            items[i] = (count - i + revision).ToString("x32", CultureInfo.InvariantCulture);
        Array.Sort(items, StringComparer.Ordinal);
        cache.Publish(items, "v" + revision, "project-epoch", revision, revision);
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static string Sample(int count, int round, int capacity)
    {
        var cache = new ReferenceGraphInventories(capacity);
        var watch = new Stopwatch();
        long empty = GC.GetTotalMemory(true);
        watch.Start();
        FirstPage(cache, count, 1);
        watch.Stop();
        double first = watch.Elapsed.TotalMilliseconds;
        long peakBytes = GC.GetTotalMemory(true) - empty;
        watch.Restart();
        FirstPage(cache, count, 2);
        watch.Stop();
        double second = watch.Elapsed.TotalMilliseconds;
        peakBytes = Math.Max(peakBytes, GC.GetTotalMemory(true) - empty);
        int slots = cache.Current.Items.Length + (cache.Previous?.Items.Length ?? 0);
        var continuation = cache.Find("v1", "project-epoch", 3);
        int restarts = continuation == null ? 1 : 0;
        if (restarts != (capacity == 0 ? 1 : 0))
            throw new InvalidOperationException("Unexpected restart count.");
        if (continuation != null && (continuation.Revision != 1 || continuation.Items[0] != 2.ToString("x32", CultureInfo.InvariantCulture)))
            throw new InvalidOperationException("Continuation returned a different revision or membership.");
        GC.KeepAlive(cache);
        return FormattableString.Invariant($"{round},{capacity},{count},{restarts},{first:F3},{second:F3},{slots},{peakBytes}");
    }

    public static string[] Run()
    {
        const int count = 12244;
        int history = ReferenceGraphInventories.MaximumPreviousGuids;
        Sample(count, -1, 0);
        Sample(count, -1, history);
        int[] capacities = { 0, history, history, 0, history, 0, 0, history };
        var rows = new string[capacities.Length + 1];
        rows[0] = "round,maximum_previous_guids,inventory_guids,restarts,a_first_page_ms,b_first_page_ms,peak_retained_guid_slots,peak_live_heap_delta_bytes";
        for (int i = 0; i < capacities.Length; i++)
            rows[i + 1] = Sample(count, i, capacities[i]);
        return rows;
    }
}
