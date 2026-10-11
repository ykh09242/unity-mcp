using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.PlayScenarios
{
    public sealed class PlayScenarioEnvironmentTests
    {
        private string root;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "mcp-environment-store-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        [Test]
        public void NativeEditorCaptureRecordsActualRuntimeEvidenceWithCanonicalFields()
        {
            PlayScenarioEnvironment environment = new PlayScenarioEnvironmentCapture().CaptureOnce();
            Assert.That(environment.SchemaVersion, Is.EqualTo(1));
            Assert.That(environment.OperatingSystem, Is.EqualTo(SystemInfo.operatingSystem));
            Assert.That(environment.Platform, Is.EqualTo(Application.platform.ToString()));
            Assert.That(environment.BuildMode, Is.EqualTo("editor"));
            Assert.That(environment.RuntimeVersion, Is.EqualTo(System.Environment.Version.ToString()));
            Assert.That(environment.Architecture, Is.EqualTo(RuntimeInformation.ProcessArchitecture.ToString().ToLowerInvariant()));
#if ENABLE_MONO && !ENABLE_IL2CPP
            Assert.That(environment.ScriptingBackend, Is.EqualTo("mono"));
#elif ENABLE_IL2CPP && !ENABLE_MONO
            Assert.That(environment.ScriptingBackend, Is.EqualTo("il2cpp"));
#else
            Assert.That(environment.ScriptingBackend, Is.Null);
#endif
            Assert.That(
                JObject.FromObject(environment).Properties().Select(field => field.Name),
                Is.EquivalentTo(
                    new[] { "schema_version", "operating_system", "platform", "architecture", "scripting_backend", "build_mode", "runtime_version" }
                )
            );
        }

        [Test]
        public void ThousandEngineReportsReuseOneLaunchCaptureWithoutRecaptureOnRead()
        {
            int calls = 0;
            var capture = new PlayScenarioEnvironmentCapture(() =>
            {
                calls++;
                return Known();
            });
            Assert.That(calls, Is.Zero, "Creating a capture owner must not start any native activity.");
            PlayScenarioEnvironment snapshot = capture.CaptureOnce();
            for (int iteration = 1; iteration <= 1000; iteration++)
            {
                PlayScenarioRun run = Run(iteration);
                run.Reproduction.Environment = capture.CaptureOnce();
                Assert.That(run.Reproduction.Environment, Is.SameAs(snapshot));
                PlayScenarioRun restored = JsonConvert.DeserializeObject<PlayScenarioRun>(JsonConvert.SerializeObject(run));
                Assert.That(JToken.DeepEquals(JObject.FromObject(restored.Reproduction.Environment), JObject.FromObject(snapshot)), Is.True);
            }
            Assert.That(calls, Is.EqualTo(1));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UnavailableCaptureIsRecordedOnceAsExplicitNullWithoutChangingPrimaryFailure(bool throws)
        {
            int calls = 0;
            var capture = new PlayScenarioEnvironmentCapture(() =>
            {
                calls++;
                if (throws)
                    throw new IOException("Injected capture failure with private details.");
                return null;
            });
            PlayScenarioRun run = Run(1);
            run.Status = "failed";
            run.Error = "Original scenario outcome.";
            run.Failure = new PlayScenarioFailure { Code = "action_exception", Message = run.Error };
            PlayScenarioFailure original = run.Failure;
            run.Reproduction.Environment = capture.CaptureOnce();
            Assert.That(capture.CaptureOnce(), Is.SameAs(run.Reproduction.Environment));
            Assert.That(calls, Is.EqualTo(1));
            JObject environment = JObject.FromObject(
                run.Reproduction.Environment,
                JsonSerializer.Create(new JsonSerializerSettings { NullValueHandling = NullValueHandling.Ignore })
            );
            Assert.That(environment.Properties().Count(), Is.EqualTo(7));
            Assert.That(environment.Properties().Where(field => field.Name != "schema_version").All(field => field.Value.Type == JTokenType.Null), Is.True);
            Assert.That(environment.ToString(), Does.Not.Contain("private details"));
            Assert.That(run.Status, Is.EqualTo("failed"));
            Assert.That(run.Error, Is.EqualTo("Original scenario outcome."));
            Assert.That(run.Failure, Is.SameAs(original));
        }

        [Test]
        public void UnknownEnumValuesAndOversizedFieldsCannotPretendToMatchKnownEnvironment()
        {
            PlayScenarioEnvironment environment = PlayScenarioEnvironment.FromValues(
                new string('x', 257),
                new string('x', 65),
                new string('x', 33),
                "guessed",
                "guessed",
                new string('x', 65)
            );
            JObject serialized = JObject.FromObject(environment);
            Assert.That(serialized.Properties().Where(field => field.Name != "schema_version").All(field => field.Value.Type == JTokenType.Null), Is.True);
            Assert.That(PlayScenarioEnvironment.FromValues(" ", "\n", null, null, null, null).OperatingSystem, Is.Null);
            Assert.That(PlayScenarioEnvironment.FromValues(null, "\n", null, null, null, null).Platform, Is.Null);
        }

        [TestCase(254, true)]
        [TestCase(255, false)]
        public void UnicodeAtOperatingSystemBoundRemainsValidOrExplicitlyUnavailable(int prefix, bool known)
        {
            string value = new string('x', prefix) + "\U0001F680";
            PlayScenarioEnvironment environment = PlayScenarioEnvironment.FromValues(value, "WindowsPlayer", "x64", "mono", "development", "4.0.30319.42000");
            Assert.That(environment.OperatingSystem, Is.EqualTo(known ? value : null));
            Assert.DoesNotThrow(() => new UTF8Encoding(false, true).GetBytes(JsonConvert.SerializeObject(environment)));
            Assert.That(PlayScenarioEnvironment.FromValues(new string('x', 255) + "\uD83D", null, null, null, null, null).OperatingSystem, Is.Null);
        }

        [Test]
        public void SavedEnvironmentRoundTripsWithoutExposingPublicMutationOrSamplingLegacyReports()
        {
            PlayScenarioRun legacy = Run(1);
            JObject legacyJson = JObject.FromObject(legacy);
            Assert.That(((JObject)legacyJson["reproduction"]).Property("environment"), Is.Null);
            Assert.That(JsonConvert.DeserializeObject<PlayScenarioRun>(legacyJson.ToString()).Reproduction.Environment, Is.Null);
            Assert.That(typeof(PlayScenarioEnvironment).GetProperties().All(property => property.GetSetMethod() == null), Is.True);

            legacy.Reproduction.Environment = Known();
            string saved = JsonConvert.SerializeObject(legacy);
            PlayScenarioRun restored = JsonConvert.DeserializeObject<PlayScenarioRun>(saved);
            Assert.That(JToken.DeepEquals(JObject.FromObject(restored.Reproduction.Environment), JObject.FromObject(legacy.Reproduction.Environment)), Is.True);
            Assert.That(restored.Reproduction.Environment.RuntimeVersion, Is.EqualTo("4.0.30319.42000"));
            JObject unversioned = JObject.FromObject(legacy.Reproduction.Environment);
            unversioned.Remove("schema_version");
            Assert.That(
                unversioned.ToObject<PlayScenarioEnvironment>().SchemaVersion,
                Is.Zero,
                "Reading incomplete legacy metadata must not invent a supported schema."
            );
        }

        [TestCase("known")]
        [TestCase("unknown")]
        [TestCase("absent")]
        [TestCase("null")]
        public void ActualStoreSaveLoadAndHistoryPreserveKnownUnknownAndLegacyEnvironment(string kind)
        {
            var store = new PlayScenarioStore(root);
            PlayScenarioRun run = Terminal();
            run.Reproduction.Environment =
                kind == "known" ? Known()
                : kind == "unknown" ? PlayScenarioEnvironment.FromValues(null, null, null, null, null, null)
                : null;
            string path = Path.Combine(root, store.SaveReport(run));
            if (kind == "null")
            {
                JObject saved = JObject.Parse(File.ReadAllText(path));
                saved["reproduction"]["environment"] = JValue.CreateNull();
                File.WriteAllText(path, saved.ToString(Formatting.None), new UTF8Encoding(false, true));
            }
            byte[] original = File.ReadAllBytes(path);
            PlayScenarioRun loaded = store.GetReport(run.JobId);
            PlayScenarioRun history = store.ListReports(run.Scenario.Name).Single();
            foreach (PlayScenarioRun report in new[] { loaded, history })
            {
                Assert.That(report.Status, Is.EqualTo(run.Status));
                Assert.That(report.Error, Is.EqualTo(run.Error));
                if (run.Reproduction.Environment == null)
                    Assert.That(report.Reproduction.Environment, Is.Null);
                else
                    Assert.That(
                        JToken.DeepEquals(JObject.FromObject(report.Reproduction.Environment), JObject.FromObject(run.Reproduction.Environment)),
                        Is.True
                    );
            }
            Assert.That(File.ReadAllBytes(path), Is.EqualTo(original), "Reading legacy metadata must not rewrite or capture launch evidence.");
        }

        [TestCase("scalar")]
        [TestCase("array")]
        [TestCase("missing_field")]
        [TestCase("unknown_field")]
        [TestCase("missing_version")]
        [TestCase("future_version")]
        [TestCase("string_version")]
        [TestCase("float_version")]
        [TestCase("null_version")]
        [TestCase("oversized_os")]
        [TestCase("wrong_type")]
        [TestCase("backend")]
        [TestCase("mode")]
        [TestCase("control")]
        public void ActualStoredReportRejectsMalformedPresentEnvironment(string mutation)
        {
            var store = new PlayScenarioStore(root);
            PlayScenarioRun run = Terminal();
            run.Reproduction.Environment = Known();
            string path = Path.Combine(root, store.SaveReport(run));
            JObject saved = JObject.Parse(File.ReadAllText(path));
            var environment = (JObject)saved["reproduction"]["environment"];
            switch (mutation)
            {
                case "scalar":
                    saved["reproduction"]["environment"] = true;
                    break;
                case "array":
                    saved["reproduction"]["environment"] = new JArray();
                    break;
                case "missing_field":
                    environment.Remove("operating_system");
                    break;
                case "unknown_field":
                    environment["device_identifier"] = "unowned";
                    break;
                case "missing_version":
                    environment.Remove("schema_version");
                    break;
                case "future_version":
                    environment["schema_version"] = 2;
                    break;
                case "string_version":
                    environment["schema_version"] = "1";
                    break;
                case "float_version":
                    environment["schema_version"] = 1.0;
                    break;
                case "null_version":
                    environment["schema_version"] = JValue.CreateNull();
                    break;
                case "oversized_os":
                    environment["operating_system"] = new string('x', 257);
                    break;
                case "wrong_type":
                    environment["architecture"] = 64;
                    break;
                case "backend":
                    environment["scripting_backend"] = "other";
                    break;
                case "mode":
                    environment["build_mode"] = "other";
                    break;
                case "control":
                    environment["platform"] = "Windows\nPlayer";
                    break;
            }
            File.WriteAllText(path, saved.ToString(Formatting.None), new UTF8Encoding(false, true));
            Assert.Throws<ArgumentException>(() => store.GetReport(run.JobId));
            Assert.Throws<ArgumentException>(() => store.ListReports(run.Scenario.Name));
        }

        [Test]
        public void ActualStoreRejectsInvalidUtf16BeforeReplacingSavedEvidenceAndAcceptsBoundaryPair()
        {
            var store = new PlayScenarioStore(root);
            PlayScenarioRun run = Terminal();
            run.Reproduction.Environment = PlayScenarioEnvironment.FromValues(
                new string('x', 254) + "\U0001F680",
                "WindowsPlayer",
                "x64",
                "mono",
                "development",
                "4.0.30319.42000"
            );
            string path = Path.Combine(root, store.SaveReport(run));
            byte[] original = File.ReadAllBytes(path);
            Assert.That(store.GetReport(run.JobId).Reproduction.Environment.OperatingSystem, Is.EqualTo(run.Reproduction.Environment.OperatingSystem));
            JObject invalid = JObject.FromObject(run.Reproduction.Environment);
            invalid["operating_system"] = new string('x', 255) + "\uD83D";
            run.Reproduction.Environment = invalid.ToObject<PlayScenarioEnvironment>();
            Assert.Throws<ArgumentException>(() => store.SaveReport(run));
            Assert.That(File.ReadAllBytes(path), Is.EqualTo(original));
            Assert.That(store.GetReport(run.JobId).Reproduction.Environment.OperatingSystem, Is.EqualTo(new string('x', 254) + "\U0001F680"));
        }

        [Test]
        public void ActualSuiteSaveLoadUsesTypedEnvironmentValidationForEmbeddedChild()
        {
            PlayScenarioRun run = Terminal();
            run.Reproduction.Environment = Known();
            var definition = new PlayScenarioSuiteDefinition { Name = "environment-suite" };
            definition.Scenarios.Add(run.Scenario.Name);
            var suite = new PlayScenarioSuiteReport
            {
                SuiteId = new string('b', 32),
                Suite = definition,
                Status = run.Status,
                RepeatCount = 1,
                TimeoutSeconds = 60,
                StartedUnixMs = run.StartedUnixMs,
                FinishedUnixMs = run.FinishedUnixMs,
            };
            suite.Scenarios.Add(
                new PlayScenarioSuiteChild
                {
                    Name = run.Scenario.Name,
                    Status = run.Status,
                    DefinitionHash = run.Reproduction.DefinitionHash,
                    JobId = run.JobId,
                    Report = JObject.FromObject(run),
                }
            );
            var store = new PlayScenarioSuiteStore(root);
            string path = Path.Combine(root, store.SaveReport(suite));
            JObject child = store.GetReport(suite.SuiteId).Scenarios.Single().Report;
            Assert.That(JToken.DeepEquals(child["reproduction"]["environment"], JObject.FromObject(run.Reproduction.Environment)), Is.True);
            JObject saved = JObject.Parse(File.ReadAllText(path));
            saved["scenarios"][0]["report"]["reproduction"]["environment"]["schema_version"] = "1";
            File.WriteAllText(path, saved.ToString(Formatting.None), new UTF8Encoding(false, true));
            Assert.Throws<ArgumentException>(() => store.GetReport(suite.SuiteId));
        }

        private static PlayScenarioRun Terminal()
        {
            PlayScenarioRun run = Run(1);
            var engine = new PlayScenarioEngine(run, new NeverEvaluatedHost());
            engine.Cancel(run.StartedUnixMs + 1);
            Assert.That(engine.Running, Is.False);
            return run;
        }

        private sealed class NeverEvaluatedHost : IPlayScenarioHost
        {
            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll) =>
                throw new InvalidOperationException("Cancelled before evaluation.");

            public void Release() { }
        }

        private static PlayScenarioEnvironment Known() =>
            PlayScenarioEnvironment.FromValues("Test OS 1", "WindowsPlayer", "x64", "mono", "development", "4.0.30319.42000");

        private static PlayScenarioRun Run(int iteration)
        {
            var definition = new PlayScenarioDefinition { Name = "environment-test" };
            definition.Steps.Add(
                new PlayScenarioStep
                {
                    Name = "load",
                    Action = "load_scene",
                    Scene = "Assets/Test.unity",
                }
            );
            return PlayScenarioEngine.Create(definition, new string('a', 32), 1, 60, 1000L + iteration * 1000L);
        }
    }
}
