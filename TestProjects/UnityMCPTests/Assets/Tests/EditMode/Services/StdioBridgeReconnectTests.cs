using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
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
    /// Tests that StdioBridgeHost correctly handles client reconnection scenarios.
    /// After an abrupt client disconnect, a new client must be able to connect and
    /// have its commands processed — this was broken by the zombie state bug (#785).
    /// </summary>
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class StdioBridgeReconnectTests
    {
        private const int ConnectTimeoutMs = 5000;
        private const int ReadTimeoutMs = 10000;

        private sealed class TimingValue
        {
            internal int Reads;
            internal string Text;
            public string Payload
            {
                get
                {
                    Reads++;
                    return Text;
                }
            }
        }

        // Only admitted by sole QA in a fresh owned scratch Editor. Never stops an existing host.
        internal sealed class TimingHostScope : IDisposable
        {
            private const BindingFlags Flags = BindingFlags.NonPublic | BindingFlags.Static;
            private readonly FieldInfo _logging = typeof(McpLogRecord).GetField("_isEnabledCached", Flags);
            private readonly FieldInfo _sampling = typeof(CommandPhaseTiming).GetField("_admissions", Flags);
            private readonly bool _previousLogging;
            private readonly long _previousSampling;
            private readonly IDictionary _handlers;
            private readonly object _previousHandler;
            private readonly string _logPath;
            private readonly long _logOffset;
            private readonly System.Collections.Generic.List<Task> _work = new();
            internal readonly string Name = "core13_owned_" + Guid.NewGuid().ToString("N");
            internal readonly string Token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            internal int Port => StdioBridgeHost.GetCurrentPort();

            internal Task<T> Track<T>(Task<T> task)
            {
                _work.Add(task);
                return task;
            }

            internal TimingHostScope(Func<JObject, Task<object>> handler)
            {
                Assert.IsFalse(StdioBridgeHost.IsRunning, "This fixture requires an idle owned scratch Editor; never replace another host.");
                CommandRegistry.Initialize();
                _handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", Flags).GetValue(null);
                _previousHandler = _handlers[Name];
                _previousLogging = (bool)_logging.GetValue(null);
                _previousSampling = (long)_sampling.GetValue(null);
                _logPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Library", "MCPForUnity", "Logs", "mcp.log"));
                _logOffset = File.Exists(_logPath) ? new FileInfo(_logPath).Length : 0;
                Assert.Less(_logOffset, 512 * 1024, "Use a fresh owned case log; do not truncate or rotate pre-existing evidence.");
                try
                {
                    var handlerType = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Tools.HandlerInfo", true);
                    _handlers[Name] = Activator.CreateInstance(handlerType, new object[] { Name, null, handler });
                    // Cache only: do not call the EditorPrefs-writing property setter.
                    _logging.SetValue(null, true);
                    _sampling.SetValue(null, 0L);
                    StdioBridgeHost.StartOwned(0, Token);
                    Assert.IsTrue(StdioBridgeHost.IsRunning);
                }
                catch
                {
                    Dispose();
                    throw;
                }
            }

            internal JObject[] TimingRows()
            {
                Assert.IsTrue(McpLogRecord.Flush(5000));
                lock (typeof(McpLogRecord).GetField("_logLock", Flags).GetValue(null))
                {
                    if (!File.Exists(_logPath))
                        return Array.Empty<JObject>();
                    using var stream = new FileStream(_logPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
                    Assert.GreaterOrEqual(stream.Length, _logOffset, "Owned fixture log unexpectedly rotated.");
                    stream.Position = _logOffset;
                    using var reader = new StreamReader(stream, Encoding.UTF8);
                    var rows = new System.Collections.Generic.List<JObject>();
                    string line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        var row = JObject.Parse(line);
                        if (row.Value<string>("event") == "phase_timing" && row.Value<string>("transport") == "stdio")
                            rows.Add(row);
                    }
                    return rows.ToArray();
                }
            }

            public void Dispose()
            {
                try
                {
                    StdioBridgeHost.Stop();
                    foreach (var connection in _work.OfType<Task<TcpClient>>())
                        if (connection.Status == TaskStatus.RanToCompletion)
                            connection.Result.Dispose();
                    try
                    {
                        Task.WaitAll(_work.ToArray(), 5000);
                    }
                    catch (AggregateException) { /* Read failures after an owned disconnect are observed below. */ }
                    foreach (var task in _work)
                        if (task.IsFaulted)
                            _ = task.Exception;
                    foreach (var connection in _work.OfType<Task<TcpClient>>())
                        if (connection.Status == TaskStatus.RanToCompletion)
                            connection.Result.Dispose();
                    Assert.IsTrue(_work.All(task => task.IsCompleted), "Owned network tasks must be terminal after host/socket cleanup.");
                }
                finally
                {
                    if (_previousHandler == null)
                        _handlers.Remove(Name);
                    else
                        _handlers[Name] = _previousHandler;
                    _logging.SetValue(null, _previousLogging);
                    _sampling.SetValue(null, _previousSampling);
                }
            }
        }

        private static TcpClient ConnectTimingPeer(int port, string token)
        {
            return ConnectProgressPeer(port, token, null, out _);
        }

        internal static TcpClient ConnectProgressPeer(int port, string token, JToken offered, out JObject reply, bool expectAdmitted = true)
        {
            var client = new TcpClient();
            try
            {
                Assert.IsTrue(client.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs));
                var stream = client.GetStream();
                string[] banner = ReadLine(stream, ReadTimeoutMs).Trim().Split(' ');
                string generation = banner.Single(part => part.StartsWith("SERVER=", StringComparison.Ordinal)).Substring(7);
                string challenge = banner.Single(part => part.StartsWith("CHALLENGE=", StringComparison.Ordinal)).Substring(10);
                string nonce = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
                var request = new JObject
                {
                    ["type"] = "authenticate",
                    ["version"] = 2,
                    ["client_nonce"] = nonce,
                    ["proof"] = StdioBridgeAuthentication.Proof(token, "client", generation, challenge, nonce),
                };
                if (offered != null)
                    request["capabilities"] = offered;
                SendFrame(stream, Encoding.UTF8.GetBytes(request.ToString(Formatting.None)));
                reply = JObject.Parse(Encoding.UTF8.GetString(ReadFrame(stream, ReadTimeoutMs)));
                if (!expectAdmitted)
                {
                    Assert.AreEqual("authentication_refused", reply.Value<string>("type"));
                    Assert.AreEqual("client_admission_refused", reply.Value<string>("code"));
                    return client;
                }
                Assert.AreEqual("authenticated", reply.Value<string>("type"));
                Assert.AreEqual(2, reply.Value<int>("version"));
                string session = reply.Value<string>("session_id");
                Assert.IsTrue(Guid.TryParseExact(session, "N", out _));
                Assert.AreEqual(StdioBridgeAuthentication.Proof(token, "server", generation, challenge, nonce, session), reply.Value<string>("proof"));
                return client;
            }
            catch
            {
                client.Dispose();
                throw;
            }
        }

        [TestCase("accepted")]
        [TestCase("absent")]
        [TestCase("scalar")]
        [TestCase("too-many")]
        [TestCase("too-long")]
        public async Task ProgressAuth_OnlyBoundedOfferedCapabilityIsAccepted(string offer)
        {
            using var host = new TimingHostScope(_ => Task.FromResult<object>(new { done = true }));
            JToken offered = null;
            if (offer == "accepted")
                offered = new JArray("command_progress_v1");
            if (offer == "scalar")
                offered = new JValue("command_progress_v1");
            if (offer == "too-many")
                offered = new JArray(Enumerable.Repeat("command_progress_v1", 17));
            if (offer == "too-long")
                offered = new JArray(new string('x', 65), "command_progress_v1");
            JObject ack = null;
            var connecting = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, offered, out ack)));
            await NativeProgressProbe.AwaitOwned(connecting);
            using var connected = await connecting;
            JToken capabilities = ack["capabilities"];
            if (offer == "accepted")
            {
                Assert.IsNotNull(capabilities, "Negotiation prerequisite: a valid offer must be acknowledged before the progress behavioral run.");
                Assert.AreEqual(JTokenType.Array, capabilities.Type);
                CollectionAssert.AreEqual(new[] { "command_progress_v1" }, capabilities.Values<string>().ToArray());
            }
            else if (offer == "absent")
            {
                CollectionAssert.AreEquivalent(new[] { "type", "version", "session_id", "proof" }, ack.Properties().Select(item => item.Name));
            }
            else
            {
                Assert.IsTrue(capabilities == null || capabilities.Type == JTokenType.Array && !capabilities.Any());
            }
            var ping = host.Track(
                Task.Run(() =>
                {
                    SendFrame(connected.GetStream(), Encoding.UTF8.GetBytes("ping"));
                    return Encoding.UTF8.GetString(ReadFrame(connected.GetStream(), ReadTimeoutMs));
                })
            );
            await NativeProgressProbe.AwaitOwned(ping);
            Assert.That(await ping, Does.Contain("pong"), "Optional capability junk must not weaken or break the valid required HMAC session.");
        }

        [Test]
        public Task ProgressWire_NegotiatedBatchReportsBeforeHeldSecondAndKeepsFifo() => ProgressBatchWireCase(true, true);

        [Test]
        public Task ProgressWire_NegotiatedWithoutPreludeKeepsOriginalResult() => ProgressBatchWireCase(true, false);

        [Test]
        public Task ProgressWire_OldClientKeepsOriginalResult() => ProgressBatchWireCase(false, false);

        private static async Task ProgressBatchWireCase(bool offerCapability, bool optIn)
        {
            const string first = "__core06_stdio_first",
                second = "__core06_stdio_second";
            var release = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            int firstCalls = 0,
                secondCalls = 0,
                nextCalls = 0;
            using var handlers = new ProgressHandlerScope();
            using var enabled = NativeProgressProbe.EnableTools("batch_execute");
            handlers.Set(
                first,
                new HandlerInfo(
                    first,
                    _ =>
                    {
                        firstCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            handlers.Set(
                second,
                new HandlerInfo(
                    second,
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
            using var host = new TimingHostScope(_ =>
            {
                nextCalls++;
                return Task.FromResult<object>(new { next = true });
            });
            TcpClient peer = null;
            Task<byte[]> reading = null;
            string id = Guid.NewGuid().ToString("D");
            var batch = new JObject
            {
                ["type"] = "batch_execute",
                ["params"] = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = second }) },
            };
            NativeProgressProbe.RequireBatchCapacity((JObject)batch["params"]);
            byte[] originalBusiness = Encoding.UTF8.GetBytes(batch.ToString(Formatting.None));
            try
            {
                JObject ack = null;
                var connecting = host.Track(
                    Task.Run(() => ConnectProgressPeer(host.Port, host.Token, offerCapability ? new JArray("command_progress_v1") : null, out ack))
                );
                await NativeProgressProbe.AwaitOwned(connecting);
                using var connected = await connecting;
                peer = connected;
                if (offerCapability)
                {
                    Assert.IsNotNull(ack["capabilities"], "Negotiation prerequisite; this assertion alone is not progress behavioral RED.");
                    CollectionAssert.Contains(ack["capabilities"].Values<string>().ToArray(), "command_progress_v1");
                }
                var sending = host.Track(
                    Task.Run(() =>
                    {
                        if (optIn)
                            SendFrame(
                                peer.GetStream(),
                                Encoding.UTF8.GetBytes(
                                    new JObject
                                    {
                                        ["type"] = "command_progress_begin",
                                        ["version"] = 1,
                                        ["id"] = id,
                                    }.ToString(Formatting.None)
                                )
                            );
                        SendFrame(peer.GetStream(), originalBusiness);
                        return true;
                    })
                );
                await NativeProgressProbe.AwaitOwned(sending);
                await NativeProgressProbe.AwaitOwned(entered.Task);
                Assert.IsFalse(settled.Task.IsCompleted);
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(1, secondCalls);
                reading = host.Track(Task.Run(() => ReadFrame(peer.GetStream(), ReadTimeoutMs)));
                if (optIn)
                {
                    await NativeProgressProbe.AwaitOwned(reading);
                    AssertProgressPayload(await reading, id, 1);
                    Assert.IsFalse(settled.Task.IsCompleted, "An actual MCPG1/2 must arrive while the second native handler is held.");
                    reading = host.Track(Task.Run(() => ReadFrame(peer.GetStream(), ReadTimeoutMs)));
                }
                else
                {
                    await Task.Delay(200);
                    Assert.IsFalse(reading.IsCompleted, "No opt-in means no optional frame while the real response is held.");
                }
                var nextSend = host.Track(
                    Task.Run(() =>
                    {
                        SendFrame(
                            peer.GetStream(),
                            Encoding.UTF8.GetBytes(new JObject { ["type"] = host.Name, ["params"] = new JObject() }.ToString(Formatting.None))
                        );
                        return true;
                    })
                );
                await NativeProgressProbe.AwaitOwned(nextSend);
                Assert.AreEqual(0, nextCalls, "Sending the next FIFO request must not split/release the held batch.");
                release.TrySetResult(new { done = true });
                await NativeProgressProbe.AwaitOwned(settled.Task);
                int lastCompleted = optIn ? 1 : 0;
                byte[] final;
                while (true)
                {
                    await NativeProgressProbe.AwaitOwned(reading);
                    final = await reading;
                    if (!HasProgressPrefix(final))
                        break;
                    Assert.IsTrue(optIn);
                    var update = JObject.Parse(Encoding.UTF8.GetString(final, 4, final.Length - 4));
                    int completed = update.Value<int>("completed");
                    Assert.Greater(completed, lastCompleted);
                    Assert.LessOrEqual(completed, 2);
                    AssertProgressPayload(final, id, completed);
                    lastCompleted = completed;
                    reading = host.Track(Task.Run(() => ReadFrame(peer.GetStream(), ReadTimeoutMs)));
                }
                var response = JObject.Parse(Encoding.UTF8.GetString(final));
                Assert.AreEqual("success", response.Value<string>("status"));
                Assert.AreEqual(2, response.SelectToken("result.data.attemptedCount").Value<int>());
                Assert.AreEqual(0, response.SelectToken("result.data.notRunCount").Value<int>());
                var nextRead = host.Track(Task.Run(() => ReadFrame(peer.GetStream(), ReadTimeoutMs)));
                await NativeProgressProbe.AwaitOwned(nextRead);
                Assert.IsFalse(HasProgressPrefix(await nextRead), "No late progress may precede the next ordinary final response.");
                Assert.AreEqual(1, nextCalls);
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(1, secondCalls);
                var barrier = host.Track(
                    Task.Run(() =>
                    {
                        SendFrame(peer.GetStream(), Encoding.UTF8.GetBytes("ping"));
                        return Encoding.UTF8.GetString(ReadFrame(peer.GetStream(), ReadTimeoutMs));
                    })
                );
                await NativeProgressProbe.AwaitOwned(barrier);
                Assert.That(
                    await barrier,
                    Does.Contain("pong"),
                    "The shared writer remains healthy after final and no late optional frame occupies the barrier."
                );
            }
            finally
            {
                release.TrySetResult(new { done = true });
                peer?.Dispose();
                if (entered.Task.IsCompleted)
                    await NativeProgressProbe.AwaitOwned(settled.Task);
                await AwaitProgressQueueEmpty();
                if (reading != null)
                {
                    try
                    {
                        await NativeProgressProbe.AwaitOwned(reading);
                    }
                    catch (IOException) { }
                    catch (ObjectDisposedException) { }
                }
            }
        }

        internal static bool HasProgressPrefix(byte[] body) => body.Length >= 4 && body[0] == 'M' && body[1] == 'C' && body[2] == 'P' && body[3] == 'G';

        internal static void AssertProgressPayload(byte[] body, string id, int completed)
        {
            Assert.IsTrue(HasProgressPrefix(body), "Expected a genuine optional MCPG frame, not a terminal/error response.");
            Assert.LessOrEqual(body.Length - 4, 512);
            var update = JObject.Parse(Encoding.UTF8.GetString(body, 4, body.Length - 4));
            CollectionAssert.AreEquivalent(new[] { "type", "id", "completed", "total", "stage" }, update.Properties().Select(item => item.Name));
            Assert.AreEqual("command_progress", update.Value<string>("type"));
            Assert.AreEqual(id, update.Value<string>("id"));
            Assert.AreEqual(JTokenType.Integer, update["completed"].Type);
            Assert.AreEqual(JTokenType.Integer, update["total"].Type);
            Assert.AreEqual(completed, update.Value<int>("completed"));
            Assert.AreEqual(2, update.Value<int>("total"));
            Assert.AreEqual("batch_items", update.Value<string>("stage"));
        }

        internal static async Task AwaitProgressQueueEmpty()
        {
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            object gate = typeof(StdioBridgeHost).GetField("lockObj", flags).GetValue(null);
            var queue = (IDictionary)typeof(StdioBridgeHost).GetField("commandQueue", flags).GetValue(null);
            var watch = System.Diagnostics.Stopwatch.StartNew();
            while (true)
            {
                lock (gate)
                {
                    if (queue.Count == 0)
                        return;
                }
                Assert.Less(watch.ElapsedMilliseconds, 5000, "Release and join actual native work before restoring handlers/stopping the owned host.");
                await Task.Delay(10);
            }
        }

        [TestCase("repeat")]
        [TestCase("bad-id")]
        [TestCase("eof")]
        public async Task ProgressPrelude_RepeatedMalformedOrEofNeverDispatches(string fault)
        {
            int calls = 0;
            using var host = new TimingHostScope(_ =>
            {
                Interlocked.Increment(ref calls);
                return Task.FromResult<object>(new { done = true });
            });
            JObject ack = null;
            var connecting = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, new JArray("command_progress_v1"), out ack)));
            await NativeProgressProbe.AwaitOwned(connecting);
            using var peer = await connecting;
            CollectionAssert.Contains(ack["capabilities"].Values<string>().ToArray(), "command_progress_v1");
            string firstId = Guid.NewGuid().ToString("D");
            var closed = host.Track(
                Task.Run(() =>
                {
                    SendFrame(peer.GetStream(), ProgressPrelude(firstId));
                    if (fault == "repeat")
                        SendFrame(peer.GetStream(), ProgressPrelude(Guid.NewGuid().ToString("D")));
                    else if (fault == "bad-id")
                        SendFrame(peer.GetStream(), ProgressPrelude("not-a-command-uuid"));
                    else
                        peer.Client.Shutdown(SocketShutdown.Send);
                    return ReadProgressPeerClosed(peer);
                })
            );
            await NativeProgressProbe.AwaitOwned(closed);
            Assert.IsTrue(await closed, "Association refusal must close this peer, not dispatch an unknown command or return a result.");
            Assert.AreEqual(0, calls);
            Assert.AreEqual(0, StdioBridgeHost.QueuedCommandCount);
            var fresh = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, null, out _)));
            await NativeProgressProbe.AwaitOwned(fresh);
            using var replacement = await fresh;
            var response = host.Track(
                Task.Run(() =>
                {
                    SendFrame(
                        replacement.GetStream(),
                        Encoding.UTF8.GetBytes(new JObject { ["type"] = host.Name, ["params"] = new JObject() }.ToString(Formatting.None))
                    );
                    return ReadFrame(replacement.GetStream(), ReadTimeoutMs);
                })
            );
            await NativeProgressProbe.AwaitOwned(response);
            Assert.IsFalse(HasProgressPrefix(await response), "A fresh peer cannot inherit an orphaned association.");
            Assert.AreEqual("success", JObject.Parse(Encoding.UTF8.GetString(await response)).Value<string>("status"));
            Assert.AreEqual(1, calls);
            await AwaitProgressQueueEmpty();
        }

        [Test]
        public async Task ProgressWire_IdenticalNextBatchUsesFreshAssociation()
        {
            const string first = "__core06_fresh_first",
                second = "__core06_fresh_second";
            int firstCalls = 0,
                secondCalls = 0;
            TaskCompletionSource<object> release = null;
            TaskCompletionSource<bool> entered = null,
                settled = null;
            using var handlers = new ProgressHandlerScope();
            using var enabled = NativeProgressProbe.EnableTools("batch_execute");
            handlers.Set(
                first,
                new HandlerInfo(
                    first,
                    _ =>
                    {
                        firstCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            handlers.Set(
                second,
                new HandlerInfo(
                    second,
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
            using var host = new TimingHostScope(_ => Task.FromResult<object>(new { unused = true }));
            var batch = new JObject
            {
                ["type"] = "batch_execute",
                ["params"] = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = second }) },
            };
            NativeProgressProbe.RequireBatchCapacity((JObject)batch["params"]);
            string original = batch.ToString(Formatting.None);
            var connecting = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, new JArray("command_progress_v1"), out _)));
            await NativeProgressProbe.AwaitOwned(connecting);
            using var peer = await connecting;
            string previousId = null;
            try
            {
                for (int round = 0; round < 2; round++)
                {
                    release = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
                    entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    string id = Guid.NewGuid().ToString("D");
                    Assert.AreNotEqual(previousId, id);
                    var sending = host.Track(
                        Task.Run(() =>
                        {
                            SendFrame(peer.GetStream(), ProgressPrelude(id));
                            SendFrame(peer.GetStream(), Encoding.UTF8.GetBytes(original));
                            return true;
                        })
                    );
                    await NativeProgressProbe.AwaitOwned(sending);
                    await NativeProgressProbe.AwaitOwned(entered.Task);
                    var progress = host.Track(Task.Run(() => ReadFrame(peer.GetStream(), ReadTimeoutMs)));
                    await NativeProgressProbe.AwaitOwned(progress);
                    AssertProgressPayload(await progress, id, 1);
                    Assert.IsFalse(settled.Task.IsCompleted);
                    Assert.AreEqual(round + 1, firstCalls);
                    Assert.AreEqual(round + 1, secondCalls);
                    var reading = host.Track(Task.Run(() => ReadFrame(peer.GetStream(), ReadTimeoutMs)));
                    release.TrySetResult(new { done = true });
                    await NativeProgressProbe.AwaitOwned(settled.Task);
                    byte[] final = await ReadProgressFinal(host, peer, reading, id);
                    Assert.AreEqual(2, JObject.Parse(Encoding.UTF8.GetString(final)).SelectToken("result.data.attemptedCount").Value<int>());
                    await AwaitProgressQueueEmpty();
                    previousId = id;
                }
            }
            finally
            {
                release?.TrySetResult(new { done = true });
                if (entered?.Task.IsCompleted == true)
                    await NativeProgressProbe.AwaitOwned(settled.Task);
                peer.Dispose();
                await AwaitProgressQueueEmpty();
            }
        }

        internal static byte[] ProgressPrelude(string id) =>
            Encoding.UTF8.GetBytes(
                new JObject
                {
                    ["type"] = "command_progress_begin",
                    ["version"] = 1,
                    ["id"] = id,
                }.ToString(Formatting.None)
            );

        internal static bool ReadProgressPeerClosed(TcpClient peer)
        {
            try
            {
                return peer.GetStream().ReadByte() == -1;
            }
            catch (IOException error)
                when (error.InnerException is SocketException socket
                    && (
                        socket.SocketErrorCode == SocketError.ConnectionReset
                        || socket.SocketErrorCode == SocketError.ConnectionAborted
                        || socket.SocketErrorCode == SocketError.Shutdown
                    )
                )
            {
                return true;
            }
        }

        internal static async Task<byte[]> ReadProgressFinal(TimingHostScope host, TcpClient peer, Task<byte[]> reading, string id)
        {
            for (int remaining = 2; remaining >= 0; remaining--)
            {
                await NativeProgressProbe.AwaitOwned(reading);
                byte[] body = await reading;
                if (!HasProgressPrefix(body))
                    return body;
                Assert.Greater(remaining, 0, "Optional frames cannot replace the bounded final response.");
                AssertProgressPayload(body, id, 2);
                reading = host.Track(Task.Run(() => ReadFrame(peer.GetStream(), ReadTimeoutMs)));
            }
            throw new AssertionException("No terminal frame");
        }

        [TestCase("legacy")]
        [TestCase("2026-07-28")]
        [Explicit("Requires the existing guarded concurrent SDK process owner and fresh control-root binding.")]
        public async Task ProgressSdk_RealNotificationReleasesOwnedNativeSecondItem(string mode)
        {
            string root = Environment.GetEnvironmentVariable("UNITY_MCP_COMMAND_PROGRESS_SDK_ROOT");
            Assert.IsTrue(
                !string.IsNullOrEmpty(root) && Path.IsPathRooted(root) && Directory.Exists(root),
                "SDK owner prerequisite: root must bind a fresh owned control directory and launch the real adapter through existing native_python/cleanup_process."
            );
            string folder = Path.Combine(Path.GetFullPath(root), "stdio", mode);
            Assert.IsFalse(Directory.Exists(folder), "Never overwrite an earlier SDK attempt.");
            Directory.CreateDirectory(folder);
            const string first = "__core06_sdk_first",
                second = "__core06_sdk_second";
            int firstCalls = 0,
                secondCalls = 0;
            var release = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var handlers = new ProgressHandlerScope();
            using var enabled = NativeProgressProbe.EnableTools("batch_execute");
            handlers.Set(
                first,
                new HandlerInfo(
                    first,
                    _ =>
                    {
                        firstCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            handlers.Set(
                second,
                new HandlerInfo(
                    second,
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
            using var host = new TimingHostScope(_ => Task.FromResult<object>(new { unused = true }));
            NativeProgressProbe.RequireBatchCapacity(new JObject { ["commands"] = new JArray(new JObject(), new JObject()) });
            try
            {
                using (
                    var manifest = new StreamWriter(
                        new FileStream(Path.Combine(folder, "endpoint.json"), FileMode.CreateNew, FileAccess.Write),
                        new UTF8Encoding(false)
                    )
                )
                    manifest.Write(
                        new JObject
                        {
                            ["protocol"] = "core06-owned-stdio-probe-v1",
                            ["mode"] = mode,
                            ["port"] = host.Port,
                            ["token"] = host.Token,
                            ["first"] = first,
                            ["second"] = second,
                        }.ToString(Formatting.None)
                    );
                var observed = await AwaitSdkCheckpoint(Path.Combine(folder, "progress-observed.json"), 15000);
                Assert.AreEqual("core06-owned-stdio-probe-v1", observed.Value<string>("protocol"));
                Assert.AreEqual(mode, observed.Value<string>("mode"));
                Assert.AreEqual(1, observed.Value<int>("completed"));
                Assert.AreEqual(2, observed.Value<int>("total"));
                Assert.IsTrue(observed.Value<bool>("call_pending"));
                Assert.IsTrue(entered.Task.IsCompleted);
                Assert.IsFalse(settled.Task.IsCompleted, "The actual SDK callback must precede this native gate release.");
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(1, secondCalls);
                release.TrySetResult(new { done = true });
                await NativeProgressProbe.AwaitOwned(settled.Task);
                var final = await AwaitSdkCheckpoint(Path.Combine(folder, "final-result.json"), 5000);
                Assert.AreEqual(mode, final.Value<string>("mode"));
                Assert.AreEqual(2, final.Value<int>("attempted"));
                Assert.AreEqual(0, final.Value<int>("not_run"));
                Assert.IsFalse(final.Value<bool>("sdk_is_error"));
                Assert.IsTrue(final.Value<bool>("no_late_progress"));
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(1, secondCalls);
                await AwaitProgressQueueEmpty();
            }
            finally
            {
                release.TrySetResult(new { done = true });
                if (entered.Task.IsCompleted)
                    await NativeProgressProbe.AwaitOwned(settled.Task);
                await AwaitProgressQueueEmpty();
                // The external existing owner must always cleanup/join its adapter process.
            }
        }

        internal static async Task<JObject> AwaitSdkCheckpoint(string path, int milliseconds)
        {
            var elapsed = System.Diagnostics.Stopwatch.StartNew();
            while (true)
            {
                Assert.Less(elapsed.ElapsedMilliseconds, milliseconds, "The existing SDK process owner did not publish its real checkpoint.");
                if (File.Exists(path))
                {
                    try
                    {
                        // Acquire only after the no-clobber Python publisher closes its file.
                        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None);
                        Assert.Greater(input.Length, 0);
                        Assert.LessOrEqual(input.Length, 4096);
                        using var reader = new StreamReader(input, Encoding.UTF8);
                        return JObject.Parse(reader.ReadToEnd());
                    }
                    catch (IOException) { }
                }
                await Task.Delay(10);
            }
        }

        [Test]
        [Explicit("Requires a fresh owned Editor with UNITY_MCP_STDIO_COMMAND_TIMEOUT_MS in [500,2000].")]
        public async Task ProgressDeadline_LateFirstSettlementDoesNotRenewOriginalBudget()
        {
            int budget = (int)typeof(StdioBridgeHost).GetField("FrameIOTimeoutMs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            Assert.That(budget, Is.InRange(500, 2000), "A short original deadline must be fixed before this fresh Editor starts.");
            var clock = (System.Diagnostics.Stopwatch)typeof(StdioBridgeHost).GetField("_uptime", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            const string first = "__core06_deadline_first",
                second = "__core06_deadline_second";
            var releaseFirst = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var releaseSecond = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var enteredFirst = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var enteredSecond = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settledFirst = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settledSecond = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var handlers = new ProgressHandlerScope();
            using var enabled = NativeProgressProbe.EnableTools("batch_execute");
            handlers.Set(
                first,
                new HandlerInfo(
                    first,
                    null,
                    async _ =>
                    {
                        enteredFirst.TrySetResult(true);
                        try
                        {
                            return await releaseFirst.Task.ConfigureAwait(true);
                        }
                        finally
                        {
                            settledFirst.TrySetResult(true);
                        }
                    }
                )
            );
            handlers.Set(
                second,
                new HandlerInfo(
                    second,
                    null,
                    async _ =>
                    {
                        enteredSecond.TrySetResult(true);
                        try
                        {
                            return await releaseSecond.Task.ConfigureAwait(true);
                        }
                        finally
                        {
                            settledSecond.TrySetResult(true);
                        }
                    }
                )
            );
            using var host = new TimingHostScope(_ => Task.FromResult<object>(new { unused = true }));
            string business = new JObject
            {
                ["type"] = "batch_execute",
                ["params"] = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = second }) },
            }.ToString(Formatting.None);
            NativeProgressProbe.RequireBatchCapacity((JObject)JObject.Parse(business)["params"]);
            var connecting = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, new JArray("command_progress_v1"), out _)));
            await NativeProgressProbe.AwaitOwned(connecting);
            using var peer = await connecting;
            string id = Guid.NewGuid().ToString("D");
            try
            {
                var sending = host.Track(
                    Task.Run(() =>
                    {
                        SendFrame(peer.GetStream(), ProgressPrelude(id));
                        SendFrame(peer.GetStream(), Encoding.UTF8.GetBytes(business));
                        return true;
                    })
                );
                await NativeProgressProbe.AwaitOwned(sending);
                await NativeProgressProbe.AwaitOwned(enteredFirst.Task);
                var queued = StdioBrokerResendTests.FindProgressQueued(business);
                long admittedAt = queued.DeadlineAtMs - budget;
                Assert.Greater(queued.DeadlineAtMs, clock.ElapsedMilliseconds);
                long firstReleaseAt = admittedAt + budget * 7 / 10;
                Assert.Less(clock.ElapsedMilliseconds, firstReleaseAt, "The fixture must reach the first held item before its late-release checkpoint.");
                while (clock.ElapsedMilliseconds < firstReleaseAt)
                    await Task.Delay(5);
                releaseFirst.TrySetResult(new { done = true });
                await NativeProgressProbe.AwaitOwned(enteredSecond.Task);
                var update = host.Track(Task.Run(() => ReadFrame(peer.GetStream(), ReadTimeoutMs)));
                await NativeProgressProbe.AwaitOwned(update);
                AssertProgressPayload(await update, id, 1);
                Assert.IsFalse(settledSecond.Task.IsCompleted);
                var closed = host.Track(Task.Run(() => ReadProgressPeerClosed(peer)));
                await NativeProgressProbe.AwaitOwned(closed);
                Assert.IsTrue(await closed, "Original admitted expiry must close the captured socket rather than renew on the first settlement.");
                Assert.Less(
                    clock.ElapsedMilliseconds - admittedAt,
                    budget * 1.35,
                    "Renewing the full budget after the 70% settlement would exceed this bound."
                );
                Assert.IsFalse(settledSecond.Task.IsCompleted, "Delivery expiry must not masquerade as actual legacy handler settlement.");
            }
            finally
            {
                releaseFirst.TrySetResult(new { done = true });
                releaseSecond.TrySetResult(new { done = true });
                if (enteredFirst.Task.IsCompleted)
                    await NativeProgressProbe.AwaitOwned(settledFirst.Task);
                if (enteredSecond.Task.IsCompleted)
                    await NativeProgressProbe.AwaitOwned(settledSecond.Task);
                peer.Dispose();
                await AwaitProgressQueueEmpty();
            }
        }

        [Test]
        public async Task ProgressReconnect_OldSessionCannotWriteToReplacement()
        {
            const string first = "__core06_replace_first",
                second = "__core06_replace_second";
            int nextCalls = 0;
            var release = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var handlers = new ProgressHandlerScope();
            using var enabled = NativeProgressProbe.EnableTools("batch_execute");
            handlers.Set(first, new HandlerInfo(first, _ => new { done = true }, null));
            handlers.Set(
                second,
                new HandlerInfo(
                    second,
                    null,
                    async _ =>
                    {
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
            using var host = new TimingHostScope(_ =>
            {
                nextCalls++;
                return Task.FromResult<object>(new { next = true });
            });
            string business = new JObject
            {
                ["type"] = "batch_execute",
                ["params"] = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = second }) },
            }.ToString(Formatting.None);
            NativeProgressProbe.RequireBatchCapacity((JObject)JObject.Parse(business)["params"]);
            JObject oldAck = null,
                newAck = null;
            var connectOld = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, new JArray("command_progress_v1"), out oldAck)));
            await NativeProgressProbe.AwaitOwned(connectOld);
            using var oldPeer = await connectOld;
            TcpClient replacement = null;
            try
            {
                string oldId = Guid.NewGuid().ToString("D");
                var sendOld = host.Track(
                    Task.Run(() =>
                    {
                        SendFrame(oldPeer.GetStream(), ProgressPrelude(oldId));
                        SendFrame(oldPeer.GetStream(), Encoding.UTF8.GetBytes(business));
                        return true;
                    })
                );
                await NativeProgressProbe.AwaitOwned(sendOld);
                await NativeProgressProbe.AwaitOwned(entered.Task);
                var oldUpdate = host.Track(Task.Run(() => ReadFrame(oldPeer.GetStream(), ReadTimeoutMs)));
                await NativeProgressProbe.AwaitOwned(oldUpdate);
                AssertProgressPayload(await oldUpdate, oldId, 1);
                var original = StdioBrokerResendTests.FindProgressQueued(business);
                Assert.AreEqual(oldAck.Value<string>("session_id"), original.Session.SessionId);
                oldPeer.Dispose(); // Reconnect follows an actual disconnect; a new healthy peer no longer evicts it.
                var connectNew = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, new JArray("command_progress_v1"), out newAck)));
                await NativeProgressProbe.AwaitOwned(connectNew);
                replacement = await connectNew;
                Assert.AreNotEqual(oldAck.Value<string>("session_id"), newAck.Value<string>("session_id"));
                var closing = System.Diagnostics.Stopwatch.StartNew();
                while ((!original.OwnerCancellation.IsCancellationRequested || !original.Progress.IsClosed) && closing.ElapsedMilliseconds < 3000)
                    await Task.Delay(10);
                Assert.IsTrue(original.OwnerCancellation.IsCancellationRequested);
                Assert.IsTrue(original.Progress.IsClosed);
                original.Progress.Report(2, 2, CommandProgressStage.BatchItems);
                Assert.IsFalse(original.Progress.TryTake(out _), "A closed old association cannot retain a late callback.");
                string next = new JObject { ["type"] = host.Name, ["params"] = new JObject() }.ToString(Formatting.None);
                var sendNew = host.Track(
                    Task.Run(() =>
                    {
                        SendFrame(replacement.GetStream(), Encoding.UTF8.GetBytes(next));
                        return true;
                    })
                );
                await NativeProgressProbe.AwaitOwned(sendNew);
                var waiting = host.Track(Task.Run(() => ReadFrame(replacement.GetStream(), ReadTimeoutMs)));
                // A real queue admission, not an elapsed delay, proves the replacement request is waiting behind actual old work.
                var limit = System.Diagnostics.Stopwatch.StartNew();
                while (StdioBridgeHost.QueuedCommandCount < 2 && limit.ElapsedMilliseconds < 3000)
                    await Task.Delay(10);
                Assert.AreEqual(2, StdioBridgeHost.QueuedCommandCount);
                var replacementCommand = StdioBrokerResendTests.FindProgressQueued(next);
                Assert.AreEqual(newAck.Value<string>("session_id"), replacementCommand.Session.SessionId);
                Assert.IsNull(replacementCommand.Progress, "An ordinary new request cannot inherit the old optional association.");
                Assert.AreEqual(0, nextCalls);
                Assert.IsFalse(waiting.IsCompleted);
                Assert.IsFalse(settled.Task.IsCompleted);
                release.TrySetResult(new { done = true });
                await NativeProgressProbe.AwaitOwned(settled.Task);
                await NativeProgressProbe.AwaitOwned(waiting);
                Assert.IsFalse(HasProgressPrefix(await waiting));
                Assert.AreEqual("success", JObject.Parse(Encoding.UTF8.GetString(await waiting)).Value<string>("status"));
                Assert.AreEqual(1, nextCalls);
                await AwaitProgressQueueEmpty();
            }
            finally
            {
                release.TrySetResult(new { done = true });
                if (entered.Task.IsCompleted)
                    await NativeProgressProbe.AwaitOwned(settled.Task);
                replacement?.Dispose();
                oldPeer.Dispose();
                await AwaitProgressQueueEmpty();
            }
        }

        [Test]
        [Explicit("Requires a fresh owned Editor with UNITY_MCP_STDIO_COMMAND_TIMEOUT_MS in [500,2000].")]
        public async Task ProgressDeadline_LatePreludeCannotRenewBusinessFrameReadBudget()
        {
            int budget = (int)typeof(StdioBridgeHost).GetField("FrameIOTimeoutMs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            Assert.That(budget, Is.InRange(500, 2000));
            int calls = 0;
            using var host = new TimingHostScope(_ =>
            {
                Interlocked.Increment(ref calls);
                return Task.FromResult<object>(new { done = true });
            });
            var connecting = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, new JArray("command_progress_v1"), out _)));
            await NativeProgressProbe.AwaitOwned(connecting);
            using var peer = await connecting;
            // The authenticated ACK brackets this connection's first read. No
            // test-only timer field or runtime preference mutation is needed.
            var elapsed = System.Diagnostics.Stopwatch.StartNew();
            while (elapsed.ElapsedMilliseconds < budget * 7 / 10)
                await Task.Delay(5);
            var sendingPrelude = host.Track(
                Task.Run(() =>
                {
                    SendFrame(peer.GetStream(), ProgressPrelude(Guid.NewGuid().ToString("D")));
                    return true;
                })
            );
            await NativeProgressProbe.AwaitOwned(sendingPrelude);
            var closed = host.Track(Task.Run(() => ReadProgressPeerClosed(peer)));
            while (elapsed.ElapsedMilliseconds < budget * 12 / 10)
                await Task.Delay(5);
            // A renewed post-prelude budget would still admit this real business
            // frame. A correctly expired captured peer has already reached EOF.
            var lateBusiness = host.Track(
                Task.Run(() =>
                {
                    try
                    {
                        SendFrame(
                            peer.GetStream(),
                            Encoding.UTF8.GetBytes(new JObject { ["type"] = host.Name, ["params"] = new JObject() }.ToString(Formatting.None))
                        );
                    }
                    catch (IOException error)
                        when (error.InnerException is SocketException socket
                            && (
                                socket.SocketErrorCode == SocketError.ConnectionReset
                                || socket.SocketErrorCode == SocketError.ConnectionAborted
                                || socket.SocketErrorCode == SocketError.Shutdown
                            )
                        ) { }
                    return true;
                })
            );
            await NativeProgressProbe.AwaitOwned(lateBusiness);
            await NativeProgressProbe.AwaitOwned(closed);
            Assert.IsTrue(await closed, "No result may be dispatched under a renewed second-frame budget.");
            Assert.Less(elapsed.ElapsedMilliseconds, budget * 1.35);
            Assert.AreEqual(0, calls);
            Assert.AreEqual(0, StdioBridgeHost.QueuedCommandCount);
            var fresh = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, null, out _)));
            await NativeProgressProbe.AwaitOwned(fresh);
            using var replacement = await fresh;
            var result = host.Track(
                Task.Run(() =>
                {
                    SendFrame(
                        replacement.GetStream(),
                        Encoding.UTF8.GetBytes(new JObject { ["type"] = host.Name, ["params"] = new JObject() }.ToString(Formatting.None))
                    );
                    return ReadFrame(replacement.GetStream(), ReadTimeoutMs);
                })
            );
            await NativeProgressProbe.AwaitOwned(result);
            Assert.IsFalse(HasProgressPrefix(await result));
            Assert.AreEqual("success", JObject.Parse(Encoding.UTF8.GetString(await result)).Value<string>("status"));
            Assert.AreEqual(1, calls);
            await AwaitProgressQueueEmpty();
        }

        private static IEnumerator WaitTimingTask(Task task, bool allowFault = false)
        {
            var watch = System.Diagnostics.Stopwatch.StartNew();
            while (!task.IsCompleted && watch.ElapsedMilliseconds < 20000)
                yield return null;
            Assert.IsTrue(task.IsCompleted, "Owned timing task exceeded its finite watchdog.");
            if (task.IsFaulted)
            {
                var observed = task.Exception;
                if (!allowFault)
                    throw observed.GetBaseException();
            }
        }

        private static IEnumerator WaitTimingCondition(Func<bool> predicate)
        {
            var watch = System.Diagnostics.Stopwatch.StartNew();
            while (!predicate() && watch.ElapsedMilliseconds < 20000)
                yield return null;
            Assert.IsTrue(predicate(), "Owned timing condition exceeded its finite watchdog.");
        }

        private static byte[] ExpectedTimingFrame(byte[] payload)
        {
            var frame = new byte[8 + payload.Length];
            ulong length = (ulong)payload.Length;
            for (int i = 7; i >= 0; i--)
            {
                frame[i] = (byte)length;
                length >>= 8;
            }
            Buffer.BlockCopy(payload, 0, frame, 8, payload.Length);
            return frame;
        }

        private static byte[] ReadTimingFrame(NetworkStream stream)
        {
            stream.ReadTimeout = ReadTimeoutMs;
            byte[] header = ReadExact(stream, 8, ReadTimeoutMs);
            ulong length = 0;
            foreach (byte part in header)
                length = (length << 8) | part;
            if (length == 0 || length > 16 * 1024 * 1024)
                throw new IOException("Invalid owned timing frame length");
            byte[] payload = ReadExact(stream, (int)length, ReadTimeoutMs);
            var frame = new byte[8 + payload.Length];
            Buffer.BlockCopy(header, 0, frame, 0, 8);
            Buffer.BlockCopy(payload, 0, frame, 8, payload.Length);
            return frame;
        }

        [UnityTest]
        public IEnumerator TimingOwnedHostNormalAndLargeUnicodeFramesPreserveExactBytes()
        {
            TimingValue current = null;
            int calls = 0;
            using var scope = new TimingHostScope(_ =>
            {
                Interlocked.Increment(ref calls);
                return Task.FromResult<object>(current);
            });
            var connected = scope.Track(Task.Run(() => ConnectTimingPeer(scope.Port, scope.Token)));
            yield return WaitTimingTask(connected);
            using var peer = connected.Result;
            foreach (int characters in new[] { 32, 300000 })
            {
                string text = new string('\u6f22', characters) + "\ud83d\ude80";
                current = new TimingValue { Text = text };
                byte[] expected = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(new { status = "success", result = new { Payload = text } }));
                string command = JsonConvert.SerializeObject(new { type = scope.Name });
                SendFrame(peer.GetStream(), Encoding.UTF8.GetBytes(command));
                var received = scope.Track(Task.Run(() => ReadTimingFrame(peer.GetStream())));
                yield return WaitTimingTask(received);
                CollectionAssert.AreEqual(ExpectedTimingFrame(expected), received.Result);
                Assert.AreEqual(1, current.Reads);
                yield return WaitTimingCondition(() => StdioBridgeHost.QueuedCommandCount == 0);
            }
            Assert.AreEqual(2, calls);
            yield return WaitTimingCondition(() => scope.TimingRows().Length == 1);
            var row = scope.TimingRows().Single();
            Assert.AreEqual("sent", row.Value<string>("delivery"));
            Assert.IsNotNull(row["serialization_ms"]);
            Assert.IsNotNull(row["send_ms"]);
            StringAssert.DoesNotContain("Payload", row.ToString());
        }

        [UnityTest]
        public IEnumerator TimingOwnedResendPreservesCanceledEnvelopeAndOneExecutionUntilSettlement()
        {
            var deferred = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            int calls = 0;
            using var scope = new TimingHostScope(_ =>
                Interlocked.Increment(ref calls) == 1 ? deferred.Task : Task.FromResult<object>(new { Payload = "after-settlement" })
            );
            TcpClient first = null;
            TcpClient second = null;
            Task<byte[]> firstRead = null;
            try
            {
                var firstConnected = scope.Track(Task.Run(() => ConnectTimingPeer(scope.Port, scope.Token)));
                yield return WaitTimingTask(firstConnected);
                first = firstConnected.Result;
                string command = JsonConvert.SerializeObject(new { type = scope.Name });
                SendFrame(first.GetStream(), Encoding.UTF8.GetBytes(command));
                firstRead = scope.Track(Task.Run(() => ReadTimingFrame(first.GetStream())));
                yield return WaitTimingCondition(() => Volatile.Read(ref calls) == 1);
                var secondConnected = scope.Track(Task.Run(() => ConnectTimingPeer(scope.Port, scope.Token)));
                yield return WaitTimingTask(secondConnected);
                second = secondConnected.Result;
                SendFrame(second.GetStream(), Encoding.UTF8.GetBytes(command));
                var secondRead = scope.Track(Task.Run(() => ReadTimingFrame(second.GetStream())));
                yield return WaitTimingTask(secondRead);
                int fallback = (int)typeof(StdioBridgeHost).GetField("FrameIOTimeoutMs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                int timeout = StdioBridgeHost.ResolveCommandTimeoutMs(command, fallback);
                byte[] expected = Encoding.UTF8.GetBytes(
                    JsonConvert.SerializeObject(new { status = "error", error = $"Command processing timed out after {timeout} ms" })
                );
                CollectionAssert.AreEqual(ExpectedTimingFrame(expected), secondRead.Result);
                Assert.AreEqual(1, Volatile.Read(ref calls));
                Assert.IsFalse(deferred.Task.IsCompleted);
                Assert.AreEqual(0, scope.TimingRows().Length, "Do not finalize execution timing merely because delivery was canceled.");
                deferred.SetResult(new { Payload = "late-original" });
                yield return WaitTimingCondition(() => StdioBridgeHost.QueuedCommandCount == 0 && scope.TimingRows().Length == 1);
                var row = scope.TimingRows().Single();
                Assert.AreEqual("canceled", row.Value<string>("delivery"));
                Assert.IsTrue(row.Value<bool>("execution_settled_after_delivery"));
                Assert.IsNotNull(row["handler_ms"]);
                Assert.IsNull(row["send_ms"]);
                SendFrame(second.GetStream(), Encoding.UTF8.GetBytes(command));
                var followup = scope.Track(Task.Run(() => ReadTimingFrame(second.GetStream())));
                yield return WaitTimingTask(followup);
                CollectionAssert.AreEqual(
                    ExpectedTimingFrame(Encoding.UTF8.GetBytes("{\"status\":\"success\",\"result\":{\"Payload\":\"after-settlement\"}}")),
                    followup.Result
                );
                Assert.AreEqual(2, Volatile.Read(ref calls), "Only the post-settlement follow-up may start another execution.");
                yield return WaitTimingCondition(() => StdioBridgeHost.QueuedCommandCount == 0);
            }
            finally
            {
                deferred.TrySetCanceled();
                first?.Dispose();
                second?.Dispose();
                if (firstRead?.IsFaulted == true)
                    _ = firstRead.Exception;
            }
        }

        [UnityTest]
        public IEnumerator NewClient_AfterAbruptDisconnect_CanSendAndReceiveCommands()
        {
            if (!StdioBridgeHost.IsRunning)
            {
                Assert.Ignore("StdioBridgeHost is not running; skipping reconnect test.");
                yield break;
            }

            int port = StdioBridgeHost.GetCurrentPort();

            // --- First client: connect, verify ping/pong, then abruptly close ---
            using (var client1 = new TcpClient())
            {
                Assert.IsTrue(client1.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs), "First client connect timed out");
                client1.ReceiveTimeout = ReadTimeoutMs;
                var stream1 = client1.GetStream();

                string handshake1 = ReadLine(stream1, ReadTimeoutMs);
                Assert.That(handshake1, Does.Contain("FRAMING=1"), "First client should receive handshake");

                // Send a framed ping
                SendFrame(stream1, Encoding.UTF8.GetBytes("ping"));
                byte[] pongBytes = ReadFrame(stream1, ReadTimeoutMs);
                string pong1 = Encoding.UTF8.GetString(pongBytes);
                Assert.That(pong1, Does.Contain("pong"), "First client should get pong response");

                // Abrupt close — simulates server crash / domain reload disconnect
                client1.Client.LingerState = new LingerOption(true, 0);
                client1.Close();
            }

            // Wait a few frames for cleanup
            for (int i = 0; i < 10; i++)
                yield return null;

            // --- Second client: connect and verify commands still work ---
            using (var client2 = new TcpClient())
            {
                Assert.IsTrue(client2.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs), "Second client connect timed out");
                client2.ReceiveTimeout = ReadTimeoutMs;
                var stream2 = client2.GetStream();

                string handshake2 = ReadLine(stream2, ReadTimeoutMs);
                Assert.That(handshake2, Does.Contain("FRAMING=1"), "Second client should receive handshake");

                // Send a framed ping — this is the critical check that would fail
                // if the bridge is in zombie state.
                SendFrame(stream2, Encoding.UTF8.GetBytes("ping"));
                byte[] pongBytes2 = ReadFrame(stream2, ReadTimeoutMs);
                string pong2 = Encoding.UTF8.GetString(pongBytes2);
                Assert.That(pong2, Does.Contain("pong"), "Second client should get pong response after reconnect");

                client2.Close();
            }
        }

        [UnityTest]
        public IEnumerator NewClient_WhileOldClientStillConnected_ClosesStaleClient()
        {
            if (!StdioBridgeHost.IsRunning)
            {
                Assert.Ignore("StdioBridgeHost is not running; skipping reconnect test.");
                yield break;
            }

            int port = StdioBridgeHost.GetCurrentPort();

            // --- First client: connect and verify handshake (but don't close) ---
            var client1 = new TcpClient();
            try
            {
                Assert.IsTrue(client1.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs), "First client connect timed out");
                client1.ReceiveTimeout = ReadTimeoutMs;
                var stream1 = client1.GetStream();

                string handshake1 = ReadLine(stream1, ReadTimeoutMs);
                Assert.That(handshake1, Does.Contain("FRAMING=1"), "First client should receive handshake");

                // Verify ping works on first client
                SendFrame(stream1, Encoding.UTF8.GetBytes("ping"));
                byte[] pong1Bytes = ReadFrame(stream1, ReadTimeoutMs);
                Assert.That(Encoding.UTF8.GetString(pong1Bytes), Does.Contain("pong"));

                // --- Second client: connect while first is still open ---
                using (var client2 = new TcpClient())
                {
                    Assert.IsTrue(client2.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs), "Second client connect timed out");
                    client2.ReceiveTimeout = ReadTimeoutMs;
                    var stream2 = client2.GetStream();

                    string handshake2 = ReadLine(stream2, ReadTimeoutMs);
                    Assert.That(handshake2, Does.Contain("FRAMING=1"), "Second client should receive handshake");

                    // Stale-client cleanup runs synchronously in HandleClientAsync before
                    // the read loop, so by the time we read the handshake it's already done.
                    // No yield needed — yielding here creates a window for the MCP Python
                    // server to reconnect and close our test client as stale.
                    SendFrame(stream2, Encoding.UTF8.GetBytes("ping"));
                    byte[] pong2Bytes = ReadFrame(stream2, ReadTimeoutMs);
                    Assert.That(Encoding.UTF8.GetString(pong2Bytes), Does.Contain("pong"), "Second client should get pong after stale client cleanup");

                    client2.Close();
                }

                // First client should now be disconnected by the bridge.
                // A read attempt should throw or return 0 bytes.
                yield return null;
                bool firstClientDisconnected = false;
                try
                {
                    SendFrame(stream1, Encoding.UTF8.GetBytes("ping"));
                    ReadFrame(stream1, 2000);
                }
                catch
                {
                    firstClientDisconnected = true;
                }

                Assert.IsTrue(firstClientDisconnected, "First client should be disconnected after second client connects");
            }
            finally
            {
                try
                {
                    client1.Close();
                }
                catch { }
            }
        }

        [Test, Explicit("Requires a fresh admitted CORE12 disposable project and the existing owned loopback lane.")]
        public async Task NativeConsent_AuthenticatedStdioUsesLiveGrantForEveryCommand()
        {
            using var consent = new NativeConsentProjectScope(includeResources: true);
            using var handlers = new ProgressHandlerScope();
            int calls = 0;
            handlers.Set(
                "execute_code",
                new HandlerInfo(
                    "execute_code",
                    _ =>
                    {
                        Interlocked.Increment(ref calls);
                        return new { status = "success", ownedConsentProbe = true };
                    },
                    null
                )
            );
            Assert.IsTrue(consent.Grant("execute_code", ToolConsentScope.EditorSession).Succeeded);
            Assert.IsTrue(consent.Discovery.IsToolEnabled("execute_code"));
            using var host = new TimingHostScope(_ =>
                throw new AssertionException("The unregistered timing helper must never substitute for the canonical consent tool.")
            );
            var connecting = host.Track(Task.Run(() => ConnectProgressPeer(host.Port, host.Token, null, out _)));
            await NativeProgressProbe.AwaitOwned(connecting);
            using var peer = await connecting;
            for (int phase = 0; phase < 3; phase++)
            {
                if (phase == 1)
                    Assert.IsTrue(consent.Grant("execute_code", ToolConsentScope.EditorSession).Succeeded);
                else
                    Assert.IsTrue(consent.Revoke("execute_code").Succeeded);
                var command = new JObject
                {
                    ["type"] = "execute_code",
                    ["params"] = new JObject { ["ownedConsentProbe"] = Guid.NewGuid().ToString("N") },
                };
                var exchange = host.Track(
                    Task.Run(() =>
                    {
                        // Ordinary business frame: no prelude, resend or capability.
                        SendFrame(peer.GetStream(), Encoding.UTF8.GetBytes(command.ToString(Formatting.None)));
                        return JObject.Parse(Encoding.UTF8.GetString(ReadFrame(peer.GetStream(), ReadTimeoutMs)));
                    })
                );
                await NativeProgressProbe.AwaitOwned(exchange);
                var result = await exchange;
                Assert.AreEqual(phase == 1 ? "success" : "error", result.Value<string>("status"));
                if (phase != 1)
                    StringAssert.Contains("disabled", result.Value<string>("error"));
                Assert.AreEqual(phase == 0 ? 0 : 1, Volatile.Read(ref calls));
            }
            Assert.Greater(consent.Store.Reads, 0);
            CollectionAssert.AreEquivalent(new[] { consent.MainThread }, consent.Store.AccessThreads);
        }

        #region Frame protocol helpers

        private static string ReadLine(NetworkStream stream, int timeoutMs)
        {
            var sb = new StringBuilder();
            var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            stream.ReadTimeout = timeoutMs;

            while (DateTime.UtcNow < deadline)
            {
                int b = stream.ReadByte();
                if (b < 0)
                    throw new IOException("Connection closed while reading line");
                if (b == '\n')
                    return sb.ToString();
                sb.Append((char)b);
            }
            throw new TimeoutException("Timed out reading line from stream");
        }

        internal static void SendFrame(NetworkStream stream, byte[] payload)
        {
            byte[] header = new byte[8];
            ulong len = (ulong)payload.LongLength;
            header[0] = (byte)(len >> 56);
            header[1] = (byte)(len >> 48);
            header[2] = (byte)(len >> 40);
            header[3] = (byte)(len >> 32);
            header[4] = (byte)(len >> 24);
            header[5] = (byte)(len >> 16);
            header[6] = (byte)(len >> 8);
            header[7] = (byte)(len);
            stream.Write(header, 0, 8);
            stream.Write(payload, 0, payload.Length);
            stream.Flush();
        }

        internal static byte[] ReadFrame(NetworkStream stream, int timeoutMs)
        {
            stream.ReadTimeout = timeoutMs;

            byte[] header = ReadExact(stream, 8, timeoutMs);
            ulong payloadLen =
                ((ulong)header[0] << 56)
                | ((ulong)header[1] << 48)
                | ((ulong)header[2] << 40)
                | ((ulong)header[3] << 32)
                | ((ulong)header[4] << 24)
                | ((ulong)header[5] << 16)
                | ((ulong)header[6] << 8)
                | header[7];

            if (payloadLen == 0 || payloadLen > 16 * 1024 * 1024)
                throw new IOException($"Invalid frame length: {payloadLen}");

            return ReadExact(stream, (int)payloadLen, timeoutMs);
        }

        private static byte[] ReadExact(NetworkStream stream, int count, int timeoutMs)
        {
            byte[] buffer = new byte[count];
            int offset = 0;
            var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);

            while (offset < count)
            {
                if (DateTime.UtcNow > deadline)
                    throw new TimeoutException($"Timed out reading {count} bytes (got {offset})");

                int remaining = count - offset;
                int read = stream.Read(buffer, offset, remaining);
                if (read == 0)
                    throw new IOException("Connection closed before reading expected bytes");
                offset += read;
            }

            return buffer;
        }

        #endregion
    }
}
