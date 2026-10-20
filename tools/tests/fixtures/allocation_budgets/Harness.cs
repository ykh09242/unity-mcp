using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using UnityEditor.Compilation;

internal static class Harness
{
    private static readonly List<object> Results = new();
    private static bool failed;

    private static void Check(string name, Action action, long limit)
    {
        action();
        long before = GC.GetAllocatedBytesForCurrentThread();
        action();
        long bytes = GC.GetAllocatedBytesForCurrentThread() - before;
        Results.Add(
            new
            {
                name,
                allocatedBytes = bytes,
                limit,
                passed = bytes <= limit,
            }
        );
        failed |= bytes > limit;
    }

    private static int Main()
    {
        long before = GC.GetAllocatedBytesForCurrentThread();
        GC.KeepAlive(new byte[4096]);
        if (GC.GetAllocatedBytesForCurrentThread() - before < 4096)
            throw new InvalidOperationException("Allocation instrumentation is unavailable.");
        var buffer = new PlayScenarioLogBuffer(new PlayScenarioLogPolicy { Mode = "strict" });
        string message = new string('x', 1024 * 1024);
        Check("large_strict_log", () => buffer.Add(1, "Error", message, message), 65536);
        buffer.Close();
        Check("closed_log", () => buffer.Add(2, "Error", message, message), 0);
        var warnings = Enumerable
            .Repeat(
                new CompilerMessage
                {
                    type = CompilerMessageType.Warning,
                    file = "Assets/Fixture.cs",
                    message = new string('x', 1000),
                },
                1000
            )
            .ToArray();
        Check(
            "compiler_warning_burst",
            () =>
            {
                var ledger = new CompilationLedger("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Environment.CurrentDirectory, _ => { }, now: () => 1);
                ledger.Started(new object());
                ledger.Assembly("A.dll", warnings);
            },
            32L * 1024 * 1024
        );
        Console.WriteLine(JsonConvert.SerializeObject(new { runtime = Environment.Version.ToString(), results = Results }));
        return failed ? 1 : 0;
    }
}
