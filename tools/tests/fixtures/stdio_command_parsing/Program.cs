using System;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal static class Program
{
    private static void Check(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }

    private static QueuedCommand Prepare(string text, out Command command) => StdioBridgeHost.Prepare(text, out command, out _, out _, out _);

    private static void Main(string[] args)
    {
        foreach (var text in new[] { "null", "true", "123", "\"text\"", "{", "{\"type\":", "{} {}", "{}/*tail*/" })
        {
            string response = StdioBridgeHost.Run(Prepare(text, out _));
            Check((string)JObject.Parse(response)["error"] == "Invalid JSON format", "Invalid JSON response changed: " + text);
        }
        Check((string)JObject.Parse(StdioBridgeHost.Run(Prepare(" ", out _)))["error"] == "Empty command received", "Empty input changed");
        Check((string)JObject.Parse(StdioBridgeHost.Run(Prepare(" ping ", out _)))["result"]["message"] == "pong", "Ping changed");
        foreach (
            var text in new[] { "[]", "{\"type\":{}}", "{\"type\":\"x\",\"params\":[]}", "{\"type\":\"x\",\"params\":5}", "  {\"type\":\"x\",\"params\":5}  " }
        )
        {
            string expected = null;
            try
            {
                TransportJson.DeserializeCommand(text.Trim());
            }
            catch (JsonException error)
            {
                expected = error.Message;
            }
            var response = JObject.Parse(StdioBridgeHost.Run(Prepare(text, out _)));
            Check(expected != null && (string)response["error"] == expected, "Typed error changed: " + text);
        }
        foreach (
            var text in new[]
            {
                "{\"TYPE\":\"a\",\"type\":\"b\",\"TYPE\":\"c\",\"params\":{\"when\":\"2026-10-19T01:02:03Z\"}}",
                "{\"type\":\"x\",\"Params\":{\"a\":1},\"params\":{\"b\":2},\"Params\":{\"c\":3}}",
                "{\"type\":\"x\",\"params\":{\"a\":1,\"a\":2}}",
                "{\"type\":\"x\",\"params\":null}",
                "{\"TYPE\":\"x\",\"PARAMS\":{\"text\":\"한글\"}}",
            }
        )
        {
            var expected = TransportJson.DeserializeCommand(text);
            StdioBridgeHost.Run(Prepare(text, out _));
            var actual = TransportCommandDispatcher.Captured;
            Check(actual.type == expected.type && JToken.DeepEquals(actual.@params, expected.@params), "Typed command changed: " + text);
        }
        const string id = "11111111-2222-3333-4444-555555555555";
        foreach (
            var item in new[]
            {
                ("{\"type\":\"command_progress_begin\",\"version\":1,\"id\":\"" + id + "\"}", "Accepted"),
                ("{\"type\":\"command_progress_begin\",\"version\":1,\"version\":1,\"id\":\"" + id + "\"}", "Refused"),
                ("{\"type\":\"x\",\"params\":{\"a\":1,\"a\":2}}", "None"),
            }
        )
        {
            StdioBridgeHost.Prepare(item.Item1, out _, out var prelude, out var progressId, out var business);
            Check(prelude == item.Item2, "Prelude classification changed");
            Check(prelude != "Accepted" || progressId == id, "Prelude identity changed");
            Check(StdioBridgeHost.Business(item.Item1) == (item.Item2 == "None"), "Business association changed");
        }
        Check(StdioBridgeHost.ResolveCommandTimeoutMs("{\"type\":\"x\",\"params\":{\"timeout_seconds\":600}}", 300000) == 600000, "Timeout changed");
        Check(
            StdioBridgeHost.ResolveCommandTimeoutMs("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":600}}", 300000) == 630000,
            "Blender timeout changed"
        );
        foreach (
            var item in new[]
            {
                ("{\"type\":\"x\",\"params\":{\"timeout_seconds\":600}}", 600000),
                ("{\"TYPE\":\"x\",\"params\":{\"timeout_seconds\":600}}", 300000),
                ("{\"type\":\"2026-10-19T01:02:03Z\",\"params\":{\"timeout_seconds\":600}}", 300000),
                ("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":true}}", 300000),
                ("{\"type\":\"x\",\"params\":{\"timeoutSeconds\":600}}", 600000),
            }
        )
            Check(Prepare(item.Item1, out _).TimeoutMs == item.Item2, "Ingress timeout changed: " + item.Item1);
        Check(!StdioBridgeHost.Business("{\"type\":\"2026-10-19T01:02:03Z\"}"), "Date-like progress business type classification changed");
        const string bound =
            "{\"type\":\"x\",\"arbitration\":{\"version\":1,\"request_nonce\":\"1234567890123456\",\"instance_generation\":\"fixture-generation\"}}";
        var negotiated = StdioBridgeHost.Prepare(bound, out _, out _, out _, out _, true);
        Check(negotiated.RequestNonce == "1234567890123456", "Negotiated nonce lost");
        Check(Prepare(bound, out _).RequestNonce == null, "Unnegotiated nonce accepted");
        var canceled = Prepare("{\"type\":\"x\"}", out _);
        using (var cancellation = new System.Threading.CancellationTokenSource())
        {
            cancellation.Cancel();
            canceled.OwnerCancellation = cancellation.Token;
            int calls = TransportCommandDispatcher.Calls;
            try
            {
                StdioBridgeHost.Run(canceled);
                throw new InvalidOperationException("Canceled queue was executed");
            }
            catch (System.Threading.Tasks.TaskCanceledException) { }
            Check(TransportCommandDispatcher.Calls == calls && canceled.CommandJson == null, "Canceled input was dispatched or retained");
        }

        string payload = "{\"type\":\"x\",\"params\":{\"text\":\"" + new string('x', 16384) + "\"}}";
        for (int i = 0; i < 30; i++)
            StdioBridgeHost.Run(Prepare(payload, out _));
        const int iterations = 300;
        long before = GC.GetAllocatedBytesForCurrentThread();
        for (int i = 0; i < iterations; i++)
            StdioBridgeHost.Run(Prepare(payload, out _));
        long allocated = GC.GetAllocatedBytesForCurrentThread() - before;
        var prepared = Prepare(payload, out var initial);
        before = GC.GetAllocatedBytesForCurrentThread();
        StdioBridgeHost.Run(prepared);
        long editorAllocated = GC.GetAllocatedBytesForCurrentThread() - before;
        Console.WriteLine(
            $"ALLOCATIONS payload_chars={payload.Length} iterations={iterations} total_bytes={allocated} bytes_per_command={allocated / iterations} editor_bytes={editorAllocated}"
        );
        if (Array.IndexOf(args, "--require-reuse") >= 0)
            Check(ReferenceEquals(initial, TransportCommandDispatcher.Captured), "Editor dispatch reparsed the ingress command instead of reusing it");
