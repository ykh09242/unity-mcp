using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools.Profiler;
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
        string oversized = new string('x', 33 * 1024 * 1024);
        Check(
            "oversized_wire_serialization",
            () =>
            {
                try
                {
                    BoundedResponseJson.Serialize(new { result = oversized });
                    throw new Exception("Oversized response was admitted.");
                }
                catch (ResponseLimitException) { }
            },
            65536
        );
        using var reader = new WebSocketMessageReader();
        using var socket = new SocketProbe();
        Check(
            "small_websocket_message",
            () =>
            {
                socket.Remaining = 32;
                string result = reader.ReadAsync(socket, CancellationToken.None).GetAwaiter().GetResult();
                if (result.Length != 32)
                    throw new Exception("Message assembly changed.");
            },
            2048
        );
        var snapshotA = new MemorySnapshotComparison.Snapshot
        {
            Session = 1,
            Format = 18,
            Path = "synthetic",
        };
        var snapshotB = new MemorySnapshotComparison.Snapshot
        {
            Session = 1,
            Format = 18,
            Path = "synthetic",
        };
        for (int i = 0; i < 200000; i++)
        {
            string key = i.ToString("D6");
            snapshotA.Objects.Add(
                key,
                new MemorySnapshotComparison.NativeObject
                {
                    Key = key,
                    Id = key,
                    Name = key,
                    Type = "T" + (i % 8),
                    Bytes = 1,
                }
            );
            string addedKey = "b" + key;
            snapshotB.Objects.Add(
                addedKey,
                new MemorySnapshotComparison.NativeObject
                {
                    Key = addedKey,
                    Id = addedKey,
                    Name = addedKey,
                    Type = "T" + (i % 8),
                    Bytes = 2,
                }
            );
        }
        Check(
            "large_snapshot_comparison",
            () =>
            {
                var comparison = MemorySnapshotComparison.Compare(snapshotA, snapshotB, 8);
                if (
                    comparison.BytesA != 200000
                    || comparison.BytesB != 400000
                    || (int)comparison.Delta["added_count"] != 200000
                    || comparison.Delta["added"].Count() != 8
                    || (int)comparison.Delta["removed_count"] != 200000
                    || comparison.Types.Count != 8
                    || (string)comparison.Delta["added"][0]["snapshot_identity"] != "b000000"
                    || (string)comparison.Delta["removed"][7]["snapshot_identity"] != "000007"
                )
                    throw new Exception("Snapshot comparison semantics changed.");
            },
            1024 * 1024
        );
        Console.WriteLine(JsonConvert.SerializeObject(new { runtime = Environment.Version.ToString(), results = Results }));
        return failed ? 1 : 0;
    }
}
