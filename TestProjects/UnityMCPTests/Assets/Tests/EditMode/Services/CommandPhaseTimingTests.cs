using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using CommandPhaseTiming = MCPForUnityTests.Editor.Services.TimingProbe;

namespace MCPForUnityTests.Editor.Services
{
    // Reflection keeps the baseline fixture compilable before the new feature exists.
    // Missing timing support fails a contract assertion, not C# compilation.
    internal sealed class TimingProbe
    {
        internal enum Transport
        {
            Direct,
            WebSocket,
            Stdio,
        }

        internal enum Phase
        {
            Queue,
            Handler,
            Prepare,
            Serialization,
            Send,
        }

        internal enum Delivery
        {
            Unobserved,
            Sent,
            Canceled,
            Failed,
        }

        private readonly object _instance;
        private static Type Implementation
        {
            get
            {
                var type = typeof(MCPForUnity.Editor.Services.Transport.Transports.WebSocketTransportClient).Assembly.GetType(
                    "MCPForUnity.Editor.Services.Transport.CommandPhaseTiming"
                );
                Assert.IsNotNull(type, "Opt-in native phase timing must expose sampled, payload-free phase records.");
                return type;
            }
        }

        internal TimingProbe(Func<long> clock, long frequency, Action<JObject> sink, Transport transport)
        {
            _instance = Activator.CreateInstance(
                Implementation,
                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic,
                null,
                new object[] { clock, frequency, sink, ConvertEnum(transport) },
                null
            );
        }

        private static object ConvertEnum(Enum value) =>
            Enum.ToObject(Implementation.GetNestedType(value.GetType().Name, System.Reflection.BindingFlags.NonPublic), Convert.ToInt32(value));

        internal static bool ShouldSample(long admission) =>
            (bool)
                Implementation
                    .GetMethod("ShouldSample", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic)
                    .Invoke(null, new object[] { admission });

        internal static object Create(bool enabled, Transport transport) =>
            Implementation
                .GetMethod("Create", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic)
                .Invoke(null, new[] { (object)enabled, ConvertEnum(transport) });

        private void Invoke(string name, params object[] arguments) =>
            Implementation.GetMethod(name, System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic).Invoke(_instance, arguments);

        internal void Start(Phase phase) => Invoke("Start", ConvertEnum(phase));

        internal void Stop(Phase phase) => Invoke("Stop", ConvertEnum(phase));

        internal void ExecutionDone() => Invoke("ExecutionDone");

        internal void DeliveryDone(Delivery delivery) => Invoke("DeliveryDone", ConvertEnum(delivery));
    }

