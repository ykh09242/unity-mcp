using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioStepObservationTests
    {
        private sealed class Host : IPlayScenarioHost
        {
            public int Calls;
            public int Releases;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
            {
                Calls++;
                return new PlayScenarioObservation(true, "ready");
            }

            public void Release() => Releases++;
        }

        private sealed class Observer : IPlayScenarioStepObserver
        {
            public PlayScenarioStepResult Result;
            public bool Ready;
            public int Disposals;
            public int Aborts;

            public void Begin(PlayScenarioStep step, PlayScenarioStepResult result, long now) => Result = result;

            public bool Complete(long now) => Ready;

            public void Abort(string reason, long now) => Aborts++;

            public void Dispose() => Disposals++;
        }

        private static PlayScenarioDefinition Definition(string options = "") =>
            PlayScenarioDefinition.Parse(
                JObject.Parse("{'name':'observation','steps':[{'name':'Menu','action':'load_scene','scene':'Assets/Menu.unity'" + options + "}]}")
            );

        [Test]
        public void EightKCheckpointDefinitionAndSubstantialEncodedEvidenceArePreserved()
        {
            var definition = Definition(",'screenshot':{'name':'large','width':8192,'height':8192}");
            string root = Path.Combine(Path.GetTempPath(), "mcp-large-capture-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = new PlayScenarioStore(root);
                store.Save(definition);
                Assert.That(store.Get(definition.Name).Steps[0].Screenshot.Width, Is.EqualTo(8192));
                var run = PlayScenarioEngine.Create(definition, new string('d', 32), 1, 300, 1000);
                run.Steps[0].Screenshot = new PlayScenarioScreenshotResult { Name = "large" };
                byte[] encoded = new byte[4 * 1024 * 1024 + 1];
                string name = store.SaveCheckpointScreenshot(run.JobId, run.Steps[0], encoded);
                Assert.That(new FileInfo(Path.Combine(root, "Library/MCPForUnity/PlayScenarioRuns", name)).Length, Is.EqualTo(encoded.Length));
            }
            finally
            {
                if (Directory.Exists(root))
                    Directory.Delete(root, true);
            }
        }

        [Test]
        public void PersistedLegacyReportsOmitObservationFieldsAndKeepRequiredStepFieldsStrict()
        {
            string directory = Path.Combine(Path.GetTempPath(), "mcp-observation-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = new PlayScenarioStore(directory);
                PlayScenarioRun run = PlayScenarioEngine.Create(Definition(), new string('b', 32), 1, 30, 1000);
                string relative = store.SaveReport(run);
                PlayScenarioRun loaded = store.GetReport(run.JobId);
                Assert.That(loaded.Steps[0].Performance, Is.Null);
                Assert.That(loaded.Steps[0].Screenshot, Is.Null);
                string path = Path.Combine(directory, relative);
                JObject serialized = JObject.Parse(File.ReadAllText(path));
                Assert.That(serialized["steps"][0]["performance"], Is.Null);
                Assert.That(serialized["steps"][0]["screenshot"], Is.Null);
                ((JObject)serialized["steps"][0]).Remove("status");
                File.WriteAllText(path, serialized.ToString(Formatting.None));
                Assert.Throws<ArgumentException>(() => store.GetReport(run.JobId));
            }
            finally
            {
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
            }
        }

        [Test]
        public void PersistedNewObservationEvidenceRoundTripsAndRejectsUnknownFieldsAndTraversal()
        {
            string directory = Path.Combine(Path.GetTempPath(), "mcp-observation-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = new PlayScenarioStore(directory);
                PlayScenarioDefinition definition = Definition(",'performance':{'max_duration_ms':100},'screenshot':{'name':'menu','width':1,'height':1}");
                PlayScenarioRun run = PlayScenarioEngine.Create(definition, new string('c', 32), 1, 30, 1000);
                run.Steps[0].Status = "passed";
                run.Steps[0].Performance = new PlayScenarioPerformanceResult { DurationMs = 12.5, Passed = true };
                run.Steps[0].Screenshot = new PlayScenarioScreenshotResult
                {
                    Name = "menu",
                    Status = "captured",
                    RequestedWidth = 1,
                    RequestedHeight = 1,
                    Width = 1,
                    Height = 1,
                    Sha256 = new string('a', 64),
                    Path = run.JobId + "-1-main-0-menu.png",
                };
                string relative = store.SaveReport(run);
                Assert.That(store.GetReport(run.JobId).Steps[0].Performance.DurationMs, Is.EqualTo(12.5));
                string path = Path.Combine(directory, relative);
                JObject serialized = JObject.Parse(File.ReadAllText(path));
                serialized["steps"][0]["screenshot"]["path"] = "../escaped.png";
                File.WriteAllText(path, serialized.ToString(Formatting.None));
                Assert.Throws<ArgumentException>(() => store.GetReport(run.JobId));
                serialized["steps"][0]["screenshot"]["path"] = run.Steps[0].Screenshot.Path;
                serialized["steps"][0]["performance"]["unknown"] = 1;
                File.WriteAllText(path, serialized.ToString(Formatting.None));
                Assert.Throws<ArgumentException>(() => store.GetReport(run.JobId));
            }
            finally
            {
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
            }
        }

        [Test]
        public void OldDefinitionsOmitNewOptionsAndRetainTheirSerializedShape()
        {
            JObject serialized = JObject.FromObject(Definition());
            Assert.That(serialized["steps"][0]["performance"], Is.Null);
            Assert.That(serialized["steps"][0]["screenshot"], Is.Null);
        }

        [TestCase("'performance':{'sample_limit':0}")]
        [TestCase("'performance':{'max_duration_ms':-1}")]
        [TestCase("'performance':{'max_frame_time_p95_ms':null}")]
        [TestCase("'screenshot':{'name':'../bad','width':1,'height':1}")]
        [TestCase("'screenshot':{'name':'ok','width':8193,'height':8192}")]
        [TestCase("'screenshot':{'name':'ok','width':1}")]
        public void InvalidObservationOptionsAreRejected(string options) => Assert.Throws<ArgumentException>(() => Definition("," + options));

        [Test]
        public void BudgetsAcceptEqualityAndFailAboveBoundary()
        {
            var options = new PlayScenarioPerformanceOptions
            {
                MaxDurationMs = 100,
                MaxFrameTimeP95Ms = 20,
                MaxGcAllocatedBytesPerFrame = 64,
            };
            var samples = new PlayScenarioPerformanceSamples(options);
            samples.AddFrame(20, 64);
            Assert.That(samples.Finish(100).Passed, Is.True);
            Assert.That(samples.Finish(100.01).Passed, Is.False);
            samples.AddFrame(20.01, 65);
            Assert.That(samples.Finish(100).Passed, Is.False);
        }

        [Test]
        public void P95UsesNearestRankAndRealFramesRatherThanPolls()
        {
            var samples = new PlayScenarioPerformanceSamples(new PlayScenarioPerformanceOptions { MaxFrameTimeP95Ms = 19 });
            for (int index = 1; index <= 20; index++)
                samples.AddFrame(index, null);
            PlayScenarioPerformanceResult result = samples.Finish(20);
            Assert.That(result.FrameTimeP95Ms, Is.EqualTo(19));
            Assert.That(result.FrameSampleCount, Is.EqualTo(20));
            Assert.That(result.Passed, Is.True);
        }

        [Test]
        public void MissingCountersAndOverflowCannotPassAsZeroOrPartialEvidence()
        {
            var missing = new PlayScenarioPerformanceSamples(new PlayScenarioPerformanceOptions { MaxGcAllocatedBytesPerFrame = 0 });
            missing.AddFrame(16, null);
            Assert.That(missing.Finish(1).Passed, Is.False);
            Assert.That(missing.Finish(1).GcAllocatedBytesPerFrameMax, Is.Null);
            var overflow = new PlayScenarioPerformanceSamples(new PlayScenarioPerformanceOptions { MaxFrameTimeP95Ms = 20, SampleLimit = 1 });
            overflow.AddFrame(16, null);
            overflow.AddFrame(100, null);
            PlayScenarioPerformanceResult result = overflow.Finish(1);
            Assert.That(result.Passed, Is.False);
            Assert.That(result.SampleLimitExceeded, Is.True);
            Assert.That(result.ObservedFrameCount, Is.EqualTo(2));
            Assert.That(result.FrameSampleCount, Is.EqualTo(1));
        }

        [Test]
        public void EmptySelectedFrameMetricsFailButDurationOnlyCanPass()
        {
            Assert.That(new PlayScenarioPerformanceSamples(new PlayScenarioPerformanceOptions { MaxFrameTimeP95Ms = 10 }).Finish(0).Passed, Is.False);
            Assert.That(new PlayScenarioPerformanceSamples(new PlayScenarioPerformanceOptions { MaxDurationMs = 10 }).Finish(10).Passed, Is.True);
        }

        [Test]
        public void PendingCaptureNeverReplaysEffectsAndCancelReleasesOnce()
        {
            var host = new Host();
            var observer = new Observer();
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(Definition(), new string('a', 32), 1, 30, 1000), host, stepObserver: observer);
            engine.EnteredPlayMode();
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            engine.Tick(1500, true);
            Assert.That(host.Calls, Is.EqualTo(1));
            engine.Cancel(1750);
            engine.Release();
            Assert.That(engine.State.Status, Is.EqualTo("cancelled"));
            Assert.That(observer.Aborts, Is.GreaterThanOrEqualTo(1));
            Assert.That(observer.Disposals, Is.EqualTo(1));
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void UnavailableScreenshotFailsWithoutReplacingExistingFailure()
        {
            var host = new Host();
            var engine = new PlayScenarioEngine(
                PlayScenarioEngine.Create(Definition(",'screenshot':{'name':'menu','width':1,'height':1}"), new string('a', 32), 1, 30, 1000),
                host
            );
            engine.EnteredPlayMode();
            engine.Tick(1000, true);
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("screenshot_checkpoint_failed"));
            Assert.That(engine.State.Steps[0].Screenshot.Status, Is.EqualTo("unavailable"));
            Assert.That(engine.State.Steps[0].Screenshot.Path, Is.Null);
            Assert.That(host.Calls, Is.EqualTo(1));
            engine.Cancel(1001);
            Assert.That(engine.State.Failure.Code, Is.EqualTo("screenshot_checkpoint_failed"));
        }

        [Test]
        public void SimultaneousBudgetAndCaptureFailuresKeepTheFirstErrorAndItsCodeTogether()
        {
            var host = new Host();
            var observer = new Observer();
            var engine = new PlayScenarioEngine(
                PlayScenarioEngine.Create(
                    Definition(",'performance':{'max_duration_ms':100},'screenshot':{'name':'menu','width':1,'height':1}"),
                    new string('d', 32),
                    1,
                    30,
                    1000
                ),
                host,
                stepObserver: observer
            );
            engine.EnteredPlayMode();
            engine.Tick(1000, true);
            observer.Result.Performance = new PlayScenarioPerformanceResult { Passed = false, Error = "Performance budget exceeded." };
            observer.Result.Screenshot = new PlayScenarioScreenshotResult
            {
                Name = "menu",
                Status = "unavailable",
                Error = "Capture unavailable.",
            };
            observer.Ready = true;
            engine.Tick(1100, true);
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("performance_budget_failed"));
            Assert.That(engine.State.Failure.Message, Is.EqualTo("Performance budget exceeded."));
            Assert.That(engine.State.Error, Is.EqualTo("Performance budget exceeded."));
            Assert.That(host.Calls, Is.EqualTo(1));
        }

        [Test]
        public void ObservationBudgetFailureRunsCleanupWithoutOverwritingOriginalCause()
        {
            PlayScenarioDefinition definition = Definition();
            definition.CleanupSteps.Add(
                new PlayScenarioStep
                {
                    Name = "Cleanup",
                    Action = "wait_scene",
                    Scene = "Assets/Menu.unity",
                }
            );
            var host = new Host();
            var observer = new Observer { Ready = true };
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(definition, new string('a', 32), 1, 30, 1000), host, stepObserver: observer);
            engine.EnteredPlayMode();
            observer.Ready = false;
            engine.Tick(1000, true);
            observer.Result.Performance = new PlayScenarioPerformanceResult { Passed = false, Error = "budget exceeded" };
            observer.Ready = true;
            engine.Tick(1100, true);
            Assert.That(engine.State.Phase, Is.EqualTo("cleaning"));
            engine.Tick(1200, true);
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("performance_budget_failed"));
            Assert.That(engine.State.Steps[1].Status, Is.EqualTo("passed"));
        }
    }
}
