using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests
{
    public sealed class PlayScenarioPlayerTests
    {
        private static PlayScenarioDefinition Definition() =>
            PlayScenarioDefinition.Parse(
                new JObject
                {
                    ["name"] = "player-unit",
                    ["steps"] = new JArray(
                        new JObject
                        {
                            ["name"] = "load",
                            ["action"] = "load_scene",
                            ["scene"] = "Assets/PlayerUnit.unity",
                        }
                    ),
                }
            );

        private static JObject Request() =>
            new JObject
            {
                ["schema_version"] = 1,
                ["job_id"] = new string('a', 32),
                ["scenario_name"] = "player-unit",
                ["definition_hash"] = new string('b', 64),
                ["repeat_count"] = 2,
                ["timeout_seconds"] = 60,
            };

        [Test]
        public void FrozenBundleRejectsChangedDefinitionHashAndSceneInventory()
        {
            JObject value = PlayScenarioPlayerBundle.Create(Definition(), "6000.0.69f1", "1.0.0");
            Assert.That(PlayScenarioPlayerBundle.Parse(value.ToString()).DefinitionHash, Is.EqualTo(PlayScenarioReproduction.Hash(Definition())));
            value["definition_hash"] = new string('0', 64);
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerBundle.Parse(value.ToString()));
            value = PlayScenarioPlayerBundle.Create(Definition(), "6000.0.69f1", "1.0.0");
            value["scene_paths"] = new JArray("Assets/Other.unity");
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerBundle.Parse(value.ToString()));
        }

        [TestCase("repeat_count", 0)]
        [TestCase("repeat_count", 11)]
        [TestCase("timeout_seconds", 0)]
        [TestCase("timeout_seconds", 1801)]
        public void RequestRejectsOutOfRangeOverrides(string field, int value)
        {
            JObject request = Request();
            request[field] = value;
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerRequest.Parse(request.ToString()));
        }

        [Test]
        public void RequestRejectsUnknownFieldsDuplicateFieldsAndNonStringRevision()
        {
            JObject request = Request();
            request["report_path"] = "elsewhere.json";
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerRequest.Parse(request.ToString()));
            request = Request();
            request["source_revision"] = 123;
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerRequest.Parse(request.ToString()));
            Assert.Throws<Newtonsoft.Json.JsonReaderException>(() => PlayScenarioPlayerRequest.Parse("{\"schema_version\":1,\"schema_version\":1}"));
        }

        [TestCase("resources")]
        [TestCase("metrics")]
        [TestCase("screenshot")]
        [TestCase("property")]
        [TestCase("ugui")]
        public void UnsupportedCapabilitiesFailBeforeHostExecution(string capability)
        {
            PlayScenarioDefinition definition = Definition();
            if (capability == "resources")
                definition.Resources.Enabled = true;
            if (capability == "metrics")
                definition.Metrics.Enabled = true;
            if (capability == "screenshot")
                definition.Diagnostics.ScreenshotOnFailure = true;
            if (capability == "property")
                definition.Steps[0].Property = new PlayScenarioPropertyCondition { Path = "m_Name", Equals = "test" };
            if (capability == "ugui")
                definition.Steps.Add(
                    new PlayScenarioStep
                    {
                        Name = "click",
                        Action = "click_ui",
                        TargetId = "button",
                    }
                );
            PlayScenarioException error = Assert.Throws<PlayScenarioException>(() => PlayScenarioPlayerCapabilities.Validate(definition, false));
            Assert.That(error.Failure.Code, Is.EqualTo("unsupported_capability"));
        }

        [Test]
        public void FinalReportCannotOverwriteExistingEvidence()
        {
            string directory = Path.Combine(Path.GetTempPath(), "mcp-player-unit-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                string report = Path.Combine(directory, "run.json");
                PlayScenarioPlayerFiles.WriteNew(report, "{\"status\":\"passed\"}", 100);
                Assert.Throws<IOException>(() => PlayScenarioPlayerFiles.WriteNew(report, "{}", 100));
                Assert.That(File.ReadAllText(report), Does.Contain("passed"));
                Assert.Throws<IOException>(() => PlayScenarioPlayerFiles.WriteNew(Path.Combine(directory, "oversized.json"), new string('x', 101), 100));
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        [Test]
        public void PlayerIdLookupCountsActualVisitsAndRejectsInactiveDuplicate()
        {
            Scene previous = SceneManager.GetActiveScene();
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(scene);
            var first = new GameObject("renamed");
            var duplicate = new GameObject("inactive");
            try
            {
                first.AddComponent<PlayScenarioTarget>().TargetId = "stable-id";
                duplicate.AddComponent<PlayScenarioTarget>().TargetId = "stable-id";
                duplicate.SetActive(false);
                var host = new PlayScenarioPlayerHost(Array.Empty<string>());
                Assert.Throws<PlayScenarioException>(() => host.Evaluate(new PlayScenarioStep { Action = "wait_object", TargetId = "stable-id" }, true));
                Assert.That(host.CaptureQueryCounts().TargetSearches, Is.EqualTo(1));
                Assert.That(host.CaptureQueryCounts().HierarchyVisits, Is.EqualTo(2));
                for (int index = 0; index < 10; index++)
                    host.CaptureQueryCounts();
                Assert.That(host.CaptureQueryCounts().TargetSearches, Is.EqualTo(1));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(first);
                UnityEngine.Object.DestroyImmediate(duplicate);
                SceneManager.SetActiveScene(previous);
                EditorSceneManager.CloseScene(scene, true);
            }
        }

        [Test]
        public void RepeatedPreparationIsReusedWhileDestroyedAndReplacedTargetsAreObservedFreshly()
        {
            Scene previous = SceneManager.GetActiveScene();
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(scene);
            GameObject target = new GameObject("CachedPlayerTarget", typeof(BoxCollider));
            var host = new PlayScenarioPlayerHost(Array.Empty<string>());
            var step = new PlayScenarioStep
            {
                Action = "wait_object",
                Target = "CachedPlayerTarget",
                Component = "UnityEngine.BoxCollider",
            };
            FieldInfo segmentsField = typeof(PlayScenarioPlayerHost).GetField("targetSegments", BindingFlags.Instance | BindingFlags.NonPublic);
            FieldInfo typeField = typeof(PlayScenarioPlayerHost).GetField("componentType", BindingFlags.Instance | BindingFlags.NonPublic);
            try
            {
                Assert.That(host.Evaluate(step, true).Ready, Is.True);
                object segments = segmentsField.GetValue(host);
                Assert.That(typeField.GetValue(host), Is.SameAs(typeof(BoxCollider)));
                UnityEngine.Object.DestroyImmediate(target);
                target = null;
                Assert.That(host.Evaluate(step, false).Ready, Is.False, "Destroyed targets must not remain cached.");
                target = new GameObject("CachedPlayerTarget");
                Assert.That(host.Evaluate(step, false).Ready, Is.False, "A replacement must satisfy its own component condition.");
                target.AddComponent<BoxCollider>();
                Assert.That(host.Evaluate(step, false).Ready, Is.True);
                Assert.That(segmentsField.GetValue(host), Is.SameAs(segments), "Repeated selector preparation must not allocate new segments.");
                Assert.That(typeField.GetValue(host), Is.SameAs(typeof(BoxCollider)));
                Assert.That(host.CaptureQueryCounts().TargetSearches, Is.EqualTo(4), "Every observation must still resolve the current native target.");
                step.Component = "UnityEngine.Transform";
                Assert.That(host.Evaluate(step, false).Ready, Is.True);
                Assert.That(typeField.GetValue(host), Is.SameAs(typeof(Transform)), "A changed condition must invalidate cached preparation.");
                host.Release();
                Assert.That(segmentsField.GetValue(host), Is.Null);
                Assert.That(typeField.GetValue(host), Is.Null);
            }
            finally
            {
                if (target != null)
                    UnityEngine.Object.DestroyImmediate(target);
                SceneManager.SetActiveScene(previous);
                EditorSceneManager.CloseScene(scene, true);
            }
        }

        [Test]
        public void ProductBuildRejectsUntitledScenesBeforeCreatingOutput()
        {
            Scene previous = SceneManager.GetActiveScene();
            Scene untitled = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            string output = Path.Combine(Path.GetTempPath(), "mcp-player-preflight-" + Guid.NewGuid().ToString("N"));
            try
            {
                InvalidOperationException error = Assert.Throws<InvalidOperationException>(() => PlayScenarioPlayerBuild.Build("does-not-exist", output));
                Assert.That(error.Message, Does.Contain("untitled"));
                Assert.That(Directory.Exists(output), Is.False);
                Assert.That(untitled.path, Is.Empty, "A product build must not implicitly save the user's untitled scene.");
            }
            finally
            {
                SceneManager.SetActiveScene(previous);
                EditorSceneManager.CloseScene(untitled, true);
            }
        }

        private sealed class ReadyHost : IPlayScenarioHost
        {
            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll) => new PlayScenarioObservation(true, "ready");

            public void Release() { }
        }

        [Test]
        public void LateUnexpectedLogChangesNativeSuccessAndExitCodeBeforePublication()
        {
            PlayScenarioDefinition definition = Definition();
            definition.CompletionStableMs = 0;
            PlayScenarioRun run = PlayScenarioEngine.Create(definition, new string('a', 32), 1, 30, 1000);
            var engine = new PlayScenarioEngine(run, new ReadyHost());
            engine.EnteredPlayMode();
            for (long now = 1000; engine.Running && now < 4000; now += 100)
                engine.Tick(now, true);
            Assert.That(run.Status, Is.EqualTo("succeeded"));
            Assert.That(PlayScenarioPlayerLogDrain.ExitCode(run), Is.Zero);
            var logs = new PlayScenarioLogBuffer(definition.LogPolicy);
            logs.Add(3000, "Error", "native listener error", "");
            int processed = 0;
            PlayScenarioPlayerLogDrain.Apply(logs, engine, ref processed, 3000);
            Assert.That(run.Status, Is.EqualTo("failed"));
            Assert.That(run.Failure.Code, Is.EqualTo("unexpected_log"));
            Assert.That(PlayScenarioPlayerLogDrain.ExitCode(run), Is.EqualTo(1));
            PlayScenarioPlayerLogDrain.Apply(logs, engine, ref processed, 3100);
            Assert.That(run.UnexpectedLogCount, Is.EqualTo(1));
        }

        [TestCase("log_only", false)]
        [TestCase("strict", true)]
        public void AllowedAndLogOnlyErrorsKeepNativeSuccess(string mode, bool allowed)
        {
            PlayScenarioDefinition definition = Definition();
            definition.CompletionStableMs = 0;
            definition.LogPolicy.Mode = mode;
            if (allowed)
                definition.LogPolicy.AllowedMessages.Add("allowed native message");
            var run = PlayScenarioEngine.Create(definition, new string('a', 32), 1, 30, 1000);
            var engine = new PlayScenarioEngine(run, new ReadyHost());
            engine.EnteredPlayMode();
            for (long now = 1000; engine.Running && now < 4000; now += 100)
                engine.Tick(now, true);
            var logs = new PlayScenarioLogBuffer(definition.LogPolicy);
            logs.Add(3000, "Error", "allowed native message", "");
            int processed = 0;
            PlayScenarioPlayerLogDrain.Apply(logs, engine, ref processed, 3000);
            Assert.That(run.Status, Is.EqualTo("succeeded"));
            Assert.That(PlayScenarioPlayerLogDrain.ExitCode(run), Is.Zero);
        }

        [Test]
        public void OrdinaryEditorAssemblyDoesNotContainPlayerBootstrap()
        {
            Assert.That(typeof(PlayScenarioPlayerHost).Assembly.GetType("MCPForUnity.Runtime.PlayScenarios.PlayScenarioPlayerRunner"), Is.Null);
        }
    }
}
