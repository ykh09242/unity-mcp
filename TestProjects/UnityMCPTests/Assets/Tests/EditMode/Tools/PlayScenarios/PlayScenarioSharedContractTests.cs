using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Windows.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioSharedContractTests
    {
        private static string FixturePath => Path.Combine(Application.dataPath, "Tests/EditMode/Tools/PlayScenarios/Fixtures/shared-report-contract.json");

        private static IEnumerable<TestCaseData> Cases(string section)
        {
            JObject corpus = PlayScenarioPlayerSessionEditor.ReadJson(FixturePath);
            Assert.AreEqual(1, (int)corpus["schema_version"]);
            foreach (JObject item in (JArray)corpus[section])
                yield return new TestCaseData(item).SetName("SharedContract." + (string)item["name"]);
        }

        private static IEnumerable<TestCaseData> NativeCases() => Cases("native_cases");

        private static IEnumerable<TestCaseData> SessionCases() => Cases("session_cases");

        private static IEnumerable<TestCaseData> EngineCases() => Cases("engine_cases");

        private static IEnumerable<TestCaseData> UnicodeCases() => Cases("unicode_cases");

        private static IEnumerable<TestCaseData> SoakCases() => Cases("soak_cases");

        [TestCaseSource(nameof(NativeCases))]
        public void NativeCorpusUsesStoreAndImportedReportAdmission(JObject item)
        {
            var report = (JObject)item["report"].DeepClone();
            int repeats = (int)report["repeat_count"];
            if (!(bool)item["accepted"])
            {
                Assert.That(() => PlayScenarioStore.ValidateIterationResults(report, repeats), Throws.ArgumentException);
                Assert.That(() => PlayScenarioPlayerSessionEditor.ValidateReport(report), Throws.Exception);
                return;
            }
            Assert.DoesNotThrow(() => PlayScenarioStore.ValidateIterationResults(report, repeats));
            Assert.DoesNotThrow(() => PlayScenarioPlayerSessionEditor.ValidateReport(report));
            if (report["iteration_results"] is JArray ledger)
            {
                JObject counts = (JObject)item["expected_counts"];
                Assert.AreEqual((int)counts["planned"], ledger.Count);
                Assert.AreEqual((int)counts["executed"], ledger.Count(row => row["started_unix_ms"].Type == JTokenType.Integer));
                foreach (string status in new[] { "passed", "failed", "timed_out", "cancelled", "skipped", "pending", "running" })
                    Assert.AreEqual((int)counts[status], ledger.Count(row => (string)row["status"] == status), status);
            }
        }

        [TestCaseSource(nameof(SessionCases))]
        public void SessionCorpusUsesRealEditorImporterValidation(JObject item)
        {
            var session = (JObject)item["session"].DeepClone();
            string root = Path.Combine(Path.GetTempPath(), "unity-mcp-contract-" + Guid.NewGuid().ToString("N"));
            if ((bool)item["accepted"])
                Assert.DoesNotThrow(() => PlayScenarioPlayerSessionEditor.Validate(session, root));
            else
                Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.Validate(session, root));
        }

        private sealed class Host : IPlayScenarioHost
        {
            internal PlayScenarioEngine Engine;
            internal bool FailSecond;
            internal int Releases;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool first)
            {
                if (FailSecond && Engine.State.Steps[Engine.State.Cursor].Iteration == 2)
                    throw new InvalidOperationException("Shared corpus second iteration failure.");
                return new PlayScenarioObservation(true, "ready");
            }

            public void Release() => Releases++;
        }

        [TestCaseSource(nameof(EngineCases))]
        public void ActualNativeEngineOutputsMatchIndependentCorpus(JObject item)
        {
            JObject definition = JObject.Parse(
                "{\"name\":\"shared-contract\",\"steps\":[{\"name\":\"Scene\",\"action\":\"load_scene\",\"scene\":\"Assets/Scenes/Test.unity\"}]}"
            );
            var host = new Host { FailSecond = (string)item["driver"] == "fail_second" };
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(PlayScenarioDefinition.Parse(definition), new string('1', 32), 3, 30, 0), host);
            host.Engine = engine;
            switch ((string)item["driver"])
            {
                case "cancel":
                    engine.Cancel(1);
                    break;
                case "timeout":
                    engine.Tick(30000, false);
                    break;
                case "pass":
                case "fail_second":
                    engine.EnteredPlayMode();
                    for (long now = 0; engine.Running && now < 10000; now += 10)
                        engine.Tick(now, true);
                    break;
                default:
                    Assert.Fail("Unknown shared corpus engine driver.");
                    break;
            }
            Assert.AreEqual((string)item["status"], engine.State.Status);
            CollectionAssert.AreEqual(((JArray)item["expected_statuses"]).Values<string>(), engine.State.IterationResults.Select(row => row.Status));
            var report = JObject.FromObject(engine.State);
            Assert.DoesNotThrow(() => PlayScenarioStore.ValidateReport(report));
            Assert.DoesNotThrow(() => PlayScenarioPlayerSessionEditor.ValidateReport(report));
            Assert.AreEqual((int)item["expected_counts"]["executed"], engine.State.IterationResults.Count(row => row.StartedUnixMs.HasValue));
            Assert.AreEqual(1, host.Releases);
        }

        [TestCaseSource(nameof(UnicodeCases))]
        public void SharedUnicodeControlBytesSurviveNativeJsonExport(JObject item)
        {
            string text = (string)item["value"];
            string encoded = JsonConvert.SerializeObject(text, new JsonSerializerSettings { StringEscapeHandling = StringEscapeHandling.EscapeNonAscii });
            Assert.AreEqual((int)item["json_utf8_bytes"], new UTF8Encoding(false, true).GetByteCount(encoded));
            Assert.AreEqual(text, JsonConvert.DeserializeObject<string>(encoded));
        }

        [TestCaseSource(nameof(SoakCases))]
        public void SameProcessSoakImporterUsesExactSharedJournalBytes(JObject item)
        {
            string root = Path.Combine(Path.GetTempPath(), "unity-mcp-soak-contract-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                var summary = (JObject)item["summary"].DeepClone();
                string file = Path.Combine(root, "soak.json");
                File.WriteAllText(file, summary.ToString(Formatting.None), new UTF8Encoding(false, true));
                File.WriteAllBytes(Path.Combine(root, "iterations.jsonl"), new UTF8Encoding(false, true).GetBytes((string)item["journal_text"]));
                var report = (JObject)item["run"].DeepClone();
                File.WriteAllText(Path.Combine(root, "run.json"), report.ToString(Formatting.None), new UTF8Encoding(false, true));
                switch ((string)item["editor_result"])
                {
                    case "rejected":
                        Assert.That(() => PlayScenarioSoakEvidence.Read(file), Throws.Exception);
                        break;
                    case "complete":
                        PlayScenarioSoakEvidence complete = PlayScenarioSoakEvidence.Read(file);
                        StringAssert.StartsWith("Complete:", complete.Completeness);
                        Assert.AreEqual((int)summary["recorded_iterations"], complete.Rows.Count);
                        break;
                    case "incomplete":
                        PlayScenarioSoakEvidence incomplete = PlayScenarioSoakEvidence.Read(file);
                        StringAssert.StartsWith("Incomplete:", incomplete.Completeness);
                        break;
                    default:
                        Assert.Fail("Unknown shared corpus importer result.");
                        break;
                }
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }
    }
}
