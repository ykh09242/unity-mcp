using System;
using System.Collections;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class TransportArchitectureTests
    {
        [Test]
        public void MainThreadCallback_WithMissingContext_WaitsForEditorPump()
        {
            var context = typeof(TransportCommandDispatcher).GetField("_mainThreadContext", BindingFlags.Static | BindingFlags.NonPublic);
            var original = context.GetValue(null);
            int mainThread = Thread.CurrentThread.ManagedThreadId;
            Task<int> queued = null;
            try
            {
                context.SetValue(null, null);
                Task.Run(() =>
                    {
                        queued = TransportCommandDispatcher.RunOnMainThreadAsync(() => Thread.CurrentThread.ManagedThreadId, CancellationToken.None);
                    })
                    .GetAwaiter()
                    .GetResult();
                Assert.IsFalse(queued.IsCompleted, "Missing Unity synchronization context must never execute Unity work on a receiver thread.");
                typeof(TransportCommandDispatcher).GetMethod("ProcessQueue", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
                Assert.AreEqual(mainThread, queued.GetAwaiter().GetResult());
            }
            finally
            {
                context.SetValue(null, original);
            }
        }

        [Test]
        public void OwnedWork_BoundsIdsAndRetainsFifoUntilExecutionCompletes()
        {
            using var lifetime = new CancellationTokenSource();
            var owner = new ConnectionCommandWork(lifetime.Token, 2);
            var active = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            bool secondStarted = false;
            Assert.IsNull(owner.TryStart("first", _ => active.Task));
            Assert.AreEqual("Duplicate command id", owner.TryStart("first", _ => Task.CompletedTask));
            Assert.IsNull(
                owner.TryStart(
                    "second",
                    async previous =>
                    {
                        await previous.ConfigureAwait(false);
                        secondStarted = true;
                    }
                )
            );
            Assert.AreEqual("Command queue is full", owner.TryStart("third", _ => Task.CompletedTask));
            Assert.IsFalse(secondStarted);
            active.SetResult(true);
            Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            Assert.IsTrue(secondStarted);
        }

        [Test]
        public void TypedAndLegacySyncResponses_PreserveExactEnvelope()
        {
            CommandRegistry.Initialize();
            const string name = "architecture_sync_parity";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            handlers[name] = new HandlerInfo(name, _ => new { text = "\uD55C\uAE00😀", nested = new { count = 3 } }, null);
            try
            {
                var command = new Command { type = name, @params = new JObject() };
                var typed = TransportCommandDispatcher.ExecuteCommandAsync(command, CancellationToken.None);
                var legacy = TransportCommandDispatcher.ExecuteCommandJsonAsync("{\"type\":\"" + name + "\"}", CancellationToken.None);
                Assert.AreEqual(legacy.GetAwaiter().GetResult(), typed.Response.GetAwaiter().GetResult().ToJson());
                Assert.IsTrue(typed.Completion.IsCompleted);
                Assert.IsInstanceOf<JToken>(typed.Response.Result.Payload, "Unity-facing values are frozen without a JSON text round trip.");
            }
            finally
            {
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }

        private sealed class AdmissionValues : System.Collections.Generic.IEnumerable<int>
        {
            internal int Visited;
            internal bool Disposed;

            public System.Collections.Generic.IEnumerator<int> GetEnumerator()
            {
                try
                {
                    for (int i = 0; i < 2000000; i++)
                    {
                        Visited++;
                        yield return i & 1;
                    }
                }
                finally
                {
                    Disposed = true;
                }
            }

            IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
        }

        [TestCase(false)]
        [TestCase(true)]
        public void AsyncAdmission_StopsLazyResultsBeforeFullEnumeration(bool legacyCaller)
        {
            CommandRegistry.Initialize();
            const string name = "architecture_async_admission";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            var values = new AdmissionValues();
            handlers[name] = new HandlerInfo(name, null, _ => Task.FromResult<object>(values));
            try
            {
                string json;
                if (legacyCaller)
                {
                    var completion = new TaskCompletionSource<string>();
                    Assert.IsNull(CommandRegistry.ExecuteCommand(name, new JObject(), completion));
                    json = completion.Task.GetAwaiter().GetResult();
                }
                else
                {
                    var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, CancellationToken.None);
                    json = operation.Response.GetAwaiter().GetResult().ToJson();
                    Assert.IsTrue(operation.Completion.IsCompleted);
                }
                Assert.Less(values.Visited, 2000000, "Admission must stop the original lazy producer before creating a full JSON string.");
                Assert.IsTrue(values.Disposed, "The refused producer must release its iterator.");
                Assert.AreEqual("error", (string)JObject.Parse(json)["status"]);
                StringAssert.Contains("size or complexity limit", json);
            }
            finally
            {
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void AsyncAdmission_PreservesSerializerSettingsAndAcceptedWireValues(bool customSettings)
        {
            CommandRegistry.Initialize();
            const string name = "architecture_async_wire_admission";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            var settings = JsonConvert.DefaultSettings;
            try
            {
                if (customSettings)
                    JsonConvert.DefaultSettings = () =>
                        new JsonSerializerSettings
                        {
                            DateFormatString = "yyyy/MM/dd HH:mm:ss",
                            FloatFormatHandling = FloatFormatHandling.Symbol,
                            StringEscapeHandling = StringEscapeHandling.EscapeNonAscii,
                            Formatting = Formatting.Indented,
                            Converters = { new AdmissionConverter() },
                        };
                object[] results =
                {
                    null,
                    new { text = new string('x', 4 * 1024 * 1024), complete = true },
                    new
                    {
                        precise = 0.1234567890123456789012345678m,
                        single = 1.2345678f,
                        nonfinite = double.NaN,
                        bytes = new byte[] { 1, 2, 3 },
                        date = new DateTime(2026, 10, 24, 1, 2, 3, DateTimeKind.Utc),
                        text = "2026-10-24T01:02:03Z \u6f22\U0001f680",
                        raw = new JRaw("{ \"x\" : [ 1, 2 ] }"),
                        converted = new AdmissionConvertedValue(),
                        fragmented = new AdmissionFragmentedValue(),
                    },
                };
                foreach (object result in results)
                {
                    handlers[name] = new HandlerInfo(name, null, _ => Task.FromResult(result));
                    string expected = JsonConvert.SerializeObject(new { status = "success", result });
                    var completion = new TaskCompletionSource<string>();
                    CommandRegistry.ExecuteCommand(name, new JObject(), completion);
                    Assert.AreEqual(expected, completion.Task.GetAwaiter().GetResult(), "Public string completion retains the original serializer contract.");
                    var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, CancellationToken.None);
                    Assert.AreEqual(TransportCommandResponse.FromJson(expected).ToJson(), operation.Response.GetAwaiter().GetResult().ToJson());
                    Assert.IsTrue(operation.Completion.IsCompleted);
                }
            }
            finally
            {
                JsonConvert.DefaultSettings = settings;
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }

        private sealed class AdmissionConvertedValue
        {
            public int Value => 7;
        }

        private sealed class AdmissionFragmentedValue { }

        private sealed class AdmissionConverter : JsonConverter
        {
            public override bool CanConvert(Type type) => type == typeof(AdmissionConvertedValue) || type == typeof(AdmissionFragmentedValue);

            public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer)
            {
                if (value is AdmissionFragmentedValue)
                {
                    writer.WriteRawValue("[");
                    writer.WriteRaw("1,");
                    writer.WriteRaw("2]");
                    return;
                }
                writer.WriteStartArray();
                writer.WriteValue((double?)double.NaN);
                writer.WriteValue((float?)1.25f);
                writer.WriteValue((byte[])null);
                writer.WriteValue((Uri)null);
                writer.WriteRawValue("{ \"converted\" : true }");
                writer.WriteEndArray();
            }

            public override object ReadJson(JsonReader reader, Type type, object existing, JsonSerializer serializer) => throw new NotSupportedException();
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        [TestCase(3)]
        public void AsyncAdmission_ByteBudgetPreservesLargeAsciiAndSplitSurrogates(int boundary)
        {
            var settings = JsonConvert.DefaultSettings;
            try
            {
                JsonConvert.DefaultSettings = () => new JsonSerializerSettings { Converters = { new AdmissionByteBoundaryConverter() } };
                var value = new AdmissionByteBoundary { Boundary = boundary };
                if (boundary == 3)
                {
                    Assert.Throws<ResponseLimitException>(() => BoundedResponseJson.SerializeAdmitted(value));
                    return;
                }
                string json = BoundedResponseJson.SerializeAdmitted(value);
                long expectedBytes = boundary == 0 ? LargeResultWriter.MaxResultBytes : LargeResultWriter.MaxResultBytes / 3 + 4;
                Assert.AreEqual(expectedBytes, System.Text.Encoding.UTF8.GetByteCount(json));
                Assert.AreEqual('"', json[0]);
                Assert.AreEqual('"', json[json.Length - 1]);
                if (boundary != 0)
                    StringAssert.Contains("\ud83d\ude80", json);
            }
            finally
            {
                JsonConvert.DefaultSettings = settings;
            }
        }

        private sealed class AdmissionByteBoundary
        {
            internal int Boundary;
        }

        private sealed class AdmissionByteBoundaryConverter : JsonConverter
        {
            public override bool CanConvert(Type type) => type == typeof(AdmissionByteBoundary);

            public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer)
            {
                int boundary = ((AdmissionByteBoundary)value).Boundary;
                int threshold = LargeResultWriter.MaxResultBytes / 3;
                writer.WriteRawValue("\"");
                if (boundary == 0 || boundary == 3)
                {
                    WriteAscii(writer, LargeResultWriter.MaxResultBytes - 2 + (boundary == 3 ? 1 : 0));
                }
                else if (boundary == 1)
                {
                    WriteAscii(writer, threshold - 2);
                    writer.WriteRaw("\ud83d");
                    writer.WriteRaw("\ude80");
                }
                else
                {
                    // The pair straddles the fixed buffer used when exact counting starts.
                    WriteAscii(writer, 8190);
                    writer.WriteRaw("\ud83d\ude80");
                    WriteAscii(writer, threshold - 8192);
                }
                writer.WriteRaw("\"");
            }

            private static void WriteAscii(JsonWriter writer, int count)
            {
                string block = new string('x', 8192);
                while (count >= block.Length)
                {
                    writer.WriteRaw(block);
                    count -= block.Length;
                }
                if (count > 0)
                    writer.WriteRaw(block.Substring(0, count));
            }

            public override object ReadJson(JsonReader reader, Type type, object existing, JsonSerializer serializer) => throw new NotSupportedException();
        }

        [Test]
        public void AsyncAdmission_RejectsDeepValuesAndSettlesFollowingCommand()
        {
            CommandRegistry.Initialize();
            const string name = "architecture_async_deep_admission";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            JToken value = new JValue(1);
            for (int i = 0; i < 128; i++)
                value = new JArray(value);
            handlers[name] = new HandlerInfo(name, null, _ => Task.FromResult<object>(value));
            try
            {
                var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, CancellationToken.None);
                StringAssert.Contains("size or complexity limit", operation.Response.GetAwaiter().GetResult().ToJson());
                Assert.IsTrue(operation.Completion.IsCompleted);
                Assert.AreEqual(
                    "success",
                    (string)JObject.Parse(TransportCommandDispatcher.ExecuteCommandJsonAsync("ping", CancellationToken.None).Result)["status"]
                );
            }
            finally
            {
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }

        [Test]
        public void TypedAndLegacyAsyncResponses_PreserveExactEnvelope()
        {
            CommandRegistry.Initialize();
            const string name = "architecture_async_parity";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            handlers[name] = new HandlerInfo(name, null, _ => Task.FromResult<object>(new { text = "\uD55C\uAE00😀", values = new[] { 1, 2, 3 } }));
            try
            {
                var typed = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, CancellationToken.None);
                var legacy = TransportCommandDispatcher.ExecuteCommandJsonAsync("{\"type\":\"" + name + "\"}", CancellationToken.None);
                Assert.AreEqual(legacy.GetAwaiter().GetResult(), typed.Response.GetAwaiter().GetResult().ToJson());
                Assert.IsTrue(typed.Completion.IsCompleted);
            }
            finally
            {
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }

        [Test]
        public void RawCommandAndAsyncResponse_PreserveDateShapedLiteral()
        {
            CommandRegistry.Initialize();
            const string name = "architecture_literal_json";
            const string literal = "2026-10-18T01:02:03.1234567+09:00";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            var observed = new System.Collections.Generic.List<JToken>();
            handlers[name] = new HandlerInfo(
                name,
                null,
                parameters =>
                {
                    observed.Add(parameters["text"].DeepClone());
                    return Task.FromResult<object>(new { text = (string)parameters["text"] });
                }
            );
            try
            {
                var typed = TransportCommandDispatcher.ExecuteCommandAsync(
                    new Command
                    {
                        type = name,
                        @params = new JObject { ["text"] = literal },
                    },
                    CancellationToken.None
                );
                var raw = TransportCommandDispatcher.ExecuteCommandJsonAsync(
                    "{\"type\":\"" + name + "\",\"params\":{\"text\":\"" + literal + "\"}}",
                    CancellationToken.None
                );
                Assert.IsTrue(raw.Wait(TimeSpan.FromSeconds(5)));
                Assert.AreEqual(2, observed.Count);
                foreach (var token in observed)
                {
                    Assert.AreEqual(JTokenType.String, token.Type, "The raw dispatcher boundary must preserve a JSON string.");
                    Assert.AreEqual(literal, (string)token);
                }
                var result = (JObject)((JObject)typed.Response.GetAwaiter().GetResult().Payload)["result"];
                Assert.AreEqual(JTokenType.String, result["text"].Type, "The actual async response compatibility parse must also preserve strings.");
                Assert.AreEqual(literal, (string)result["text"]);
                Assert.AreEqual(raw.Result, typed.Response.Result.ToJson());
                Assert.IsTrue(typed.Completion.IsCompleted);
            }
            finally
            {
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }

        [Test]
        public async Task ActiveDeadline_CancelsResponseButTracksActualAsyncSettlement()
        {
            CommandRegistry.Initialize();
            const string name = "architecture_async_deadline";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            var handlerCompletion = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            handlers[name] = new HandlerInfo(name, null, _ => handlerCompletion.Task);
            using var deadline = new CancellationTokenSource();
            try
            {
                var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, deadline.Token);
                deadline.Cancel();
                Assert.IsTrue(operation.Response.IsCanceled);
                Assert.IsFalse(operation.Completion.IsCompleted, "Response cancellation must not release the active mutation.");
                handlerCompletion.SetResult(new { done = true });
                Assert.AreSame(operation.Completion, await Task.WhenAny(operation.Completion, Task.Delay(TimeSpan.FromSeconds(5))));
            }
            finally
            {
                handlerCompletion.TrySetResult(new { done = true });
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }
    }
}
