using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;

internal static class RawBench
{
    private static double Milliseconds(long start) => Stopwatch.GetElapsedTime(start).TotalMilliseconds;

    private static double Quantile(List<double> values, double q) => values.Order().ElementAt((int)Math.Ceiling(q * values.Count) - 1);

    public static async Task Run(string fixtures)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(2));
        var cases = new List<object>();
        foreach (string kind in new[] { "tcp", "pipe", "unix" })
        {
            try
            {
                await using var streams = await Streams.Open(kind, timeout.Token);
                foreach (string workload in new[] { "small", "state", "large", "job" })
                {
                    byte[] value = File.ReadAllBytes(Path.Combine(fixtures, workload + ".json"));
                    var samples = new List<double>();
                    for (int iteration = -3; iteration < 30; iteration++)
                    {
                        var echo = Echo(streams.Server, timeout.Token);
                        long start = Stopwatch.GetTimestamp();
                        await Frames.Write(streams.Client, value, timeout.Token);
                        var result = await Frames.Read(streams.Client, timeout.Token);
                        double ms = Milliseconds(start);
                        await echo;
                        if (!result.AsSpan().SequenceEqual(value))
                            throw new InvalidDataException("Echo mismatch");
                        if (iteration >= 0)
                            samples.Add(ms);
                    }
                    cases.Add(
                        new
                        {
                            transport = kind,
                            workload,
                            response_bytes = value.Length,
                            output_sha256 = Convert.ToHexString(SHA256.HashData(value)).ToLowerInvariant(),
                            p50_ms = Quantile(samples, .50),
                            p95_ms = Quantile(samples, .95),
                            samples_ms = samples,
                        }
                    );
                }
            }
            catch (PlatformNotSupportedException ex)
            {
                cases.Add(new { transport = kind, unsupported = ex.Message });
            }
            catch (System.Net.Sockets.SocketException ex) when (kind == "unix")
            {
                cases.Add(new { transport = kind, unsupported = ex.SocketErrorCode.ToString() });
            }
        }
        var tails = new List<object>();
        foreach (bool paired in new[] { false, true })
        {
            var samples = new List<double>();
            for (int index = 0; index < 30; index++)
                samples.Add(await Backpressure(paired));
            tails.Add(
                new
                {
                    mode = paired ? "paired" : "single",
                    payload_bytes = 4 * 1024 * 1024,
                    read_chunk_bytes = 65536,
                    throttle_delay_ms = 2,
                    p50_ms = Quantile(samples, .50),
                    p95_ms = Quantile(samples, .95),
                    samples_ms = samples,
                }
            );
        }
        var invariants = await VerifyBinding();
        Console.WriteLine(
            JsonSerializer.Serialize(
                new
                {
                    schema_id = "unity-mcp-raw-stream-probe-v1",
                    exclusions = "No MCP SDK, JSON encode/decode, process boundary, Unity or product authentication in echo timing",
                    environment = new
                    {
                        os = Environment.OSVersion.ToString(),
                        runtime = Environment.Version.ToString(),
                        processors = Environment.ProcessorCount,
                    },
                    echo = cases,
                    backpressure = tails,
                    invariants,
                }
            )
        );
    }

    private static async Task Echo(Stream server, CancellationToken token) => await Frames.Write(server, await Frames.Read(server, token), token);

    private static async Task<double> Backpressure(bool paired)
    {
        using var session = new SessionBinding();
        await using var data = await Streams.Open("tcp", session.Token, boundedBuffers: true);
        await using var extraControl = paired ? await Streams.Open("tcp", session.Token, boundedBuffers: true) : null;
        Streams control = extraControl ?? data;
        if (!await session.Authenticate(data, session.Greeting(paired ? "data" : "control"), paired ? "data" : "control"))
            throw new InvalidDataException("Owned binding rejected");
        if (paired && !await session.Authenticate(control, session.Greeting("control"), "control"))
            throw new InvalidDataException("Owned control binding rejected");
        byte[] payload = new byte[4 * 1024 * 1024];
        var started = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        using var gate = new SemaphoreSlim(1);
        var bulk = SendBulk();
        await started.Task;
        var reader = Frames.Read(data.Client, session.Token, delayMs: 2);
        long start = Stopwatch.GetTimestamp();
        await Frames.Write(control.Client, "ping"u8.ToArray(), session.Token);
        var pong = SendPong();
        // Single-stream ordering means pong cannot be decoded before the large frame.
        if (!paired)
            await reader;
        var result = await Frames.Read(control.Client, session.Token);
        double ms = Milliseconds(start);
        if (!result.AsSpan().SequenceEqual("pong"u8))
            throw new InvalidDataException("Pong mismatch");
        await Task.WhenAll(bulk, pong, reader);
        if (reader.Result.Length != payload.Length)
            throw new InvalidDataException("Bulk mismatch");
        return ms;

        async Task SendBulk()
        {
            await gate.WaitAsync(session.Token);
            try
            {
                // Writer signals while holding frame ordering lock; capacity forces backpressure.
                started.SetResult();
                await Frames.Write(data.Server, payload, session.Token);
            }
            finally
            {
                gate.Release();
            }
        }
        async Task SendPong()
        {
            var request = await Frames.Read(control.Server, session.Token);
            if (!request.AsSpan().SequenceEqual("ping"u8))
                throw new InvalidDataException("Ping mismatch");
            if (!paired)
                await gate.WaitAsync(session.Token);
            try
            {
                await Frames.Write(control.Server, "pong"u8.ToArray(), session.Token);
            }
            finally
            {
                if (!paired)
                    gate.Release();
            }
        }
    }

    private static async Task<object> VerifyBinding()
    {
        using var session = new SessionBinding();
        await using var data = await Streams.Open("pipe", session.Token);
        Hello greeting = session.Greeting("data");
        bool wrongSessionRejected = !await session.Authenticate(data, greeting with { Session = "foreign" }, "data");
        bool wrongGenerationRejected = !await session.Authenticate(data, greeting with { Generation = 0 }, "data");
        bool wrongRoleRejected = !await session.Authenticate(data, greeting with { Role = "control" }, "data");
        bool wrongTokenRejected = !await session.Authenticate(data, greeting with { Token = new string('0', 64) }, "data");
        bool malformedTokenRejected = !await session.Authenticate(data, greeting with { Token = new string('z', 64) }, "data");
        bool dataAccepted = await session.Authenticate(data, greeting, "data");
        bool duplicateRoleRejected = !await session.Authenticate(data, greeting, "data");
        await using var control = await Streams.Open("pipe", session.Token);
        bool controlAccepted = await session.Authenticate(control, session.Greeting("control"), "control");
        var pendingData = Frames.Read(data.Server, session.Token);
        var monitor = session.MonitorControl(control.Server);
        long start = Stopwatch.GetTimestamp();
        await control.Client.DisposeAsync();
        await monitor.WaitAsync(TimeSpan.FromSeconds(2));
        bool cancelled;
        try
        {
            await pendingData;
            cancelled = false;
        }
        catch (OperationCanceledException)
        {
            cancelled = true;
        }
        double cleanupMs = Milliseconds(start);
        if (
            !(
                wrongSessionRejected
                && wrongGenerationRejected
                && wrongRoleRejected
                && wrongTokenRejected
                && malformedTokenRejected
                && duplicateRoleRejected
                && dataAccepted
                && controlAccepted
                && cancelled
            )
        )
            throw new InvalidDataException("Binding invariant failed");
        return new
        {
            wrongSessionRejected,
            wrongGenerationRejected,
            wrongRoleRejected,
            wrongTokenRejected,
            malformedTokenRejected,
            duplicateRoleRejected,
            dataAccepted,
            controlAccepted,
            pending_data_cancelled_on_control_disconnect = cancelled,
            cleanup_ms = cleanupMs,
            cleanup_bound_ms = 2000,
            per_session_lifetime_bound_ms = 15000,
        };
    }
}
