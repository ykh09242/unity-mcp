using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Windows.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    public class PlayScenarioSoakEditorTests
    {
        private string root;
        private SoakViewerTestWindow window;

        private sealed class SoakViewerTestWindow : UnityEditor.EditorWindow { }

        private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;
        private const string JobId = "22222222222222222222222222222222";

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "unity-mcp-soak-viewer-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            window = ScriptableObject.CreateInstance<SoakViewerTestWindow>();
            window.Show();
            yield return null;
        }

        [TearDown]
        public void TearDown()
        {
            if (window != null)
                UnityEngine.Object.DestroyImmediate(window);
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private static JObject Identity() =>
            new JObject
            {
                ["schema_version"] = 1,
                ["job_id"] = JobId,
                ["definition_hash"] = new string('a', 64),
                ["build_id"] = new string('b', 32),
                ["build_source_revision"] = null,
                ["payload_hash"] = new string('c', 64),
                ["process_id"] = 4567,
            };

        private static JObject Row(int iteration, bool fail = false)
        {
            JObject row = Identity();
            row["iteration"] = iteration;
            row["status"] = fail ? "failed" : "succeeded";
            row["started_unix_ms"] = iteration * 10;
            row["finished_unix_ms"] = iteration * 10 + 5;
            row["runner_resources_released"] = true;
            row["failure_code"] = fail ? "assertion_failed" : null;
            row["error"] = fail ? "First actual failure" : null;
            row["cleanup_error"] = null;
            row["unexpected_log_count"] = 0;
            row["query_counts"] = new JObject { ["target_searches"] = 0, ["hierarchy_visits"] = 0 };
            row["metrics"] = new JArray();
            row["report_file"] = "iteration-" + iteration.ToString("D4") + ".json";
            return row;
        }

        private string Save(int count = 2, int? firstFailure = null)
        {
            var rows = Enumerable.Range(1, count).Select(index => Row(index, index == firstFailure)).ToList();
            byte[] journal = new UTF8Encoding(false).GetBytes(string.Join("\n", rows.Select(row => row.ToString(Formatting.None))) + "\n");
            File.WriteAllBytes(Path.Combine(root, "iterations.jsonl"), journal);
            JObject summary = Identity();
            summary["process_scope"] = "same-process";
            summary["requested_iterations"] = count;
            summary["attempted_iterations"] = count;
            summary["passed_iterations"] = count - (firstFailure.HasValue ? 1 : 0);
            summary["failed_iterations"] = firstFailure.HasValue ? 1 : 0;
            summary["recorded_iterations"] = count;
            summary["skipped_iterations"] = 0;
            summary["first_failure_iteration"] = firstFailure;
            summary["started_unix_ms"] = 1;
            summary["deadline_unix_ms"] = count * 10 + 100;
            summary["finished_unix_ms"] = count * 10 + 6;
            summary["status"] = firstFailure.HasValue ? "failed" : "succeeded";
            summary["finalization_state"] = "completed";
            summary["failure_policy"] = "continue";
            summary["retain_reports"] = 4;
            summary["journal_file"] = "iterations.jsonl";
            summary["journal_rows"] = count;
            summary["journal_bytes"] = journal.Length;
            using (SHA256 hash = SHA256.Create())
                summary["journal_sha256"] = string.Concat(hash.ComputeHash(journal).Select(value => value.ToString("x2")));
            summary["journal_complete"] = true;
            summary["runner_resources_released"] = true;
            summary["selected_iteration"] = firstFailure ?? count;
            summary["retained_report_files"] = new JArray("iteration-" + (firstFailure ?? count).ToString("D4") + ".json");
            summary["error"] = null;
            summary["metrics_summary"] = null;
            JObject selected = rows[(firstFailure ?? count) - 1];
            JObject report = new JObject
            {
                ["job_id"] = JobId,
                ["player_schema_version"] = 2,
                ["repeat_count"] = 1,
                ["status"] = selected["status"].DeepClone(),
                ["execution_environment"] = "player",
                ["finalization_state"] = "completed",
                ["started_unix_ms"] = selected["started_unix_ms"].DeepClone(),
                ["finished_unix_ms"] = selected["finished_unix_ms"].DeepClone(),
                ["runner_resources_released"] = true,
                ["iteration_results_version"] = 1,
                ["iteration_results"] = new JArray(
                    new JObject
                    {
                        ["iteration"] = 1,
                        ["status"] = firstFailure.HasValue ? "failed" : "passed",
                        ["started_unix_ms"] = selected["started_unix_ms"].DeepClone(),
                        ["finished_unix_ms"] = selected["finished_unix_ms"].DeepClone(),
                    }
                ),
                ["reproduction"] = new JObject
                {
                    ["definition_hash"] = summary["definition_hash"].DeepClone(),
                    ["build_id"] = summary["build_id"].DeepClone(),
                    ["build_source_revision"] = null,
                    ["payload_hash"] = summary["payload_hash"].DeepClone(),
                },
                ["exit_code"] = firstFailure.HasValue ? 1 : 0,
                ["soak"] = summary.DeepClone(),
                ["steps"] = new JArray(),
            };
            Write("run.json", report);
            return Write("soak.json", summary);
        }

        private string Write(string filename, JObject value)
        {
            string file = Path.Combine(root, filename);
            File.WriteAllText(file, value.ToString(Formatting.None), new UTF8Encoding(false));
            return file;
        }

        private JObject RunningCheckpointOneBehind(string file)
        {
            JObject value = PlayScenarioPlayerSessionEditor.ReadJson(file);
            value["recorded_iterations"] = 1;
            value["attempted_iterations"] = 1;
            value["passed_iterations"] = 1;
            value["failed_iterations"] = 0;
            value["skipped_iterations"] = (int)value["requested_iterations"] - 1;
            value["selected_iteration"] = 1;
            value["finished_unix_ms"] = null;
            value["status"] = "running";
            value["finalization_state"] = "running";
            value["journal_rows"] = 1;
            byte[] journal = File.ReadAllBytes(Path.Combine(root, "iterations.jsonl"));
            value["journal_bytes"] = Array.IndexOf(journal, (byte)10) + 1;
            value["journal_sha256"] = null;
            value["journal_complete"] = false;
            value["runner_resources_released"] = false;
            value["retained_report_files"] = new JArray("iteration-0001.json");
            return value;
        }

        [UnityTest]
        public IEnumerator JournalFirstPublicationRetriesUntilTheMatchingCheckpointIsPublished()
        {
            string file = Save();
            JObject completed = PlayScenarioPlayerSessionEditor.ReadJson(file);
            Write("soak.json", RunningCheckpointOneBehind(file));
            int reads = 0;
            object imported = null;
            Exception failure = null;
            using (var publicationGap = new ManualResetEventSlim())
            using (var releaseWriter = new ManualResetEventSlim())
            using (var worker = new PlayScenarioImportWorker())
            {
                worker.RequestRead(
                    file,
                    token =>
                    {
                        Interlocked.Increment(ref reads);
                        try
                        {
                            return PlayScenarioSoakEvidence.Read(file, token);
                        }
                        catch (PlayScenarioImportRaceException)
                        {
                            publicationGap.Set();
                            releaseWriter.Wait(token);
                            throw;
                        }
                    },
                    (value, error) =>
                    {
                        imported = value;
                        failure = error;
                    }
                );
                var elapsed = Stopwatch.StartNew();
                while (!publicationGap.IsSet && worker.IsBusy && elapsed.ElapsedMilliseconds < 4000)
                    yield return null;
                Assert.IsTrue(publicationGap.IsSet, "The valid journal-first checkpoint gap was rejected as permanent evidence.");
                Assert.IsNull(imported);
                Write("soak.json", completed);
                releaseWriter.Set();
                yield return PlayScenarioImportTestPump.Wait(worker);
                Assert.IsNull(failure);
                Assert.AreEqual(2, reads);
                StringAssert.StartsWith("Complete:", ((PlayScenarioSoakEvidence)imported).Completeness);
                Assert.AreEqual(2, ((PlayScenarioSoakEvidence)imported).Rows.Count);
            }
        }

        [UnityTest]
        public IEnumerator PersistenceBrokenRunningCheckpointRemainsABoundedFailure()
        {
            string file = Save();
            Write("soak.json", RunningCheckpointOneBehind(file));
            JObject report = PlayScenarioPlayerSessionEditor.ReadJson(Path.Combine(root, "run.json"));
            report["exit_code"] = 2;
            report["persistence_error"] = new JObject
            {
                ["schema_version"] = 1,
                ["stage"] = "checkpoint_replace",
                ["exception_type"] = "IOException",
                ["error"] = "Checkpoint publication failed",
                ["iteration"] = 2,
            };
            Write("run.json", report);
            int reads = 0;
            object imported = null;
            Exception failure = null;
            using (var worker = new PlayScenarioImportWorker())
            {
                var elapsed = Stopwatch.StartNew();
                worker.RequestRead(
                    file,
                    token =>
                    {
                        Interlocked.Increment(ref reads);
                        return PlayScenarioSoakEvidence.Read(file, token);
                    },
                    (value, error) =>
                    {
                        imported = value;
                        failure = error;
                    }
                );
                yield return PlayScenarioImportTestPump.Wait(worker);
                Assert.AreEqual(PlayScenarioImportWorker.MaximumAttempts, reads);
                Assert.IsInstanceOf<PlayScenarioImportRaceException>(failure);
                Assert.IsNull(imported);
                Assert.IsFalse(worker.IsPumping);
                Assert.Less(elapsed.ElapsedMilliseconds, PlayScenarioImportWorker.DeadlineMilliseconds);
            }
        }

        [TestCase("prefix-counter")]
        [TestCase("prefix-bytes")]
        [TestCase("malformed-row")]
        [TestCase("completed")]
        [TestCase("two-rows-ahead")]
        public void PublicationRaceClassificationRequiresAValidPrefixAndExactlyOneRunningRow(string contradiction)
        {
            string file = Save(contradiction == "two-rows-ahead" ? 3 : 2);
            JObject running = RunningCheckpointOneBehind(file);
            if (contradiction == "prefix-counter")
            {
                running["passed_iterations"] = 0;
                running["failed_iterations"] = 1;
            }
            else if (contradiction == "prefix-bytes")
                running["journal_bytes"] = (long)running["journal_bytes"] + 1;
            else if (contradiction == "malformed-row")
                File.AppendAllText(Path.Combine(root, "iterations.jsonl"), "{ malformed }\n", new UTF8Encoding(false));
            else if (contradiction == "completed")
            {
                running["status"] = "failed";
                running["finalization_state"] = "completed";
                running["journal_complete"] = true;
                running["journal_sha256"] = new string('a', 64);
                running["finished_unix_ms"] = 26;
            }
            Write("soak.json", running);
            Exception failure =
                contradiction == "malformed-row"
                    ? Assert.Throws<JsonReaderException>(() => PlayScenarioSoakEvidence.Read(file))
                    : Assert.Throws<InvalidDataException>(() => PlayScenarioSoakEvidence.Read(file));
            Assert.IsFalse(PlayScenarioImportWorker.IsTransient(failure));
        }

        private VisualElement Viewer(string file, out Func<string> message)
        {
            string latest = "";
            var parent = new VisualElement();
            window.rootVisualElement.Add(parent);
            new PlayScenarioSoakEditor(parent, (text, _) => latest = text, () => file);
            message = () => latest;
            return parent;
        }

        private static void Click(VisualElement parent, string name)
        {
            Button button = parent.Q<Button>(name);
            Assert.IsNotNull(button);
            Assert.IsTrue(button.enabledSelf);
            typeof(Clickable).GetMethod("Invoke", PrivateInstance).Invoke(button.clickable, new object[] { null });
            PlayScenarioImportTestPump.Drain(parent, "playerSoak");
        }

        private static string Summary(VisualElement parent) =>
            string.Join("\n", parent.Q("playerSoakSummary").Query<Label>().ToList().Select(label => label.text));

        [Test]
        public void ActualImportDisplaysNativeCountsFirstFailureAndUnavailableMetricsWithoutWrites()
        {
            string file = Save(100, 3);
            byte[] original = File.ReadAllBytes(file);
            VisualElement parent = Viewer(file, out var message);
            Assert.IsEmpty(parent.Q("playerSoakSummary").Query<Label>().ToList());
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("Imported", message());
            string text = Summary(parent);
            StringAssert.Contains("Same PID: 4567", text);
            StringAssert.Contains("planned 100; attempted 100; passed 99; failed 1; skipped 0", text);
            StringAssert.Contains("First failure: 3", text);
            StringAssert.Contains("First actual failure", text);
            StringAssert.Contains("Complete:", text);
            StringAssert.Contains("disabled or unavailable", text);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(file));
            Assert.AreEqual(3, Directory.GetFiles(root).Length);
        }

        [Test]
        public void ThousandRowsPageInGroupsOfTwentyFiveWithoutFurtherFileReads()
        {
            VisualElement parent = Viewer(Save(1000), out var message);
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("Imported", message());
            Assert.AreEqual(25, parent.Q<ScrollView>("playerSoakRows").contentContainer.childCount);
            Directory.Delete(root, true);
            for (int page = 1; page < 40; page++)
                Click(parent, "nextSoakIterations");
            StringAssert.Contains("40 of 40", parent.Q<Label>("playerSoakPage").text);
            Assert.IsNotNull(parent.Q("soakIteration_1000"));
            Assert.IsFalse(parent.Q<Button>("nextSoakIterations").enabledSelf);
            Click(parent, "previousSoakIterations");
            Assert.IsNotNull(parent.Q("soakIteration_951"));
        }

        [TestCase("failed_iterations", 1)]
        [TestCase("skipped_iterations", 1)]
        [TestCase("passed_iterations", 3)]
        [TestCase("journal_rows", 1)]
        [TestCase("process_id", 0)]
        public void InvalidCountsAndIdentityAreRejectedBeforeDisplay(string field, int number)
        {
            string file = Save();
            JObject value = JObject.Parse(File.ReadAllText(file));
            value[field] = number;
            Write("soak.json", value);
            VisualElement parent = Viewer(file, out var message);
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("import failed", message());
            Assert.IsNull(parent.Q("soakIteration_1"));
        }

        [TestCase("hash")]
        [TestCase("pid")]
        [TestCase("order")]
        [TestCase("duplicate")]
        public void AmbiguousOrMismatchedJournalCannotClaimCompleteEvidence(string corruption)
        {
            string file = Save();
            string journalFile = Path.Combine(root, "iterations.jsonl");
            string text = File.ReadAllText(journalFile);
            if (corruption == "hash")
                text = text.Replace("\"target_searches\":0", "\"target_searches\":1");
            else if (corruption == "pid")
                text = text.Replace("\"process_id\":4567", "\"process_id\":4568");
            else if (corruption == "order")
                text = text.Replace("\"iteration\":2", "\"iteration\":1");
            else
                text = text.Replace("\"process_id\":4567", "\"process_id\":4567,\"process_id\":4567");
            File.WriteAllText(journalFile, text, new UTF8Encoding(false));
            Assert.Catch<Exception>(() => PlayScenarioSoakEvidence.Read(file));
        }

        [Test]
        public void TruncatedJournalShowsOnlyCompletePrefixAndExplicitIncompleteStatus()
        {
            string file = Save();
            string journalFile = Path.Combine(root, "iterations.jsonl");
            File.WriteAllText(journalFile, File.ReadAllText(journalFile).TrimEnd('\n'), new UTF8Encoding(false));
            VisualElement parent = Viewer(file, out _);
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("Incomplete: truncated", Summary(parent));
            Assert.IsNotNull(parent.Q("soakIteration_1"));
            Assert.IsNull(parent.Q("soakIteration_2"));
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        public void JournalImportSharesAccessWithLiveWriterWithoutClaimingIncompleteEvidenceComplete(bool completed, bool truncated)
        {
            string file = Save();
            string journalFile = Path.Combine(root, "iterations.jsonl");
            if (!completed)
            {
                JObject summary = JObject.Parse(File.ReadAllText(file));
                summary["status"] = "running";
                summary["finalization_state"] = "running";
                summary["finished_unix_ms"] = null;
                summary["journal_complete"] = false;
                summary["journal_sha256"] = null;
                Write("soak.json", summary);
            }
            if (truncated)
                File.WriteAllText(journalFile, File.ReadAllText(journalFile).TrimEnd('\n'), new UTF8Encoding(false));
            using (var writer = new FileStream(journalFile, FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete))
            {
                PlayScenarioSoakEvidence evidence = null;
                Assert.DoesNotThrow(() => evidence = PlayScenarioSoakEvidence.Read(file), "An evidence import must share access with its live journal writer.");
                Assert.AreEqual(truncated ? 1 : 2, evidence.Rows.Count);
                StringAssert.StartsWith(completed ? "Complete:" : "Incomplete:", evidence.Completeness);
                if (truncated)
                    StringAssert.Contains("truncated", evidence.Completeness);
                writer.Position = writer.Length;
                byte[] appended = new UTF8Encoding(false).GetBytes("\n");
                Assert.DoesNotThrow(() => writer.Write(appended, 0, appended.Length));
                writer.Flush(true);
            }
        }

        [Test]
        public void MissingFinalReportAndUnfinishedCheckpointStayUnverified()
        {
            string file = Save();
            File.Delete(Path.Combine(root, "run.json"));
            StringAssert.Contains("final run.json is missing", PlayScenarioSoakEvidence.Read(file).Completeness);
            JObject value = JObject.Parse(File.ReadAllText(file));
            value["status"] = "running";
            value["finalization_state"] = "running";
            value["finished_unix_ms"] = null;
            value["journal_complete"] = false;
            value["journal_sha256"] = null;
            Write("soak.json", value);
            StringAssert.Contains("unfinished checkpoint", PlayScenarioSoakEvidence.Read(file).Completeness);
        }

        [Test]
        public void ClientWithoutNativeArtifactsPreservesUnknownCountsAsUnavailable()
        {
            JObject client = new JObject
            {
                ["schema_version"] = 1,
                ["execution_environment"] = "player",
                ["process_scope"] = "same-process",
                ["evidence_state"] = "incomplete",
                ["status"] = "incomplete",
                ["job_id"] = JobId,
                ["process_id"] = null,
                ["requested_iterations"] = 100,
                ["recorded_iterations"] = null,
                ["attempted_iterations"] = null,
                ["passed_iterations"] = null,
                ["failed_iterations"] = null,
                ["skipped_iterations"] = null,
                ["unknown_iterations"] = null,
                ["journal_bytes"] = null,
                ["journal_sha256"] = null,
                ["client_error"] = "Owned receipt unavailable",
                ["native_summary_file"] = "soak.json",
                ["journal_file"] = "iterations.jsonl",
                ["native_report_file"] = "run.json",
            };
            VisualElement parent = Viewer(Write("client-soak.json", client), out var message);
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("Imported", message());
            StringAssert.Contains("attempted unavailable; passed unavailable", Summary(parent));
            StringAssert.Contains("native soak.json is missing", Summary(parent));
        }

        [Test]
        public void ZeroMeasurementsRemainDistinctFromUnavailableAndClientCompleteRequiresMatchingFiles()
        {
            string file = Save();
            JObject native = JObject.Parse(File.ReadAllText(file));
            JObject sample = new JObject
            {
                ["iteration"] = 1,
                ["timestamp_unix_ms"] = 15,
                ["managed_bytes"] = 0,
                ["allocated_bytes"] = null,
                ["object_count"] = 0,
                ["runner_subscription_count"] = 0,
                ["runner_handle_count"] = null,
                ["ignored_for_trend"] = false,
                ["error"] = null,
            };
            string[] lines = File.ReadAllLines(Path.Combine(root, "iterations.jsonl"));
            JObject row = JObject.Parse(lines[0]);
            row["metrics"] = new JArray(sample.DeepClone());
            lines[0] = row.ToString(Formatting.None);
            byte[] journal = new UTF8Encoding(false).GetBytes(string.Join("\n", lines) + "\n");
            File.WriteAllBytes(Path.Combine(root, "iterations.jsonl"), journal);
            native["journal_bytes"] = journal.Length;
            using (SHA256 hash = SHA256.Create())
                native["journal_sha256"] = string.Concat(hash.ComputeHash(journal).Select(value => value.ToString("x2")));
            native["metrics_summary"] = new JObject
            {
                ["sample_count"] = 1,
                ["usable_samples"] = 1,
                ["warmup_iterations"] = 0,
                ["bounded_samples"] = new JArray(sample),
                ["warnings"] = new JArray(),
                ["summary"] = "Insufficient samples.",
            };
            Write("soak.json", native);
            JObject report = JObject.Parse(File.ReadAllText(Path.Combine(root, "run.json")));
            report["soak"] = native.DeepClone();
            Write("run.json", report);
            var client = new JObject
            {
                ["schema_version"] = 1,
                ["execution_environment"] = "player",
                ["process_scope"] = "same-process",
                ["evidence_state"] = "complete",
                ["status"] = "succeeded",
                ["job_id"] = JobId,
                ["process_id"] = 4567,
                ["requested_iterations"] = 2,
                ["recorded_iterations"] = 2,
                ["attempted_iterations"] = 2,
                ["passed_iterations"] = 2,
                ["failed_iterations"] = 0,
                ["skipped_iterations"] = 0,
                ["unknown_iterations"] = 0,
                ["journal_bytes"] = native["journal_bytes"].DeepClone(),
                ["journal_sha256"] = native["journal_sha256"].DeepClone(),
                ["client_error"] = null,
                ["native_summary_file"] = "soak.json",
                ["journal_file"] = "iterations.jsonl",
                ["native_report_file"] = "run.json",
            };
            string clientFile = Write("client-soak.json", client);
            VisualElement parent = Viewer(clientFile, out var message);
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("Imported", message());
            StringAssert.Contains("managed 0; allocated unavailable; objects 0", Summary(parent));
            client["journal_sha256"] = new string('0', 64);
            Write("client-soak.json", client);
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("import failed", message());
            Assert.IsNotNull(parent.Q("soakIteration_1"));
            StringAssert.Contains("stale", parent.Q<Label>("playerSoakImportState").text);
        }

        [Test]
        public void JournalRowsAndSummaryBytesHaveIndependentHardBounds()
        {
            string file = Save();
            File.WriteAllText(Path.Combine(root, "iterations.jsonl"), new string(' ', 16384) + "\n", new UTF8Encoding(false));
            Assert.Throws<InvalidDataException>(() => PlayScenarioSoakEvidence.Read(file));
            Save();
            File.WriteAllText(file, new string(' ', 65537), new UTF8Encoding(false));
            Assert.Throws<InvalidDataException>(() => PlayScenarioSoakEvidence.Read(file));
        }

        [Test]
        public void CancelledIterationCannotContinueAndIterationIntervalsCannotOverlap()
        {
            string file = Save(2, 1);
            string journal = Path.Combine(root, "iterations.jsonl");
            string text = File.ReadAllText(journal);
            File.WriteAllText(journal, text.Replace("\"status\":\"failed\"", "\"status\":\"cancelled\""), new UTF8Encoding(false));
            Assert.Throws<InvalidDataException>(() => PlayScenarioSoakEvidence.Read(file));
            Save();
            text = File.ReadAllText(journal).Replace("\"started_unix_ms\":20", "\"started_unix_ms\":14");
            File.WriteAllText(journal, text, new UTF8Encoding(false));
            Assert.Throws<InvalidDataException>(() => PlayScenarioSoakEvidence.Read(file));
        }

        [Test]
        public void RetainOneKeepsLastStartedSuccessWhenNextIterationIsCancelledBeforeStarting()
        {
            string file = Save();
            JObject native = JObject.Parse(File.ReadAllText(file));
            JObject first = Row(1);
            JObject unstarted = Row(2);
            unstarted["status"] = "cancelled";
            unstarted["started_unix_ms"] = null;
            unstarted["finished_unix_ms"] = null;
            unstarted["failure_code"] = "run_cancelled";
            unstarted["error"] = "Cancelled before first evaluation.";
            byte[] journal = new UTF8Encoding(false).GetBytes(first.ToString(Formatting.None) + "\n" + unstarted.ToString(Formatting.None) + "\n");
            File.WriteAllBytes(Path.Combine(root, "iterations.jsonl"), journal);
            native["attempted_iterations"] = 1;
            native["passed_iterations"] = 1;
            native["skipped_iterations"] = 1;
            native["status"] = "cancelled";
            native["selected_iteration"] = 1;
            native["retain_reports"] = 1;
            native["retained_report_files"] = new JArray("iteration-0001.json");
            native["journal_bytes"] = journal.Length;
            using (SHA256 hash = SHA256.Create())
                native["journal_sha256"] = string.Concat(hash.ComputeHash(journal).Select(value => value.ToString("x2")));
            Write("soak.json", native);
            JObject report = JObject.Parse(File.ReadAllText(Path.Combine(root, "run.json")));
            report["started_unix_ms"] = first["started_unix_ms"].DeepClone();
            report["finished_unix_ms"] = first["finished_unix_ms"].DeepClone();
            report["iteration_results"][0]["started_unix_ms"] = first["started_unix_ms"].DeepClone();
            report["iteration_results"][0]["finished_unix_ms"] = first["finished_unix_ms"].DeepClone();
            report["exit_code"] = 1;
            report["soak"] = native.DeepClone();
            Write("run.json", report);
            JObject retained = (JObject)report.DeepClone();
            retained.Remove("soak");
            retained["exit_code"] = 0;
            string retainedFile = Write("iteration-0001.json", retained);
            byte[] original = File.ReadAllBytes(retainedFile);

            PlayScenarioSoakEvidence evidence = PlayScenarioSoakEvidence.Read(file);
            Assert.AreEqual("cancelled", (string)evidence.Summary["status"]);
            Assert.AreEqual(1, (int)evidence.Summary["selected_iteration"]);
            StringAssert.StartsWith("Complete:", evidence.Completeness);
            VisualElement parent = Viewer(file, out var message);
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("Imported", message());
            StringAssert.Contains("native status cancelled", Summary(parent));
            StringAssert.Contains("planned 2; attempted 1; passed 1; failed 0; skipped 1; recorded 2", Summary(parent));
            StringAssert.Contains("selected iteration: 1", Summary(parent));
            StringAssert.Contains("not attempted", string.Join("\n", parent.Q("soakIteration_2").Query<Label>().ToList().Select(label => label.text)));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(retainedFile));
            Assert.AreEqual("succeeded", (string)JObject.Parse(File.ReadAllText(retainedFile))["status"]);
        }

        [TestCase("started_unix_ms", 11)]
        [TestCase("finished_unix_ms", 24)]
        public void ActualJournalIntervalsMustStayWithinCheckpointSessionBounds(string field, long timestamp)
        {
            string file = Save();
            JObject native = JObject.Parse(File.ReadAllText(file));
            native[field] = timestamp;
            Write("soak.json", native);
            JObject report = JObject.Parse(File.ReadAllText(Path.Combine(root, "run.json")));
            report["soak"] = native.DeepClone();
            Write("run.json", report);
            InvalidDataException exception = Assert.Throws<InvalidDataException>(() => PlayScenarioSoakEvidence.Read(file));
            StringAssert.Contains("interval leaves", exception.Message);
            VisualElement parent = Viewer(file, out var message);
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("import failed", message());
            Assert.IsNull(parent.Q("soakIteration_1"));
        }

        [Test]
        public void OneRepeatJournalRowCannotContainMultipleBoundarySamples()
        {
            string file = Save();
            string journalFile = Path.Combine(root, "iterations.jsonl");
            string[] lines = File.ReadAllLines(journalFile);
            JObject row = JObject.Parse(lines[0]);
            JObject sample = new JObject
            {
                ["iteration"] = 1,
                ["timestamp_unix_ms"] = 15,
                ["managed_bytes"] = null,
                ["allocated_bytes"] = null,
                ["object_count"] = null,
                ["runner_subscription_count"] = null,
                ["runner_handle_count"] = null,
                ["ignored_for_trend"] = false,
                ["error"] = null,
            };
            row["metrics"] = new JArray(sample, sample.DeepClone());
            lines[0] = row.ToString(Formatting.None);
            File.WriteAllText(journalFile, string.Join("\n", lines) + "\n", new UTF8Encoding(false));
            InvalidDataException exception = Assert.Throws<InvalidDataException>(() => PlayScenarioSoakEvidence.Read(file));
            StringAssert.Contains("samples exceed", exception.Message);
            VisualElement parent = Viewer(file, out var message);
            Click(parent, "openPlayerSoak");
            StringAssert.Contains("import failed", message());
            Assert.IsNull(parent.Q("soakIteration_1"));
        }
    }
}
