using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Windows.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    public class PlayScenarioPlayerSessionEditorTests
    {
        private string root;
        private SessionViewerTestWindow testWindow;

        private sealed class SessionViewerTestWindow : UnityEditor.EditorWindow { }

        private const string SessionId = "11111111111111111111111111111111";
        private const string JobId = "22222222222222222222222222222222";
        private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "unity-mcp-session-viewer-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            testWindow = ScriptableObject.CreateInstance<SessionViewerTestWindow>();
            testWindow.Show();
            yield return null;
        }

        [TearDown]
        public void TearDown()
        {
            if (testWindow != null)
                UnityEngine.Object.DestroyImmediate(testWindow);
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private static JObject Counts(int planned = 2, int passed = 1, int failed = 1) =>
            new JObject
            {
                ["planned"] = planned,
                ["executed"] = passed + failed,
                ["passed"] = passed,
                ["failed"] = failed,
                ["timed_out"] = 0,
                ["cancelled"] = 0,
                ["skipped"] = 0,
                ["unknown"] = 0,
                ["pending"] = 0,
                ["running"] = 0,
            };

        private static JObject Outcome() =>
            new JObject
            {
                ["schema_version"] = 2,
                ["session_id"] = SessionId,
                ["job_id"] = JobId,
                ["sequence"] = 1,
                ["mode"] = "shared-batches",
                ["repeat_count"] = 2,
                ["status"] = "failed",
                ["artifact_directory"] = "runs/" + JobId,
                ["iteration_counts"] = Counts(),
                ["iteration_results_source"] = "native",
                ["native_report_available"] = true,
                ["native_report_sha256"] = new string('0', 64),
                ["failure"] = new JObject
                {
                    ["code"] = "assertion_failed",
                    ["message"] = "Saved failure",
                    ["stage"] = "cleanup",
                    ["iteration"] = 2,
                    ["step_index"] = 0,
                },
            };

        private static JObject Session() =>
            new JObject
            {
                ["schema_version"] = 2,
                ["session_id"] = SessionId,
                ["mode"] = "shared-batches",
                ["status"] = "failed",
                ["requested_iterations"] = 2,
                ["outcomes_recorded"] = 1,
                ["completed_iterations"] = new JObject { ["shared-batches"] = 2 },
                ["iteration_counts"] = new JObject { ["shared-batches"] = Counts() },
                ["last_outcomes"] = new JArray(Outcome()),
            };

        private string SaveSession(JObject session = null)
        {
            string file = Path.Combine(root, "session.json");
            File.WriteAllText(file, (session ?? Session()).ToString(), new UTF8Encoding(false));
            return file;
        }

        private static void Click(VisualElement parent, string name)
        {
            Button button = parent.Q<Button>(name);
            Assert.IsNotNull(button, name);
            Assert.IsTrue(button.enabledSelf, name);
            typeof(Clickable).GetMethod("Invoke", PrivateInstance).Invoke(button.clickable, new object[] { null });
        }

        private VisualElement Viewer(string file, out Func<string> readMessage)
        {
            string lastMessage = "";
            var parent = new VisualElement();
            testWindow.rootVisualElement.Add(parent);
            Assert.IsNotNull(parent.panel);
            new PlayScenarioPlayerSessionEditor(parent, (text, _) => lastMessage = text, () => file);
            readMessage = () => lastMessage;
            return parent;
        }

        [Test]
        public void ImportShowsTruthfulCountersAndMissingEvidenceWithoutMutatingFiles()
        {
            string file = SaveSession();
            string original = File.ReadAllText(file);
            VisualElement parent = Viewer(file, out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Imported", readMessage());
            string summary = string.Join("\n", parent.Q("playerSessionSummary").Query<Label>().ToList().Select(label => label.text));
            StringAssert.Contains("planned 2; executed 2; passed 1; failed 1", summary);
            StringAssert.Contains("unknown 0; pending 0; running 0", summary);
            Click(parent, "playerOutcome_1");
            Assert.IsTrue(parent.Q("playerSessionDetails").Query<Label>().ToList().Any(label => label.text.Contains("Unavailable")));
            Assert.AreEqual(original, File.ReadAllText(file));
            CollectionAssert.AreEquivalent(new[] { "session.json" }, Directory.GetFiles(root).Select(Path.GetFileName));
        }

        [Test]
        public void LegacyImportDoesNotInferPassedIterationsFromCompletionOrSucceededOutcome()
        {
            JObject session = Session();
            session["schema_version"] = 1;
            session.Remove("iteration_counts");
            JObject row = (JObject)session["last_outcomes"][0];
            row["schema_version"] = 1;
            row["status"] = "succeeded";
            row.Remove("iteration_counts");
            row.Remove("iteration_results_source");
            VisualElement parent = Viewer(SaveSession(session), out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Imported", readMessage());
            string summary = string.Join("\n", parent.Q("playerSessionSummary").Query<Label>().ToList().Select(label => label.text));
            StringAssert.Contains("counts unavailable", summary);
            StringAssert.Contains("do not prove passes", summary);
            StringAssert.DoesNotContain("passed 2", summary);
        }

        [Test]
        public void RecoveredSnapshotWithNullArtifactNeverFollowsOriginalSessionPaths()
        {
            JObject session = Session();
            session["status"] = "interrupted";
            session["recovered"] = true;
            session["session_error"] = "session_recovered_incomplete";
            session["last_outcomes"][0]["artifact_directory"] = null;
            VisualElement parent = Viewer(SaveSession(session), out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Imported", readMessage());
            Click(parent, "playerOutcome_1");
            Assert.IsTrue(parent.Q("playerSessionDetails").Query<Label>().ToList().Any(label => label.text.Contains("recovery has no child artifacts")));
        }

        [TestCase("planned", 3)]
        [TestCase("executed", 1)]
        [TestCase("passed", -1)]
        [TestCase("unknown", 1001)]
        public void CounterRelationshipsAndBoundsAreRejected(string field, int number)
        {
            JObject counts = Counts();
            counts[field] = number;
            Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.ValidateCounts(counts));
        }

        [Test]
        public void SummaryAndOutcomeModeIdentityAndRepeatRelationshipsAreRejected()
        {
            foreach (
                Action<JObject> corrupt in new Action<JObject>[]
                {
                    value => value["iteration_counts"]["fresh-process"] = Counts(),
                    value => value["completed_iterations"]["shared-batches"] = 1,
                    value => value["last_outcomes"][0]["session_id"] = JobId,
                    value => value["last_outcomes"][0]["repeat_count"] = 3,
                    value => value["last_outcomes"][0]["failure"]["iteration"] = 3,
                    value => value["last_outcomes"][0]["iteration_counts"]["passed"] = 1.0,
                }
            )
            {
                JObject session = Session();
                corrupt(session);
                Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.Validate(session, root));
            }
        }

        [TestCase("../outside")]
        [TestCase("runs/../../outside")]
        [TestCase("runs/33333333333333333333333333333333")]
        [TestCase("https://example.com/session")]
        public void ArtifactPathMustMatchTheOwnedChild(string artifact)
        {
            JObject row = Outcome();
            row["artifact_directory"] = artifact;
            Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.ArtifactDirectory(root, row));
        }

        [Test]
        public void DuplicateAndTruncatedJsonAndOversizedSummaryAreRejected()
        {
            string file = Path.Combine(root, "session.json");
            foreach (string json in new[] { "{\"schema_version\":2,\"schema_version\":1}", "{\"schema_version\":", "{} {}" })
            {
                File.WriteAllText(file, json);
                Assert.Throws<Newtonsoft.Json.JsonReaderException>(() => PlayScenarioPlayerSessionEditor.ReadJson(file));
            }
            File.WriteAllText(file, new string(' ', 65537));
            Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.ReadJson(file, 65536));
        }

        [Test]
        public void JournalMustAgreeWithSummaryAndRejectDuplicateIdentities()
        {
            string file = SaveSession();
            string journal = Path.Combine(root, "outcomes.jsonl");
            File.WriteAllText(journal, Outcome().ToString(Newtonsoft.Json.Formatting.None));
            VisualElement parent = Viewer(file, out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("truncated", readMessage());
            JObject session = Session();
            session["outcomes_recorded"] = 2;
            SaveSession(session);
            JObject second = Outcome();
            second["sequence"] = 2;
            File.WriteAllText(journal, Outcome().ToString(Newtonsoft.Json.Formatting.None) + "\n" + second.ToString(Newtonsoft.Json.Formatting.None) + "\n");
            Click(parent, "openPlayerSession");
            StringAssert.Contains("duplicate", readMessage());
        }

        [Test]
        public void JournalPaginationAndSavedStepNavigationAreBounded()
        {
            JObject session = Session();
            session["outcomes_recorded"] = 26;
            session["requested_iterations"] = 52;
            session["iteration_counts"] = new JObject { ["shared-batches"] = Counts(52, 26, 26) };
            session["completed_iterations"] = new JObject { ["shared-batches"] = 52 };
            session["last_outcomes"] = new JArray();
            string journal = "";
            for (int index = 1; index <= 26; index++)
            {
                JObject row = Outcome();
                row["sequence"] = index;
                row["job_id"] = index.ToString("x32");
                row["artifact_directory"] = "runs/" + row["job_id"];
                journal += row.ToString(Newtonsoft.Json.Formatting.None) + "\n";
            }
            File.WriteAllText(Path.Combine(root, "outcomes.jsonl"), journal);
            string child = Path.Combine(root, "runs", 26.ToString("x32"));
            Directory.CreateDirectory(child);
            File.WriteAllText(
                Path.Combine(child, "run.json"),
                new JObject
                {
                    ["job_id"] = 26.ToString("x32"),
                    ["steps"] = new JArray(
                        new JObject
                        {
                            ["stage"] = "main",
                            ["iteration"] = 1,
                            ["step_index"] = 0,
                            ["name"] = "First",
                            ["status"] = "passed",
                        },
                        new JObject
                        {
                            ["stage"] = "cleanup",
                            ["iteration"] = 2,
                            ["step_index"] = 1,
                            ["name"] = "Cleanup",
                            ["status"] = "failed",
                        }
                    ),
                    ["logs"] = new JArray(new JObject { ["type"] = "Error", ["message"] = "saved-log" }),
                }.ToString()
            );
            string reportFile = Path.Combine(child, "run.json");
            JObject finalOutcome = Outcome();
            finalOutcome["job_id"] = 26.ToString("x32");
            finalOutcome["sequence"] = 26;
            finalOutcome["artifact_directory"] = "runs/" + finalOutcome["job_id"];
            using (SHA256 hash = SHA256.Create())
                finalOutcome["native_report_sha256"] = string.Concat(hash.ComputeHash(File.ReadAllBytes(reportFile)).Select(value => value.ToString("x2")));
            string[] journalLines = journal.TrimEnd('\n').Split('\n');
            journalLines[25] = finalOutcome.ToString(Newtonsoft.Json.Formatting.None);
            File.WriteAllText(Path.Combine(root, "outcomes.jsonl"), string.Join("\n", journalLines) + "\n");
            VisualElement parent = Viewer(SaveSession(session), out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Imported", readMessage());
            Assert.AreEqual(25, parent.Q("playerSessionOutcomes").Query<Button>().ToList().Count);
            Click(parent, "nextPlayerOutcomes");
            Assert.AreEqual(1, parent.Q("playerSessionOutcomes").Query<Button>().ToList().Count);
            Click(parent, "playerOutcome_26");
            parent.Q<PopupField<string>>("playerReportStage").value = "cleanup";
            parent.Q<IntegerField>("playerReportIteration").value = 2;
            parent.Q<IntegerField>("playerReportStep").value = 1;
            string evidence = string.Join("\n", parent.Q("playerReportEvidence").Query<Label>().ToList().Select(label => label.text));
            StringAssert.Contains("cleanup.2.1 Cleanup", evidence);
            StringAssert.DoesNotContain("First", evidence);
            StringAssert.Contains("saved-log", evidence);
            Directory.Delete(child, true);
            Click(parent, "playerOutcome_26");
            Assert.IsTrue(parent.Q("playerSessionDetails").Query<Label>().ToList().Any(label => label.text.Contains("Unavailable")));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SavedReportMissingOrMismatchingHashCannotDisplayStaleEvidence(bool supplyHash)
        {
            JObject session = Session();
            if (!supplyHash)
                ((JObject)session["last_outcomes"][0]).Remove("native_report_sha256");
            if (supplyHash)
                session["last_outcomes"][0]["native_report_sha256"] = new string('0', 64);
            string child = Path.Combine(root, "runs", JobId);
            Directory.CreateDirectory(child);
            File.WriteAllText(
                Path.Combine(child, "run.json"),
                new JObject
                {
                    ["job_id"] = JobId,
                    ["steps"] = new JArray(),
                    ["logs"] = new JArray(),
                }.ToString()
            );
            VisualElement parent = Viewer(SaveSession(session), out var readMessage);
            Click(parent, "openPlayerSession");
            if (!supplyHash)
            {
                StringAssert.Contains("Native iteration evidence requires", readMessage());
                Assert.IsNull(parent.Q("playerOutcome_1"));
                return;
            }
            StringAssert.Contains("Imported", readMessage());
            Click(parent, "playerOutcome_1");
            string details = string.Join("\n", parent.Q("playerSessionDetails").Query<Label>().ToList().Select(label => label.text));
            StringAssert.Contains("SHA256 mismatch", details);
            Assert.IsNull(parent.Q("playerReportStage"));
        }

        [Test]
        public void SucceededSummaryAndOutcomeCannotHideFailedOrUnknownIterations()
        {
            JObject session = Session();
            session["status"] = "succeeded";
            Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.Validate(session, root));
            session = Session();
            session["last_outcomes"][0]["status"] = "succeeded";
            Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.Validate(session, root));
        }

        [Test]
        public void CompleteJournalCannotUndercountSummaryAndLiveActiveSlotsAreUnknown()
        {
            JObject session = Session();
            session["requested_iterations"] = 4;
            session["completed_iterations"] = new JObject { ["shared-batches"] = 4 };
            session["iteration_counts"] = new JObject { ["shared-batches"] = Counts(4, 2, 2) };
            File.WriteAllText(Path.Combine(root, "outcomes.jsonl"), Outcome().ToString(Newtonsoft.Json.Formatting.None) + "\n");
            VisualElement parent = Viewer(SaveSession(session), out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Complete journal counters differ", readMessage());

            session["status"] = "running";
            JObject total = Counts(4, 1, 1);
            total["unknown"] = 1;
            total["pending"] = 1;
            session["iteration_counts"] = new JObject { ["shared-batches"] = total };
            session["completed_iterations"] = new JObject { ["shared-batches"] = 2 };
            session["active_child"] = new JObject
            {
                ["job_id"] = "33333333333333333333333333333333",
                ["mode"] = "shared-batches",
                ["repeat_count"] = 1,
                ["artifact_directory"] = "runs/33333333333333333333333333333333",
            };
            SaveSession(session);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Imported Player session snapshot", readMessage());
        }

        [Test]
        public void CompleteTerminalJournalAccountsForUnadmittedSlotsAsSkipped()
        {
            JObject session = Session();
            session["requested_iterations"] = 4;
            JObject total = Counts(4, 1, 1);
            total["skipped"] = 2;
            session["iteration_counts"] = new JObject { ["shared-batches"] = total };
            File.WriteAllText(Path.Combine(root, "outcomes.jsonl"), Outcome().ToString(Newtonsoft.Json.Formatting.None) + "\n");
            VisualElement parent = Viewer(SaveSession(session), out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Imported Player session snapshot", readMessage());
        }

        [Test]
        public void RecoveryMissingAdmissionEvidenceKeepsUnclassifiedRemainderUnknown()
        {
            JObject session = Session();
            session["status"] = "interrupted";
            session["recovered"] = true;
            session["recovery_admissions_complete"] = false;
            session["requested_iterations"] = 4;
            JObject total = Counts(4, 1, 1);
            total["unknown"] = 2;
            session["iteration_counts"] = new JObject { ["shared-batches"] = total };
            session["last_outcomes"][0]["artifact_directory"] = null;
            File.WriteAllText(Path.Combine(root, "outcomes.jsonl"), session["last_outcomes"][0].ToString(Newtonsoft.Json.Formatting.None) + "\n");
            VisualElement parent = Viewer(SaveSession(session), out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Imported Player session snapshot", readMessage());
            string summary = string.Join("\n", parent.Q("playerSessionSummary").Query<Label>().ToList().Select(label => label.text));
            StringAssert.Contains("skipped 0; unknown 2", summary);
        }

        [TestCase("unavailable", false)]
        [TestCase("unavailable", true)]
        [TestCase("native", false)]
        [TestCase("legacy_steps", false)]
        public void MissingIterationEvidenceCannotClaimKnownExecution(string source, bool nativeAvailable)
        {
            JObject session = Session();
            JObject row = (JObject)session["last_outcomes"][0];
            row["iteration_results_source"] = source;
            row["native_report_available"] = nativeAvailable;
            if (!nativeAvailable)
                row["native_report_sha256"] = null;
            Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.Validate(session, root));
        }

        [TestCase("native")]
        [TestCase("legacy_steps")]
        public void KnownIterationEvidenceRequiresAnAvailableReportWithItsHash(string source)
        {
            JObject session = Session();
            JObject row = (JObject)session["last_outcomes"][0];
            row["iteration_results_source"] = source;
            row.Remove("native_report_sha256");
            Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.Validate(session, root));
            row["native_report_sha256"] = new string('0', 64);
            row.Remove("native_report_available");
            Assert.Throws<InvalidDataException>(() => PlayScenarioPlayerSessionEditor.Validate(session, root));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UnavailableEvidenceCanPreserveOnlyUnknownAdmittedIterations(bool nativeAvailable)
        {
            JObject session = Session();
            JObject unknown = Counts(2, 0, 0);
            unknown["unknown"] = 2;
            session["iteration_counts"] = new JObject { ["shared-batches"] = unknown.DeepClone() };
            session["completed_iterations"] = new JObject { ["shared-batches"] = 0 };
            JObject row = (JObject)session["last_outcomes"][0];
            row["iteration_results_source"] = "unavailable";
            row["iteration_counts"] = unknown;
            row["native_report_available"] = nativeAvailable;
            row["native_report_sha256"] = nativeAvailable ? new string('0', 64) : null;
            row["status"] = "infrastructure_error";
            VisualElement parent = Viewer(SaveSession(session), out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Imported Player session snapshot", readMessage());
            string summary = string.Join("\n", parent.Q("playerSessionSummary").Query<Label>().ToList().Select(label => label.text));
            StringAssert.Contains("executed 0; passed 0; failed 0", summary);
            StringAssert.Contains("unknown 2", summary);
        }

        [Test]
        public void NestedDuplicateSummaryPropertiesAreRejectedBeforeInterpretation()
        {
            string duplicate = Session().ToString(Newtonsoft.Json.Formatting.None).Replace("\"passed\":1", "\"passed\":1,\"passed\":0");
            string file = Path.Combine(root, "session.json");
            File.WriteAllText(file, duplicate);
            VisualElement parent = Viewer(file, out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("import failed", readMessage());
            StringAssert.Contains("already exists", readMessage());
            Assert.IsNull(parent.Q("playerOutcome_1"));
        }

        [Test]
        public void NestedDuplicateJournalPropertiesAreRejectedBeforeInterpretation()
        {
            string duplicate = Outcome()
                .ToString(Newtonsoft.Json.Formatting.None)
                .Replace("\"message\":\"Saved failure\"", "\"message\":\"Saved failure\",\"message\":\"Other failure\"");
            File.WriteAllText(Path.Combine(root, "outcomes.jsonl"), duplicate + "\n");
            VisualElement parent = Viewer(SaveSession(), out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("import failed", readMessage());
            StringAssert.Contains("already exists", readMessage());
            Assert.IsNull(parent.Q("playerOutcome_1"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void NativeFailureWithNullOrMissingMessageImportsWithoutInventingDetail(bool removeMessage)
        {
            JObject session = Session();
            JObject failure = (JObject)session["last_outcomes"][0]["failure"];
            if (removeMessage)
                failure.Remove("message");
            else
                failure["message"] = null;
            VisualElement parent = Viewer(SaveSession(session), out var readMessage);
            Click(parent, "openPlayerSession");
            StringAssert.Contains("Imported Player session snapshot", readMessage());
            Click(parent, "playerOutcome_1");
            Assert.IsTrue(parent.Q("playerSessionDetails").Query<Label>().ToList().Any(label => label.text == "Failure message unavailable."));
        }
    }
}
