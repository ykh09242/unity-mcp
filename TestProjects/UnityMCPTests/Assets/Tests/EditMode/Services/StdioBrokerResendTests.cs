using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// In-flight retry reuse requires negotiated request identity. Independent legacy requests
    /// remain distinct, and a disconnected legacy owner's identical payload has an ambiguous outcome.
    /// </summary>
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class StdioBrokerResendTests
    {
        private const int ReadTimeoutMs = 10000;

        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":900}}", 300000, 930000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeoutSeconds\":900}}", 300000, 930000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":999999}}", 300000, 86430000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":900}}", 1200000, 1200000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":1}}", 1000, 35000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":2147483648}}", 1000, 210000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":900.5}}", 1000, 210000)]
        [TestCase("{\"type\":\"blender_bridge\"}", 1000, 210000)]
        [TestCase("{\"type\":\"manage_scene\",\"params\":{\"timeout_seconds\":900}}", 300000, 900000)]
        [TestCase("invalid json", 300000, 300000)]
        [TestCase("{\"type\":[]}", 300000, 300000)]
        public void CommandTimeout_UsesBoundedOperationBudget(string payload, int baseline, int expected)
        {
            Assert.AreEqual(expected, StdioBridgeHost.ResolveCommandTimeoutMs(payload, baseline));
        }

        [Test]
        public void IsBrokerResend_SamePayloadFromAnotherConnection_IsAResend()
        {
            object first = new object();
            object second = new object();

            Assert.IsTrue(
                StdioBridgeHost.IsBrokerResend("{\"type\":\"a\"}", first, "{\"type\":\"a\"}", second),
                "raw equality is only a collision candidate, never proof of retry intent"
            );
        }

        [Test]
        public void IsBrokerResend_SameConnection_IsNotAResend()
        {
            object connection = new object();

            Assert.IsFalse(
                StdioBridgeHost.IsBrokerResend("{\"type\":\"a\"}", connection, "{\"type\":\"a\"}", connection),
                "one connection handles commands sequentially, so identical payloads are distinct requests"
            );
        }

        [Test]
        public void IsBrokerResend_DifferentPayload_IsNotAResend()
        {
            Assert.IsFalse(
                StdioBridgeHost.IsBrokerResend("{\"type\":\"a\"}", new object(), "{\"type\":\"b\"}", new object()),
                "different work must never be collapsed"
            );
        }

        [Test]
        public void IsBrokerResend_UnknownOwner_IsNotAResend()
        {
            Assert.IsFalse(
                StdioBridgeHost.IsBrokerResend("{\"type\":\"a\"}", null, "{\"type\":\"a\"}", new object()),
                "a command with no recorded owner cannot be proven to be a resend"
            );
            Assert.IsFalse(
                StdioBridgeHost.IsBrokerResend("{\"type\":\"a\"}", new object(), "{\"type\":\"a\"}", null),
                "an incoming command with no owner cannot be proven to be a resend"
            );
        }

        [TestCase(false, false, false, "Distinct")]
        [TestCase(true, false, false, "AmbiguousLegacyOutcome")]
        [TestCase(false, true, true, "ExistingRequest")]
        [TestCase(true, true, true, "ExistingRequest")]
        [TestCase(true, true, false, "Distinct")]
        public void RetryIdentity_NeverInfersIntentFromIndependentLegacyPayloads(bool disconnected, bool bound, bool sameNonce, string expected)
        {
            using var connection = new CancellationTokenSource();
            using var command = new CancellationTokenSource();
            if (disconnected)
                connection.Cancel();
            command.Cancel(); // A command timeout is not a connection identity change.
            var queued = new QueuedCommand
            {
                CommandJson = "exact bytes",
                Owner = new object(),
                ConnectionCancellation = connection.Token,
                OwnerCancellation = command.Token,
                RequestNonce = bound ? "nonce-original" : null,
            };
            Assert.AreEqual(
                expected,
                StdioBridgeHost
                    .ClassifyBrokerRequest(queued, "exact bytes", new object(), bound ? (sameNonce ? "nonce-original" : "nonce-other") : null)
                    .ToString()
            );
            Assert.AreEqual(
                StdioBridgeHost.BrokerRequestDisposition.Distinct,
                StdioBridgeHost.ClassifyBrokerRequest(queued, "different bytes", new object(), queued.RequestNonce)
            );
        }

        [Test]
        public void ProgressBusinessIdentity_PreservesLiteralStringsAndExactBytes()
        {
            string business = "{\"type\":\"batch_execute\",\"params\":{\"text\":\"2026-10-19T01:02:03Z\",\"commands\":[]}}";
            object original = new object(),
                replacement = new object();
            Assert.IsTrue(StdioBridgeHost.IsBrokerResend(business, original, business, replacement));
            Assert.IsFalse(StdioBridgeHost.IsBrokerResend(business, original, business, original));
            Assert.IsFalse(StdioBridgeHost.IsBrokerResend(business, original, business, null));
        }

        [Test]
        public void ProgressBusinessIdentity_DoesNotNormalizeWhitespaceOrPropertyOrder()
        {
            const string business = "{\"type\":\"batch_execute\",\"params\":{\"commands\":[]}}";
            object original = new object(),
                replacement = new object();
            Assert.IsFalse(StdioBridgeHost.IsBrokerResend(business, original, "{ \"type\":\"batch_execute\",\"params\":{\"commands\":[]}}", replacement));
            Assert.IsFalse(StdioBridgeHost.IsBrokerResend(business, original, "{\"params\":{\"commands\":[]},\"type\":\"batch_execute\"}", replacement));
        }

        [Test]
        public async Task ProgressIdentifiedResend_KeepsOriginalOwnerAndDoesNotAttachNewObserver()
        {
            const string firstName = "__core06_broker_first",
                secondName = "__core06_broker_second";
            int firstCalls = 0,
                secondCalls = 0;
            var release = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var suppressed = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var handlers = new ProgressHandlerScope();
            using var enabled = NativeProgressProbe.EnableTools("batch_execute");
            handlers.Set(
                firstName,
                new HandlerInfo(
                    firstName,
                    _ =>
                    {
                        firstCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            handlers.Set(
                secondName,
                new HandlerInfo(
                    secondName,
                    null,
                    async _ =>
                    {
                        secondCalls++;
                        entered.TrySetResult(true);
                        try
                        {
                            return await release.Task.ConfigureAwait(true);
                        }
                        finally
                        {
                            settled.TrySetResult(true);
                        }
                    }
                )
            );
            using var host = new StdioBridgeReconnectTests.TimingHostScope(_ => Task.FromResult<object>(new { unused = true }));
            var batch = new JObject
            {
                ["type"] = "batch_execute",
                ["arbitration"] = new JObject
                {
                    ["version"] = 1,
                    ["request_nonce"] = Guid.NewGuid().ToString("N"),
                    ["instance_generation"] = NativeCommandArbitrator.Shared.Generation,
                },
                ["params"] = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = firstName }, new JObject { ["tool"] = secondName }) },
            };
            NativeProgressProbe.RequireBatchCapacity((JObject)batch["params"]);
            string original = batch.ToString(Formatting.None);
            string firstId = Guid.NewGuid().ToString("D"),
                secondId = Guid.NewGuid().ToString("D");
            TcpClient first = null,
                second = null;
            Application.LogCallback onLog = (message, _stack, _kind) =>
            {
                if (message.Contains("Suppressed repeated identified stdio command; awaiting its in-flight result."))
                    suppressed.TrySetResult(true);
            };
            Application.logMessageReceivedThreaded += onLog;
            try
            {
                var connectFirst = host.Track(
                    Task.Run(() =>
                        StdioBridgeReconnectTests.ConnectProgressPeer(
                            host.Port,
                            host.Token,
                            new JArray("command_progress_v1", NativeCommandArbitrator.Capability),
                            out _
                        )
                    )
                );
                await NativeProgressProbe.AwaitOwned(connectFirst);
                first = await connectFirst;
                var sendFirst = host.Track(
                    Task.Run(() =>
                    {
                        StdioBridgeReconnectTests.SendFrame(first.GetStream(), StdioBridgeReconnectTests.ProgressPrelude(firstId));
                        StdioBridgeReconnectTests.SendFrame(first.GetStream(), Encoding.UTF8.GetBytes(original));
                        return true;
                    })
                );
                await NativeProgressProbe.AwaitOwned(sendFirst);
                await NativeProgressProbe.AwaitOwned(entered.Task);
                var progress = host.Track(Task.Run(() => StdioBridgeReconnectTests.ReadFrame(first.GetStream(), ReadTimeoutMs)));
                await NativeProgressProbe.AwaitOwned(progress);
                StdioBridgeReconnectTests.AssertProgressPayload(await progress, firstId, 1);
                Assert.IsFalse(settled.Task.IsCompleted);
                var before = FindProgressQueued(original);
                object originalProgress = before.Progress;
                object originalOwner = before.Owner;
                object originalTcs = before.Tcs;
                Assert.IsNotNull(originalProgress);
                Assert.AreEqual(firstId, before.ProgressId);
                var connectSecond = host.Track(
                    Task.Run(() =>
                        StdioBridgeReconnectTests.ConnectProgressPeer(
                            host.Port,
                            host.Token,
                            new JArray("command_progress_v1", NativeCommandArbitrator.Capability),
                            out _
                        )
                    )
                );
                await NativeProgressProbe.AwaitOwned(connectSecond);
                second = await connectSecond;
                var sendSecond = host.Track(
                    Task.Run(() =>
                    {
                        StdioBridgeReconnectTests.SendFrame(second.GetStream(), StdioBridgeReconnectTests.ProgressPrelude(secondId));
                        StdioBridgeReconnectTests.SendFrame(second.GetStream(), Encoding.UTF8.GetBytes(original));
                        return true;
                    })
                );
                await NativeProgressProbe.AwaitOwned(sendSecond);
                // This real host warning is the suppression checkpoint; a count-only sleep is not proof.
                await NativeProgressProbe.AwaitOwned(suppressed.Task);
                var after = FindProgressQueued(original);
                Assert.AreSame(before, after);
                Assert.AreSame(originalOwner, after.Owner);
                Assert.AreSame(originalTcs, after.Tcs);
                Assert.AreSame(originalProgress, after.Progress);
                Assert.AreEqual(firstId, after.ProgressId);
                Assert.AreNotEqual(secondId, after.ProgressId);
                Assert.IsFalse(settled.Task.IsCompleted);
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(1, secondCalls);
                var readSecond = host.Track(Task.Run(() => StdioBridgeReconnectTests.ReadFrame(second.GetStream(), ReadTimeoutMs)));
                release.TrySetResult(new { done = true });
                await NativeProgressProbe.AwaitOwned(settled.Task);
                await NativeProgressProbe.AwaitOwned(readSecond);
                byte[] body = await readSecond;
                Assert.IsNotNull(body);
                Assert.IsFalse(StdioBridgeReconnectTests.HasProgressPrefix(body), "The identified retry cannot subscribe to original work's progress.");
                var response = JObject.Parse(Encoding.UTF8.GetString(body));
                Assert.AreEqual("started", response.SelectToken("native_arbitration.disposition").Value<string>());
                Assert.AreEqual("success", response.SelectToken("payload.status").Value<string>());
            }
            finally
            {
                Application.logMessageReceivedThreaded -= onLog;
                release.TrySetResult(new { done = true });
                if (entered.Task.IsCompleted)
                    await NativeProgressProbe.AwaitOwned(settled.Task);
                first?.Dispose();
                second?.Dispose();
                await StdioBridgeReconnectTests.AwaitProgressQueueEmpty();
            }
            Assert.AreEqual(1, firstCalls);
            Assert.AreEqual(1, secondCalls);
        }

        internal static QueuedCommand FindProgressQueued(string business)
        {
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            object gate = typeof(StdioBridgeHost).GetField("lockObj", flags).GetValue(null);
            var queue = (IDictionary)typeof(StdioBridgeHost).GetField("commandQueue", flags).GetValue(null);
            lock (gate)
            {
                var matching = queue.Values.Cast<QueuedCommand>().Where(item => item.CommandJson == business).ToArray();
                Assert.AreEqual(1, matching.Length, "Capture the unique actual queued invocation while its real child is held.");
                return matching.Single();
            }
        }
    }
}
