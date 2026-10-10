using System;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioEngineTests
    {
        private sealed class Host : IPlayScenarioHost, IPlayScenarioMetricsHost
        {
            public int Calls;
            public int Loads;
            public int Clicks;
            public int Releases;
            public int MetricCalls;
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
    }
}
