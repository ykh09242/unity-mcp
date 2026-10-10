using System;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioEngineTests
    {
        private sealed class Host : IPlayScenarioHost, IPlayScenarioMetricsHost, IPlayScenarioResourceHost
        {
            public int Calls;
            public int Loads;
            public int Clicks;
            public int Releases;
            public int MetricCalls;
            public int ResourceCalls;
            public Func<PlayScenarioRegisteredResources> SampleResources;
            public Func<int, long, PlayScenarioMetricsSnapshot> Sample;
            public Func<PlayScenarioStep, bool, PlayScenarioObservation> EvaluateStep;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool first)
            {
                Calls++;
                if (step.Action == "load_scene" && first)
                    Loads++;
                if (step.Action == "click_ui")
                    Clicks++;
                return EvaluateStep?.Invoke(step, first) ?? new PlayScenarioObservation(true, "ready");
            }

            public void Release() => Releases++;

            public PlayScenarioRegisteredResources CaptureResources()
            {
                ResourceCalls++;
                return SampleResources?.Invoke() ?? new PlayScenarioRegisteredResources();
            }

            public PlayScenarioMetricsSnapshot CaptureMetrics(int iteration, long now)
            {
                MetricCalls++;
                return Sample?.Invoke(iteration, now)
                    ?? new PlayScenarioMetricsSnapshot
                    {
                        ManagedBytes = 100,
                        AllocatedBytes = 200,
                        ObjectCount = 1,
                    };
            }
        }

        private static PlayScenarioDefinition Definition() =>
            PlayScenarioDefinition.Parse(
                JObject.Parse(
                    @"{
            'name':'menu-start', 'poll_interval_ms':250,
            'steps':[
                {'name':'Menu','action':'load_scene','scene':'Assets/Scenes/Menu.unity','timeout_seconds':2},
                {'name':'Start','action':'click_ui','target':'Canvas/Start','timeout_seconds':2},
                {'name':'Game','action':'wait_scene','scene':'Assets/Scenes/Game.unity','timeout_seconds':2},
                {'name':'Player','action':'wait_object','target':'Player','timeout_seconds':2}
            ]}"
                )
            );

        private static PlayScenarioEngine Engine(Host host, int repeats = 1, int timeout = 30)
        {
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(Definition(), new string('a', 32), repeats, timeout, 1000), host);
            engine.EnteredPlayMode();
            return engine;
        }

        [Test]
        public void RepeatsResetTheMenuAndClickOncePerIteration()
        {
            var host = new Host { EvaluateStep = (step, first) => new PlayScenarioObservation(step.Action != "load_scene" || !first, "observed") };
            var engine = Engine(host, 3);
            for (long now = 1000; now < 10000 && engine.Running; now += 250)
                engine.Tick(now, true);

            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(host.Loads, Is.EqualTo(3));
            Assert.That(host.Clicks, Is.EqualTo(3));
            Assert.That(engine.State.Steps.Count, Is.EqualTo(12));
            Assert.That(engine.State.Steps.All(step => step.Status == "passed"), Is.True);
            Assert.That(engine.State.Steps.Select(step => step.Iteration).Distinct(), Is.EqualTo(new[] { 1, 2, 3 }));
            engine.Tick(20000, true);
            engine.Cancel(20000);
            engine.Release();
            Assert.That(host.Calls, Is.EqualTo(15));
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void ConditionWaitDoesNotQueryAgainBeforeItsInterval()
        {
            var host = new Host { EvaluateStep = (_, __) => new PlayScenarioObservation(false, "waiting for menu") };
            var engine = Engine(host);
            engine.Tick(1000, true);
            for (long now = 1001; now < 1250; now++)
                engine.Tick(now, true);
            Assert.That(host.Calls, Is.EqualTo(1));
            engine.Tick(1250, true);
            Assert.That(host.Calls, Is.EqualTo(2));
            Assert.That(host.Loads, Is.EqualTo(1));
            Assert.That(engine.State.Steps[0].PollCount, Is.EqualTo(2));
        }

        [Test]
        public void StepTimeoutPreservesObservationAndSkipsRemainingSteps()
        {
            var host = new Host { EvaluateStep = (_, __) => new PlayScenarioObservation(false, "player is not present") };
            var engine = Engine(host);
            engine.Tick(1000, true);
            engine.Tick(3000, true);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(engine.State.Error, Does.Contain("player is not present"));
            Assert.That(engine.State.Steps[0].Status, Is.EqualTo("timed_out"));
            Assert.That(engine.State.Steps.Skip(1).All(step => step.Status == "skipped"), Is.True);
            Assert.That(host.Calls, Is.EqualTo(1));
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void PausedOrUnreadyEditorMakesNoQueriesButStillTimesOut()
        {
            var host = new Host();
            var engine = Engine(host, timeout: 1);
            engine.Tick(1000, false);
            engine.Tick(2000, false);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(host.Calls, Is.Zero);
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void FailureAfterDispatchNeverRetriesClick()
        {
            var host = new Host
            {
                EvaluateStep = (step, _) =>
                    step.Action == "click_ui"
                        ? throw new InvalidOperationException("listener failed after dispatch")
                        : new PlayScenarioObservation(true, "loaded"),
            };
            var engine = Engine(host);
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            for (int i = 0; i < 10; i++)
                engine.Tick(1500 + i * 250, true);
            Assert.That(host.Clicks, Is.EqualTo(1));
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Error, Does.Contain("listener failed after dispatch"));
            Assert.That(engine.State.Steps[1].Status, Is.EqualTo("failed"));
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void CancellationFromClickListenerCannotBeOverwrittenBySuccessfulDispatch()
        {
            var host = new Host();
            var engine = Engine(host);
            host.EvaluateStep = (step, _) =>
            {
                if (step.Action == "click_ui")
                    engine.Cancel(1250);
                return new PlayScenarioObservation(true, "callback returned");
            };
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            Assert.That(engine.State.Status, Is.EqualTo("cancelled"));
            Assert.That(engine.State.Steps[1].Status, Is.EqualTo("cancelled"));
            Assert.That(engine.State.Cursor, Is.EqualTo(1));
            Assert.That(host.Clicks, Is.EqualTo(1));
            Assert.That(host.Releases, Is.EqualTo(1));
            Assert.That(engine.State.Steps[1].Detail, Does.Contain("Cancelled"));
        }

        [Test]
        public void CancelBeforePlayEntryRemovesAllPendingWork()
        {
            var host = new Host();
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(Definition(), new string('b', 32), 1, 30, 1000), host);
            engine.Cancel(1100);
            engine.EnteredPlayMode();
            engine.Tick(1250, true);
            Assert.That(host.Calls, Is.Zero);
            Assert.That(engine.State.Status, Is.EqualTo("cancelled"));
            Assert.That(engine.State.Steps.All(step => step.Status == "skipped"), Is.True);
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void StartupCheckpointCanResumeWithoutKeepingRuntimeReferences()
        {
            PlayScenarioRun run = PlayScenarioEngine.Create(Definition(), new string('c', 32), 1, 30, 1000);
            run.PlayRequested = true;
            var restored = JsonConvert.DeserializeObject<PlayScenarioRun>(JsonConvert.SerializeObject(run));
            var host = new Host();
            var engine = new PlayScenarioEngine(restored, host);
            engine.Tick(1100, true);
            Assert.That(host.Calls, Is.Zero);
            engine.EnteredPlayMode();
            engine.Tick(1250, true);
            Assert.That(host.Loads, Is.EqualTo(1));
            Assert.That(engine.State.PlayRequested, Is.True);
        }

        [Test]
        public void StartingJobAlsoHasAWallClockDeadline()
        {
            var host = new Host();
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(Definition(), new string('d', 32), 1, 1, 1000), host);
            engine.Tick(2000, false);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(host.Calls, Is.Zero);
        }

        [Test]
        public void LogBufferBoundsMessagesAndRejectsLateCallbacksAfterClose()
        {
            var buffer = new PlayScenarioLogBuffer();
            var run = new PlayScenarioRun();
            for (int i = 0; i < 80; i++)
                buffer.Add(i, "Error", new string('m', 2000), new string('s', 3000));
            buffer.DrainTo(run);
            Assert.That(run.Logs.Count, Is.EqualTo(50));
            Assert.That(run.Logs[0].TimestampUnixMs, Is.EqualTo(30));
            Assert.That(run.DroppedLogCount, Is.EqualTo(30));
            Assert.That(run.Logs[0].Message.Length, Is.EqualTo(1024));
            Assert.That(run.Logs[0].StackTrace.Length, Is.EqualTo(2048));
            buffer.Add(80, "Warning", "last", "");
            buffer.Close();
            buffer.Add(81, "Error", "late", "");
            buffer.DrainTo(run);
            Assert.That(run.Logs.Last().Message, Is.EqualTo("last"));
            Assert.That(run.DroppedLogCount, Is.EqualTo(31));
        }

        private static PlayScenarioDefinition LifecycleDefinition()
        {
            var value = JObject.Parse(
                @"{
                'name':'lifecycle','completion_stable_ms':0,
                'setup_steps':[{'name':'Reset','action':'load_scene','scene':'Assets/Reset.unity'}],
                'steps':[{'name':'Main','action':'click_ui','target':'Canvas/Start'}],
                'cleanup_steps':[
                    {'name':'Cleanup one','action':'wait_object','target':'Cleanup'},
                    {'name':'Cleanup two','action':'wait_scene','scene':'Assets/Reset.unity'}]
            }"
            );
            return PlayScenarioDefinition.Parse(value);
        }

        private static PlayScenarioEngine LifecycleEngine(
            Host host,
            PlayScenarioDefinition definition = null,
            int repeat = 1,
            int timeout = 30,
            Action<PlayScenarioRun, PlayScenarioStep, long> onFailure = null,
            Action onEvaluationCompleted = null
        )
        {
            var engine = new PlayScenarioEngine(
                PlayScenarioEngine.Create(definition ?? LifecycleDefinition(), new string('e', 32), repeat, timeout, 1000),
                host,
                onFailure,
                onEvaluationCompleted
            );
            engine.EnteredPlayMode();
            return engine;
        }

        [Test]
        public void StableConditionResetsOnAFalseObservation()
        {
            var definition = Definition();
            definition.Steps = definition.Steps.Take(1).Concat(definition.Steps.Skip(3)).ToList();
            definition.Steps[1].StableForMs = 500;
            var host = new Host();
            var engine = LifecycleEngine(host, definition);
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            Assert.AreEqual(1250, engine.State.Steps[1].StableSinceUnixMs);
            host.EvaluateStep = (_, __) => new PlayScenarioObservation(false, "not ready");
            engine.Tick(1500, true);
            Assert.IsNull(engine.State.Steps[1].StableSinceUnixMs);
            host.EvaluateStep = null;
            engine.Tick(1750, true);
            engine.Tick(2000, true);
            Assert.IsTrue(engine.Running);
            engine.Tick(2250, true);
            Assert.AreEqual("settling", engine.State.Phase);
            int calls = host.Calls;
            engine.Tick(2499, true);
            Assert.AreEqual(calls, host.Calls);
            engine.Tick(2500, true);
            Assert.AreEqual("succeeded", engine.State.Status);
            Assert.AreEqual(1750, engine.State.Steps[1].StableSinceUnixMs);
        }

        [Test]
        public void SetupMainCleanupRunInOrderAndMetricsOnlyAfterCleanup()
        {
            var host = new Host();
            var definition = LifecycleDefinition();
            definition.Metrics.Enabled = true;
            var engine = LifecycleEngine(host, definition, repeat: 2);
            for (long now = 1000; engine.Running && now < 10000; now += 250)
                engine.Tick(now, true);
            Assert.AreEqual("succeeded", engine.State.Status);
            CollectionAssert.AreEqual(
                new[] { "setup", "main", "cleanup", "cleanup", "setup", "main", "cleanup", "cleanup" },
                engine.State.Steps.Select(step => step.Stage)
            );
            Assert.AreEqual(2, host.Loads);
            Assert.AreEqual(2, host.Clicks);
            Assert.AreEqual(2, host.MetricCalls);
            Assert.AreEqual(2, engine.State.Metrics.Count);
            Assert.IsTrue(engine.State.Metrics[0].IgnoredForTrend);
            Assert.IsFalse(engine.State.Metrics[1].IgnoredForTrend);
            Assert.That(engine.State.MetricsSummary, Does.Contain("Insufficient post-warmup samples"));
        }

        [Test]
        public void FailureSnapshotPrecedesBestEffortCleanupAndPrimaryErrorIsPreserved()
        {
            var events = new System.Collections.Generic.List<string>();
            var host = new Host
            {
                EvaluateStep = (step, _) =>
                {
                    events.Add(step.Name);
                    if (step.Name == "Main")
                        throw new InvalidOperationException("primary failure");
                    if (step.Name == "Cleanup one")
                        throw new InvalidOperationException("cleanup failure");
                    return new PlayScenarioObservation(true, "ready");
                },
            };
            int captures = 0;
            var engine = LifecycleEngine(
                host,
                onFailure: (_, step, __) =>
                {
                    captures++;
                    events.Add("Snapshot " + step.Name);
                }
            );
            for (long now = 1000; engine.Running && now < 10000; now += 250)
                engine.Tick(now, true);
            CollectionAssert.AreEqual(new[] { "Reset", "Main", "Snapshot Main", "Cleanup one", "Cleanup two" }, events);
            Assert.AreEqual(1, captures);
            Assert.AreEqual("failed", engine.State.Status);
            Assert.That(engine.State.Error, Does.Contain("primary failure"));
            Assert.That(engine.State.CleanupError, Does.Contain("cleanup failure"));
            Assert.AreEqual("passed", engine.State.Steps[3].Status);
            Assert.AreEqual(1, host.Clicks);
            Assert.AreEqual(1, host.Releases);
        }

        [Test]
        public void ReentrantCancelSkipsMainResultThenRunsCleanupOnce()
        {
            var host = new Host();
            var engine = LifecycleEngine(host);
            host.EvaluateStep = (step, _) =>
            {
                if (step.Name == "Main")
                    engine.Cancel(1250);
                return new PlayScenarioObservation(true, "ready");
            };
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            Assert.AreEqual("cleaning", engine.State.Phase);
            Assert.AreEqual("cancelled", engine.State.Steps[1].Status);
            engine.Cancel(1300);
            engine.Tick(1500, true);
            engine.Tick(1750, true);
            Assert.AreEqual("cancelled", engine.State.Status);
            Assert.AreEqual(1, host.Clicks);
            Assert.AreEqual(4, host.Calls);
            Assert.AreEqual(1, host.Releases);
        }

        [Test]
        public void OverallTimeoutAllowsCleanupItsOwnDeadlineAndCleanupTimeoutPreservesPrimary()
        {
            var definition = LifecycleDefinition();
            definition.CleanupTimeoutSeconds = 1;
            var host = new Host { EvaluateStep = (_, __) => new PlayScenarioObservation(false, "waiting") };
            var engine = LifecycleEngine(host, definition, timeout: 1);
            engine.Tick(1000, true);
            engine.Tick(2000, false);
            Assert.AreEqual("cleaning", engine.State.Phase);
            Assert.IsNull(engine.State.CleanupDeadlineUnixMs);
            engine.Tick(2250, true);
            Assert.AreEqual(3250, engine.State.CleanupDeadlineUnixMs);
            Assert.AreEqual(2, host.Calls);
            engine.Tick(3000, false);
            Assert.IsTrue(engine.Running);
            Assert.AreEqual(3250, engine.State.CleanupDeadlineUnixMs);
            engine.Tick(3250, false);
            Assert.AreEqual("timed_out", engine.State.Status);
            Assert.That(engine.State.Error, Does.Contain("Run exceeded"));
            Assert.That(engine.State.CleanupError, Does.Contain("Cleanup exceeded"));
            Assert.AreEqual("skipped", engine.State.Steps[3].Status);
            Assert.AreEqual(1, host.Releases);
        }

        [Test]
        public void FreshCleanupLogsAreSeparateAndTerminalSuccessCanBeCorrectedBeforePublication()
        {
            var engine = LifecycleEngine(new Host());
            engine.Tick(1000, true);
            engine.RequestFailure("primary log", 1100);
            engine.RequestFailure("cleanup log", 1200);
            engine.Tick(1250, true);
            engine.Tick(1500, true);
            Assert.AreEqual("primary log", engine.State.Error);
            Assert.That(engine.State.CleanupError, Does.Contain("cleanup log"));

            var host = new Host();
            engine = LifecycleEngine(host);
            for (long now = 1000; engine.Running && now < 10000; now += 250)
                engine.Tick(now, true);
            engine.ObserveUnexpectedError("last evaluation log", 3000);
            Assert.AreEqual("failed", engine.State.Status);
            Assert.AreEqual("last evaluation log", engine.State.Error);
            Assert.AreEqual(1, host.Releases);
        }

        [Test]
        public void WarmupAndCaptureErrorsCannotCreateFalseSustainedTrends()
        {
            var definition = LifecycleDefinition();
            definition.Metrics.Enabled = true;
            definition.Metrics.ManagedGrowthBytes = 100;
            var host = new Host
            {
                Sample = (iteration, _) =>
                    new PlayScenarioMetricsSnapshot
                    {
                        ManagedBytes = iteration * 1000,
                        AllocatedBytes = 500,
                        ObjectCount = 1,
                    },
            };
            var engine = LifecycleEngine(host, definition, repeat: 4);
            for (long now = 1000; engine.Running && now < 20000; now += 250)
                engine.Tick(now, true);
            Assert.AreEqual("succeeded", engine.State.Status);
            Assert.AreEqual(4, host.MetricCalls);
            Assert.AreEqual(1, engine.State.MetricWarnings.Count);
            Assert.That(engine.State.MetricWarnings[0], Does.Contain("managed_bytes"));
            Assert.That(engine.State.MetricWarnings[0], Does.Contain("not proof of a leak"));

            definition.Metrics.WarmupIterations = 0;
            host = new Host
            {
                Sample = (iteration, _) =>
                    iteration == 2
                        ? throw new InvalidOperationException("sample unavailable")
                        : new PlayScenarioMetricsSnapshot { ManagedBytes = iteration * 1000 },
            };
            engine = LifecycleEngine(host, definition, repeat: 4);
            for (long now = 1000; engine.Running && now < 20000; now += 250)
                engine.Tick(now, true);
            Assert.AreEqual("succeeded", engine.State.Status);
            Assert.IsNull(engine.State.Metrics[1].ManagedBytes);
            Assert.That(engine.State.Metrics[1].Error, Does.Contain("sample unavailable"));
            Assert.IsEmpty(engine.State.MetricWarnings);
        }

        [Test]
        public void StickyUnexpectedLogsSurviveRotationAndMatchCompleteLiteralBeforeTruncation()
        {
            var policy = new PlayScenarioLogPolicy();
            policy.AllowedMessages.Add("expected");
            policy.AllowedMessages.Add(new string('x', 1024));
            var buffer = new PlayScenarioLogBuffer(policy);
            var run = new PlayScenarioRun();
            buffer.Add(1, "Error", "expected", "");
            buffer.Add(2, "Warning", "ordinary", "");
            buffer.Add(3, "Assert", "first unexpected", "");
            for (int i = 0; i < 80; i++)
                buffer.Add(i + 4, "Log", "rotation", "");
            buffer.Add(100, "Error", new string('x', 1025), "");
            buffer.DrainTo(run);
            Assert.AreEqual(2, run.UnexpectedLogCount);
            Assert.AreEqual("Assert: first unexpected", run.UnexpectedLogError);
            Assert.That(run.LastUnexpectedLogError, Does.StartWith("Error: "));
            Assert.IsFalse(run.Logs.Any(log => log.Message == "first unexpected"));
            buffer.DrainTo(run);
            Assert.AreEqual(2, run.UnexpectedLogCount);
            buffer.Close();
            buffer.Add(101, "Exception", "late", "");
            buffer.DrainTo(run);
            Assert.AreEqual(2, run.UnexpectedLogCount);

            policy.Mode = "log_only";
            buffer = new PlayScenarioLogBuffer(policy);
            run = new PlayScenarioRun();
            buffer.Add(1, "Exception", "captured only", "");
            buffer.DrainTo(run);
            Assert.AreEqual(0, run.UnexpectedLogCount);
            Assert.AreEqual(1, run.Logs.Count);
        }

        [Test]
        public void CleanupBudgetStartsAfterCaptureDelayAndNeverResetsOnCancelOrLaterTicks()
        {
            var definition = LifecycleDefinition();
            definition.CleanupTimeoutSeconds = 1;
            var host = new Host();
            var engine = LifecycleEngine(host, definition);
            engine.Tick(1000, true);
            engine.RequestFailure("primary failure", 1100);
            Assert.AreEqual("cleaning", engine.State.Phase);
            Assert.IsNull(engine.State.CleanupDeadlineUnixMs);

            // Simulate the service withholding all engine ticks during two seconds of capture.
            engine.Tick(3100, false);
            Assert.AreEqual(4100, engine.State.CleanupDeadlineUnixMs);
            Assert.AreEqual(1, host.Calls);
            host.EvaluateStep = (_, __) => new PlayScenarioObservation(false, "cleanup waiting");
            engine.Tick(3350, true);
            engine.Cancel(3400);
            engine.Tick(4099, true);
            Assert.IsTrue(engine.Running);
            Assert.AreEqual(4100, engine.State.CleanupDeadlineUnixMs);
            Assert.AreEqual(3, host.Calls);
            engine.Tick(4100, false);
            Assert.AreEqual("failed", engine.State.Status);
            Assert.AreEqual("primary failure", engine.State.Error);
            Assert.That(engine.State.CleanupError, Does.Contain("Cleanup exceeded"));
            Assert.AreEqual(1, host.Releases);
        }

        [TestCase(1)]
        [TestCase(2)]
        public void FinalCleanupEvaluationLogsFailTheirOwnIterationBeforeMetricsOrFurtherEffects(int repeats)
        {
            bool pendingError = false;
            var definition = LifecycleDefinition();
            definition.Metrics.Enabled = true;
            var host = new Host
            {
                EvaluateStep = (step, _) =>
                {
                    if (step.Name == "Cleanup two")
                        pendingError = true;
                    return new PlayScenarioObservation(true, "ready");
                },
            };
            PlayScenarioEngine engine = null;
            long clock = 1000;
            engine = LifecycleEngine(
                host,
                definition,
                repeat: repeats,
                onEvaluationCompleted: () =>
                {
                    if (!pendingError)
                        return;
                    pendingError = false;
                    engine.ObserveUnexpectedError("final cleanup emitted Error", clock);
                }
            );
            for (; engine.Running && clock < 10000; clock += 250)
                engine.Tick(clock, true);

            Assert.AreEqual("failed", engine.State.Status);
            Assert.AreEqual("final cleanup emitted Error", engine.State.Error);
            Assert.That(engine.State.CleanupError, Does.Contain("final cleanup emitted Error"));
            Assert.IsEmpty(engine.State.Metrics);
            Assert.AreEqual(0, host.MetricCalls);
            Assert.AreEqual(1, host.Loads);
            Assert.AreEqual(1, host.Clicks);
            Assert.AreEqual(4, host.Calls);
            Assert.IsTrue(engine.State.Steps.Where(step => step.Iteration > 1).All(step => step.Status == "skipped"));
            Assert.AreEqual(1, host.Releases);
        }

        private static long CompleteLedgerRun(PlayScenarioEngine engine, long now = 1000)
        {
            for (; engine.Running && now < 20000; now += 250)
                engine.Tick(now, true);
            Assert.IsFalse(engine.Running);
            return now - 250;
        }

        private static void AssertLedger(PlayScenarioRun run, params string[] statuses)
        {
            Assert.AreEqual(1, run.IterationResultsVersion);
            Assert.AreEqual(run.RepeatCount, run.IterationResults.Count);
            CollectionAssert.AreEqual(Enumerable.Range(1, run.RepeatCount), run.IterationResults.Select(result => result.Iteration));
            CollectionAssert.AreEqual(statuses, run.IterationResults.Select(result => result.Status));
            foreach (PlayScenarioIterationResult result in run.IterationResults)
            {
                Assert.AreEqual(result.Status != "pending" && result.Status != "skipped", result.StartedUnixMs.HasValue);
                Assert.AreEqual(result.Status != "pending" && result.Status != "running", result.FinishedUnixMs.HasValue);
                if (result.StartedUnixMs.HasValue && result.FinishedUnixMs.HasValue)
                    Assert.GreaterOrEqual(result.FinishedUnixMs.Value, result.StartedUnixMs.Value);
            }
        }

        [Test]
        public void LedgerWaitsForCleanupResourceAndMetricBoundariesBeforePassing()
        {
            var definition = LifecycleDefinition();
            definition.Resources = new PlayScenarioResourceOptions { Enabled = true };
            definition.Metrics.Enabled = true;
            var host = new Host();
            var engine = LifecycleEngine(host, definition, repeat: 2);
            var boundaries = new System.Collections.Generic.List<bool>();
            host.Sample = (iteration, _) =>
            {
                boundaries.Add(
                    engine.State.IterationResults[iteration - 1].Status == "running"
                        && !engine.State.IterationResults[iteration - 1].FinishedUnixMs.HasValue
                        && engine.State.ResourceChecks.Last().Iteration == iteration
                );
                return new PlayScenarioMetricsSnapshot();
            };
            engine.Tick(1000, false);
            AssertLedger(engine.State, "pending", "pending");
            engine.Tick(1250, true);
            AssertLedger(engine.State, "running", "pending");
            Assert.AreEqual(1250, engine.State.IterationResults[0].StartedUnixMs);
            long finished = CompleteLedgerRun(engine, 1500);
            AssertLedger(engine.State, "passed", "passed");
            Assert.AreEqual(finished, engine.State.IterationResults[1].FinishedUnixMs);
            Assert.Less(engine.State.IterationResults[0].FinishedUnixMs.Value, engine.State.IterationResults[1].StartedUnixMs.Value);
            Assert.AreEqual(4, host.ResourceCalls);
            Assert.AreEqual(2, host.MetricCalls);
            CollectionAssert.AreEqual(new[] { true, true }, boundaries);
        }

        [TestCase("Reset")]
        [TestCase("Main")]
        [TestCase("Cleanup one")]
        public void LaterIterationFailurePreservesEarlierPassAndSkipsFutureIterations(string failingStep)
        {
            var host = new Host();
            var engine = LifecycleEngine(host, repeat: 3);
            host.EvaluateStep = (step, _) =>
            {
                if (step.Name == failingStep && engine.State.Steps[engine.State.Cursor].Iteration == 2)
                    throw new InvalidOperationException("second iteration failed");
                return new PlayScenarioObservation(true, "ready");
            };
            long finished = CompleteLedgerRun(engine);
            AssertLedger(engine.State, "passed", "failed", "skipped");
            Assert.AreEqual(2, engine.State.Failure.Iteration);
            Assert.AreEqual(finished, engine.State.IterationResults[1].FinishedUnixMs);
            Assert.IsTrue(engine.State.Steps.Any(step => step.Iteration == 2 && step.Name == "Cleanup two" && step.Status == "passed"));
            Assert.IsTrue(engine.State.Steps.Where(step => step.Iteration == 3).All(step => step.Status == "skipped"));
            Assert.AreEqual(1, host.Releases);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CancellationOrTimeoutBeforeExecutionSkipsEveryIteration(bool timeout)
        {
            var host = new Host();
            var engine = LifecycleEngine(host, repeat: 3, timeout: 1);
            engine.Tick(1000, false);
            if (timeout)
                engine.Tick(2000, false);
            else
                engine.Cancel(1100);
            AssertLedger(engine.State, "skipped", "skipped", "skipped");
            Assert.AreEqual(timeout ? "timed_out" : "cancelled", engine.State.Status);
            Assert.AreEqual(0, host.Calls);
            Assert.AreEqual(0, host.ResourceCalls);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StopBetweenIterationsDoesNotExecuteUntouchedCleanup(bool timeout)
        {
            var host = new Host();
            var engine = LifecycleEngine(host, repeat: 3, timeout: 2);
            for (long now = 1000; engine.Running && now < 20000 && engine.State.IterationResults[0].Status != "passed"; now += 250)
                engine.Tick(now, true);
            AssertLedger(engine.State, "passed", "pending", "pending");
            int calls = host.Calls;
            if (timeout)
                engine.Tick(3000, false);
            else
                engine.Cancel(2250);
            AssertLedger(engine.State, "passed", "skipped", "skipped");
            Assert.AreEqual(calls, host.Calls);
            Assert.IsNull(engine.State.CleanupError);
            Assert.AreEqual(timeout ? "timed_out" : "cancelled", engine.State.Status);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LaterIterationCancellationOrTimeoutRemainsRunningUntilCleanupCompletes(bool timeout)
        {
            var host = new Host();
            var engine = LifecycleEngine(host, repeat: 3);
            long now = 1000;
            for (; engine.Running && now < 20000 && engine.State.IterationResults[1].Status != "running"; now += 250)
                engine.Tick(now, true);
            AssertLedger(engine.State, "passed", "running", "pending");
            host.EvaluateStep = (step, _) => new PlayScenarioObservation(step.Name != "Main", "waiting");
            engine.Tick(now, true);
            if (timeout)
                engine.State.DeadlineUnixMs = now + 250;
            else
                engine.Cancel(now + 250);
            if (timeout)
                engine.Tick(now + 250, false);
            AssertLedger(engine.State, "passed", "running", "pending");
            CompleteLedgerRun(engine, now + 500);
            AssertLedger(engine.State, "passed", timeout ? "timed_out" : "cancelled", "skipped");
            Assert.AreEqual(2, engine.State.Failure.Iteration);
            Assert.AreEqual("passed", engine.State.Steps.Last(step => step.Iteration == 2 && step.Stage == "cleanup").Status);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ResourceFailureIsAttributedToItsAttemptedIteration(bool baseline)
        {
            var definition = LifecycleDefinition();
            definition.Resources = new PlayScenarioResourceOptions { Enabled = true };
            var host = new Host();
            host.SampleResources = () =>
            {
                if (host.ResourceCalls == (baseline ? 3 : 4))
                    throw new InvalidOperationException("resource sample failed");
                return new PlayScenarioRegisteredResources();
            };
            var engine = LifecycleEngine(host, definition, repeat: 3);
            CompleteLedgerRun(engine);
            AssertLedger(engine.State, "passed", "failed", "skipped");
            Assert.AreEqual(2, engine.State.Failure.Iteration);
            Assert.AreEqual("resource_measurement_failed", engine.State.Failure.Code);
            Assert.AreEqual(2, engine.State.ResourceChecks.Last().Iteration);
            if (baseline)
                Assert.IsNull(engine.State.Steps.First(step => step.Iteration == 2).StartedUnixMs);
        }

        [Test]
        public void DiagnosticMetricFailureDoesNotChangePassedIteration()
        {
            var definition = LifecycleDefinition();
            definition.Metrics.Enabled = true;
            var host = new Host
            {
                Sample = (iteration, _) => iteration == 2 ? throw new InvalidOperationException("metric unavailable") : new PlayScenarioMetricsSnapshot(),
            };
            var engine = LifecycleEngine(host, definition, repeat: 3);
            CompleteLedgerRun(engine);
            AssertLedger(engine.State, "passed", "passed", "passed");
            Assert.That(engine.State.Metrics[1].Error, Does.Contain("metric unavailable"));
            Assert.AreEqual("succeeded", engine.State.Status);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ReentrantMetricStopBelongsToCompletingIterationAndDoesNotRunNextCleanup(bool cancel)
        {
            var definition = LifecycleDefinition();
            definition.Metrics.Enabled = true;
            var host = new Host();
            var engine = LifecycleEngine(host, definition, repeat: 3);
            bool remainedActiveDuringSample = false;
            host.Sample = (iteration, now) =>
            {
                if (iteration == 2)
                {
                    if (cancel)
                        engine.Cancel(now);
                    else
                        engine.RequestFailure("metric callback failure", now);
                    remainedActiveDuringSample = engine.Running && engine.State.IterationResults[1].Status == "running" && host.Releases == 0;
                }
                return new PlayScenarioMetricsSnapshot();
            };
            CompleteLedgerRun(engine);
            AssertLedger(engine.State, "passed", cancel ? "cancelled" : "failed", "skipped");
            Assert.AreEqual(2, engine.State.Failure.Iteration);
            Assert.AreEqual(8, host.Calls);
            Assert.IsTrue(remainedActiveDuringSample);
            Assert.AreEqual(1, host.Releases);
        }

        [Test]
        public void RestoredLedgerInterruptsCurrentIterationWithoutReplayingEffects()
        {
            var host = new Host();
            var engine = LifecycleEngine(host, repeat: 3);
            long now = 1000;
            for (; engine.Running && now < 20000 && engine.State.IterationResults[1].Status != "running"; now += 250)
                engine.Tick(now, true);
            AssertLedger(engine.State, "passed", "running", "pending");
            PlayScenarioRun restored = JsonConvert.DeserializeObject<PlayScenarioRun>(JsonConvert.SerializeObject(engine.State));
            engine.Release();
            var restoredHost = new Host();
            var resumed = new PlayScenarioEngine(restored, restoredHost);
            resumed.Interrupt("domain reload", now);
            AssertLedger(restored, "passed", "failed", "skipped");
            Assert.AreEqual(2, restored.Failure.Iteration);
            Assert.AreEqual(0, restoredHost.Calls);
            Assert.That(restored.CleanupError, Does.Contain("Cleanup unavailable"));
            Assert.AreEqual(engine.State.IterationResults[1].StartedUnixMs, restored.IterationResults[1].StartedUnixMs);
        }

        [Test]
        public void LateUnexpectedErrorCorrectsLastPassedIterationOnly()
        {
            var host = new Host();
            var engine = LifecycleEngine(host, repeat: 3);
            long finished = CompleteLedgerRun(engine);
            long firstFinished = engine.State.IterationResults[0].FinishedUnixMs.Value;
            engine.ObserveUnexpectedError("late final log", finished + 250);
            AssertLedger(engine.State, "passed", "passed", "failed");
            Assert.AreEqual(firstFinished, engine.State.IterationResults[0].FinishedUnixMs);
            Assert.AreEqual(finished + 250, engine.State.IterationResults[2].FinishedUnixMs);
            Assert.AreEqual(3, engine.State.Failure.Iteration);
            Assert.AreEqual(1, host.Releases);
        }

        [Test]
        public void LegacySavedRunRemainsReadableWithoutInventingIterationHistory()
        {
            JObject report = JObject.FromObject(PlayScenarioEngine.Create(LifecycleDefinition(), new string('f', 32), 2, 30, 1000));
            report.Remove("iteration_results_version");
            report.Remove("iteration_results");
            PlayScenarioRun legacy = report.ToObject<PlayScenarioRun>();
            Assert.IsNull(legacy.IterationResultsVersion);
            Assert.IsNull(legacy.IterationResults);
            var engine = new PlayScenarioEngine(legacy, new Host());
            engine.EnteredPlayMode();
            CompleteLedgerRun(engine);
            Assert.AreEqual("succeeded", legacy.Status);
            Assert.IsNull(JObject.FromObject(legacy)["iteration_results"]);
        }

        [Test]
        public void RetainedResourceFailureDoesNotTurnPassedStepsIntoAPassedIteration()
        {
            var definition = LifecycleDefinition();
            definition.Resources = new PlayScenarioResourceOptions { Enabled = true };
            var host = new Host();
            host.SampleResources = () => new PlayScenarioRegisteredResources { HandleIds = host.ResourceCalls == 4 ? new long[] { 42 } : Array.Empty<long>() };
            var engine = LifecycleEngine(host, definition, repeat: 3);
            CompleteLedgerRun(engine);
            AssertLedger(engine.State, "passed", "failed", "skipped");
            Assert.IsTrue(engine.State.Steps.Where(step => step.Iteration == 2).All(step => step.Status == "passed"));
            Assert.AreEqual("resource_assertion_failed", engine.State.Failure.Code);
            Assert.AreEqual(2, engine.State.Failure.Iteration);
            Assert.AreEqual(1, engine.State.ResourceChecks[1].NewHandles);
        }

        [Test]
        public void LaterCleanupTimeoutPreservesPrimaryCancellationAndFinalizesItsIteration()
        {
            var definition = LifecycleDefinition();
            definition.CleanupTimeoutSeconds = 1;
            var host = new Host();
            var engine = LifecycleEngine(host, definition, repeat: 3);
            long now = 1000;
            for (; engine.Running && now < 20000 && engine.State.IterationResults[1].Status != "running"; now += 250)
                engine.Tick(now, true);
            AssertLedger(engine.State, "passed", "running", "pending");
            engine.Cancel(now);
            host.EvaluateStep = (_, __) => new PlayScenarioObservation(false, "cleanup waiting");
            engine.Tick(now + 250, true);
            AssertLedger(engine.State, "passed", "running", "pending");
            engine.Tick(now + 1250, false);
            AssertLedger(engine.State, "passed", "cancelled", "skipped");
            Assert.AreEqual("cancelled", engine.State.Failure.Code);
            Assert.That(engine.State.CleanupError, Does.Contain("Cleanup exceeded"));
            Assert.AreEqual("timed_out", engine.State.Steps.First(step => step.Iteration == 2 && step.Stage == "cleanup").Status);
            Assert.AreEqual(now + 1250, engine.State.IterationResults[1].FinishedUnixMs);
        }

        [Test]
        public void LaterFinalCleanupErrorFailsItsIterationBeforeNextIterationStarts()
        {
            var host = new Host();
            PlayScenarioEngine engine = null;
            bool pendingError = false;
            long now = 1000;
            host.EvaluateStep = (step, _) =>
            {
                if (step.Name == "Cleanup two" && engine.State.Steps[engine.State.Cursor].Iteration == 2)
                    pendingError = true;
                return new PlayScenarioObservation(true, "ready");
            };
            engine = LifecycleEngine(
                host,
                repeat: 3,
                onEvaluationCompleted: () =>
                {
                    if (pendingError)
                    {
                        pendingError = false;
                        engine.ObserveUnexpectedError("second final cleanup log", now);
                    }
                }
            );
            for (; engine.Running && now < 20000; now += 250)
                engine.Tick(now, true);
            AssertLedger(engine.State, "passed", "failed", "skipped");
            Assert.AreEqual(2, engine.State.Failure.Iteration);
            Assert.AreEqual(8, host.Calls);
            Assert.AreEqual("passed", engine.State.Steps.Last(step => step.Iteration == 2).Status);
            Assert.That(engine.State.CleanupError, Does.Contain("second final cleanup log"));
        }

        [TestCase(2)]
        [TestCase(3)]
        public void CheckpointAtMetricBoundaryKeepsCompletedCleanupAndCorrectIterationAttribution(int checkpointIteration)
        {
            var definition = LifecycleDefinition();
            definition.Metrics.Enabled = true;
            var host = new Host();
            var engine = LifecycleEngine(host, definition, repeat: 3);
            PlayScenarioRun checkpoint = null;
            long checkpointAt = 0;
            host.Sample = (iteration, now) =>
            {
                if (iteration == checkpointIteration)
                {
                    checkpoint = JsonConvert.DeserializeObject<PlayScenarioRun>(JsonConvert.SerializeObject(engine.State));
                    checkpointAt = now;
                }
                return new PlayScenarioMetricsSnapshot();
            };
            CompleteLedgerRun(engine);
            AssertLedger(checkpoint, "passed", checkpointIteration == 2 ? "running" : "passed", checkpointIteration == 2 ? "pending" : "running");
            if (checkpointIteration == 2)
                Assert.AreEqual(3, checkpoint.Steps[checkpoint.Cursor].Iteration);
            else
                Assert.AreEqual(checkpoint.Steps.Count, checkpoint.Cursor);
            var restoredHost = new Host();
            PlayScenarioStep capturedStep = null;
            var restored = new PlayScenarioEngine(checkpoint, restoredHost, (_, step, __) => capturedStep = step);
            restored.Interrupt("boundary reload", checkpointAt + 250);
            AssertLedger(checkpoint, "passed", checkpointIteration == 2 ? "failed" : "passed", checkpointIteration == 2 ? "skipped" : "failed");
            Assert.AreEqual(checkpointIteration, checkpoint.Failure.Iteration);
            Assert.AreEqual("cleanup", checkpoint.Failure.Stage);
            Assert.AreEqual("Cleanup two", capturedStep.Name);
            Assert.IsNull(checkpoint.CleanupError);
            Assert.AreEqual(0, restoredHost.Calls);
        }
    }
}
