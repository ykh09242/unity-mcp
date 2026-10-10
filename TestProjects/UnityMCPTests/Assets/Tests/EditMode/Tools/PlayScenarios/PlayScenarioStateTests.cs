using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Threading;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public sealed class PlayScenarioStateTests
    {
        private sealed class Provider : IPlayScenarioStateProvider
        {
            public PlayScenarioStateValue Value = PlayScenarioStateValue.FromBoolean(true);
            public bool Ready = true;
            public bool Throw;
            public int Reads;
            public Action DuringRead;

            public bool TryRead(out PlayScenarioStateValue value)
            {
                Reads++;
                DuringRead?.Invoke();
                if (Throw)
                    throw new InvalidOperationException("provider read failed");
                value = Value;
                return Ready;
            }
        }

        public sealed class NativeProvider : MonoBehaviour, IPlayScenarioStateProvider
        {
            public bool TryRead(out PlayScenarioStateValue value)
            {
                value = PlayScenarioStateValue.FromBoolean(true);
                return true;
            }
        }

        private sealed class Host : IPlayScenarioHost, IPlayScenarioQueryHost
        {
            public int Calls;
            public int Releases;
            public Action<PlayScenarioStep> DuringEvaluation;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool first)
            {
                Calls++;
                DuringEvaluation?.Invoke(step);
                return new PlayScenarioObservation(true, "ready");
            }

            public PlayScenarioQueryCounts CaptureQueryCounts() => default;

            public void Release() => Releases++;
        }

        private readonly List<IDisposable> _tokens = new List<IDisposable>();
        private readonly List<UnityEngine.Object> _objects = new List<UnityEngine.Object>();
        private readonly List<string> _directories = new List<string>();

        [SetUp]
        public void SetUp() => PlayScenarioStateRegistry.ResetOnPlayEntry();

        [TearDown]
        public void TearDown()
        {
            foreach (IDisposable token in _tokens)
                token.Dispose();
            foreach (UnityEngine.Object resource in _objects)
                if (resource != null)
                    UnityEngine.Object.DestroyImmediate(resource);
            foreach (string directory in _directories)
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
        }

        private IDisposable Register(string id, IPlayScenarioStateProvider provider)
        {
            IDisposable token = PlayScenarioStateRegistry.Register(id, provider);
            _tokens.Add(token);
            return token;
        }

        private static JObject Json(string id = "state.ready") =>
            new JObject
            {
                ["name"] = "state-probe",
                ["poll_interval_ms"] = 100,
                ["completion_stable_ms"] = 0,
                ["query_budget"] = new JObject
                {
                    ["enabled"] = true,
                    ["max_target_searches"] = 0,
                    ["max_hierarchy_visits"] = 0,
                },
                ["setup_steps"] = new JArray(
                    new JObject
                    {
                        ["name"] = "load",
                        ["action"] = "load_scene",
                        ["scene"] = "Assets/Menu.unity",
                    }
                ),
                ["steps"] = new JArray(
                    new JObject
                    {
                        ["name"] = "state",
                        ["action"] = "wait_state",
                        ["state_id"] = id,
                        ["state_equals"] = true,
                        ["timeout_seconds"] = 2,
                    }
                ),
                ["cleanup_steps"] = new JArray(
                    new JObject
                    {
                        ["name"] = "cleanup",
                        ["action"] = "wait_scene",
                        ["scene"] = "Assets/Menu.unity",
                    }
                ),
            };

        private static PlayScenarioEngine Engine(Host host, JObject json = null, int repeats = 1)
        {
            var engine = new PlayScenarioEngine(
                PlayScenarioEngine.Create(PlayScenarioDefinition.Parse(json ?? Json()), new string('c', 32), repeats, 30, 1000),
                host
            );
            engine.EnteredPlayMode();
            return engine;
        }

        private static void Finish(PlayScenarioEngine engine, long from = 1000)
        {
            for (long now = from; now < from + 10000 && engine.Running; now += 100)
                engine.Tick(now, true);
        }

        [Test]
        public void MissingProviderCanRegisterLaterAndIsReadOnlyOncePerScheduledEvaluation()
        {
            var host = new Host();
            var engine = Engine(host);
            engine.Tick(1000, true);
            engine.Tick(1100, true);
            Assert.That(engine.State.Steps.Single(step => step.Stage == "main").Status, Is.EqualTo("running"));
            var provider = new Provider { Ready = false };
            Register("state.ready", provider);
            engine.Tick(1200, true);
            Assert.That(provider.Reads, Is.EqualTo(1));
            for (long now = 1201; now < 1300; now++)
            {
                JObject.FromObject(engine.State);
                engine.Tick(now, true);
            }
            Assert.That(provider.Reads, Is.EqualTo(1));
            engine.Tick(1300, false);
            Assert.That(provider.Reads, Is.EqualTo(1));
            provider.Ready = true;
            Finish(engine, 1400);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(provider.Reads, Is.EqualTo(2));
            Assert.That(engine.State.QueryCounts.TargetSearches, Is.Zero);
            Assert.That(engine.State.QueryCounts.HierarchyVisits, Is.Zero);
            Assert.That(host.Calls, Is.EqualTo(2));
        }

        [Test]
        public void FalseReadinessOrMismatchedValueRestartsTheStableWindow()
        {
            var provider = new Provider();
            Register("state.ready", provider);
            JObject json = Json();
            json["steps"][0]["stable_for_ms"] = 250;
            var engine = Engine(new Host(), json);
            engine.Tick(1000, true);
            engine.Tick(1100, true);
            engine.Tick(1200, true);
            provider.Value = PlayScenarioStateValue.FromBoolean(false);
            engine.Tick(1300, true);
            Assert.That(engine.State.Steps.Single(step => step.Stage == "main").StableSinceUnixMs, Is.Null);
            provider.Value = PlayScenarioStateValue.FromBoolean(true);
            engine.Tick(1400, true);
            provider.Ready = false;
            engine.Tick(1500, true);
            Assert.That(engine.State.Steps.Single(step => step.Stage == "main").StableSinceUnixMs, Is.Null);
            provider.Ready = true;
            engine.Tick(1600, true);
            engine.Tick(1700, true);
            engine.Tick(1800, true);
            Assert.That(engine.State.Steps.Single(step => step.Stage == "main").Status, Is.EqualTo("running"));
            engine.Tick(1900, true);
            Finish(engine, 2000);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(provider.Reads, Is.EqualTo(9));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void BoundProviderDisposalOrReplacementFailsAndStillRunsCleanup(bool replace)
        {
            var original = new Provider { Ready = false };
            IDisposable token = Register("state.ready", original);
            var host = new Host();
            var engine = Engine(host);
            engine.Tick(1000, true);
            engine.Tick(1100, true);
            token.Dispose();
            var replacement = new Provider();
            if (replace)
                Register("state.ready", replacement);
            Finish(engine, 1200);
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("state_provider_unavailable"));
            Assert.That(original.Reads, Is.EqualTo(1));
            Assert.That(replacement.Reads, Is.Zero);
            Assert.That(engine.State.Steps.Single(step => step.Stage == "cleanup").Status, Is.EqualTo("passed"));
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void ProviderDisposalDuringReadCannotReturnAFalseSuccess()
        {
            var provider = new Provider();
            IDisposable token = Register("state.ready", provider);
            provider.DuringRead = () => token.Dispose();
            var engine = Engine(new Host());
            Finish(engine);
            Assert.That(provider.Reads, Is.EqualTo(1));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("state_provider_unavailable"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExceptionsAndDefaultScalarValuesFailExplicitly(bool exception)
        {
            var provider = new Provider { Throw = exception, Value = default };
            Register("state.ready", provider);
            var engine = Engine(new Host());
            Finish(engine);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("state_provider_error"));
            Assert.That(engine.State.Failure.Target, Is.EqualTo("state.ready"));
            Assert.That(provider.Reads, Is.EqualTo(1));
            Assert.That(engine.State.Steps.Single(step => step.Stage == "cleanup").Status, Is.EqualTo("passed"));
            RoundTrip(engine.State);
        }

        [Test]
        public void MissingProviderAndMismatchedStateTimeOutWithTypedObservations()
        {
            var missing = Engine(new Host(), Json("state.not-registered"));
            Finish(missing);
            Assert.That(missing.State.Status, Is.EqualTo("timed_out"));
            Assert.That(missing.State.Failure.Code, Is.EqualTo("condition_unmet"));
            Assert.That(missing.State.Failure.Actual, Is.EqualTo("provider not registered"));
            var provider = new Provider { Value = PlayScenarioStateValue.FromInteger(1) };
            Register("state.ready", provider);
            var mismatch = Engine(new Host());
            Finish(mismatch);
            Assert.That(mismatch.State.Status, Is.EqualTo("timed_out"));
            Assert.That(mismatch.State.Failure.Expected, Is.EqualTo("true"));
            Assert.That(mismatch.State.Failure.Actual, Is.EqualTo("1"));
        }

        [Test]
        public void RepeatsAndCleanupAcquireFreshSnapshotsWithoutKeepingPreviousProviders()
        {
            var first = new Provider();
            var second = new Provider();
            var cleanup = new Provider();
            IDisposable firstToken = Register("state.ready", first);
            Register("state.cleanup", cleanup);
            JObject json = Json();
            json["cleanup_steps"] = new JArray(
                new JObject
                {
                    ["name"] = "cleanup-state",
                    ["action"] = "wait_state",
                    ["state_id"] = "state.cleanup",
                    ["state_equals"] = true,
                }
            );
            int loads = 0;
            var host = new Host();
            host.DuringEvaluation = step =>
            {
                if (step.Action == "load_scene" && ++loads == 2)
                {
                    firstToken.Dispose();
                    Register("state.ready", second);
                }
            };
            var engine = Engine(host, json, 2);
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(first.Reads, Is.EqualTo(1));
            Assert.That(second.Reads, Is.EqualTo(1));
            Assert.That(cleanup.Reads, Is.EqualTo(2));
            Assert.That(host.Calls, Is.EqualTo(2));
        }

        [Test]
        public void CancellationDuringReadonlyCleanupKeepsTheOriginalProbeUntilCompletion()
        {
            var provider = new Provider();
            var cleanup = new Provider { Ready = false };
            Register("state.ready", provider);
            Register("state.cleanup", cleanup);
            JObject json = Json();
            json["cleanup_steps"] = new JArray(
                new JObject
                {
                    ["name"] = "cleanup-state",
                    ["action"] = "wait_state",
                    ["state_id"] = "state.cleanup",
                    ["state_equals"] = true,
                }
            );
            var engine = Engine(new Host(), json);
            engine.Tick(1000, true);
            engine.Tick(1100, true);
            engine.Tick(1200, true);
            engine.Tick(1300, true);
            engine.Cancel(1350);
            cleanup.Ready = true;
            Finish(engine, 1400);
            Assert.That(engine.State.Status, Is.EqualTo("cancelled"));
            Assert.That(cleanup.Reads, Is.EqualTo(2));
            Assert.That(engine.State.Steps.Single(step => step.Stage == "cleanup").Status, Is.EqualTo("passed"));
        }

        [Test]
        public void RestoredActiveProbeFailsWithoutRebindingOrReplayingAProviderRead()
        {
            var original = new Provider { Ready = false };
            IDisposable token = Register("state.ready", original);
            var engine = Engine(new Host());
            engine.Tick(1000, true);
            engine.Tick(1100, true);
            PlayScenarioRun restored = JObject.FromObject(engine.State).ToObject<PlayScenarioRun>();
            engine.Release();
            token.Dispose();
            var replacement = new Provider();
            Register("state.ready", replacement);
            var resumed = new PlayScenarioEngine(restored, new Host());
            Finish(resumed, 1200);
            Assert.That(resumed.State.Failure.Code, Is.EqualTo("state_provider_unavailable"));
            Assert.That(original.Reads, Is.EqualTo(1));
            Assert.That(replacement.Reads, Is.Zero);
        }

        [Test]
        public void ProviderFailureDuringCleanupPreservesThePrimaryStateFailure()
        {
            var primary = new Provider { Value = default };
            var cleanup = new Provider { Throw = true };
            Register("state.ready", primary);
            Register("state.cleanup", cleanup);
            JObject json = Json();
            json["cleanup_steps"] = new JArray(
                new JObject
                {
                    ["name"] = "cleanup-state",
                    ["action"] = "wait_state",
                    ["state_id"] = "state.cleanup",
                    ["state_equals"] = true,
                }
            );
            var engine = Engine(new Host(), json);
            Finish(engine);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("state_provider_error"));
            Assert.That(engine.State.Failure.Target, Is.EqualTo("state.ready"));
            Assert.That(engine.State.Failure.Message, Does.Contain("invalid scalar"));
            Assert.That(engine.State.CleanupFailures.Single().Target, Is.EqualTo("state.cleanup"));
            Assert.That(primary.Reads, Is.EqualTo(1));
            Assert.That(cleanup.Reads, Is.EqualTo(1));
            RoundTrip(engine.State);
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private WeakReference RegisterBoundWeak(out PlayScenarioStateRegistry.Snapshot snapshot)
        {
            var provider = new Provider();
            Register("state.weak", provider);
            snapshot = PlayScenarioStateRegistry.Resolve("state.weak");
            Assert.That(snapshot.TryRead(out _, out _), Is.True);
            return new WeakReference(provider);
        }

        [Test]
        public void RegistrationAndBoundSnapshotDoNotExtendProviderLifetime()
        {
            WeakReference weak = RegisterBoundWeak(out PlayScenarioStateRegistry.Snapshot snapshot);
            for (int index = 0; index < 5 && weak.IsAlive; index++)
            {
                GC.Collect();
                GC.WaitForPendingFinalizers();
                GC.Collect();
            }
            Assert.That(weak.IsAlive, Is.False);
            var error = Assert.Throws<PlayScenarioException>(() => snapshot.TryRead(out _, out _));
            Assert.That(error.Failure.Code, Is.EqualTo("state_provider_unavailable"));
        }

        [Test]
        public void OffThreadRegistrationAndReadsFailBeforeNativeLivenessOrProviderCode()
        {
            var provider = new Provider();
            Register("state.thread", provider);
            var snapshot = PlayScenarioStateRegistry.Resolve("state.thread");
            Exception registrationError = null;
            Exception readError = null;
            var thread = new Thread(() =>
            {
                try
                {
                    PlayScenarioStateRegistry.Register("state.thread.other", provider);
                }
                catch (Exception error)
                {
                    registrationError = error;
                }
                try
                {
                    snapshot.TryRead(out _, out _);
                }
                catch (Exception error)
                {
                    readError = error;
                }
            });
            thread.Start();
            Assert.That(thread.Join(5000), Is.True);
            Assert.That(registrationError, Is.TypeOf<InvalidOperationException>());
            Assert.That(readError, Is.TypeOf<InvalidOperationException>());
            Assert.That(provider.Reads, Is.Zero);
        }

        [Test]
        public void DuplicateAndCapacityChecksKeepExactIdsAndOldTokensCannotRemoveReplacements()
        {
            var providers = new List<Provider>();
            var first = new Provider();
            IDisposable token = Register("state.case", first);
            Assert.Throws<InvalidOperationException>(() => PlayScenarioStateRegistry.Register("state.case", first));
            Register("state.Case", first);
            token.Dispose();
            Register("state.case", first);
            token.Dispose();
            var snapshot = PlayScenarioStateRegistry.Resolve("state.case");
            Assert.That(snapshot.TryRead(out _, out _), Is.True);
            for (int index = 0; index < PlayScenarioStateRegistry.RegistrationLimit - 2; index++)
            {
                var provider = new Provider();
                providers.Add(provider);
                Register("state.capacity." + index, provider);
            }
            Assert.Throws<InvalidOperationException>(() => PlayScenarioStateRegistry.Register("state.overflow", new Provider()));
            GC.KeepAlive(providers);
            GC.KeepAlive(first);
        }

        [Test]
        public void DomainEntryClearsRegistrationsAndInvalidatesBoundSnapshots()
        {
            var provider = new Provider();
            Register("state.domain", provider);
            var snapshot = PlayScenarioStateRegistry.Resolve("state.domain");
            Assert.That(snapshot.TryRead(out _, out _), Is.True);
            PlayScenarioStateRegistry.ResetOnPlayEntry();
            Register("state.domain", provider);
            Assert.Throws<PlayScenarioException>(() => snapshot.TryRead(out _, out _));
        }

        [TestCase("true", "1", false)]
        [TestCase("true", "true", true)]
        [TestCase("1", "1.0", true)]
        [TestCase("1.0", "1", true)]
        [TestCase("1.25", "1", false)]
        [TestCase("9007199254740993", "9007199254740992.0", false)]
        [TestCase("9007199254740992", "9007199254740992.0", true)]
        [TestCase("9223372036854775807", "9223372036854775808.0", false)]
        [TestCase("-9223372036854775808", "-9223372036854775808.0", true)]
        [TestCase("-9223372036854775807", "-9223372036854775808.0", false)]
        [TestCase("0", "-0.0", true)]
        [TestCase("'ready'", "'Ready'", false)]
        [TestCase("'ready'", "'ready'", true)]
        public void ScalarEqualityPreservesExactNumericValuesAndSeparatesBooleanAndStringTypes(string actual, string expected, bool equal)
        {
            PlayScenarioStateValue first = PlayScenarioStateValue.FromJson(JToken.Parse(actual));
            PlayScenarioStateValue second = PlayScenarioStateValue.FromJson(JToken.Parse(expected));
            Assert.That(first.Matches(second), Is.EqualTo(equal));
            Assert.That(second.Matches(first), Is.EqualTo(equal));
        }

        [Test]
        public void ScalarFactoriesRejectNonfiniteNumbersAndInvalidStrings()
        {
            Assert.Throws<ArgumentException>(() => PlayScenarioStateValue.FromNumber(double.NaN));
            Assert.Throws<ArgumentException>(() => PlayScenarioStateValue.FromNumber(double.PositiveInfinity));
            Assert.Throws<ArgumentException>(() => PlayScenarioStateValue.FromNumber(double.NegativeInfinity));
            Assert.Throws<ArgumentException>(() => PlayScenarioStateValue.FromString(null));
            Assert.Throws<ArgumentException>(() => PlayScenarioStateValue.FromString(new string('x', 1025)));
            Assert.That(PlayScenarioStateValue.FromString("").StringValue, Is.Empty);
            Assert.That(PlayScenarioStateValue.FromString(new string('x', 1024)).StringValue.Length, Is.EqualTo(1024));
            Assert.That(default(PlayScenarioStateValue).IsValid, Is.False);
            Assert.That(PlayScenarioStateValue.FromInteger(long.MinValue).IntegerValue, Is.EqualTo(long.MinValue));
            Assert.That(PlayScenarioStateValue.FromInteger(long.MaxValue).IntegerValue, Is.EqualTo(long.MaxValue));
            Assert.Throws<InvalidOperationException>(() => _ = PlayScenarioStateValue.FromBoolean(true).IntegerValue);
        }

        [TestCase("scene")]
        [TestCase("target")]
        [TestCase("target_id")]
        [TestCase("reset_ids")]
        [TestCase("component")]
        [TestCase("property")]
        [TestCase("count")]
        [TestCase("active")]
        [TestCase("click_mode")]
        public void WaitStateRejectsUnrelatedActionFields(string field)
        {
            JObject json = Json();
            json["steps"][0][field] = "unrelated";
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
        }

        [TestCase("null")]
        [TestCase("[]")]
        [TestCase("{}")]
        [TestCase("9223372036854775808")]
        [TestCase("NaN")]
        [TestCase("Infinity")]
        public void WaitStateRejectsInvalidExpectedScalars(string literal)
        {
            JObject json = Json();
            json["steps"][0]["state_equals"] = JToken.Parse(literal);
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
        }

        [Test]
        public void WaitStateDefinitionRequiresExactIdAndScalarAndBoundsStability()
        {
            JObject json = Json();
            ((JObject)json["steps"][0]).Remove("state_equals");
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
            json = Json("bad state id");
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
            json = Json();
            json["steps"][0]["stable_for_ms"] = 2000;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
            json["steps"][0]["stable_for_ms"] = -1;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
            json["steps"][0]["stable_for_ms"] = 0;
            json["steps"][0]["state_equals"] = new string('x', 1025);
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
            json = Json();
            json["setup_steps"][0]["state_id"] = "state.ready";
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
            json = Json();
            PlayScenarioDefinition definition = PlayScenarioDefinition.Parse(json);
            Assert.That(definition.Steps[0].StateId, Is.EqualTo("state.ready"));
            Assert.That(definition.Steps[0].StateEquals.Type, Is.EqualTo(JTokenType.Boolean));
            json["steps"][0]["state_equals"] = false;
            Assert.That(definition.Steps[0].StateEquals.Value<bool>(), Is.True);
        }

        private void RoundTrip(PlayScenarioRun run)
        {
            string directory = Path.Combine(Path.GetTempPath(), "mcp-state-probe-" + Guid.NewGuid().ToString("N"));
            _directories.Add(directory);
            var store = new PlayScenarioStore(directory);
            store.Save(run.Scenario);
            Assert.That(store.Get(run.Scenario.Name).Steps[0].StateEquals.Type, Is.EqualTo(JTokenType.Boolean));
            store.SaveReport(run);
            Assert.That(store.GetReport(run.JobId).Failure.Code, Is.EqualTo(run.Failure.Code));
            Assert.That(store.GetReport(run.JobId).Failure.Target, Is.EqualTo("state.ready"));
        }

        [Test]
        public void NativeDestroyedBoundProviderFailsExplicitly()
        {
            var owner = new GameObject("StateProbeNative");
            _objects.Add(owner);
            NativeProvider provider = owner.AddComponent<NativeProvider>();
            Register("state.native", provider);
            var snapshot = PlayScenarioStateRegistry.Resolve("state.native");
            Assert.That(snapshot.TryRead(out _, out _), Is.True);
            UnityEngine.Object.DestroyImmediate(owner);
            var error = Assert.Throws<PlayScenarioException>(() => snapshot.TryRead(out _, out _));
            Assert.That(error.Failure.Code, Is.EqualTo("state_provider_unavailable"));
        }

        [Test]
        public void NativeDestroyedRegistrationIsPrunedBeforeLiveCapacityChecks()
        {
            var owner = new GameObject("StateProbePruned");
            _objects.Add(owner);
            NativeProvider native = owner.AddComponent<NativeProvider>();
            Register("state.prune.native", native);
            var providers = new List<Provider>();
            for (int index = 0; index < PlayScenarioStateRegistry.RegistrationLimit - 1; index++)
            {
                var provider = new Provider();
                providers.Add(provider);
                Register("state.prune." + index, provider);
            }
            UnityEngine.Object.DestroyImmediate(owner);
            var last = new Provider();
            Assert.DoesNotThrow(() => Register("state.prune.last", last));
            GC.KeepAlive(providers);
            GC.KeepAlive(last);
        }

        [Test]
        public void NativePreflightNeverReadsAnExplicitlyRegisteredStateProvider()
        {
            var provider = new Provider();
            Register("state.ready", provider);
            JObject result = UnityPlayScenarioHost.Preflight(PlayScenarioDefinition.Parse(Json()));
            JObject check = ((JArray)result["data"]["checks"]).OfType<JObject>().Single(item => (string)item["name"] == "state");
            Assert.That((string)check["status"], Is.EqualTo("deferred"));
            Assert.That(provider.Reads, Is.Zero);
        }
    }
}
