using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioEventTests
    {
        private const string Id = "play07.owned";
        private readonly List<IDisposable> tokens = new List<IDisposable>();
        private readonly object owner = new object();

        private sealed class Host : IPlayScenarioHost
        {
            public int Releases;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll) => new PlayScenarioObservation(true, "owned host ready");

            public void Release() => Releases++;
        }

        private static Type Registry
        {
            get
            {
                Type type = typeof(PlayScenarioDefinition).Assembly.GetType("MCPForUnity.Runtime.PlayScenarios.PlayScenarioEventRegistry");
                Assert.That(type, Is.Not.Null, "The bounded token-owned event facility must exist.");
                return type;
            }
        }

        private IDisposable Register(string id = Id, object source = null)
        {
            var token = (IDisposable)Registry.GetMethod("Register").Invoke(null, new[] { (object)id, source ?? owner });
            tokens.Add(token);
            return token;
        }

        private static void Emit(IDisposable token, string name) => token.GetType().GetMethod("Emit").Invoke(token, new object[] { name });

        [TearDown]
        public void ReleaseOwnedTokens()
        {
            foreach (IDisposable token in tokens)
                token.Dispose();
            tokens.Clear();
        }

        private static JObject Mark(string name = "fence") =>
            new JObject
            {
                ["name"] = name,
                ["action"] = "mark_events",
                ["event_id"] = Id,
                ["event_mark"] = name,
                ["timeout_seconds"] = 1,
            };

        private static JObject Count(int count = 1, string mark = "fence") =>
            new JObject
            {
                ["name"] = "count",
                ["action"] = "wait_events",
                ["event_since"] = mark,
                ["event_name"] = "hit",
                ["event_count"] = count,
                ["timeout_seconds"] = 1,
            };

        private static JObject Order(params string[] names) =>
            new JObject
            {
                ["name"] = "order",
                ["action"] = "wait_events",
                ["event_since"] = "fence",
                ["event_order"] = new JArray(names),
                ["timeout_seconds"] = 1,
            };

        private static JObject Definition(params JObject[] steps) =>
            new JObject
            {
                ["name"] = "event-proof",
                ["completion_stable_ms"] = 0,
                ["poll_interval_ms"] = 100,
                ["setup_steps"] = new JArray(
                    new JObject
                    {
                        ["name"] = "load",
                        ["action"] = "load_scene",
                        ["scene"] = "Assets/Menu.unity",
                    }
                ),
                ["steps"] = new JArray(steps),
            };

        private static PlayScenarioEngine Engine(JObject json, Host host = null, int repeats = 1)
        {
            var engine = new PlayScenarioEngine(
                PlayScenarioEngine.Create(PlayScenarioDefinition.Parse(json), new string('e', 32), repeats, 30, 1000),
                host ?? new Host()
            );
            engine.EnteredPlayMode();
            return engine;
        }

        private static void Begin(PlayScenarioEngine engine)
        {
            engine.Tick(1000, true); // setup load
            engine.Tick(1001, true); // mark
            engine.Tick(1002, true); // first wait observation
        }

        private static void Finish(PlayScenarioEngine engine, long start = 1300)
        {
            for (long now = start; engine.Running && now < 8000; now += 100)
                engine.Tick(now, true);
        }

        private static JObject Evidence(PlayScenarioEngine engine, int index = 2) => (JObject)JObject.FromObject(engine.State.Steps[index])["event_evidence"];

        [Test]
        public void PulseBetweenPollsIsRetainedAndReported()
        {
            IDisposable token = Register();
            var host = new Host();
            var engine = Engine(Definition(Mark(), Count()), host);
            Begin(engine);
            bool scalar = true;
            Emit(token, "hit");
            scalar = false;
            Assert.That(scalar, Is.False);
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(host.Releases, Is.EqualTo(1));
            Assert.That((long)Evidence(engine)["match_count"], Is.EqualTo(1));
            Assert.That((long)Evidence(engine)["first_matched_sequence"], Is.EqualTo(1));
            Assert.That(Evidence(engine).ToString(Formatting.None).Length, Is.LessThanOrEqualTo(1024));
        }

        [Test]
        public void PollsDoNotRecountOneOccurrence()
        {
            IDisposable token = Register();
            var engine = Engine(Definition(Mark(), Count(2)));
            Begin(engine);
            Emit(token, "hit");
            engine.Tick(1200, true);
            engine.Tick(1400, true);
            Assert.That((int)Evidence(engine)["match_count"], Is.EqualTo(1));
            Emit(token, "hit");
            Finish(engine, 1500);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That((long)Evidence(engine)["last_matched_sequence"], Is.EqualTo(2));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OrderRequiresDistinctIncreasingPositions(bool complete)
        {
            IDisposable token = Register();
            var engine = Engine(Definition(Mark(), Order("start", "hit", "hit")));
            Begin(engine);
            Emit(token, "hit");
            Emit(token, "start");
            Emit(token, "noise");
            Emit(token, "hit");
            if (complete)
                Emit(token, "hit");
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo(complete ? "succeeded" : "timed_out"));
            Assert.That((JArray)Evidence(engine)["matched_sequences"], Is.EqualTo(new JArray(complete ? new long[] { 2, 4, 5 } : new long[] { 2, 4 })));
            if (!complete)
                Assert.That(engine.State.Failure.Code, Is.EqualTo("condition_unmet"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SuccessfulSnapshotCannotBeReusedThroughOlderMark(bool alias)
        {
            IDisposable token = Register();
            var steps = alias ? new[] { Mark(), Mark("alias"), Count(), Count(1, "alias") } : new[] { Mark(), Count(), Count() };
            var engine = Engine(Definition(steps));
            engine.Tick(1000, true);
            engine.Tick(1001, true);
            if (alias)
                engine.Tick(1002, true);
            Emit(token, "hit");
            Emit(token, "hit");
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("condition_unmet"));
            var first = engine.State.Steps.First(step => step.Name == "count");
            Assert.That(first.Status, Is.EqualTo("passed"));
        }

        [Test]
        public void OverflowFailsOnlyForUnobservedGapAfterFence()
        {
            IDisposable token = Register();
            for (int index = 0; index < 200; index++)
                Emit(token, "old");
            var engine = Engine(Definition(Mark(), Count()));
            Begin(engine);
            Emit(token, "hit");
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That((long)Evidence(engine)["dropped_total"], Is.GreaterThan(0));
            var overflow = Engine(Definition(Mark(), Order("start", "finish")));
            Begin(overflow);
            Emit(token, "start");
            overflow.Tick(1200, true);
            for (int index = 0; index < 129; index++)
                Emit(token, "noise");
            Finish(overflow, 1400);
            Assert.That(overflow.State.Status, Is.EqualTo("failed"));
            Assert.That(overflow.State.Failure.Code, Is.EqualTo("event_journal_overflow"));
            Assert.That((bool)Evidence(overflow)["overflow"], Is.True);
        }

        [Test]
        public void ReleasedOrReplacedSourceNeverRebinds()
        {
            IDisposable token = Register();
            var engine = Engine(Definition(Mark(), Count()));
            Begin(engine);
            token.Dispose();
            IDisposable replacement = Register();
            Emit(replacement, "hit");
            Finish(engine);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("event_stream_unavailable"));
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
        }

        [Test]
        public void ReconstructedEngineCannotInventPriorTransientMark()
        {
            IDisposable token = Register();
            var original = Engine(Definition(Mark(), Count()));
            Begin(original);
            Emit(token, "hit");
            var resumed = new PlayScenarioEngine(JsonConvert.DeserializeObject<PlayScenarioRun>(JsonConvert.SerializeObject(original.State)), new Host());
            Finish(resumed);
            Assert.That(resumed.State.Failure.Code, Is.EqualTo("event_history_unavailable"));
        }

        [Test]
        public void MissingSourceUsesExistingTimeoutSemantics()
        {
            Assert.That(Registry, Is.Not.Null);
            var engine = Engine(Definition(Mark(), Count()));
            Finish(engine, 1000);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("condition_unmet"));
        }

        [Test]
        public void RepeatsRequireNewEventsAfterNewFence()
        {
            IDisposable token = Register();
            var engine = Engine(Definition(Mark(), Count()), repeats: 2);
            Begin(engine);
            Emit(token, "hit");
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(engine.State.Steps.First(step => step.Iteration == 1 && step.Name == "count").Status, Is.EqualTo("passed"));
            Assert.That(engine.State.Steps.First(step => step.Iteration == 2 && step.Name == "count").Status, Is.EqualTo("timed_out"));
        }

        [Test]
        public void DuplicateIdsWorkerEmissionAndInvalidNamesAreRefused()
        {
            IDisposable token = Register();
            Assert.Throws<TargetInvocationException>(() => Register());
            Assert.Throws<TargetInvocationException>(() => Emit(token, new string('a', 65)));
            Exception worker = Task.Run(() =>
                {
                    try
                    {
                        Emit(token, "hit");
                        return null;
                    }
                    catch (Exception error)
                    {
                        return error;
                    }
                })
                .GetAwaiter()
                .GetResult();
            Assert.That(worker, Is.TypeOf<TargetInvocationException>());
            var engine = Engine(Definition(Mark(), Count()));
            Begin(engine);
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static WeakReference AbandonToken(object source)
        {
            object token = Registry.GetMethod("Register").Invoke(null, new[] { (object)"play07.abandoned", source });
            return new WeakReference(token);
        }

        [Test]
        public void RegistryDoesNotKeepAbandonedTokenAlive()
        {
            WeakReference weak = AbandonToken(owner);
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
            Assert.That(weak.IsAlive, Is.False, "Registry entries must not own producer tokens strongly.");
            Assert.That(Register("play07.abandoned"), Is.Not.Null);
        }

        [Test]
        public void DestroyedUnityOwnerCannotSupplyOldOrReplacementEvents()
        {
            var source = new UnityEngine.GameObject("PLAY07-owned-source");
            try
            {
                IDisposable token = Register(source: source);
                var engine = Engine(Definition(Mark(), Count()));
                Begin(engine);
                UnityEngine.Object.DestroyImmediate(source);
                Assert.Throws<TargetInvocationException>(() => Emit(token, "hit"));
                Register();
                Finish(engine);
                Assert.That(engine.State.Failure.Code, Is.EqualTo("event_stream_unavailable"));
            }
            finally
            {
                if (source != null)
                    UnityEngine.Object.DestroyImmediate(source);
            }
        }

        private sealed class Counter : MCPForUnity.Runtime.PlayScenarios.IPlayScenarioStateProvider
        {
            public bool TryRead(out MCPForUnity.Runtime.PlayScenarios.PlayScenarioStateValue value)
            {
                value = MCPForUnity.Runtime.PlayScenarios.PlayScenarioStateValue.FromInteger(2);
                return true;
            }
        }

        [Test]
        public void ExistingScalarCounterAndNumericPredicateRemainAnAlternative()
        {
            var source = new Counter();
            using (MCPForUnity.Runtime.PlayScenarios.PlayScenarioStateRegistry.Register("play07.counter", source))
            {
                var engine = Engine(
                    Definition(
                        new JObject
                        {
                            ["name"] = "counter",
                            ["action"] = "wait_state",
                            ["state_id"] = "play07.counter",
                            ["state_compare"] = new JObject { ["operator"] = "gte", ["value"] = 1 },
                        }
                    )
                );
                Finish(engine, 1000);
                Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
                Assert.That(JObject.FromObject(engine.State.Steps[1]).Property("event_evidence"), Is.Null);
            }
        }

        [Test]
        public void RegistrationCapacityAndRingCapacityAreEnforced()
        {
            IDisposable token = Register();
            for (int index = 1; index < 32; index++)
                Register("play07.capacity" + index);
            Assert.Throws<TargetInvocationException>(() => Register("play07.excess"));
            var engine = Engine(Definition(Mark(), Count(128)));
            Begin(engine);
            for (int index = 0; index < 128; index++)
                Emit(token, "hit");
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That((int)Evidence(engine)["match_count"], Is.EqualTo(128));
        }

        [Test]
        public void SequenceExhaustionCannotWrapOrDuplicate()
        {
            IDisposable token = Register();
            FieldInfo field = token.GetType().GetField("entry", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(field, Is.Not.Null);
            object entry = field.GetValue(token);
            entry.GetType().GetField("Sequence", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(entry, long.MaxValue);
            Assert.Throws<TargetInvocationException>(() => Emit(token, "hit"));
            var engine = Engine(Definition(Mark(), Count()));
            Finish(engine, 1000);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("event_sequence_exhausted"));
        }

        [Test]
        public void MaximumOrderProofIsBoundedWithLargestInt64SequenceNumbers()
        {
            IDisposable token = Register();
            object entry = token.GetType().GetField("entry", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(token);
            entry.GetType().GetField("Sequence", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(entry, long.MaxValue - 16);
            var engine = Engine(Definition(Mark(), Order(Enumerable.Repeat("hit", 16).ToArray())));
            Begin(engine);
            for (int index = 0; index < 16; index++)
                Emit(token, "hit");
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That((long)Evidence(engine)["observed_through_sequence"], Is.EqualTo(long.MaxValue));
            Assert.That(((JArray)Evidence(engine)["matched_sequences"]).Count, Is.EqualTo(16));
            Assert.That(System.Text.Encoding.UTF8.GetByteCount(Evidence(engine).ToString(Formatting.None)), Is.LessThanOrEqualTo(1024));
            string directory = Path.Combine(Path.GetTempPath(), "mcp-play07-store-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = new PlayScenarioStore(directory);
                store.SaveReport(engine.State);
                var restored = store.GetReport(engine.State.JobId);
                Assert.That(JToken.DeepEquals(JObject.FromObject(restored.Steps[2])["event_evidence"], Evidence(engine)), Is.True);
            }
            finally
            {
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
            }
        }

        [Test]
        public void DefinitionAndReportRoundTripKeepOnlyAuthoredFields()
        {
            IDisposable token = Register();
            var definition = PlayScenarioDefinition.Parse(Definition(Mark(), Count()));
            var serializer = JsonSerializer.Create(new JsonSerializerSettings { NullValueHandling = NullValueHandling.Include });
            JObject encoded = JObject.FromObject(definition, serializer);
            Assert.That(((JObject)encoded["steps"][0]).Property("event_since"), Is.Null);
            Assert.That(((JObject)encoded["steps"][1]).Property("event_order"), Is.Null);
            var engine = Engine(Definition(Mark(), Count()));
            Begin(engine);
            Emit(token, "hit");
            Finish(engine);
            JObject report = JObject.Parse(JsonConvert.SerializeObject(engine.State));
            var roundTrip = JsonConvert.DeserializeObject<PlayScenarioRun>(report.ToString());
            Assert.That(JToken.DeepEquals(JObject.FromObject(roundTrip.Steps[2])["event_evidence"], Evidence(engine)), Is.True);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RealStoreRoundTripAcceptsLegacyAndBoundedEventProof(bool events)
        {
            string directory = Path.Combine(Path.GetTempPath(), "mcp-play07-store-" + Guid.NewGuid().ToString("N"));
            try
            {
                PlayScenarioEngine engine;
                if (events)
                {
                    IDisposable token = Register();
                    engine = Engine(Definition(Mark(), Count()));
                    Begin(engine);
                    Emit(token, "hit");
                    Finish(engine);
                }
                else
                {
                    engine = Engine(
                        Definition(
                            new JObject
                            {
                                ["name"] = "legacy",
                                ["action"] = "wait_scene",
                                ["scene"] = "Assets/Menu.unity",
                            }
                        )
                    );
                    Finish(engine, 1000);
                }
                Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
                var store = new PlayScenarioStore(directory);
                store.Save(engine.State.Scenario);
                var restored = store.Get(engine.State.Scenario.Name);
                var serializer = JsonSerializer.Create(new JsonSerializerSettings { NullValueHandling = NullValueHandling.Include });
                JObject definition = JObject.FromObject(restored, serializer);
                Assert.That(((JObject)definition["setup_steps"][0]).Property("event_since"), Is.Null);
                store.SaveReport(engine.State);
                PlayScenarioRun report = store.GetReport(engine.State.JobId);
                Assert.That(report.Status, Is.EqualTo("succeeded"));
                JObject actual = JObject.FromObject(report);
                Assert.That(File.Exists(Path.Combine(directory, engine.State.ReportPath)), Is.True);
                if (events)
                    Assert.That(JToken.DeepEquals(actual["steps"][2]["event_evidence"], Evidence(engine)), Is.True);
                else
                    Assert.That(((JObject)actual["steps"][1]).Property("event_evidence"), Is.Null);
            }
            finally
            {
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
            }
        }

        [TestCase("version")]
        [TestCase("boolean_sequence")]
        [TestCase("float_sequence")]
        [TestCase("overflowing_sequence")]
        [TestCase("null_count")]
        [TestCase("negative_count")]
        [TestCase("large_count")]
        [TestCase("bad_generation")]
        [TestCase("too_many_positions")]
        [TestCase("boolean_position")]
        [TestCase("duplicate_positions")]
        [TestCase("overflowing_endpoint_span")]
        [TestCase("unknown_field")]
        public void RealStoreRejectsMalformedOrUnboundedProof(string invalid)
        {
            IDisposable token = Register();
            var engine = Engine(Definition(Mark(), Count()));
            Begin(engine);
            Emit(token, "hit");
            Finish(engine);
            string directory = Path.Combine(Path.GetTempPath(), "mcp-play07-store-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = new PlayScenarioStore(directory);
                store.SaveReport(engine.State); // Establish a valid real file before corrupting only optional proof.
                string path = Path.Combine(directory, engine.State.ReportPath);
                JObject report = JObject.Parse(File.ReadAllText(path));
                var evidence = (JObject)report["steps"][2]["event_evidence"];
                if (invalid == "version")
                    evidence["schema_version"] = 2;
                if (invalid == "boolean_sequence")
                    evidence["since_sequence"] = false;
                if (invalid == "float_sequence")
                    evidence["since_sequence"] = 0.0;
                if (invalid == "overflowing_sequence")
                    evidence["observed_through_sequence"] = JToken.Parse("9223372036854775808");
                if (invalid == "null_count")
                    evidence["match_count"] = JValue.CreateNull();
                if (invalid == "negative_count")
                    evidence["match_count"] = -1;
                if (invalid == "large_count")
                    evidence["match_count"] = 129;
                if (invalid == "bad_generation")
                    evidence["generation"] = new string('z', 2048);
                if (invalid == "too_many_positions")
                    evidence["matched_sequences"] = new JArray(Enumerable.Range(1, 17));
                if (invalid == "boolean_position")
                    evidence["matched_sequences"] = new JArray(true);
                if (invalid == "duplicate_positions")
                {
                    evidence["match_count"] = 2;
                    evidence["matched_sequences"] = new JArray(1, 1);
                }
                if (invalid == "unknown_field")
                    evidence["unowned"] = true;
                if (invalid == "overflowing_endpoint_span")
                    evidence["last_matched_sequence"] = long.MinValue;
                File.WriteAllText(path, report.ToString(Formatting.None));
                Assert.Throws<ArgumentException>(() => store.GetReport(engine.State.JobId));
            }
            finally
            {
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
            }
        }

        [TestCase("forward")]
        [TestCase("cross_stage")]
        [TestCase("conflict")]
        [TestCase("null")]
        [TestCase("bool")]
        [TestCase("too_many")]
        public void StrictSchemaRejectsInvalidEventDefinitions(string invalid)
        {
            JObject value = Definition(Mark(), Count());
            if (invalid == "forward")
                value["steps"] = new JArray(Count(), Mark());
            if (invalid == "cross_stage")
            {
                ((JArray)value["setup_steps"]).Add(Mark());
                value["steps"] = new JArray(Count());
            }
            if (invalid == "conflict")
                value["steps"][1]["event_order"] = new JArray("hit");
            if (invalid == "null")
                value["steps"][1]["event_name"] = JValue.CreateNull();
            if (invalid == "bool")
                value["steps"][1]["event_count"] = true;
            if (invalid == "too_many")
                value["steps"] = new JArray(Enumerable.Range(0, 17).Select(index => Mark("mark" + index)));
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
        }

        private sealed class Pulse : MCPForUnity.Runtime.PlayScenarios.IPlayScenarioStateProvider
        {
            public bool Value;
            public readonly List<bool> Reads = new List<bool>(16);

            public bool TryRead(out MCPForUnity.Runtime.PlayScenarios.PlayScenarioStateValue value)
            {
                if (Reads.Count < 16)
                    Reads.Add(Value);
                value = MCPForUnity.Runtime.PlayScenarios.PlayScenarioStateValue.FromBoolean(Value);
                return true;
            }
        }

        [UnityTest]
        public IEnumerator ActualEditorUpdatePulseSurvivesBetweenScheduledPolls()
        {
            IDisposable token = Register();
            var engine = Engine(Definition(Mark(), Count()));
            Begin(engine);
            var scalar = new Pulse();
            IDisposable scalarToken = null;
            PlayScenarioEngine scalarEngine = null;
            int updates = 0;
            EditorApplication.CallbackFunction callback = () =>
            {
                updates++;
                if (updates == 1)
                {
                    scalar.Value = true;
                    Emit(token, "hit");
                    scalar.Value = false;
                }
            };
            try
            {
                scalarToken = MCPForUnity.Runtime.PlayScenarios.PlayScenarioStateRegistry.Register("play07.pulse", scalar);
                scalarEngine = Engine(
                    Definition(
                        new JObject
                        {
                            ["name"] = "scalar",
                            ["action"] = "wait_state",
                            ["state_id"] = "play07.pulse",
                            ["state_equals"] = true,
                            ["timeout_seconds"] = 1,
                        }
                    )
                );
                scalarEngine.Tick(1000, true);
                scalarEngine.Tick(1002, true);
                Assert.That(scalar.Reads, Is.EqualTo(new[] { false }));
                EditorApplication.update += callback;
                for (int attempt = 0; updates == 0 && attempt < 30; attempt++)
                    yield return null;
                Assert.That(updates, Is.GreaterThan(0));
                Assert.That(scalar.Value, Is.False);
                Assert.That(engine.State.Steps[2].PollCount, Is.EqualTo(1));
                Finish(engine);
                Finish(scalarEngine);
                Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
                Assert.That(scalarEngine.State.Status, Is.EqualTo("timed_out"));
                Assert.That(scalar.Reads.Count, Is.GreaterThanOrEqualTo(2));
                Assert.That(scalar.Reads.All(value => !value), Is.True);
                TestContext.Out.WriteLine(
                    "PLAY07_EVENT_UPDATE "
                        + new JObject
                        {
                            ["updates"] = updates,
                            ["scalar_reads"] = new JArray(scalar.Reads),
                            ["scalar_status"] = scalarEngine.State.Status,
                            ["poll_count"] = engine.State.Steps[2].PollCount,
                            ["evidence"] = Evidence(engine),
                        }.ToString(Formatting.None)
                );
            }
            finally
            {
                EditorApplication.update -= callback;
                scalarToken?.Dispose();
                scalarEngine?.Release();
                engine.Release();
            }
        }
    }
}
