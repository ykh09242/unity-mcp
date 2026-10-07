using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Net.WebSockets;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

internal static class CancellationSocketHarness
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

    public static void Run(Action<Func<bool>, string, int> pump, Action<string> pass)
    {
        var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
        string name = "fixture_cooperative_cancel";
        string mutation = "fixture_cancel_mutation";
        object oldHandler = handlers[name];
        object oldMutation = handlers[mutation];
        var cleanup = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        int starts = 0,
            settles = 0,
            mutations = 0;
        handlers[name] = HandlerInfo.Cooperative(
            name,
            async (_, token) =>
            {
                Interlocked.Increment(ref starts);
                try
                {
                    await Task.Delay(Timeout.Infinite, token).ConfigureAwait(true);
                    return null;
                }
                finally
                {
                    await cleanup.Task.ConfigureAwait(true);
                    Interlocked.Increment(ref settles);
                }
            }
        );
        handlers[mutation] = new HandlerInfo(
            mutation,
            _ =>
            {
                Interlocked.Increment(ref mutations);
                return new { done = true };
            },
            null
        );
        try
        {
            using var peer = new LocalWebSocketPeer();
            using var socket = new ClientWebSocket();
            using var lifecycle = new CancellationTokenSource();
            using var connection = CancellationTokenSource.CreateLinkedTokenSource(lifecycle.Token);
            using var obsoleteConnection = new CancellationTokenSource();
            using var client = new WebSocketTransportClient();
            var connect = socket.ConnectAsync(peer.Uri, connection.Token);
            pump(() => connect.IsCompleted, "cancellation socket handshake", 5000);
            connect.GetAwaiter().GetResult();
            peer.Ready.GetAwaiter().GetResult();
            Set(client, "_socket", socket);
            Set(client, "_lifecycleCts", lifecycle);
            Set(client, "_connectionCts", connection);
            var work = new ConnectionCommandWork(connection.Token);
            Set(client, "_commandWork", work);
            Set(client, "_endpointUri", peer.Uri);
            Set(client, "_keepAliveInterval", TimeSpan.FromMinutes(1));
            typeof(WebSocketTransportClient).GetMethod("StartBackgroundLoops", Private).Invoke(client, new object[] { connection.Token });
            var messages = new List<JObject>();
            Func<string, JObject> result = id =>
            {
                if (peer.Reader?.IsFaulted == true)
                    peer.Reader.GetAwaiter().GetResult();
                while (peer.Received.TryDequeue(out var message))
                    messages.Add(message);
                return messages.FirstOrDefault(m => m.Value<string>("type") == "command_result" && m.Value<string>("id") == id);
            };
            peer.Send(Ack(true));
            pump(() => (bool)Get(client, "_commandCancellationNegotiated"), "cancel capability ACK", 5000);
            peer.Send(Execute("active-cancel", name));
            pump(() => Volatile.Read(ref starts) == 1, "cooperative handler starts", 5000);

            // An old receiver token cannot control the current connection's request.
            var stale = (Task)
                typeof(WebSocketTransportClient)
                    .GetMethod("HandleMessageAsync", Private)
                    .Invoke(client, new object[] { Cancel("active-cancel").ToString(), obsoleteConnection.Token });
            pump(() => stale.IsCompleted, "stale control settles", 5000);
            stale.GetAwaiter().GetResult();
            Assert.IsNull(result("active-cancel"));
            Assert.AreEqual(0, settles);
            pass("Stale connection cancel cannot stop current generation work");

            peer.Send(Execute("queued-cancel", mutation));
            peer.Send(Execute("after-cancel", mutation));
            peer.Send(Cancel("queued-cancel"));
            peer.Send(new JObject { ["type"] = "ping" });
            pump(() => result("queued-cancel") != null, "queued cancellation response", 5000);
            Assert.That(result("queued-cancel").SelectToken("result.error").Value<string>(), Does.Contain("canceled"));
            Assert.AreEqual(0, mutations);
            pass("Negotiated cancel skips queued mutation before predecessor settles");
            peer.Send(Cancel("active-cancel"));
            peer.Send(Cancel("active-cancel"));
            peer.Send(new JObject { ["type"] = "ping" });
            pump(
                () => result("active-cancel") != null && messages.Any(m => m.Value<string>("type") == "pong"),
                "active cancellation and duplicate control pong",
                5000
            );
            Assert.That(result("active-cancel").SelectToken("result.error").Value<string>(), Does.Contain("canceled"));
            Assert.AreEqual(0, settles);
            Assert.AreEqual(0, mutations);
            pass("Same-socket and duplicate cancel stop response while receive loop answers ping");
            cleanup.SetResult(true);
            pump(() => result("after-cancel") != null && work.DrainAsync().IsCompleted, "cooperative cleanup and following mutation", 5000);
            Assert.AreEqual(1, settles);
            Assert.AreEqual(1, mutations);
            pass("Following mutation waits for canceled handler cleanup and skips canceled child");

            peer.Send(Ack(false));
            pump(() => !(bool)Get(client, "_commandCancellationNegotiated"), "legacy ACK clears cancel capability", 5000);
            peer.Send(Execute("capability-off", name, 1));
            pump(() => Volatile.Read(ref starts) == 2, "legacy capability handler starts", 5000);
            peer.Send(Cancel("capability-off"));
            peer.Send(new JObject { ["type"] = "ping" });
            int pongs = messages.Count(m => m.Value<string>("type") == "pong");
            pump(
                () =>
                {
                    result("capability-off");
                    return messages.Count(m => m.Value<string>("type") == "pong") > pongs;
                },
                "legacy control processing barrier",
                5000
            );
            Assert.IsNull(result("capability-off"));
            Assert.AreEqual(1, settles);
            pump(() => result("capability-off") != null && Volatile.Read(ref settles) == 2, "legacy local deadline cooperative stop", 2500);
            Assert.That(result("capability-off").SelectToken("result.error").Value<string>(), Does.Contain("timed out"));
            pass("Unnegotiated cancel ignored; local deadline still stops cooperative work");

            peer.Send(Execute("disconnect-cooperative", name));
            pump(() => Volatile.Read(ref starts) == 3, "cooperative disconnect handler starts", 5000);
            object loops = typeof(WebSocketTransportClient).GetMethod("CaptureConnectionLoops", Private).Invoke(client, null);
            var stop = (Task)
                typeof(WebSocketTransportClient).GetMethod("StopCapturedConnectionLoopsAsync", Private).Invoke(client, new[] { loops, (object)true });
            pump(() => stop.IsCompleted && Volatile.Read(ref settles) == 3, "cooperative disconnect settlement", 5000);
            stop.GetAwaiter().GetResult();
            Assert.IsTrue(work.DrainAsync().IsCompleted);
            pass("Disconnect cancels cooperative wait and handler finally settles");
            client.ForceStop();
        }
        finally
        {
            if (oldHandler == null)
                handlers.Remove(name);
            else
                handlers[name] = oldHandler;
            if (oldMutation == null)
                handlers.Remove(mutation);
            else
                handlers[mutation] = oldMutation;
        }
    }

    private static void Set(WebSocketTransportClient client, string name, object value) =>
        typeof(WebSocketTransportClient).GetField(name, Private).SetValue(client, value);

    private static object Get(WebSocketTransportClient client, string name) => typeof(WebSocketTransportClient).GetField(name, Private).GetValue(client);

    private static JObject Ack(bool capability) =>
        new JObject
        {
            ["type"] = "registered",
            ["session_id"] = "cancel-fixture",
            ["capabilities"] = capability ? new JArray(ConnectionCommandWork.CancellationCapability) : new JArray(),
        };

    private static JObject Cancel(string id) => new JObject { ["type"] = "cancel", ["id"] = id };

    private static JObject Execute(string id, string name, int timeout = 30) =>
        new JObject
        {
            ["type"] = "execute",
            ["id"] = id,
            ["name"] = name,
            ["timeout"] = timeout,
        };
}
