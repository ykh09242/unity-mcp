using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    [Parallelizable(ParallelScope.None)]
    public class StdioBridgeMultiClientTests
    {
        private StdioBridgeReconnectTests.TimingHostScope host;
        private readonly List<TcpClient> peers = new();
        private readonly List<StdioFrameBudget.Lease> inputs = new();
        private TaskCompletionSource<object> release;
        private TaskCompletionSource<bool> entered;
        private int calls;

        private void Start()
        {
            calls = 0;
            release = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            host = new StdioBridgeReconnectTests.TimingHostScope(_ =>
            {
                if (Interlocked.Increment(ref calls) == 1)
                {
                    entered.TrySetResult(true);
                    return release.Task;
                }
                return Task.FromResult<object>(new { done = true });
            });
        }

        [UnityTearDown]
        public IEnumerator Cleanup()
        {
            release?.TrySetResult(new { done = true });
            foreach (var peer in peers)
                peer.Dispose();
            peers.Clear();
            foreach (var input in inputs)
                input.Dispose();
            inputs.Clear();
            if (host != null)
            {
                StdioBridgeHost.Stop();
                try
                {
                    yield return Until(() =>
                        StdioBridgeHost.QueuedCommandCount == 0 && StdioBridgeHost.ReservedInputBytes == 0 && StdioBridgeHost.PendingInputCount == 0
                    );
                }
                finally
                {
                    host.Dispose();
                    host = null;
                }
            }
            Assert.AreEqual(0, StdioBridgeHost.AuthenticatedClientCount);
            Assert.AreEqual(0, StdioBridgeHost.AuthenticatingClientCount);
        }

        private static IEnumerator Until(Func<bool> predicate)
        {
            var limit = Stopwatch.StartNew();
            while (!predicate() && limit.Elapsed < TimeSpan.FromSeconds(10))
                yield return null;
            Assert.IsTrue(predicate(), "Owned transport checkpoint did not settle.");
        }

        private static IEnumerator Await(Task task)
        {
            yield return Until(() => task.IsCompleted);
            task.GetAwaiter().GetResult();
        }

        private Task<TcpClient> Connect(bool arbitration = false, bool admitted = true) =>
            host.Track(
                Task.Run(() =>
                    StdioBridgeReconnectTests.ConnectProgressPeer(
                        host.Port,
                        host.Token,
                        arbitration ? new JArray(NativeCommandArbitrator.Capability) : null,
                        out _,
                        admitted
                    )
                )
            );

        private JObject Command(string name, bool bound) =>
            new JObject
            {
                ["type"] = name,
                ["params"] = new JObject(),
                ["arbitration"] = bound
                    ? new JObject
                    {
                        ["version"] = 1,
                        ["request_nonce"] = Guid.NewGuid().ToString("N"),
                        ["instance_generation"] = NativeCommandArbitrator.Shared.Generation,
                    }
                    : null,
            };

        private Task<bool> Send(TcpClient peer, string text) =>
            host.Track(
                Task.Run(() =>
                {
                    StdioBridgeReconnectTests.SendFrame(peer.GetStream(), Encoding.UTF8.GetBytes(text));
                    return true;
                })
            );

        private Task<JObject> Read(TcpClient peer) =>
            host.Track(Task.Run(() => JObject.Parse(Encoding.UTF8.GetString(StdioBridgeReconnectTests.ReadFrame(peer.GetStream(), 10000)))));

        private IEnumerator Ping(TcpClient peer)
        {
            yield return Await(Send(peer, "ping"));
            var result = Read(peer);
            yield return Await(result);
            Assert.AreEqual("pong", result.Result.SelectToken("result.message").Value<string>());
        }

        [UnityTest]
        public IEnumerator HealthyPeersKeepIndependentControlWriteAndDisconnectOwnership()
        {
            Start();
            var connectA = Connect(true);
            yield return Await(connectA);
            var a = connectA.Result;
            peers.Add(a);
            var connectB = Connect(true);
            yield return Await(connectB);
            var b = connectB.Result;
            peers.Add(b);
            Assert.AreEqual(2, StdioBridgeHost.AuthenticatedClientCount);
            string original = Command(host.Name, true).ToString(Formatting.None);
            yield return Await(Send(a, original));
            yield return Await(entered.Task);
            var owner = StdioBrokerResendTests.FindProgressQueued(original);
            long retained = StdioBridgeHost.ReservedInputBytes;
            yield return Ping(b);
            yield return Await(Send(b, Command("ping", true).ToString(Formatting.None)));
            var control = Read(b);
            yield return Await(control);
            Assert.AreEqual("started", control.Result.SelectToken("native_arbitration.disposition").Value<string>());
            yield return Await(Send(b, Command(host.Name, true).ToString(Formatting.None)));
            var contender = Read(b);
            yield return Await(contender);
            Assert.AreEqual("not_started", contender.Result.SelectToken("native_arbitration.disposition").Value<string>());
            Assert.AreEqual(1, calls);
            b.Dispose();
            yield return Until(() => StdioBridgeHost.AuthenticatedClientCount == 1);
            Assert.IsFalse(owner.ConnectionCancellation.IsCancellationRequested);
            Assert.IsFalse(owner.Tcs.Task.IsCompleted);
            Assert.GreaterOrEqual(StdioBridgeHost.ReservedInputBytes, retained);
            Assert.IsTrue(StdioBridgeHost.IsRunning);
            release.TrySetResult(new { done = true });
            var result = Read(a);
            yield return Await(result);
            Assert.AreEqual("started", result.Result.SelectToken("native_arbitration.disposition").Value<string>());
            yield return Until(() => StdioBridgeHost.QueuedCommandCount == 0 && StdioBridgeHost.ReservedInputBytes == 0);
            yield return Ping(a);
        }

        [UnityTest]
        public IEnumerator HealthyLegacyIdenticalPayloadsRemainTwoDistinctIntents()
        {
            Start();
            var first = Connect();
            yield return Await(first);
            peers.Add(first.Result);
            var second = Connect();
            yield return Await(second);
            peers.Add(second.Result);
            string raw = Command(host.Name, false).ToString(Formatting.None);
            yield return Await(Send(first.Result, raw));
            yield return Await(entered.Task);
            yield return Await(Send(second.Result, raw));
            yield return Until(() => StdioBridgeHost.QueuedCommandCount == 2);
            Assert.AreEqual(1, calls, "Legacy FIFO preserves the first actual owner while retaining the second intent.");
            release.TrySetResult(new { done = true });
            var firstResult = Read(first.Result);
            var secondResult = Read(second.Result);
            yield return Await(firstResult);
            yield return Await(secondResult);
            Assert.AreEqual(2, calls);
            Assert.IsNull(firstResult.Result["native_arbitration"]);
            Assert.IsNull(secondResult.Result["native_arbitration"]);
            Assert.AreEqual("success", secondResult.Result.Value<string>("status"));
        }

        [UnityTest]
        public IEnumerator DisconnectedLegacyCollisionRefusesAmbiguousOutcomeWithoutAdoptionOrReplay()
        {
            Start();
            var first = Connect();
            yield return Await(first);
            peers.Add(first.Result);
            string raw = Command(host.Name, false).ToString(Formatting.None);
            yield return Await(Send(first.Result, raw));
            yield return Await(entered.Task);
            var original = StdioBrokerResendTests.FindProgressQueued(raw);
            first.Result.Dispose();
            yield return Until(() => original.ConnectionCancellation.IsCancellationRequested);
            var next = Connect();
            yield return Await(next);
            peers.Add(next.Result);
            yield return Await(Send(next.Result, raw));
            var refused = Read(next.Result);
            yield return Await(refused);
            Assert.AreEqual("legacy_command_outcome_unknown", refused.Result.Value<string>("code"));
            Assert.AreEqual(1, calls);
            Assert.AreEqual(1, StdioBridgeHost.QueuedCommandCount);
            Assert.IsFalse(release.Task.IsCompleted);
            Assert.Greater(StdioBridgeHost.ReservedInputBytes, 0);
            yield return Ping(next.Result);
        }

        [UnityTest]
        public IEnumerator AuthenticatedCapacityAndInvalidProofNeverEvictExistingPeers()
        {
            Start();
            var connections = host.Track(
                Task.Run(() =>
                {
                    var result = new List<TcpClient>();
                    try
                    {
                        for (int i = 0; i < StdioBridgeHost.MaxAuthenticatedClients; i++)
                            result.Add(StdioBridgeReconnectTests.ConnectProgressPeer(host.Port, host.Token, null, out _));
                        return result;
                    }
                    catch
                    {
                        foreach (var peer in result)
                            peer.Dispose();
                        throw;
                    }
                })
            );
            yield return Await(connections);
            peers.AddRange(connections.Result);
            Assert.AreEqual(64, StdioBridgeHost.AuthenticatedClientCount);
            var overCapacity = Connect(false, false);
            yield return Await(overCapacity);
            peers.Add(overCapacity.Result);
            Assert.AreEqual(64, StdioBridgeHost.AuthenticatedClientCount);
            var badProof = host.Track(
                Task.Run(() =>
                {
                    try
                    {
                        using var invalid = StdioBridgeReconnectTests.ConnectProgressPeer(host.Port, new string('0', 64), null, out _);
                        return false;
                    }
                    catch (IOException)
                    {
                        return true;
                    }
                })
            );
            yield return Await(badProof);
            Assert.IsTrue(badProof.Result);
            Assert.AreEqual(64, StdioBridgeHost.AuthenticatedClientCount);
            Assert.AreEqual(0, calls);
            yield return Ping(peers[0]);
            peers[1].Dispose();
            yield return Until(() => StdioBridgeHost.AuthenticatedClientCount == 63);
            var replacement = Connect();
            yield return Await(replacement);
            peers.Add(replacement.Result);
            Assert.AreEqual(64, StdioBridgeHost.AuthenticatedClientCount);
            yield return Ping(peers[0]);
        }

        [UnityTest]
        public IEnumerator StopRestartRetainsOldWorkAndDoesNotClearReplacementLaunch()
        {
            Start();
            var first = Connect(true);
            yield return Await(first);
            peers.Add(first.Result);
            string raw = Command(host.Name, true).ToString(Formatting.None);
            yield return Await(Send(first.Result, raw));
            yield return Await(entered.Task);
            var original = StdioBrokerResendTests.FindProgressQueued(raw);
            long retained = StdioBridgeHost.ReservedInputBytes;
            StdioBridgeHost.Stop();
            Assert.AreEqual(retained, StdioBridgeHost.ReservedInputBytes);
            StdioBridgeHost.StartOwned(0, host.Token);
            var second = Connect(true);
            yield return Await(second);
            peers.Add(second.Result);
            yield return Ping(second.Result);
            yield return Await(Send(second.Result, Command(host.Name, true).ToString(Formatting.None)));
            var blocked = Read(second.Result);
            yield return Await(blocked);
            Assert.AreEqual("not_started", blocked.Result.SelectToken("native_arbitration.disposition").Value<string>());
            Assert.AreEqual(1, calls);
            Assert.GreaterOrEqual(StdioBridgeHost.ReservedInputBytes, retained);
            release.TrySetResult(new { done = true });
            yield return Until(() => StdioBridgeHost.QueuedCommandCount == 0 && StdioBridgeHost.ReservedInputBytes == 0);
            Assert.IsNull(original.CommandJson);
            Assert.IsTrue(StdioBridgeHost.IsRunning);
            Assert.AreEqual(1, StdioBridgeHost.AuthenticatedClientCount);
            yield return Ping(second.Result);
        }

        [UnityTest]
        public IEnumerator DisconnectingQueuedPeerReleasesOnlyItsUnstartedInput()
        {
            Start();
            var first = Connect();
            yield return Await(first);
            peers.Add(first.Result);
            var second = Connect();
            yield return Await(second);
            peers.Add(second.Result);
            string raw = Command(host.Name, false).ToString(Formatting.None);
            yield return Await(Send(first.Result, raw));
            yield return Await(entered.Task);
            long activeBytes = StdioBridgeHost.ReservedInputBytes;
            yield return Await(Send(second.Result, raw));
            yield return Until(() => StdioBridgeHost.QueuedCommandCount == 2);
            Assert.AreEqual(activeBytes * 2, StdioBridgeHost.ReservedInputBytes);
            second.Result.Dispose();
            yield return Until(() => StdioBridgeHost.QueuedCommandCount == 1 && StdioBridgeHost.ReservedInputBytes == activeBytes);
            Assert.AreEqual(1, calls);
            Assert.IsFalse(release.Task.IsCompleted);
            release.TrySetResult(new { done = true });
            var result = Read(first.Result);
            yield return Await(result);
            Assert.AreEqual("success", result.Result.Value<string>("status"));
        }

        [UnityTest]
        public IEnumerator UnauthenticatedCapacityCannotDisplaceAuthenticatedOwner()
        {
            Start();
            var healthy = Connect();
            yield return Await(healthy);
            peers.Add(healthy.Result);
            var slow = host.Track(
                Task.Run(() =>
                {
                    var result = new List<TcpClient>();
                    try
                    {
                        for (int i = 0; i < 16; i++)
                        {
                            var peer = new TcpClient();
                            result.Add(peer);
                            Assert.IsTrue(peer.ConnectAsync("127.0.0.1", host.Port).Wait(5000));
                            var stream = peer.GetStream();
                            stream.ReadTimeout = 5000;
                            int read;
                            do
                            {
                                read = stream.ReadByte();
                                Assert.GreaterOrEqual(read, 0);
                            } while (read != '\n');
                        }
                        return result;
                    }
                    catch
                    {
                        foreach (var peer in result)
                            peer.Dispose();
                        throw;
                    }
                })
            );
            yield return Await(slow);
            peers.AddRange(slow.Result);
            Assert.AreEqual(16, StdioBridgeHost.AuthenticatingClientCount);
            var rejected = host.Track(
                Task.Run(() =>
                {
                    using var peer = new TcpClient();
                    Assert.IsTrue(peer.ConnectAsync("127.0.0.1", host.Port).Wait(5000));
                    peer.GetStream().ReadTimeout = 5000;
                    try
                    {
                        return peer.GetStream().ReadByte() == -1;
                    }
                    catch (IOException)
                    {
                        return true;
                    }
                })
            );
            yield return Await(rejected);
            Assert.IsTrue(rejected.Result);
            Assert.AreEqual(16, StdioBridgeHost.AuthenticatingClientCount);
            Assert.AreEqual(1, StdioBridgeHost.AuthenticatedClientCount);
            Assert.AreEqual(0, StdioBridgeHost.ReservedInputBytes);
            Assert.AreEqual(0, calls);
            yield return Ping(healthy.Result);
            StdioBridgeHost.Stop();
            yield return Until(() => StdioBridgeHost.AuthenticatingClientCount == 0 && StdioBridgeHost.AuthenticatedClientCount == 0);
        }

        [UnityTest]
        public IEnumerator SaturatedLargeInputKeepsBoundedSmallIngressAndDisconnectReclaimsWaiter()
        {
            Start();
            var first = Connect();
            yield return Await(first);
            peers.Add(first.Result);
            var second = Connect();
            yield return Await(second);
            peers.Add(second.Result);
            Assert.AreEqual(0, StdioBridgeHost.ReservedInputBytes, "Idle header readers hold no body budget.");
            var budget = (StdioFrameBudget)typeof(StdioBridgeHost).GetField("inputBudget", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            inputs.Add(budget.AcquireAsync(128 * 1024 * 1024, CancellationToken.None).Result);
            inputs.Add(budget.AcquireAsync(128 * 1024 * 1024, CancellationToken.None).Result);
            var headerOnly = host.Track(
                Task.Run(() =>
                {
                    byte[] header = new byte[8];
                    header[6] = 64; // 16KiB declared body: large lane, no body allocated/admitted yet.
                    second.Result.GetStream().Write(header, 0, header.Length);
                    return true;
                })
            );
            yield return Await(headerOnly);
            yield return Until(() => StdioBridgeHost.PendingInputCount == 1);
            Assert.AreEqual(1280L * 1024 * 1024, StdioBridgeHost.ReservedInputBytes);
            yield return Ping(first.Result);
            second.Result.Dispose();
            yield return Until(() => StdioBridgeHost.PendingInputCount == 0);
            Assert.AreEqual(1280L * 1024 * 1024, StdioBridgeHost.ReservedInputBytes);
            foreach (var lease in inputs)
                lease.Dispose();
            inputs.Clear();
            yield return Until(() => StdioBridgeHost.ReservedInputBytes == 0);
            yield return Ping(first.Result);
        }
    }
}
