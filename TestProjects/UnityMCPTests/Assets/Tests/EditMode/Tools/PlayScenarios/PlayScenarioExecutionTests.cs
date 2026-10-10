using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public sealed class PlayScenarioExecutionTests
    {
        private sealed class Host : IPlayScenarioHost, IPlayScenarioQueryHost, IPlayScenarioResourceHost
        {
            public int Calls;
            public int Releases;
            public PlayScenarioQueryCounts Counts;
            public Func<PlayScenarioStep, PlayScenarioObservation> EvaluateStep;
            public Func<PlayScenarioRegisteredResources> Capture;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool first)
            {
                Calls++;
                return EvaluateStep?.Invoke(step) ?? new PlayScenarioObservation(true, "ready");
            }

            public PlayScenarioQueryCounts CaptureQueryCounts() => Counts;

            public PlayScenarioRegisteredResources CaptureResources() => Capture?.Invoke() ?? new PlayScenarioRegisteredResources();

            public void Release() => Releases++;
        }

        private sealed class ResetParticipant : IPlayScenarioResetParticipant
        {
            public int Begins;
            public int Polls;
            public int CompleteAfter = 3;
            public Action Begin;

            public void BeginReset()
            {
                Begins++;
                Polls = 0;
                Begin?.Invoke();
            }

            public bool IsResetComplete
            {
                get
                {
                    Polls++;
                    return Polls >= CompleteAfter;
                }
            }
        }

        private readonly List<IDisposable> _registrations = new List<IDisposable>();
        private readonly List<UnityEngine.Object> _objects = new List<UnityEngine.Object>();
        private readonly List<string> _directories = new List<string>();

        [TearDown]
        public void TearDown()
        {
            foreach (IDisposable token in _registrations)
                token.Dispose();
            foreach (UnityEngine.Object resource in _objects)
                if (resource != null)
                    UnityEngine.Object.DestroyImmediate(resource);
            foreach (string directory in _directories)
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
        }

        private IDisposable Register(string id, ResetParticipant participant)
        {
            IDisposable token = PlayScenarioResetRegistry.Register(id, participant);
            _registrations.Add(token);
            return token;
        }

        private static JObject Json() =>
            JObject.Parse(
                @"{
            'name':'execution','poll_interval_ms':100,'completion_stable_ms':0,
            'setup_steps':[{'name':'load','action':'load_scene','scene':'Assets/Menu.unity'}],
            'steps':[{'name':'wait','action':'wait_object','target':'Player','timeout_seconds':120}],
            'cleanup_steps':[{'name':'cleanup','action':'wait_scene','scene':'Assets/Menu.unity'}]
        }"
            );

        private static PlayScenarioEngine Engine(Host host, JObject json = null, int repeats = 1)
        {
            var engine = new PlayScenarioEngine(
                PlayScenarioEngine.Create(PlayScenarioDefinition.Parse(json ?? Json()), new string('a', 32), repeats, 300, 1000),
                host
            );
            engine.EnteredPlayMode();
            return engine;
        }

        private static void Finish(PlayScenarioEngine engine, long from = 1000)
        {
            for (long now = from; now < from + 30000 && engine.Running; now += 100)
                engine.Tick(now, true);
        }

        [Test]
        public void ResetBeginsOncePerIterationAndOnlyPollsCompletion()
        {
            var participant = new ResetParticipant();
            Register("execution.reset", participant);
            JObject json = Json();
            ((JArray)json["setup_steps"]).Add(JObject.Parse("{'name':'reset','action':'reset_state','reset_ids':['execution.reset']}"));
            var host = new Host();
            var engine = Engine(host, json, 3);
            Finish(engine);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(participant.Begins, Is.EqualTo(3));
            Assert.That(engine.State.Steps.Where(step => step.Action == "reset_state").All(step => step.PollCount == 3), Is.True);
            Assert.That(host.Calls, Is.EqualTo(9));
            Assert.That(engine.State.QueryCounts.TargetSearches, Is.Zero);
        }

        [Test]
        public void MissingResetSetIsResolvedBeforeAnyBeginAndCleanupStillRuns()
        {
            var participant = new ResetParticipant();
            Register("execution.first", participant);
            JObject json = Json();
            ((JArray)json["setup_steps"]).Add(JObject.Parse("{'name':'reset','action':'reset_state','reset_ids':['execution.first','execution.missing']}"));
            var host = new Host();
            var engine = Engine(host, json);
            Finish(engine);
            Assert.That(participant.Begins, Is.Zero);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("reset_participant_unavailable"));
            Assert.That(engine.State.Steps.Single(step => step.Stage == "cleanup").Status, Is.EqualTo("passed"));
        }

        [Test]
        public void LosingAnotherParticipantDuringBeginDoesNotInvokeItsReset()
        {
            var first = new ResetParticipant();
            var second = new ResetParticipant();
            Register("execution.begin.first", first);
            IDisposable secondToken = Register("execution.begin.second", second);
            first.Begin = () => secondToken.Dispose();
            JObject json = Json();
            ((JArray)json["setup_steps"]).Add(
                JObject.Parse("{'name':'reset','action':'reset_state','reset_ids':['execution.begin.first','execution.begin.second']}")
            );
            var engine = Engine(new Host(), json);
            Finish(engine);
            Assert.That(first.Begins, Is.EqualTo(1));
            Assert.That(second.Begins, Is.Zero);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("reset_participant_unavailable"));
        }

        [Test]
        public void ReplacedResetCannotCompleteOriginalStepOrReplayBegin()
        {
            var participant = new ResetParticipant { CompleteAfter = int.MaxValue };
            IDisposable token = Register("execution.replace", participant);
            JObject json = Json();
            ((JArray)json["setup_steps"]).Add(JObject.Parse("{'name':'reset','action':'reset_state','reset_ids':['execution.replace']}"));
            var engine = Engine(new Host(), json);
            engine.Tick(1000, true);
            engine.Tick(1100, true);
            token.Dispose();
            var replacement = new ResetParticipant();
            Register("execution.replace", replacement);
            Finish(engine, 1200);
            Assert.That(participant.Begins, Is.EqualTo(1));
            Assert.That(replacement.Begins, Is.Zero);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("reset_participant_unavailable"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ResetTimeoutAndCancellationDoNotReplayAndRunCleanup(bool cancel)
        {
            var participant = new ResetParticipant { CompleteAfter = int.MaxValue };
            Register("execution.never", participant);
            JObject json = Json();
            ((JArray)json["setup_steps"]).Add(JObject.Parse("{'name':'reset','action':'reset_state','reset_ids':['execution.never'],'timeout_seconds':1}"));
            var engine = Engine(new Host(), json);
            engine.Tick(1000, true);
            engine.Tick(1100, true);
            if (cancel)
                engine.Cancel(1150);
            Finish(engine, 1200);
            Assert.That(engine.State.Status, Is.EqualTo(cancel ? "cancelled" : "timed_out"));
            Assert.That(participant.Begins, Is.EqualTo(1));
            Assert.That(engine.State.Steps.Single(step => step.Stage == "cleanup").Status, Is.EqualTo("passed"));
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private WeakReference RegisterWeak()
        {
            var participant = new ResetParticipant();
            Register("execution.weak", participant);
            return new WeakReference(participant);
        }

        [Test]
        public void RegistrationTokenAndSnapshotDoNotOwnParticipantStrongly()
        {
            WeakReference weak = RegisterWeak();
            var snapshot = PlayScenarioResetRegistry.Resolve(new[] { "execution.weak" });
            for (int index = 0; index < 5 && weak.IsAlive; index++)
            {
                GC.Collect();
                GC.WaitForPendingFinalizers();
                GC.Collect();
            }
            Assert.That(weak.IsAlive, Is.False);
            Assert.Throws<PlayScenarioException>(() => snapshot.Begin());
        }

        [Test]
        public void ResetRegistryRejectsDuplicateAndLiveCapacityOverflow()
        {
            var participants = new List<ResetParticipant>();
            for (int index = 0; index < PlayScenarioResetRegistry.RegistrationLimit; index++)
            {
                var participant = new ResetParticipant();
                participants.Add(participant);
                Register("execution.capacity." + index, participant);
            }
            Assert.Throws<InvalidOperationException>(() => PlayScenarioResetRegistry.Register("execution.capacity.0", participants[0]));
            Assert.Throws<InvalidOperationException>(() => PlayScenarioResetRegistry.Register("execution.overflow", new ResetParticipant()));
            GC.KeepAlive(participants);
        }

        [Test]
        public void CountedQueriesRespectPollPacingAndRepeatedStatusSnapshots()
        {
            var host = new Host();
            host.EvaluateStep = step =>
            {
                if (step.Action == "wait_object")
                {
                    host.Counts.TargetSearches++;
                    host.Counts.HierarchyVisits += 7;
                    return new PlayScenarioObservation(false, "unchanged");
                }
                return new PlayScenarioObservation(true, "ready");
            };
            var engine = Engine(host);
            engine.Tick(1000, true);
            engine.Tick(1100, true);
            for (long now = 1101; now < 1200; now++)
            {
                JObject.FromObject(engine.State);
                engine.Tick(now, true);
            }
            Assert.That(engine.State.QueryCounts.TargetSearches, Is.EqualTo(1));
            engine.Tick(1200, true);
            Assert.That(engine.State.QueryCounts.TargetSearches, Is.EqualTo(2));
            Assert.That(engine.State.QueryCounts.HierarchyVisits, Is.EqualTo(14));
            Assert.That(engine.State.Steps.Single(step => step.Stage == "main").QueryCounts.TargetSearches, Is.EqualTo(2));
            engine.Cancel(1250);
            Finish(engine, 1300);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StrictZeroQueryBudgetCountsExceptionalEvaluationsAndAllowsCleanup(bool exceptional)
        {
            JObject json = Json();
            json["query_budget"] = JObject.Parse("{'enabled':true,'max_target_searches':0,'max_hierarchy_visits':0}");
            var host = new Host();
            host.EvaluateStep = step =>
            {
                if (step.Action == "wait_object")
                {
                    host.Counts.TargetSearches++;
                    host.Counts.HierarchyVisits += 4;
                    if (exceptional)
                        throw new InvalidOperationException("after target search");
                }
                return new PlayScenarioObservation(true, "ready");
            };
            var engine = Engine(host, json);
            Finish(engine);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("query_budget_exceeded"));
            Assert.That(engine.State.QueryCounts.TargetSearches, Is.EqualTo(1));
            Assert.That(engine.State.QueryCounts.HierarchyVisits, Is.EqualTo(4));
            Assert.That(engine.State.Steps.Single(step => step.Stage == "cleanup").Status, Is.EqualTo("passed"));
        }

        [Test]
        public void QueryOverflowDuringCleanupPreservesPrimaryFailureAndFinishesRemainingCleanup()
        {
            JObject json = Json();
            json["query_budget"] = JObject.Parse("{'enabled':true,'max_target_searches':0,'max_hierarchy_visits':0}");
            ((JArray)json["cleanup_steps"]).Add(JObject.Parse("{'name':'last','action':'wait_scene','scene':'Assets/Menu.unity'}"));
            var host = new Host();
            host.EvaluateStep = step =>
            {
                if (step.Name == "wait")
                    throw new InvalidOperationException("primary");
                if (step.Name == "cleanup")
                    host.Counts.TargetSearches++;
                return new PlayScenarioObservation(true, "ready");
            };
            var engine = Engine(host, json);
            Finish(engine);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("action_exception"));
            Assert.That(engine.State.Failure.Message, Does.Contain("primary"));
            Assert.That(engine.State.CleanupFailures.Count(failure => failure.Code == "query_budget_exceeded"), Is.EqualTo(1));
            Assert.That(engine.State.Steps.Where(step => step.Stage == "cleanup").All(step => step.Status == "passed"), Is.True);
        }

        [Test]
        public void TimelineDeduplicatesObservationsAndKeepsOnlyLast128BoundedScalarEvents()
        {
            JObject json = Json();
            json["diagnostics"] = JObject.Parse("{'record_timeline':true}");
            var host = new Host();
            bool changing = false;
            host.EvaluateStep = step => new PlayScenarioObservation(
                step.Action != "wait_object",
                step.Action == "wait_object" ? (changing ? host.Calls.ToString() : "same") + new string('x', 600) : "ready"
            );
            var engine = Engine(host, json);
            engine.Tick(1000, true);
            for (long now = 1100; now < 1600; now += 100)
                engine.Tick(now, true);
            Assert.That(engine.State.Timeline.Count(item => item.Event == "observation_changed" && item.Stage == "main"), Is.EqualTo(1));
            changing = true;
            for (long now = 1600; now < 18000; now += 100)
                engine.Tick(now, true);
            engine.Cancel(18000);
            Finish(engine, 18100);
            Assert.That(engine.State.Timeline.Count, Is.EqualTo(128));
            Assert.That(engine.State.DroppedTimelineCount, Is.GreaterThan(0));
            Assert.That(engine.State.Timeline.All(item => item.Detail == null || item.Detail.Length <= 512), Is.True);
            Assert.That(engine.State.Timeline.Last().Event, Is.EqualTo("run_finished"));
            Assert.That(engine.State.Timeline.Select(item => item.Sequence).Distinct().Count(), Is.EqualTo(128));
            Assert.That(Engine(new Host()).State.Timeline, Is.Empty);
            RoundTrip(engine.State);
        }

        private void RoundTrip(PlayScenarioRun run)
        {
            string directory = Path.Combine(Path.GetTempPath(), "mcp-scenario-execution-" + Guid.NewGuid().ToString("N"));
            _directories.Add(directory);
            var store = new PlayScenarioStore(directory);
            store.SaveReport(run);
            JObject report = JObject.FromObject(store.GetReport(run.JobId));
            Assert.That(JToken.DeepEquals(report["query_counts"], JObject.FromObject(run.QueryCounts)), Is.True);
            Assert.That(((JArray)report["timeline"]).Count, Is.EqualTo(run.Timeline.Count));
        }

        [Test]
        public void RetainedResourceDescriptionsMatchOnlyNewIdsAndOmitBeyond32()
        {
            JObject json = Json();
            json["resources"] = JObject.Parse("{'enabled':true,'max_handles':100}");
            var host = new Host();
            int captures = 0;
            host.Capture = () =>
            {
                long[] ids = captures++ == 0 ? new long[] { 1 } : Enumerable.Range(1, 41).Select(index => (long)index).ToArray();
                return new PlayScenarioRegisteredResources
                {
                    HandleIds = ids,
                    ResourceDetails = ids.Select(id => new PlayScenarioRegisteredResourceInfo(
                            id,
                            "handle",
                            "owner" + id,
                            sourceFile: "C:/Private/Source.cs",
                            sourceMember: "Create",
                            sourceLine: 12
                        ))
                        .ToArray(),
                };
            };
            var engine = Engine(host, json);
            Finish(engine);
            PlayScenarioResourceCheck check = engine.State.ResourceChecks.Single();
            Assert.That(check.NewHandles, Is.EqualTo(40));
            Assert.That(check.RetainedResources.Count, Is.EqualTo(32));
            Assert.That(check.OmittedResourceCount, Is.EqualTo(8));
            Assert.That(check.RetainedResources.Any(info => info.Id == 1), Is.False);
            Assert.That(check.RetainedResources.All(info => info.SourceFile == "Source.cs" && info.Owner == "owner" + info.Id), Is.True);
            RoundTrip(engine.State);
        }

        [Test]
        public void RealEditorQueriesCountHierarchyVisitsAndDiagnosticsDoNotChangeHostCounts()
        {
            var root = new GameObject("ExecutionQueryRoot" + Guid.NewGuid().ToString("N"));
            var child = new GameObject("Child");
            child.transform.SetParent(root.transform);
            _objects.Add(root);
            var host = new UnityPlayScenarioHost();
            var step = new PlayScenarioStep { Action = "wait_object", Target = root.name + "/Child" };
            PlayScenarioObservation observation = host.Evaluate(step, true);
            Assert.That(observation.Ready, Is.True);
            PlayScenarioQueryCounts counts = host.CaptureQueryCounts();
            Assert.That(counts.TargetSearches, Is.EqualTo(1));
            Assert.That(counts.HierarchyVisits, Is.EqualTo(SceneManager.GetActiveScene().rootCount + 1));
            UnityPlayScenarioHost.DescribeTarget(step);
            for (int index = 0; index < 100; index++)
                Assert.That(host.CaptureQueryCounts().HierarchyVisits, Is.EqualTo(counts.HierarchyVisits));
            host.Release();
            Assert.That(host.CaptureQueryCounts().TargetSearches, Is.EqualTo(1));
        }

        public sealed class NativeResetParticipant : MonoBehaviour, IPlayScenarioResetParticipant
        {
            public void BeginReset() { }

            public bool IsResetComplete => false;
        }

        [Test]
        public void DestroyedNativeResetParticipantFailsWithoutReplayingBegin()
        {
            var owner = new GameObject("ExecutionNativeReset");
            NativeResetParticipant participant = owner.AddComponent<NativeResetParticipant>();
            _objects.Add(owner);
            _registrations.Add(PlayScenarioResetRegistry.Register("execution.native", participant));
            var snapshot = PlayScenarioResetRegistry.Resolve(new[] { "execution.native" });
            snapshot.Begin();
            UnityEngine.Object.DestroyImmediate(owner);
            PlayScenarioException error = Assert.Throws<PlayScenarioException>(() => snapshot.Complete());
            Assert.That(error.Failure.Code, Is.EqualTo("reset_participant_unavailable"));
        }

        [Test]
        public void ExceptionalRealIdResolutionRetainsSearchAndVisitedNodeCounts()
        {
            string id = "execution.duplicate." + Guid.NewGuid().ToString("N");
            var root = new GameObject("ExecutionDuplicateRoot");
            var child = new GameObject("Child");
            child.transform.SetParent(root.transform);
            root.AddComponent<PlayScenarioTarget>().TargetId = id;
            child.AddComponent<PlayScenarioTarget>().TargetId = id;
            _objects.Add(root);
            var host = new UnityPlayScenarioHost();
            Assert.Throws<PlayScenarioException>(() => host.Evaluate(new PlayScenarioStep { Action = "wait_object", TargetId = id }, true));
            Assert.That(host.CaptureQueryCounts().TargetSearches, Is.EqualTo(1));
            Assert.That(host.CaptureQueryCounts().HierarchyVisits, Is.GreaterThanOrEqualTo(2));
            host.Release();
        }

        [Test]
        public void LegacyReportsRestoreWithoutNewFieldsOrChangingTheirDefinitionHash()
        {
            var run = Engine(new Host()).State;
            JObject report = JObject.FromObject(run);
            report.Remove("execution_environment");
            report.Remove("query_counts");
            report.Remove("timeline");
            report.Remove("dropped_timeline_count");
            var scenario = (JObject)report["scenario"];
            scenario.Remove("query_budget");
            ((JObject)scenario["diagnostics"]).Remove("record_timeline");
            string legacyHash = PlayScenarioReproduction.HashSerializedDefinition(scenario);
            report["reproduction"]["definition_hash"] = legacyHash;
            foreach (JObject step in (JArray)report["steps"])
                step.Remove("query_counts");
            PlayScenarioStore.ValidateReport(report);
            Assert.That((string)report["reproduction"]["definition_hash"], Is.EqualTo(legacyHash));
            Assert.That((string)report["execution_environment"], Is.EqualTo("editor"));
            Assert.That((long)report["query_counts"]["target_searches"], Is.Zero);
            Assert.That(((JArray)report["timeline"]).Count, Is.Zero);
        }

        [TestCase("['same','same']")]
        [TestCase("[]")]
        [TestCase("['bad id']")]
        public void ResetParserRejectsInvalidIds(string ids)
        {
            JObject json = Json();
            ((JArray)json["setup_steps"]).Add(JObject.Parse("{'name':'reset','action':'reset_state','reset_ids':" + ids + "}"));
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
        }

        [Test]
        public void ParserAndStoreRejectUnboundedNewFields()
        {
            JObject json = Json();
            json["query_budget"] = JObject.Parse("{'max_target_searches':1000001}");
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
            json["query_budget"] = JObject.Parse("{'max_hierarchy_visits':10000001}");
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(json));
            var run = Engine(new Host()).State;
            JObject report = JObject.FromObject(run);
            report["timeline"] = new JArray(
                Enumerable
                    .Range(0, 129)
                    .Select(index =>
                        JObject.FromObject(
                            new PlayScenarioTimelineEvent
                            {
                                Sequence = index + 1,
                                Stage = "run",
                                StepIndex = -1,
                                Event = "test",
                            }
                        )
                    )
            );
            Assert.Throws<ArgumentException>(() => PlayScenarioStore.ValidateReport(report));
            report = JObject.FromObject(run);
            report["query_counts"]["target_searches"] = -1;
            Assert.Throws<ArgumentException>(() => PlayScenarioStore.ValidateReport(report));
        }
    }
}
