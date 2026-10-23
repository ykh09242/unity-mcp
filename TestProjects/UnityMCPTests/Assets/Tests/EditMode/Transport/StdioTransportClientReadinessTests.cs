using MCPForUnity.Editor.Services.Transport.Transports;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Transport
{
    /// <summary>
    /// Regression tests for the Start Session readiness race: stdio StartAsync used to return true
    /// unconditionally and callers immediately verified StdioBridgeHost.IsRunning — which is still
    /// false while the previous port releases after a domain reload, producing a spurious
    /// "Bridge not running" until the user clicked Start Session several times. StartAsync now waits
    /// (bounded) for the bridge to actually bind; this covers the wait predicate.
    /// </summary>
    public class StdioTransportClientReadinessTests
    {
        private static readonly double Timeout = StdioTransportClient.ReadyWaitTimeoutSeconds;

        [Test]
        public async System.Threading.Tasks.Task StopRetiresPendingStartEvenWhenItsCompletionArrivesLate()
        {
            var completion = new System.Threading.Tasks.TaskCompletionSource<bool>();
            System.Threading.CancellationToken token = default;
            int stops = 0;
            var client = ControlledClient(
                t =>
                {
                    token = t;
                    return completion.Task;
                },
                () => stops++
            );
            var start = client.StartAsync();
            await client.StopAsync();
            Assert.IsTrue(token.IsCancellationRequested);
            completion.SetResult(true);
            Assert.IsFalse(await start, "A late completion must not resurrect a stopped start.");
            Assert.AreEqual(1, stops);
            var state = (MCPForUnity.Editor.Services.Transport.TransportState)
                typeof(StdioTransportClient)
                    .GetField("_state", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                    .GetValue(client);
            Assert.IsFalse(state.IsConnected);
        }

        [Test]
        public async System.Threading.Tasks.Task ReplacementStartOwnsStateAfterOldFailureArrives()
        {
            var first = new System.Threading.Tasks.TaskCompletionSource<bool>();
            var second = new System.Threading.Tasks.TaskCompletionSource<bool>();
            int calls = 0;
            var client = ControlledClient(_ => ++calls == 1 ? first.Task : second.Task, () => { });
            var oldStart = client.StartAsync();
            var currentStart = client.StartAsync();
            second.SetResult(true);
            Assert.IsTrue(await currentStart);
            first.SetException(new System.InvalidOperationException("retired failure"));
            Assert.IsFalse(await oldStart);
            var state = (MCPForUnity.Editor.Services.Transport.TransportState)
                typeof(StdioTransportClient)
                    .GetField("_state", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                    .GetValue(client);
            Assert.IsTrue(state.IsConnected);
            await client.StopAsync();
        }

        private static StdioTransportClient ControlledClient(
            System.Func<System.Threading.CancellationToken, System.Threading.Tasks.Task<bool>> start,
            System.Action stop
        )
        {
            var ctor = typeof(StdioTransportClient).GetConstructor(
                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic,
                null,
                new[] { start.GetType(), typeof(System.Action) },
                null
            );
            Assert.IsNotNull(ctor, "Controlled starts must exercise the same cancellation ownership as production.");
            return (StdioTransportClient)ctor.Invoke(new object[] { start, stop });
        }

        [Test]
        public void KeepsWaiting_WhenNotReady_AndWithinWindow()
        {
            Assert.IsTrue(StdioTransportClient.ShouldKeepWaitingForReady(bridgeReady: false, secondsWaited: 0.0));
            Assert.IsTrue(StdioTransportClient.ShouldKeepWaitingForReady(bridgeReady: false, secondsWaited: Timeout - 0.1));
        }

        [Test]
        public void StopsWaiting_AsSoonAsReady()
        {
            Assert.IsFalse(StdioTransportClient.ShouldKeepWaitingForReady(bridgeReady: true, secondsWaited: 0.0));
        }

        [Test]
        public void StopsWaiting_WhenWindowElapses_EvenIfNotReady()
        {
            Assert.IsFalse(StdioTransportClient.ShouldKeepWaitingForReady(bridgeReady: false, secondsWaited: Timeout));
            Assert.IsFalse(StdioTransportClient.ShouldKeepWaitingForReady(bridgeReady: false, secondsWaited: Timeout + 1.0));
        }

        [Test]
        public void ReadyWindow_IsGenerousEnoughForPortRelease()
        {
            // Must cover the OS port-release delay + the BusyPortFallbackWindowSeconds (3s) fallback.
            Assert.GreaterOrEqual(Timeout, 3.0);
        }
    }
}
