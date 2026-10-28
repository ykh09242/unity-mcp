using System;
using System.Collections.Concurrent;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport.Transports;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [Parallelizable(ParallelScope.None)]
    public class WebSocketLifecycleIntentTests
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

        [TestCase(false)]
        [TestCase(true)]
        public void Stop_WhileStartWaitsForOldDrain_PreventsConnectionAfterContinuation(bool force)
        {
            using var peer = CreatePeer(out Uri endpoint);
            using var client = CreateClient(endpoint);
            using var lifecycle = new CancellationTokenSource();
            var receive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var context = new HeldContext();
            Set(client, "_lifecycleCts", lifecycle);
            Set(client, "_receiveTask", receive.Task);
            Task<bool> start = context.Start(client);
            try
            {
                Assert.IsFalse(start.IsCompleted, "The old receive loop must hold startup before connection creation.");
                Task stop;
                if (force)
                {
                    client.ForceStop();
                    stop = (Task)Get(client, "_forcedCommandSettlement");
                }
                else
                    stop = client.StopAsync();
                receive.SetResult(true);
                Wait(() => stop.IsCompleted && context.HasContinuation, "old drain and captured startup continuation");
                stop.GetAwaiter().GetResult();

                context.RunNext();

                Assert.IsNull(Get(client, "_lifecycleCts"), "A stopped startup must not create a replacement lifecycle.");
                Assert.IsNull(Get(client, "_socket"), "A stopped startup must not create a replacement socket.");
                Assert.IsTrue(start.IsCompleted, "A stale startup must finish without starting network work.");
                Assert.IsFalse(start.GetAwaiter().GetResult());
                Assert.IsFalse(client.IsConnected);
                Assert.IsFalse(client.State.IsConnected);
            }
            finally
            {
                receive.TrySetResult(true);
                Cleanup(client, context, start);
            }
        }

        [Test]
        public void NewerStart_WhileOldDrainPending_SurvivesOlderContinuation()
        {
            using var peer = CreatePeer(out Uri endpoint);
            using var client = CreateClient(endpoint);
            using var lifecycle = new CancellationTokenSource();
            var receive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var firstContext = new HeldContext();
            var secondContext = new HeldContext();
            Set(client, "_lifecycleCts", lifecycle);
            Set(client, "_receiveTask", receive.Task);
            Task<bool> first = firstContext.Start(client);
            Task<bool> second = secondContext.Start(client);
            try
            {
                receive.SetResult(true);
                Wait(() => firstContext.HasContinuation && secondContext.HasContinuation, "both pending starts after old drain");
                secondContext.RunNext();
                Wait(() => secondContext.HasContinuation && client.State.SessionId == "owned-editor-session-1", "newer startup registration");
                secondContext.RunNext();
                Assert.IsTrue(second.IsCompleted && second.GetAwaiter().GetResult());
                object current = Get(client, "_lifecycleCts");

                firstContext.RunNext();

                Assert.IsTrue(first.IsCompleted && !first.GetAwaiter().GetResult(), "The superseded start must finish without replacing the newer connection.");
                Assert.AreSame(current, Get(client, "_lifecycleCts"));
                Assert.IsTrue(client.IsConnected);
                Assert.AreEqual("owned-editor-session-1", client.State.SessionId);
            }
            finally
            {
                receive.TrySetResult(true);
                Cleanup(client, firstContext, first);
                Cleanup(client, secondContext, second);
            }
        }

        [Test]
        public void Registered_BeforeStartContinuation_PreservesServerSessionId()
        {
            using var peer = CreatePeer(out Uri endpoint);
            using var client = CreateClient(endpoint);
            var context = new HeldContext();
            Task<bool> start = context.Start(client);
            try
            {
                Wait(() => context.HasContinuation && client.State.SessionId == "owned-editor-session-1", "server registration before startup publication");
                Assert.IsFalse(start.IsCompleted, "The startup continuation must still be held after registration.");

                context.RunNext();

                Assert.IsTrue(start.IsCompleted);
                Assert.IsTrue(start.GetAwaiter().GetResult());
                Assert.IsTrue(client.IsConnected);
                Assert.AreEqual("owned-editor-session-1", client.State.SessionId);
            }
            finally
            {
                Cleanup(client, context, start);
            }
        }

        private static IDisposable CreatePeer(out Uri endpoint)
        {
            // Reuse the existing owned wire peer, with synthetic identity and no saved settings.
            Type peerType = typeof(TransportEditorIntegrationTests).GetNestedType("OwnedWebSocketPeer", BindingFlags.NonPublic);
            var peer = (IDisposable)Activator.CreateInstance(peerType, new object[] { "owned-lifecycle-token", false, false });
            endpoint = (Uri)peerType.GetProperty("Uri").GetValue(peer);
            return peer;
        }

        private static WebSocketTransportClient CreateClient(Uri endpoint) =>
            new WebSocketTransportClient(
                null,
                new WebSocketTransportClient.OwnedConnectionOptions(
                    endpoint,
                    "owned-lifecycle-token",
                    "LifecycleIntent",
                    new string('a', 40),
                    "/owned/lifecycle-intent",
                    "6000.0"
                )
            );

        private static void Cleanup(WebSocketTransportClient client, HeldContext context, Task start)
        {
            client.ForceStop();
            Wait(
                () =>
                {
                    while (context.HasContinuation)
                        context.RunNext();
                    return start.IsCompleted
                        && ((Task)Get(client, "_stopSettlement")).IsCompleted
                        && ((Task)Get(client, "_forcedCommandSettlement")).IsCompleted;
                },
                "owned lifecycle cleanup"
            );
            _ = start.Exception;
        }

        private static void Wait(Func<bool> condition, string description) =>
            Assert.IsTrue(SpinWait.SpinUntil(condition, TimeSpan.FromSeconds(5)), description);

        private static void Set(WebSocketTransportClient client, string field, object value) =>
            typeof(WebSocketTransportClient).GetField(field, Private).SetValue(client, value);

        private static object Get(WebSocketTransportClient client, string field) => typeof(WebSocketTransportClient).GetField(field, Private).GetValue(client);

        private sealed class HeldContext : SynchronizationContext
        {
            private readonly ConcurrentQueue<Action> _continuations = new ConcurrentQueue<Action>();
            internal bool HasContinuation => !_continuations.IsEmpty;

            public override void Post(SendOrPostCallback callback, object state) => _continuations.Enqueue(() => callback(state));

            internal Task<bool> Start(WebSocketTransportClient client)
            {
                SynchronizationContext previous = Current;
                SetSynchronizationContext(this);
                try
                {
                    return client.StartAsync();
                }
                finally
                {
                    SetSynchronizationContext(previous);
                }
            }

            internal void RunNext()
            {
                Assert.IsTrue(_continuations.TryDequeue(out Action continuation));
                SynchronizationContext previous = Current;
                SetSynchronizationContext(this);
                try
                {
                    continuation();
                }
                finally
                {
                    SetSynchronizationContext(previous);
                }
            }
        }
    }
}