    public class CommandPhaseTimingTests
    {
        [Test]
        public void ServerCorrelationRequiresBothNegotiationAndLocalOptIn()
        {
            var type = typeof(MCPForUnity.Editor.Services.Transport.Transports.WebSocketTransportClient).Assembly.GetType(
                "MCPForUnity.Editor.Services.Transport.CommandPhaseTiming"
            );
            const System.Reflection.BindingFlags flags = System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic;
            var create = type.GetMethod("CreateForCommand", flags);
            Assert.IsNotNull(create);
            string token = new string('a', 32);
            var payload = JObject.Parse("{\"diagnostics\":{\"correlation\":\"" + token + "\"}}");
            Assert.IsNull(create.Invoke(null, new object[] { false, true, payload }));
            object accepted = create.Invoke(null, new object[] { true, true, payload });
            var correlation = type.GetField("_correlation", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
            Assert.AreEqual(token, correlation.GetValue(accepted));
            object legacy = create.Invoke(null, new object[] { true, false, payload });
            if (legacy != null)
                Assert.AreNotEqual(token, correlation.GetValue(legacy));
            foreach (JToken invalid in new JToken[] { "secret/path", new string('a', 33), new string('A', 32), new JObject(), 1 })
            {
                payload["diagnostics"]["correlation"] = invalid;
                object ignored = create.Invoke(null, new object[] { true, true, payload });
                if (ignored != null)
                    Assert.IsFalse(
                        (bool)
                            type.GetField("_serverCorrelation", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                                .GetValue(ignored)
                    );
            }
        }

        [Test]
        public void ImportSpansKeepConcurrentOwnersSeparateAndDoNotRecordPaths()
        {
            var type = typeof(MCPForUnity.Editor.Services.Transport.Transports.WebSocketTransportClient).Assembly.GetType(
                "MCPForUnity.Editor.Services.Transport.CommandPhaseTiming"
            );
            const System.Reflection.BindingFlags methods = System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic;
            const System.Reflection.BindingFlags instance = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
            var rows = new List<JObject>();
            long now = 0;
            object Make(char id) =>
                Activator.CreateInstance(
                    type,
                    instance,
                    null,
                    new object[]
                    {
                        (Func<long>)(() => now),
                        1000L,
                        (Action<JObject>)rows.Add,
                        Enum.ToObject(type.GetNestedType("Transport", System.Reflection.BindingFlags.NonPublic), 1),
                        new string(id, 32),
                    },
                    null
                );
            object first = Make('a'),
                second = Make('b');
            using ((IDisposable)type.GetMethod("EnterHandler", methods).Invoke(null, new[] { first }))
            {
                using ((IDisposable)type.GetMethod("MeasureImport", methods).Invoke(null, null))
                    now += 7;
                using ((IDisposable)type.GetMethod("EnterHandler", methods).Invoke(null, new[] { second }))
                using ((IDisposable)type.GetMethod("MeasureImport", methods).Invoke(null, null))
                    now += 13;
                using ((IDisposable)type.GetMethod("MeasureImport", methods).Invoke(null, null))
                    now += 5;
            }
            using ((IDisposable)type.GetMethod("MeasureImport", methods).Invoke(null, null))
                now += 100;
            foreach (object timing in new[] { first, second })
            {
                type.GetMethod("ExecutionDone", instance).Invoke(timing, null);
                type.GetMethod("DeliveryDone", instance)
                    .Invoke(timing, new[] { Enum.ToObject(type.GetNestedType("Delivery", System.Reflection.BindingFlags.NonPublic), 1) });
            }
            Assert.AreEqual(12d, rows[0].Value<double>("import_ms"));
            Assert.AreEqual(13d, rows[1].Value<double>("import_ms"));
            Assert.IsFalse(rows[0].Value<bool>("execution_settled_after_delivery"));
            Assert.AreEqual(new string('a', 32), rows[0].Value<string>("correlation"));
            Assert.AreEqual(new string('b', 32), rows[1].Value<string>("correlation"));
        }

        [Test]
        public void SamplesFirstAdmissionThenEverySixtyFour()
        {
            var sampled = new List<long>();
            for (long admission = 1; admission <= 130; admission++)
                if (CommandPhaseTiming.ShouldSample(admission))
                    sampled.Add(admission);
            CollectionAssert.AreEqual(new long[] { 1, 65, 129 }, sampled);
            Assert.IsNull(CommandPhaseTiming.Create(false, CommandPhaseTiming.Transport.WebSocket));
        }

        [TestCase(0, "queue_ms")]
        [TestCase(1, "handler_ms")]
        [TestCase(2, "prepare_ms")]
        [TestCase(3, "serialization_ms")]
        [TestCase(4, "send_ms")]
        public void ControlledPhaseAdvanceChangesOnlyThatInterval(int phaseIndex, string key)
        {
            var phase = (CommandPhaseTiming.Phase)phaseIndex;
            long now = 0;
            JObject row = null;
            var timing = new CommandPhaseTiming(() => now, 1000, value => row = value, CommandPhaseTiming.Transport.WebSocket);
            foreach (CommandPhaseTiming.Phase current in Enum.GetValues(typeof(CommandPhaseTiming.Phase)))
            {
                timing.Start(current);
                now += current == phase ? 23 : 0;
                timing.Stop(current);
            }
            timing.ExecutionDone();
            timing.DeliveryDone(CommandPhaseTiming.Delivery.Sent);
            Assert.AreEqual(23d, row.Value<double>(key));
            foreach (string other in new[] { "queue_ms", "handler_ms", "prepare_ms", "serialization_ms", "send_ms" })
                if (other != key)
                    Assert.AreEqual(0d, row.Value<double>(other));
        }

        [Test]
        public void DeliveryCancellationWaitsForActualExecutionAndEmitsOnce()
        {
            long now = 1;
            var rows = new List<JObject>();
            var timing = new CommandPhaseTiming(() => now, 1000, rows.Add, CommandPhaseTiming.Transport.Stdio);
            timing.Start(CommandPhaseTiming.Phase.Handler);
            now = 5;
            timing.DeliveryDone(CommandPhaseTiming.Delivery.Canceled);
            Assert.IsEmpty(rows);
            now = 25;
            timing.Stop(CommandPhaseTiming.Phase.Handler);
            timing.ExecutionDone();
            timing.ExecutionDone();
            timing.DeliveryDone(CommandPhaseTiming.Delivery.Sent);
            Assert.AreEqual(1, rows.Count);
            Assert.AreEqual(24d, rows[0].Value<double>("handler_ms"));
            Assert.AreEqual(4d, rows[0].Value<double>("total_ms"));
            Assert.AreEqual("canceled", rows[0].Value<string>("delivery"));
            Assert.IsTrue(rows[0].Value<bool>("execution_settled"));
            Assert.IsTrue(rows[0].Value<bool>("execution_settled_after_delivery"));
        }

        [Test]
        public void QueuedCancellationDoesNotInventHandlerOrSendDuration()
        {
            long now = 0;
            JObject row = null;
            var timing = new CommandPhaseTiming(() => now, 1000, value => row = value, CommandPhaseTiming.Transport.Direct);
            timing.Start(CommandPhaseTiming.Phase.Queue);
            now = 7;
            timing.ExecutionDone();
            timing.DeliveryDone(CommandPhaseTiming.Delivery.Unobserved);
            Assert.IsNull(row["handler_ms"]);
            Assert.IsNull(row["send_ms"]);
            CollectionAssert.Contains(row["incomplete_stages"].Values<string>(), "queue");
            Assert.AreEqual(64, row.Value<int>("sample_every"));
            Assert.AreEqual("phase_timing", row.Value<string>("event"));
        }

        [Test]
        public void ClockOrSinkFailureCannotEscapeTheDiagnosticBoundary()
        {
            int sinkCalls = 0;
            var brokenClock = new CommandPhaseTiming(
                () => throw new InvalidOperationException("private clock"),
                1000,
                _ => sinkCalls++,
                CommandPhaseTiming.Transport.Direct
            );
            Assert.DoesNotThrow(() =>
            {
                brokenClock.Start(CommandPhaseTiming.Phase.Handler);
                brokenClock.Stop(CommandPhaseTiming.Phase.Handler);
                brokenClock.ExecutionDone();
                brokenClock.DeliveryDone(CommandPhaseTiming.Delivery.Failed);
            });
            Assert.AreEqual(0, sinkCalls);
            var brokenSink = new CommandPhaseTiming(
                () => 0,
                1000,
                _ => throw new InvalidOperationException("private sink"),
                CommandPhaseTiming.Transport.Direct
            );
            Assert.DoesNotThrow(() =>
            {
                brokenSink.ExecutionDone();
                brokenSink.DeliveryDone(CommandPhaseTiming.Delivery.Sent);
            });
        }

        [Test]
        public void ConcurrentTerminalOwnersEmitExactlyOneBoundedRecord()
        {
            int emissions = 0;
            var timing = new CommandPhaseTiming(
                () => 0,
                1000,
                _ => System.Threading.Interlocked.Increment(ref emissions),
                CommandPhaseTiming.Transport.WebSocket
            );
            System.Threading.Tasks.Parallel.Invoke(
                () =>
                {
                    for (int i = 0; i < 10; i++)
                        timing.ExecutionDone();
                },
                () =>
                {
                    for (int i = 0; i < 10; i++)
                        timing.DeliveryDone(CommandPhaseTiming.Delivery.Sent);
                }
            );
            Assert.AreEqual(1, emissions);
        }

        [Test]
        public void BackwardClockCannotPublishMisleadingDuration()
        {
            long now = 10;
            int emissions = 0;
            var timing = new CommandPhaseTiming(() => now, 1000, _ => emissions++, CommandPhaseTiming.Transport.Direct);
            timing.Start(CommandPhaseTiming.Phase.Handler);
            now = 9;
            timing.Stop(CommandPhaseTiming.Phase.Handler);
            timing.ExecutionDone();
            timing.DeliveryDone(CommandPhaseTiming.Delivery.Failed);
            Assert.AreEqual(0, emissions);
        }
    }
}
