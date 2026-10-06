using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.Concurrent;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Threading;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>Actual Unity Editor + owned loopback sockets. Never starts the global bridge.</summary>
    public class TransportEditorIntegrationTests
    {
        private const string Token = "owned-editor-qa-synthetic-token-only";
        private const string EchoCommand = "__editor_qa_echo";
        private const string LargeCommand = "__editor_qa_large";
        private const string ReloadKey = "MCPForUnityTests.OwnedTransportReload";
        private static readonly string LargeValue = new string('x', 1024 * 1024) + "한글😀";
        private static int _domainSentinel;
        private readonly Dictionary<string, object> _previousHandlers = new Dictionary<string, object>();
        private IDictionary _handlers;
        private FieldInfo _loggingField;
        private object _previousLogging;
        private bool _ownsFixture;

        [SetUp]
        public void SetUp()
        {
            _ownsFixture = false;
            if (Environment.GetEnvironmentVariable("UNITY_MCP_OWNED_TRANSPORT_TESTS") != "1")
                Assert.Ignore("Owned transport integration requires an isolated project. Run: python tools/unity_editor_transport_qa.py launch --output <prepared-owned-report-directory> --isolation-verified --stdio-command-timeout-ms 1000");
            Assert.IsTrue(UnityEngine.Application.isBatchMode, "Integration fixture requires its disposable batch Editor.");
            Assert.IsTrue(string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("UNITY_MCP_ALLOW_BATCH")),
                "Normal autostart must remain suppressed.");
            Assert.IsFalse(StdioBridgeHost.IsRunning, "Owned tests cannot share an existing bridge.");
            _loggingField = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Helpers.McpLogRecord", true)
                .GetField("_isEnabledCached", BindingFlags.Static | BindingFlags.NonPublic);
            _previousLogging = _loggingField.GetValue(null);
            _loggingField.SetValue(null, false);
            RegisterHandlers();
        }

        private void RegisterHandlers()
        {
            _ownsFixture = true;
            CommandRegistry.Initialize();
            _handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            Register(EchoCommand, new HandlerInfo(EchoCommand, p => new { text = p.Value<string>("text"), thread = System.Threading.Thread.CurrentThread.ManagedThreadId }, null));
            Register(LargeCommand, new HandlerInfo(LargeCommand, _ => new { payload = LargeValue, characters = LargeValue.Length }, null));
        }

        private void Register(string name, object handler)
        {
            if (!_previousHandlers.ContainsKey(name)) _previousHandlers[name] = _handlers[name];
            _handlers[name] = handler;
        }

        [TearDown]
        public void TearDown()
        {
            if (!_ownsFixture) return;
            StdioBridgeHost.Stop();
            if (_handlers != null)
            {
                foreach (var previous in _previousHandlers)
                    if (previous.Value == null) _handlers.Remove(previous.Key);
                    else _handlers[previous.Key] = previous.Value;
            }
            _previousHandlers.Clear();
            _loggingField?.SetValue(null, _previousLogging);
            SessionState.EraseBool(ReloadKey);
            SessionState.EraseString(ReloadKey + ".server");
        }

        private static int StartOwned()
        {
            StdioBridgeHost.StartOwned(0, Token);
            Assert.IsTrue(StdioBridgeHost.IsRunning);
            int port = StdioBridgeHost.GetCurrentPort();
            Assert.That(port, Is.GreaterThan(0).And.Not.EqualTo(6400).And.Not.EqualTo(6401));
            TestContext.WriteLine("OWNED_STDIO_ENDPOINT=127.0.0.1:" + port);
            return port;
        }

        [Test]
        public async Task Stdio_UnauthenticatedReplacement_PreservesEstablishedPeer()
        {
            int port = StartOwned();
            using var established = await StdioPeer.Connect(port, Token);
            Assert.That(await established.Ping(), Does.Contain("pong"));
            using var unauthorized = new TcpClient();
            await unauthorized.ConnectAsync(IPAddress.Loopback, port);
            string banner = await StdioPeer.ReadLine(unauthorized.GetStream());
            Assert.That(banner, Does.Contain("AUTH=HMAC-SHA256"));
            await StdioPeer.WriteFrame(unauthorized.GetStream(), Encoding.UTF8.GetBytes("ping"));
            await Task.Delay(100);
            Assert.That(await established.Ping(), Does.Contain("pong"),
                "A peer without a valid proof must never replace the established peer.");
        }

        [Test]
        public async Task Stdio_AuthenticatedReconnect_GetsFreshSessionAndExactUnityRpc()
        {
            int port = StartOwned();
            string firstSession;
            string server;
            using (var first = await StdioPeer.Connect(port, Token))
            {
                firstSession = first.Session;
                server = first.Server;
                Assert.That(await first.Ping(), Does.Contain("pong"));
            }
            using var second = await StdioPeer.Connect(port, Token);
            Assert.AreEqual(server, second.Server);
            Assert.AreNotEqual(firstSession, second.Session);
            string text = "Owned RPC\n한글😀";
            int mainThread = System.Threading.Thread.CurrentThread.ManagedThreadId;
            JObject response = await second.Command(EchoCommand, new JObject { ["text"] = text });
            Assert.AreEqual("success", response.Value<string>("status"));
            Assert.AreEqual(text, response["result"].Value<string>("text"));
            Assert.AreEqual(mainThread, response["result"].Value<int>("thread"), "Unity-facing RPC must execute on the Editor thread.");
        }

        [Test]
        public async Task Stdio_LargeUnicodeResult_PreservesExactPayload()
        {
            int port = StartOwned();
            using var peer = await StdioPeer.Connect(port, Token);
            JObject response = await peer.Command(LargeCommand, new JObject());
            Assert.AreEqual("success", response.Value<string>("status"));
            Assert.AreEqual(LargeValue, response["result"].Value<string>("payload"));
            Assert.AreEqual(LargeValue.Length, response["result"].Value<int>("characters"));
            Assert.That(await peer.Ping(), Does.Contain("pong"), "Large RPC must leave the session usable.");
            TestContext.WriteLine("LARGE_RESULT_UTF8_BYTES=" + Encoding.UTF8.GetByteCount(LargeValue));
        }

        [Test]
        public async Task Stdio_DisconnectedPeer_CancelsAndSettlesOwnedCooperativeRpc()
        {
            const string name = "__editor_qa_cancel";
            var started = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Register(name, HandlerInfo.Cooperative(name, async (_, cancellationToken) =>
            {
                started.TrySetResult(true);
                try { await Task.Delay(System.Threading.Timeout.Infinite, cancellationToken); return new { unexpected = true }; }
                finally { settled.TrySetResult(cancellationToken.IsCancellationRequested); }
            }));
            int port = StartOwned();
            var peer = await StdioPeer.Connect(port, Token);
            var command = peer.Command(name, new JObject());
            Assert.AreSame(started.Task, await Task.WhenAny(started.Task, Task.Delay(10000)), "Cooperative handler must start on the actual Editor dispatcher.");
            peer.Dispose();
            Assert.AreSame(settled.Task, await Task.WhenAny(settled.Task, Task.Delay(10000)), "Disconnect must settle the handler rather than just hide its response.");
            Assert.IsTrue(await settled.Task, "Handler must observe its owned connection cancellation token.");
            try { await command; Assert.Fail("A disposed peer must not receive a successful RPC."); }
            catch (IOException) { }
            catch (ObjectDisposedException) { }
            using var reconnect = await StdioPeer.Connect(port, Token);
            Assert.That(await reconnect.Ping(), Does.Contain("pong"));
            TestContext.WriteLine("DISCONNECT_CANCELLATION_OBSERVED=true; HANDLER_SETTLED=true; RECONNECT_PONG=true");
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task Stdio_LegacyHandler_TimeoutOrAuthenticatedReplacementRetainsMutationOrder(bool replacement)
        {
            Assert.AreEqual("1000", Environment.GetEnvironmentVariable("UNITY_MCP_STDIO_COMMAND_TIMEOUT_MS"),
                "This bounded deadline regression requires the owned launch's explicit1000ms timeout.");
            const string legacyName = "__editor_qa_legacy_blocked";
            const string nextName = "__editor_qa_legacy_next";
            var started = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var release = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var nextStarted = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            bool legacySettled = false;
            Register(legacyName, new HandlerInfo(legacyName, null, async _ =>
            {
                started.TrySetResult(true);
                try { return await release.Task; }
                finally { legacySettled = true; settled.TrySetResult(true); }
            }));
            Register(nextName, new HandlerInfo(nextName, _ =>
            {
                nextStarted.TrySetResult(true);
                return new { oldHandlerSettled = legacySettled };
            }, null));
            int port = StartOwned();
            using var first = await StdioPeer.Connect(port, Token);
            StdioPeer second = null;
            var original = first.Command(legacyName, new JObject());
            try
            {
                Assert.AreSame(started.Task, await Task.WhenAny(started.Task, Task.Delay(10000)));
                if (replacement)
                    second = await StdioPeer.Connect(port, Token);
                else
                {
                    var timeout = await original;
                    Assert.AreEqual("error", timeout.Value<string>("status"));
                    Assert.That(timeout.Value<string>("error"), Does.Contain("timed out"));
                    Assert.IsFalse(legacySettled, "The legacy handler remains active after its response deadline.");
                }
                var following = (second ?? first).Command(nextName, new JObject());
                var queuedDeadline = DateTime.UtcNow.AddSeconds(5);
                while (StdioBridgeHost.QueuedCommandCount < 2 && !nextStarted.Task.IsCompleted)
                {
                    if (DateTime.UtcNow >= queuedDeadline) throw new TimeoutException("Owned following mutation arrival");
                    await Task.Delay(10);
                }
                Assert.IsFalse(nextStarted.Task.IsCompleted, "Response cancellation must not let a following mutation overtake an active legacy handler.");
                release.TrySetResult(new { settled = true });
                var response = await following;
                Assert.AreEqual("success", response.Value<string>("status"), response.ToString());
                Assert.IsTrue(response["result"].Value<bool>("oldHandlerSettled"));
                Assert.That(await (second ?? first).Ping(), Does.Contain("pong"));
                if (replacement)
                {
                    try { await original; Assert.Fail("The superseded peer cannot receive a successful response."); }
                    catch (IOException) { }
                    catch (ObjectDisposedException) { }
                }
                TestContext.WriteLine("LEGACY_TRIGGER=" + (replacement ? "authenticated-replacement" : "response-timeout") +
                    "; FOLLOWING_QUEUED_BEFORE_RELEASE=true; OVERTAKE=false; OLD_SETTLED_BEFORE_NEXT=true; FOLLOWUP_PONG=true");
            }
            finally
            {
                release.TrySetResult(new { cleanup = true });
                second?.Dispose();
                if (started.Task.IsCompleted)
                {
                    Assert.AreSame(settled.Task, await Task.WhenAny(settled.Task, Task.Delay(10000)),
                        "Fixture cleanup must wait for its actual legacy handler settlement.");
                    await WaitQueue(count => count == 0);
                }
            }
        }

        [Test]
        public async Task Stdio_DisconnectedQueuedReplacementOwners_ArePrunedWhileLegacyExecutionRemains()
        {
            const string activeName = "__editor_qa_prune_active";
            const string queuedName = "__editor_qa_prune_queued";
            var started = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var release = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            int queuedInvocations = 0;
            var settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Register(activeName, new HandlerInfo(activeName, null, async _ =>
            {
                started.TrySetResult(true);
                try { return await release.Task; }
                finally { settled.TrySetResult(true); }
            }));
            Register(queuedName, new HandlerInfo(queuedName, _ => { queuedInvocations++; return new { done = true }; }, null));
            int port = StartOwned();
            using var active = await StdioPeer.Connect(port, Token);
            var original = active.Command(activeName, new JObject());
            try
            {
                Assert.AreSame(started.Task, await Task.WhenAny(started.Task, Task.Delay(10000)));
                for (int attempt = 0; attempt < 3; attempt++)
                {
                    var replacement = await StdioPeer.Connect(port, Token);
                    var queued = replacement.Command(queuedName, new JObject { ["attempt"] = attempt });
                    await WaitQueue(count => count >= 2);
                    replacement.Dispose();
                    try { await queued; Assert.Fail("A disconnected queued peer must not receive successful execution."); }
                    catch (IOException) { }
                    catch (ObjectDisposedException) { }
                    await WaitQueue(count => count == 1);
                    Assert.AreEqual(0, queuedInvocations, "Canceled queued replacements must neither execute nor accumulate behind the active legacy handler.");
                }
                release.TrySetResult(new { settled = true });
                await WaitQueue(count => count == 0);
                using var next = await StdioPeer.Connect(port, Token);
                var response = await next.Command(queuedName, new JObject());
                Assert.AreEqual("success", response.Value<string>("status"));
                Assert.AreEqual(1, queuedInvocations);
                TestContext.WriteLine("DISCONNECTED_QUEUED_REPLACEMENTS=3; QUEUE_RETAINS_ONLY_ACTIVE=1; CANCELED_INVOCATIONS=0; FOLLOWUP_RPC=success");
                try { await original; Assert.Fail("Superseded active peer cannot receive success."); }
                catch (IOException) { }
                catch (ObjectDisposedException) { }
            }
            finally
            {
                release.TrySetResult(new { cleanup = true });
                if (started.Task.IsCompleted)
                {
                    Assert.AreSame(settled.Task, await Task.WhenAny(settled.Task, Task.Delay(10000)));
                    await WaitQueue(count => count == 0);
                }
            }
        }

        private static async Task WaitQueue(Func<int, bool> predicate)
        {
            var deadline = DateTime.UtcNow.AddSeconds(5);
            while (!predicate(StdioBridgeHost.QueuedCommandCount))
            { if (DateTime.UtcNow >= deadline) throw new TimeoutException("Owned stdio queue criterion"); await Task.Delay(10); }
        }

        [Test]
        public async Task Stdio_ReadConsoleProjection_PagesFullMultilineMessagesAndPreservesLegacyKeys()
        {
            Register("read_console", new HandlerInfo("read_console", ReadConsole.HandleCommand, null));
            const string marker = "OwnedEditorQAConsole";
            string multiline = marker + " first\nsecond line 한글😀";
            const string logCommand = "__editor_qa_console_seed";
            Register(logCommand, new HandlerInfo(logCommand, _ =>
            {
                UnityEngine.Debug.Log(multiline);
                UnityEngine.Debug.LogWarning(marker + " warning");
                UnityEngine.Debug.Log(marker + " final");
                return new { count = 3 };
            }, null));
            LogAssert.Expect(UnityEngine.LogType.Log, multiline);
            LogAssert.Expect(UnityEngine.LogType.Warning, marker + " warning");
            LogAssert.Expect(UnityEngine.LogType.Log, marker + " final");
            using var peer = await StdioPeer.Connect(StartOwned(), Token);
            await peer.Command(logCommand, new JObject());
            JObject request = new JObject { ["action"] = "get", ["format"] = "json", ["types"] = new JArray("all"),
                ["filterText"] = marker, ["fields"] = new JArray("type", "message"), ["pageSize"] = 2, ["cursor"] = "0" };
            var page = (await peer.Command("read_console", request))["result"];
            Assert.IsTrue(page.Value<bool>("success"));
            var items = (JArray)page["data"]["items"];
            Assert.AreEqual(2, items.Count);
            Assert.That(items[0].Value<string>("message"), Does.Contain(multiline));
            foreach (JObject item in items) CollectionAssert.AreEquivalent(new[] { "type", "message" }, item.Properties().Select(p => p.Name));
            Assert.AreEqual("2", page["data"].Value<string>("nextCursor"));
            request["cursor"] = "2";
            var next = (await peer.Command("read_console", request))["result"]["data"];
            Assert.AreEqual(1, ((JArray)next["items"]).Count);
            Assert.That(next["items"][0].Value<string>("message"), Does.Contain(marker + " final"));
            Assert.IsNull(next.Value<string>("nextCursor"));
            request.Remove("fields"); request.Remove("pageSize"); request.Remove("cursor");
            var legacy = (JArray)(await peer.Command("read_console", request))["result"]["data"];
            Assert.AreEqual(3, legacy.Count);
            CollectionAssert.AreEquivalent(new[] { "type", "message", "file", "line", "stackTrace" }, ((JObject)legacy[0]).Properties().Select(p => p.Name));
        }

        [UnityTest]
        public IEnumerator DomainReload_OwnedStdio_ReauthenticatesWithFreshGenerationAndRunsUnityRpc()
        {
            if (!SessionState.GetBool(ReloadKey, false))
            {
                int beforePort = StartOwned();
                var beforeTask = StdioPeer.Connect(beforePort, Token);
                while (!beforeTask.IsCompleted) yield return null;
                using (var before = beforeTask.GetAwaiter().GetResult())
                    SessionState.SetString(ReloadKey + ".server", before.Server);
                StdioBridgeHost.Stop();
                SessionState.SetBool(ReloadKey, true);
                _domainSentinel = 73;
                EditorUtility.RequestScriptReload();
            }
            yield return new WaitForDomainReload();
            Assert.AreEqual(0, _domainSentinel, "An actual Unity managed domain reload must have reset the sentinel.");
            RegisterHandlers();
            int afterPort = StartOwned();
            var afterTask = StdioPeer.Connect(afterPort, Token);
            while (!afterTask.IsCompleted) yield return null;
            using var after = afterTask.GetAwaiter().GetResult();
            Assert.AreNotEqual(SessionState.GetString(ReloadKey + ".server", ""), after.Server);
            var command = after.Command(EchoCommand, new JObject { ["text"] = "after actual domain reload 한글😀" });
            while (!command.IsCompleted) yield return null;
            Assert.AreEqual("after actual domain reload 한글😀", command.GetAwaiter().GetResult()["result"].Value<string>("text"));
            TestContext.WriteLine("ACTUAL_DOMAIN_RELOAD_SENTINEL=0; FRESH_SERVER_GENERATION=true; RPC=success");
        }

        private static WebSocketTransportClient OwnedWebSocket(OwnedWebSocketPeer peer, bool compression = false)
        {
            var options = new WebSocketTransportClient.OwnedConnectionOptions(peer.Uri, Token,
                "OwnedEditorQA", new string('a', 40), Directory.GetParent(UnityEngine.Application.dataPath).FullName,
                UnityEngine.Application.unityVersion, compression);
            return new WebSocketTransportClient(null, options);
        }

        [Test]
        public async Task LocalHttp_CancelAndPingControl_SettleActiveRpcBeforeQueuedUnityMutation()
        {
            const string name = "__editor_qa_ws_cancel";
            var started = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Register(name, HandlerInfo.Cooperative(name, async (_, cancellationToken) =>
            {
                started.TrySetResult(true);
                try { await Task.Delay(Timeout.Infinite, cancellationToken); return new { unexpected = true }; }
                finally { settled.TrySetResult(cancellationToken.IsCancellationRequested); }
            }));
            using var peer = new OwnedWebSocketPeer(Token);
            using var client = OwnedWebSocket(peer);
            try
            {
                Assert.IsTrue(await client.StartAsync());
                await peer.WaitRegistered(1);
                string activeId = Guid.NewGuid().ToString("D");
                string queuedId = Guid.NewGuid().ToString("D");
                peer.Execute(activeId, name, new JObject());
                Assert.AreSame(started.Task, await Task.WhenAny(started.Task, Task.Delay(10000)));
                peer.Execute(queuedId, EchoCommand, new JObject { ["text"] = "queued after settlement 한글😀" });
                peer.Send(new JObject { ["type"] = "ping" });
                await peer.Message(m => m.Value<string>("type") == "pong");
                Assert.IsFalse(settled.Task.IsCompleted, "Receive-loop control must respond while actual Unity work remains active.");
                peer.Send(new JObject { ["type"] = "cancel", ["id"] = activeId });
                Assert.AreSame(settled.Task, await Task.WhenAny(settled.Task, Task.Delay(10000)));
                Assert.IsTrue(await settled.Task);
                var cancelled = await peer.Result(activeId);
                Assert.AreEqual("error", cancelled["result"].Value<string>("status"));
                var queued = await peer.Result(queuedId);
                Assert.AreEqual("queued after settlement 한글😀", queued["result"]["result"].Value<string>("text"));
                Assert.IsTrue(peer.AllAuthHeadersMatch, "The real HTTP upgrade must carry only the synthetic owned launch token.");
                TestContext.WriteLine("LOCALHTTP_CONTROL_PONG_DURING_ACTIVE=true; CANCELLATION_SETTLED=true; QUEUED_RPC=success");
            }
            finally { await client.StopAsync(); }
        }

        [Test]
        public async Task LocalHttp_ConnectionReset_AutomaticallyRegistersNewSessionAndExecutesUnityRpc()
        {
            using var peer = new OwnedWebSocketPeer(Token);
            using var client = OwnedWebSocket(peer);
            try
            {
                Assert.IsTrue(await client.StartAsync());
                await peer.WaitRegistered(1);
                peer.DropConnection();
                await peer.WaitRegistered(2);
                Assert.IsTrue(client.IsConnected);
                string id = Guid.NewGuid().ToString("D");
                peer.Execute(id, EchoCommand, new JObject { ["text"] = "after reset 한글😀" });
                var response = await peer.Result(id);
                Assert.AreEqual("after reset 한글😀", response["result"]["result"].Value<string>("text"));
                Assert.IsTrue(peer.AllAuthHeadersMatch);
                TestContext.WriteLine("LOCALHTTP_AUTOMATIC_RECONNECT_REGISTRATIONS=" + peer.RegisteredCount);
            }
            finally { await client.StopAsync(); }
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task LocalHttp_LargeUnicodeChunks_ReassembleExactResultAndNegotiatedGzip(bool compression)
        {
            using var peer = new OwnedWebSocketPeer(Token, compression);
            using var client = OwnedWebSocket(peer, compression);
            try
            {
                Assert.IsTrue(await client.StartAsync());
                await peer.WaitRegistered(1);
                string id = Guid.NewGuid().ToString("D");
                peer.Execute(id, LargeCommand, new JObject());
                var descriptor = await peer.Message(m => m.Value<string>("type") == "result_start" && m.Value<string>("id") == id);
                var response = await peer.Result(id);
                Assert.AreEqual(LargeValue, response["result"]["result"].Value<string>("payload"));
                Assert.AreEqual(LargeValue.Length, response["result"]["result"].Value<int>("characters"));
                Assert.AreEqual(compression ? "gzip" : null, descriptor.Value<string>("encoding"));
                Assert.That(peer.LargestBinaryFrame, Is.LessThanOrEqualTo(64 * 1024));
                Assert.That(descriptor.Value<int>("chunk_count"), Is.GreaterThan(0));
                peer.Send(new JObject { ["type"] = "ping" });
                await peer.Message(m => m.Value<string>("type") == "pong");
                TestContext.WriteLine("LOCALHTTP_LARGE_ENCODING=" + (descriptor.Value<string>("encoding") ?? "identity") +
                    "; WIRE_BYTES=" + descriptor.Value<int>("total_bytes") + "; CHUNKS=" + descriptor.Value<int>("chunk_count") +
                    "; MAX_BINARY_FRAME=" + peer.LargestBinaryFrame + "; EXACT_UNICODE=true; FOLLOWUP_PONG=true");
            }
            finally { await client.StopAsync(); }
        }

        /// <summary>Independent RFC6455 loopback peer; it owns only its listener and accepted sockets.</summary>
        private sealed class OwnedWebSocketPeer : IDisposable
        {
            private readonly TcpListener _listener = new TcpListener(IPAddress.Loopback, 0);
            private readonly object _sendGate = new object();
            private readonly object _connectionGate = new object();
            private readonly ConcurrentQueue<JObject> _messages = new ConcurrentQueue<JObject>();
            private readonly List<JObject> _collected = new List<JObject>();
            private readonly string _token;
            private readonly bool _gzip;
            private readonly Task _acceptTask;
            private readonly List<TcpClient> _ownedClients = new List<TcpClient>();
            private TcpClient _active;
            private NetworkStream _activeStream;
            private volatile bool _disposed;
            private volatile Exception _error;
            public Uri Uri { get; }
            public int RegisteredCount;
            public int LargestBinaryFrame;
            public bool AllAuthHeadersMatch = true;
            public OwnedWebSocketPeer(string token, bool gzip = false)
            {
                _token = token; _gzip = gzip; _listener.Start();
                Uri = new Uri("ws://127.0.0.1:" + ((IPEndPoint)_listener.LocalEndpoint).Port + "/owned-editor-qa");
                Assert.That(Uri.Port, Is.Not.EqualTo(6400).And.Not.EqualTo(6401));
                TestContext.WriteLine("OWNED_LOCALHTTP_ENDPOINT=" + Uri);
                _acceptTask = Task.Run(AcceptLoop);
            }
            private void AcceptLoop()
            {
                while (!_disposed)
                {
                    try
                    {
                        var client = _listener.AcceptTcpClient();
                        lock (_connectionGate) { _ownedClients.Add(client); _active = client; _activeStream = client.GetStream(); }
                        _ = Task.Run(() => ReadConnection(client));
                    }
                    catch (Exception e) { if (!_disposed) _error = e; return; }
                }
            }
            private void ReadConnection(TcpClient client)
            {
                try
                {
                    var stream = client.GetStream();
                    var header = new StringBuilder();
                    while (!header.ToString().EndsWith("\r\n\r\n", StringComparison.Ordinal))
                    { int b = stream.ReadByte(); if (b < 0) throw new EndOfStreamException(); header.Append((char)b); if (header.Length > 16384) throw new InvalidDataException("Oversized upgrade"); }
                    var headers = header.ToString().Split(new[] { "\r\n" }, StringSplitOptions.None)
                        .Where(line => line.Contains(":"))
                        .ToDictionary(line => line.Substring(0, line.IndexOf(':')).Trim(), line => line.Substring(line.IndexOf(':') + 1).Trim(), StringComparer.OrdinalIgnoreCase);
                    // Header spelling is defined by the production local-auth protocol.
                    AllAuthHeadersMatch &= headers.TryGetValue("X-Unity-MCP-Token", out string suppliedToken) && suppliedToken == _token;
                    using var sha = SHA1.Create();
                    string accept = Convert.ToBase64String(sha.ComputeHash(Encoding.ASCII.GetBytes(headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")));
                    byte[] upgrade = Encoding.ASCII.GetBytes("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n");
                    lock (_sendGate) stream.Write(upgrade, 0, upgrade.Length);
                    var transfers = new Dictionary<string, Transfer>();
                    using var fragments = new MemoryStream();
                    int fragmentOpcode = 0;
                    while (!_disposed)
                    {
                        byte[] prefix = ReadExact(stream, 2); int opcode = prefix[0] & 15; bool final = (prefix[0] & 128) != 0;
                        int length = prefix[1] & 127;
                        if (length == 126) { var size = ReadExact(stream, 2); length = size[0] * 256 + size[1]; }
                        if (length == 127) { var size = ReadExact(stream, 8); length = checked((int)IPAddress.NetworkToHostOrder(BitConverter.ToInt64(size, 0))); }
                        if (length < 0 || length > 8 * 1024 * 1024) throw new InvalidDataException("Oversized owned websocket frame");
                        byte[] mask = (prefix[1] & 128) != 0 ? ReadExact(stream, 4) : null;
                        byte[] payload = ReadExact(stream, length);
                        if (mask != null) for (int i = 0; i < payload.Length; i++) payload[i] ^= mask[i % 4];
                        if (opcode == 8) return;
                        if (opcode == 9) { SendFrame(stream, 10, payload); continue; }
                        if (opcode == 10) continue;
                        if (opcode != 0) { fragmentOpcode = opcode; fragments.SetLength(0); }
                        fragments.Write(payload, 0, payload.Length);
                        if (!final) continue;
                        payload = fragments.ToArray(); fragments.SetLength(0);
                        if (fragmentOpcode == 2)
                        {
                            LargestBinaryFrame = Math.Max(LargestBinaryFrame, payload.Length);
                            if (payload.Length < 44 || Encoding.ASCII.GetString(payload, 0, 4) != "ULR1") throw new InvalidDataException("Invalid chunk header");
                            string id = Encoding.ASCII.GetString(payload, 4, 36);
                            var transfer = transfers[id];
                            int offset = payload[40] * 16777216 + payload[41] * 65536 + payload[42] * 256 + payload[43];
                            if (offset != transfer.Offset || offset + payload.Length - 44 > transfer.Data.Length) throw new InvalidDataException("Invalid chunk offset");
                            Buffer.BlockCopy(payload, 44, transfer.Data, offset, payload.Length - 44);
                            transfer.Offset += payload.Length - 44; transfer.Chunks++;
                            if (transfer.Offset == transfer.Data.Length)
                            {
                                if (transfer.Chunks != transfer.Descriptor.Value<int>("chunk_count")) throw new InvalidDataException("Invalid chunk count");
                                byte[] decoded = transfer.Data;
                                if (transfer.Descriptor.Value<string>("encoding") == "gzip")
                                {
                                    using var compressed = new MemoryStream(decoded); using var gzip = new GZipStream(compressed, CompressionMode.Decompress); using var result = new MemoryStream();
                                    gzip.CopyTo(result); decoded = result.ToArray();
                                    if (decoded.Length != transfer.Descriptor.Value<int>("decoded_bytes")) throw new InvalidDataException("Invalid decoded size");
                                }
                                _messages.Enqueue(JObject.Parse(Encoding.UTF8.GetString(decoded))); transfers.Remove(id);
                            }
                        }
                        else if (fragmentOpcode == 1)
                        {
                            var message = JObject.Parse(Encoding.UTF8.GetString(payload)); _messages.Enqueue(message);
                            if (message.Value<string>("type") == "register")
                            {
                                int generation = Interlocked.Increment(ref RegisteredCount);
                                var capabilities = new JArray("large_result_v1", "command_cancel_v1");
                                if (_gzip) capabilities.Add("large_result_gzip_v1");
                                SendFrame(stream, 1, Encoding.UTF8.GetBytes(new JObject { ["type"] = "registered", ["session_id"] = "owned-editor-session-" + generation, ["capabilities"] = capabilities }.ToString(Newtonsoft.Json.Formatting.None)));
                            }
                            if (message.Value<string>("type") == "result_start")
                            {
                                int total = message.Value<int>("total_bytes"); if (total <= 0 || total > 8 * 1024 * 1024) throw new InvalidDataException("Invalid transfer size");
                                transfers.Add(message.Value<string>("id"), new Transfer { Descriptor = message, Data = new byte[total] });
                            }
                        }
                    }
                }
                catch (IOException) { }
                catch (SocketException) { }
                catch (ObjectDisposedException) { }
                catch (Exception e) { if (!_disposed) _error = e; }
            }
            private sealed class Transfer { public JObject Descriptor; public byte[] Data; public int Offset; public int Chunks; }
            private static byte[] ReadExact(NetworkStream stream, int length)
            { var result = new byte[length]; for (int at = 0; at < length;) { int read = stream.Read(result, at, length - at); if (read == 0) throw new EndOfStreamException(); at += read; } return result; }
            private void SendFrame(NetworkStream stream, int opcode, byte[] payload)
            {
                if (payload.Length > 65535) throw new InvalidOperationException("Owned server control frame is too large");
                lock (_sendGate)
                {
                    stream.WriteByte((byte)(128 | opcode));
                    if (payload.Length < 126) stream.WriteByte((byte)payload.Length);
                    else { stream.WriteByte(126); stream.WriteByte((byte)(payload.Length >> 8)); stream.WriteByte((byte)payload.Length); }
                    stream.Write(payload, 0, payload.Length);
                }
            }
            public void Send(JObject message)
            { NetworkStream stream; lock (_connectionGate) stream = _activeStream; SendFrame(stream, 1, Encoding.UTF8.GetBytes(message.ToString(Newtonsoft.Json.Formatting.None))); }
            public void Execute(string id, string name, JObject parameters)
                => Send(new JObject { ["type"] = "execute", ["id"] = id, ["name"] = name, ["params"] = parameters, ["timeout"] = 30 });
            public async Task WaitRegistered(int count)
            { var deadline = DateTime.UtcNow.AddSeconds(15); while (RegisteredCount < count) { Check(); if (DateTime.UtcNow >= deadline) throw new TimeoutException("Owned websocket registration"); await Task.Delay(10); } await Task.Delay(100); }
            public async Task<JObject> Message(Func<JObject, bool> predicate)
            {
                var deadline = DateTime.UtcNow.AddSeconds(15);
                while (true)
                {
                    Check(); while (_messages.TryDequeue(out var message)) _collected.Add(message);
                    var result = _collected.FirstOrDefault(predicate); if (result != null) { _collected.Remove(result); return result; }
                    if (DateTime.UtcNow >= deadline) throw new TimeoutException("Owned websocket expected message");
                    await Task.Delay(10);
                }
            }
            public Task<JObject> Result(string id) => Message(m => m.Value<string>("type") == "command_result" && m.Value<string>("id") == id);
            private void Check() { if (_error != null) throw new InvalidOperationException("Owned peer failure", _error); }
            public void DropConnection() { lock (_connectionGate) { _active.Client.LingerState = new LingerOption(true, 0); _active.Close(); } }
            public void Dispose()
            {
                _disposed = true; _listener.Stop();
                lock (_connectionGate) foreach (var client in _ownedClients) client.Dispose();
                _acceptTask.Wait(TimeSpan.FromSeconds(1));
            }
        }

        internal sealed class StdioPeer : IDisposable
        {
            private readonly TcpClient _client;
            public string Server { get; private set; }
            public string Session { get; private set; }
            private StdioPeer(TcpClient client) { _client = client; }
            public static async Task<StdioPeer> Connect(int port, string token)
            {
                var client = new TcpClient();
                try
                {
                    await client.ConnectAsync(IPAddress.Loopback, port).ConfigureAwait(false);
                    var peer = new StdioPeer(client);
                    string banner = await ReadLine(client.GetStream()).ConfigureAwait(false);
                    Assert.That(banner, Does.StartWith("WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 "));
                    var values = banner.Split(' ').Where(part => part.Contains("=")).ToDictionary(part => part.Split('=')[0], part => part.Split('=')[1]);
                    peer.Server = values["SERVER"];
                    string challenge = values["CHALLENGE"];
                    string nonce = Hex(RandomBytes(32));
                    string clientProof = Proof(token, "unity-mcp-stdio-v2\nclient\n" + peer.Server + "\n" + challenge + "\n" + nonce);
                    await WriteFrame(client.GetStream(), Encoding.UTF8.GetBytes(new JObject { ["type"] = "authenticate", ["version"] = 2, ["client_nonce"] = nonce, ["proof"] = clientProof }.ToString(Newtonsoft.Json.Formatting.None))).ConfigureAwait(false);
                    var ack = JObject.Parse(Encoding.UTF8.GetString(await ReadFrame(client.GetStream()).ConfigureAwait(false)));
                    Assert.AreEqual("authenticated", ack.Value<string>("type"));
                    Assert.AreEqual(2, ack.Value<int>("version"));
                    peer.Session = ack.Value<string>("session_id");
                    Assert.AreEqual(Proof(token, "unity-mcp-stdio-v2\nserver\n" + peer.Server + "\n" + challenge + "\n" + nonce + "\n" + peer.Session), ack.Value<string>("proof"));
                    return peer;
                }
                catch { client.Dispose(); throw; }
            }
            private static byte[] RandomBytes(int length) { var result = new byte[length]; using var random = RandomNumberGenerator.Create(); random.GetBytes(result); return result; }
            private static string Hex(byte[] bytes) => BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
            private static string Proof(string token, string transcript) { using var hmac = new HMACSHA256(Encoding.UTF8.GetBytes(token)); return Hex(hmac.ComputeHash(Encoding.ASCII.GetBytes(transcript))); }
            public async Task<string> Ping() { await WriteFrame(_client.GetStream(), Encoding.UTF8.GetBytes("ping")).ConfigureAwait(false); return Encoding.UTF8.GetString(await ReadFrame(_client.GetStream()).ConfigureAwait(false)); }
            public async Task<JObject> Command(string name, JObject parameters)
            {
                await WriteFrame(_client.GetStream(), Encoding.UTF8.GetBytes(new JObject { ["type"] = name, ["params"] = parameters }.ToString(Newtonsoft.Json.Formatting.None))).ConfigureAwait(false);
                return JObject.Parse(Encoding.UTF8.GetString(await ReadFrame(_client.GetStream()).ConfigureAwait(false)));
            }
            internal static async Task<string> ReadLine(NetworkStream stream)
            {
                var result = new StringBuilder();
                while (true) { var bytes = await Exact(stream, 1).ConfigureAwait(false); if (bytes[0] == 10) return result.ToString(); result.Append((char)bytes[0]); if (result.Length > 1024) throw new InvalidDataException("Oversized authentication banner"); }
            }
            internal static async Task WriteFrame(NetworkStream stream, byte[] payload)
            {
                byte[] header = BitConverter.GetBytes(IPAddress.HostToNetworkOrder((long)payload.Length));
                await stream.WriteAsync(header, 0, header.Length).ConfigureAwait(false);
                await stream.WriteAsync(payload, 0, payload.Length).ConfigureAwait(false);
            }
            private static async Task<byte[]> ReadFrame(NetworkStream stream)
            {
                var header = await Exact(stream, 8).ConfigureAwait(false);
                int length = checked((int)IPAddress.NetworkToHostOrder(BitConverter.ToInt64(header, 0)));
                if (length < 0 || length > 8 * 1024 * 1024) throw new InvalidDataException("Unexpected owned frame size");
                return await Exact(stream, length).ConfigureAwait(false);
            }
            private static async Task<byte[]> Exact(NetworkStream stream, int length)
            {
                var result = new byte[length];
                int at = 0;
                while (at < length)
                {
                    var read = stream.ReadAsync(result, at, length - at);
                    if (await Task.WhenAny(read, Task.Delay(10000)).ConfigureAwait(false) != read) throw new TimeoutException("Owned peer read deadline");
                    int count = await read.ConfigureAwait(false);
                    if (count == 0) throw new EndOfStreamException("Owned peer closed");
                    at += count;
                }
                return result;
            }
            public void Dispose() { _client.Dispose(); }
        }
    }
}
