using System.Diagnostics;
using MCPForUnity.Editor.Services.ScriptQuality;
using NUnit.Framework;
using UnityMcp.ScriptDiagnostics;

namespace ScriptDiagnosticsHost.Tests;

public class ProcessOwnershipTests
{
    [Test]
    public async Task CancellationReapsTheActualChildBeforeCapacityReturns()
    {
        using var owner = new AnalysisHostProcess();
        using var cancellation = new CancellationTokenSource();
        // The real host waits on stdin and does not cooperate with the parent's token.
        var run = owner.RunAsync(
            "dotnet",
            [typeof(AnalysisRequest).Assembly.Location],
            AppContext.BaseDirectory,
            new BlockingInput(),
            framed: true,
            cancellation.Token
        );
        for (int i = 0; i < 100 && owner.ActiveProcessId == 0; i++)
            await Task.Delay(20);
        int pid = owner.ActiveProcessId;
        Assert.That(pid, Is.GreaterThan(0));
        cancellation.Cancel();
        Assert.ThrowsAsync<OperationCanceledException>(async () => await run);
        Assert.That(owner.ActiveProcessId, Is.Zero);
        Assert.That(owner.Occupied, Is.Zero);
        Assert.That(Exited(pid), Is.True);
    }

    [Test]
    public async Task ShutdownCancelsQueuedJobsAndReapsOwnedHost()
    {
        using var owner = new AnalysisHostProcess();
        var jobs = Enumerable
            .Range(0, 3)
            .Select(_ =>
                owner.RunAsync(
                    "dotnet",
                    [typeof(AnalysisRequest).Assembly.Location],
                    AppContext.BaseDirectory,
                    new BlockingInput(),
                    true,
                    CancellationToken.None
                )
            )
            .ToArray();
        for (int i = 0; i < 100 && owner.ActiveProcessId == 0; i++)
            await Task.Delay(20);
        Assert.ThrowsAsync<InvalidOperationException>(async () =>
            await owner.RunAsync("dotnet", [], AppContext.BaseDirectory, null, false, CancellationToken.None)
        );
        int pid = owner.ActiveProcessId;
        Assert.That(owner.Stop(), Is.True);
        foreach (var job in jobs)
            Assert.CatchAsync<OperationCanceledException>(async () => await job);
        Assert.That(Exited(pid), Is.True);
        Assert.That(owner.Occupied, Is.Zero);
    }

    [Test]
    public void MissingRuntimeDoesNotOccupyCapacity()
    {
        using var owner = new AnalysisHostProcess();
        Assert.CatchAsync(async () => await owner.RunAsync("missing-unity-analysis-dotnet", [], AppContext.BaseDirectory, null, true, CancellationToken.None));
        Assert.That(owner.Occupied, Is.Zero);
    }

    private static bool Exited(int pid)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            return process.HasExited;
        }
        catch (ArgumentException)
        {
            return true;
        }
    }

    private sealed class BlockingInput : MemoryStream
    {
        public override Task CopyToAsync(Stream destination, int bufferSize, CancellationToken cancellationToken) =>
            Task.Delay(Timeout.Infinite, cancellationToken);
    }
}