#if PREPARED
        Check(prepared.Input == null && prepared.CommandJson == null, "Settled input retained parsed/raw payload");
        Check(canceled.Input == null, "Canceled input retained parsed payload");
#endif
        var queuedInputs = new QueuedCommand[128];
        long retainedBefore = GC.GetTotalMemory(true);
        for (int i = 0; i < queuedInputs.Length; i++)
            queuedInputs[i] = Prepare(payload, out _);
        long retainedQueued = GC.GetTotalMemory(true);
        foreach (var queued in queuedInputs)
            StdioBridgeHost.Release(queued);
        long retainedReleased = GC.GetTotalMemory(true);
        Console.WriteLine(
            $"RETENTION queued_commands=128 shared_raw_payload=true retained_bytes={retainedQueued - retainedBefore} released_input_bytes={retainedQueued - retainedReleased}"
        );
        GC.KeepAlive(queuedInputs);
        string densePayload = "{\"type\":\"x\",\"params\":{\"numbers\":[" + string.Join(",", new int[8192]) + "]}}";
        for (int i = 0; i < 10; i++)
            StdioBridgeHost.Run(Prepare(densePayload, out _));
        before = GC.GetAllocatedBytesForCurrentThread();
        for (int i = 0; i < 30; i++)
            StdioBridgeHost.Run(Prepare(densePayload, out _));
        long denseAllocated = GC.GetAllocatedBytesForCurrentThread() - before;
        var denseQueued = new QueuedCommand[16];
        retainedBefore = GC.GetTotalMemory(true);
        for (int i = 0; i < denseQueued.Length; i++)
            denseQueued[i] = Prepare(densePayload, out _);
        retainedQueued = GC.GetTotalMemory(true);
        foreach (var queued in denseQueued)
            StdioBridgeHost.Release(queued);
        retainedReleased = GC.GetTotalMemory(true);
        Console.WriteLine(
            $"DENSE payload_chars={densePayload.Length} numbers=8192 bytes_per_command={denseAllocated / 30} queued_commands=16 retained_bytes={retainedQueued - retainedBefore} released_input_bytes={retainedQueued - retainedReleased}"
        );
        GC.KeepAlive(denseQueued);
        if (Array.IndexOf(args, "--require-reuse") >= 0)
        {
            var grow = typeof(StdioFrameBudget.Lease).GetMethod(
                "TryIncreaseTo",
                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic
            );
            Check(grow != null, "Prepared command graphs have no retained-input reservation growth");
            var budget = new StdioFrameBudget(100, 10, 1);
            var lease = budget.AcquireAsync(10, default).Result;
            Check((bool)grow.Invoke(lease, new object[] { 90L }), "Reservation growth failed within capacity");
            Check(budget.ReservedBytes == 90, "Graph growth was not charged");
            Check(!(bool)grow.Invoke(lease, new object[] { 101L }), "Graph growth exceeded lane capacity");
            lease.Dispose();
            Check(budget.ReservedBytes == 0, "Adjusted lease was not fully released");
#if PREPARED
            int denseBytes = System.Text.Encoding.UTF8.GetByteCount(densePayload);
            var limited = new StdioFrameBudget(denseBytes * 5L + 1024, 1024, 1);
            var denied = limited.AcquireAsync(denseBytes, default).Result;
            int calls = TransportCommandDispatcher.Calls;
            Check(
                !StdioBridgeHost.TryPrepareCommandInput(densePayload, JObject.Parse(densePayload), denied, out var deniedInput),
                "Dense graph bypassed capacity"
            );
            Check(deniedInput == null && TransportCommandDispatcher.Calls == calls, "Capacity refusal prepared or dispatched input");
            Check(limited.ReservedBytes == denseBytes * 5L, "Failed growth changed the frame charge");
            denied.Dispose();
            Check(limited.ReservedBytes == 0, "Capacity-refused frame retained a reservation");
            int scalarBytes = System.Text.Encoding.UTF8.GetByteCount(payload);
            var scalarBudget = new StdioFrameBudget(scalarBytes * 6L, 1024, 1);
            var scalarLease = scalarBudget.AcquireAsync(scalarBytes, default).Result;
            Check(
                StdioBridgeHost.TryPrepareCommandInput(payload, JObject.Parse(payload), scalarLease, out var scalarInput),
                "Sparse large string was rejected"
            );
            using (var cancellation = new System.Threading.CancellationTokenSource())
            {
                cancellation.Cancel();
                var canceledPrepared = new QueuedCommand
                {
                    CommandJson = payload,
                    Input = scalarInput,
                    InputReservation = scalarLease,
                    OwnerCancellation = cancellation.Token,
                    Tcs = new System.Threading.Tasks.TaskCompletionSource<string>(),
                };
                try
                {
                    StdioBridgeHost.Run(canceledPrepared);
                    throw new InvalidOperationException("Canceled prepared input executed");
                }
                catch (System.Threading.Tasks.TaskCanceledException) { }
                Check(canceledPrepared.Input == null && scalarBudget.ReservedBytes == 0, "Cancellation retained adjusted input ownership");
            }
            for (int i = 0; i < 128; i++)
            {
                var raced = budget.AcquireAsync(10, default).Result;
                System
                    .Threading.Tasks.Task.WhenAll(
                        System.Threading.Tasks.Task.Run(() => raced.TryIncreaseTo(90)),
                        System.Threading.Tasks.Task.Run(() => raced.Dispose())
                    )
                    .GetAwaiter()
                    .GetResult();
                Check(!raced.TryIncreaseTo(100), "Disposed lease acquired capacity again");
                raced.Dispose();
                Check(budget.ReservedBytes == 0, "Concurrent growth/dispose leaked or double-released capacity");
            }
#endif
        }
        Console.WriteLine("PASS stdio parsing, admission, response compatibility and allocation fixture");
    }
}
