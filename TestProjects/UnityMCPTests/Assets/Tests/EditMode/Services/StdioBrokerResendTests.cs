using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// When a long-running command outlives the broker's patience, the Python side reconnects and
    /// resends it. The bridge used to queue that resend as a brand-new command behind the original,
    /// so the work ran twice — visibly, for commands with side effects (issue #1130).
    /// </summary>
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class StdioBrokerResendTests
    {
        private const int ConnectTimeoutMs = 5000;
        private const int ReadTimeoutMs = 10000;

        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":900}}", 300000, 930000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeoutSeconds\":900}}", 300000, 930000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":999999}}", 300000, 3630000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":900}}", 1200000, 1200000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":1}}", 1000, 35000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":2147483648}}", 1000, 210000)]
        [TestCase("{\"type\":\"blender_bridge\",\"params\":{\"timeout_seconds\":900.5}}", 1000, 210000)]
        [TestCase("{\"type\":\"blender_bridge\"}", 1000, 210000)]
        [TestCase("{\"type\":\"manage_scene\",\"params\":{\"timeout_seconds\":900}}", 300000, 300000)]
        [TestCase("invalid json", 300000, 300000)]
        [TestCase("{\"type\":[]}", 300000, 300000)]
        public void CommandTimeout_UsesBoundedBlenderBudgetOnly(string payload, int baseline, int expected)
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
                "same payload arriving on a different connection is the resend signature"
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

        /// <summary>
        /// The bridge must not queue a second copy of a command that is already in flight from
        /// another connection. Asserted on the queue itself while the main thread is blocked, so
        /// no command has been dispatched yet and the result does not depend on what the command
        /// would have done. Requires the bridge (batchmode needs UNITY_MCP_ALLOW_BATCH).
        /// </summary>
        [UnityTest]
        public IEnumerator IdenticalCommandResentOnNewConnection_IsQueuedOnce()
        {
            if (!StdioBridgeHost.IsRunning)
            {
                Assert.Ignore("StdioBridgeHost is not running; skipping broker-resend test.");
                yield break;
            }

            int port = StdioBridgeHost.GetCurrentPort();
            // MCP polls this same bridge while the test runs. Identify only our command,
            // so unrelated get_test_job traffic does not look like a duplicate resend.
            string commandJson =
                "{\"type\":\"read_console\",\"test_nonce\":\"" + Guid.NewGuid().ToString("N") + "\",\"params\":{\"action\":\"get\",\"count\":1}}";
            byte[] command = Encoding.UTF8.GetBytes(commandJson);

            TcpClient first = null;
            TcpClient second = null;
            int queuedAfterFirst;
            int queuedAfterResend;
            try
            {
                first = Connect(port);
                SendFrame(first.GetStream(), command);

                // Sleeping on the main thread lets the listener task read and queue the frame while
                // ProcessCommands — an editor-update hook — cannot run and drain it. The wait also
                // has to happen before the second connect: a new connection closes stale clients,
                // and if it wins that race the first frame is never read at all.
                Thread.Sleep(1500);
                queuedAfterFirst = CountQueuedPayload(commandJson);

                // A second connection is what the broker opens after giving up on the first.
                second = Connect(port);
                SendFrame(second.GetStream(), command);
                Thread.Sleep(1500);
                queuedAfterResend = CountQueuedPayload(commandJson);
            }
            finally
            {
                SafeClose(first);
                SafeClose(second);
            }

            // Let the queue drain so we do not leak state into later tests.
            for (int i = 0; i < 120; i++)
                yield return null;

            Assert.AreEqual(
                1,
                queuedAfterFirst,
                "precondition: the first command must be sitting in the queue undrained — "
                    + $"found {queuedAfterFirst} matching entries, so this run proves nothing about the resend"
            );
            Assert.AreEqual(
                1,
                queuedAfterResend,
                $"the resend should have attached to the in-flight command, but {queuedAfterResend} "
                    + "matching entries were queued — the command would run that many times"
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
        public async Task ProgressSuppressedResend_KeepsOriginalOwnerAndDoesNotAttachNewObserver()
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
                if (message.Contains("Suppressed duplicate command resent on a new connection; awaiting the in-flight result instead of running it twice."))
                    suppressed.TrySetResult(true);
            };
            Application.logMessageReceivedThreaded += onLog;
            try
            {
                var connectFirst = host.Track(
                    Task.Run(() => StdioBridgeReconnectTests.ConnectProgressPeer(host.Port, host.Token, new JArray("command_progress_v1"), out _))
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
                    Task.Run(() => StdioBridgeReconnectTests.ConnectProgressPeer(host.Port, host.Token, new JArray("command_progress_v1"), out _))
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
                var readSecond = host.Track(
                    Task.Run(() =>
                    {
                        try
                        {
                            return StdioBridgeReconnectTests.ReadFrame(second.GetStream(), ReadTimeoutMs);
                        }
                        catch (IOException error)
                            when (error.Message == "Connection closed before reading expected bytes"
                                || error.InnerException is SocketException socket
                                    && (socket.SocketErrorCode == SocketError.ConnectionReset || socket.SocketErrorCode == SocketError.ConnectionAborted)
                            )
                        {
                            return null;
                        }
                    })
                );
                // Old ownership may already be canceled/unknown; never invent successful delivery.
                await NativeProgressProbe.AwaitOwned(readSecond);
                byte[] body = await readSecond;
                if (body != null)
                {
                    Assert.IsFalse(StdioBridgeReconnectTests.HasProgressPrefix(body), "The replacement cannot subscribe to original work's progress.");
                    var response = JObject.Parse(Encoding.UTF8.GetString(body));
                    Assert.That(response.Value<string>("status"), Is.EqualTo("error").Or.EqualTo("success"));
                }
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

        private static int CountQueuedPayload(string commandJson)
        {
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            var queueField = typeof(StdioBridgeHost).GetField("commandQueue", flags);
            var lockField = typeof(StdioBridgeHost).GetField("lockObj", flags);
            Assert.NotNull(queueField);
            Assert.NotNull(lockField);
            lock (lockField.GetValue(null))
            {
                var queue = (IDictionary)queueField.GetValue(null);
                int count = 0;
                foreach (QueuedCommand queued in queue.Values)
                    if (queued.CommandJson == commandJson)
                        count++;
                return count;
            }
        }

        private static TcpClient Connect(int port)
        {
            var client = new TcpClient();
            Assert.IsTrue(client.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs), "connect timed out");
            client.ReceiveTimeout = ReadTimeoutMs;
            string handshake = ReadLine(client.GetStream(), ReadTimeoutMs);
            Assert.That(handshake, Does.Contain("FRAMING=1"), "expected the framing handshake");
            return client;
        }

        private static void SafeClose(TcpClient client)
        {
            if (client == null)
                return;
            try
            {
                client.Client.LingerState = new LingerOption(true, 0);
                client.Close();
            }
            catch { }
        }

        private static string ReadLine(NetworkStream stream, int timeoutMs)
        {
            stream.ReadTimeout = timeoutMs;
            var sb = new StringBuilder();
            var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (DateTime.UtcNow <= deadline)
            {
                int b = stream.ReadByte();
                if (b < 0)
                    throw new IOException("Connection closed while reading handshake");
                if (b == '\n')
                    return sb.ToString();
                sb.Append((char)b);
            }
            throw new TimeoutException("Timed out reading handshake line");
        }

        private static void SendFrame(NetworkStream stream, byte[] payload)
        {
            byte[] header = new byte[8];
            ulong len = (ulong)payload.LongLength;
            for (int i = 0; i < 8; i++)
                header[i] = (byte)(len >> (56 - 8 * i));
            stream.Write(header, 0, 8);
            stream.Write(payload, 0, payload.Length);
            stream.Flush();
        }
    }
}
