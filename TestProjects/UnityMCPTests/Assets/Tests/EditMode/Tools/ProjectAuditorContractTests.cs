using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Tools.ProjectAuditor;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class ProjectAuditorContractTests
    {
        private const string Id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        private const string OtherId = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        private const string StateKey = "MCPForUnity.ProjectAuditor.Jobs.v1";
        private Func<IProjectAuditorBackend> previousFactory;
        private Func<string, string> previousReader;
        private Action<string, string> previousWriter;
        private Action<string> previousEraser;
        private Func<long> previousClock;
        private Dictionary<string, string> storage;
        private long now;
        private Backend backend;

        private sealed class Backend : IProjectAuditorBackend
        {
            internal int Starts;
            internal int Initializes;
            internal bool ThrowBeforeInvoke;
            internal bool ThrowAfterInvoke;
            internal int Rules = 4;
            internal string Ready = "available";
            internal Action<JObject> Incoming;
            internal Action<string> Module;
            internal Action Completed;
            public bool ObservationTruncated { get; set; }
            public string ObservationError { get; set; }

            public JObject Availability() => new JObject { ["state"] = Ready, ["reason"] = "test_readiness" };

            public int Initialize()
            {
                Initializes++;
                return Rules;
            }

            public void Start(Action<JObject> issue, Action<string> moduleCompleted, Action completed, Action beforeInvoke)
            {
                if (ThrowBeforeInvoke)
                    throw new InvalidOperationException("preparation failed");
                beforeInvoke();
                Starts++;
                Incoming = issue;
                Module = moduleCompleted;
                Completed = completed;
                if (ThrowAfterInvoke)
                    throw new InvalidOperationException("invocation outcome unknown");
            }
        }

        [SetUp]
        public void SetUp()
        {
            previousFactory = ProjectAuditorJobs.BackendFactory;
            previousReader = ProjectAuditorJobs.SessionReader;
            previousWriter = ProjectAuditorJobs.SessionWriter;
            previousEraser = ProjectAuditorJobs.SessionEraser;
            previousClock = ProjectAuditorJobs.Clock;
            storage = new Dictionary<string, string>(StringComparer.Ordinal);
            now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            ProjectAuditorJobs.SessionReader = key => storage.TryGetValue(key, out string value) ? value : string.Empty;
            ProjectAuditorJobs.SessionWriter = (key, value) => storage[key] = value;
            ProjectAuditorJobs.SessionEraser = key => storage.Remove(key);
            ProjectAuditorJobs.Clock = () => now;
            backend = new Backend();
            ProjectAuditorJobs.BackendFactory = () => backend;
            ProjectAuditorJobs.Restore("[]", false);
        }

        [TearDown]
        public void TearDown()
        {
            ProjectAuditorJobs.Restore("[]", false);
            ProjectAuditorJobs.BackendFactory = previousFactory;
            ProjectAuditorJobs.SessionReader = previousReader;
            ProjectAuditorJobs.SessionWriter = previousWriter;
            ProjectAuditorJobs.SessionEraser = previousEraser;
            ProjectAuditorJobs.Clock = previousClock;
        }

        private string Persisted()
        {
            typeof(ProjectAuditorJobs).GetMethod("Persist", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            return storage[StateKey];
        }

        private static string SnapshotKey(JObject summary) => StateKey + ".issues." + (string)summary["editor_session_id"] + "." + (string)summary["audit_id"];

        private void ReplaceSnapshot(JObject summary, string text)
        {
            storage[SnapshotKey(summary)] = text;
            using var hash = SHA256.Create();
            summary["diagnostic_snapshot_sha256"] = string.Concat(hash.ComputeHash(Encoding.UTF8.GetBytes(text)).Select(value => value.ToString("x2")));
        }

        private static JObject Request(string action, string id = Id) =>
            JObject.FromObject(ManageProjectAuditor.HandleCommand(new JObject { ["action"] = action, ["audit_id"] = id }));

        private void Start()
        {
            var result = Request("start");
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            ProjectAuditorJobs.LaunchForTests();
        }

        [Test]
        public void AvailabilityDoesNotInitializeOrInvokeAnalysis()
        {
            var result = JObject.FromObject(ManageProjectAuditor.HandleCommand(new JObject { ["action"] = "availability" }));
            Assert.IsTrue(result.Value<bool>("success"));
            Assert.AreEqual(0, backend.Initializes);
            Assert.AreEqual(0, backend.Starts);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void InvocationUncertaintyRetainsOwnerUntilRealCompletionProof(bool invoked)
        {
            backend.ThrowBeforeInvoke = !invoked;
            backend.ThrowAfterInvoke = invoked;
            Start();
            Assert.AreEqual("failed", (string)Request("status")["data"]["state"]);
            Assert.AreEqual(invoked, ProjectAuditorJobs.HasNativeOwner);
            Assert.AreEqual(invoked, ProjectAuditorJobs.RecoveryRequired);
            if (invoked)
            {
                backend.Module("Success");
                backend.Completed();
                Assert.IsFalse(ProjectAuditorJobs.HasNativeOwner);
                Assert.IsFalse(ProjectAuditorJobs.RecoveryRequired);
            }
        }

        [Test]
        public void DuplicateIdentityNeverRepeatsAndBusyDoesNotAdoptAnotherAudit()
        {
            Start();
            Assert.AreEqual("running", Request("start")["data"]["state"].Value<string>());
            var busy = Request("start", OtherId);
            Assert.IsFalse(busy.Value<bool>("success"));
            Assert.AreEqual("busy", (string)busy["data"]["state"]);
            Assert.AreEqual(1, backend.Starts);
            Assert.IsFalse(Request("status", OtherId).Value<bool>("success"));
            backend.Module("Success");
            backend.Completed();
            Request("start");
            Assert.AreEqual(1, backend.Starts);
            Assert.IsFalse(ProjectAuditorJobs.HasNativeOwner);
        }

        [TestCase("unavailable", 4, "test_readiness")]
        [TestCase("available", 0, "no_registered_rules")]
        public void MissingPackageOrRulesNeverLooksLikeCleanCompletion(string readiness, int rules, string reason)
        {
            backend.Ready = readiness;
            backend.Rules = rules;
            Request("start");
            ProjectAuditorJobs.LaunchForTests();
            JObject state = (JObject)Request("status")["data"];
            Assert.AreEqual("unavailable", (string)state["state"]);
            Assert.AreEqual(reason, (string)state["reason"]);
            Assert.AreEqual(JTokenType.Null, state["clean"].Type);
            Assert.AreEqual(0, backend.Starts);
            Assert.IsFalse(ProjectAuditorJobs.HasNativeOwner);
        }

        [TestCase("Success", "completed", true)]
        [TestCase("Failure", "failed", false)]
        [TestCase("Cancelled", "interrupted", false)]
        public void NativeResultsAndEmptySuccessfulAnalysisRemainDistinct(string native, string expected, bool clean)
        {
            Start();
            backend.Module(native);
            backend.Completed();
            JObject data = (JObject)Request("status")["data"];
            Assert.AreEqual(expected, (string)data["state"]);
            if (clean)
                Assert.IsTrue(data.Value<bool>("clean"));
            else
                Assert.AreEqual(JTokenType.Null, data["clean"].Type);
            Assert.IsFalse(ProjectAuditorJobs.HasNativeOwner);
            var page = Request("issues");
            Assert.AreEqual(0, ((JArray)page["data"]["items"]).Count);
        }

        [Test]
        public void CompletionWithoutAnyExecutedModuleIsUnavailable()
        {
            Start();
            backend.Completed();
            Assert.AreEqual("unavailable", (string)Request("status")["data"]["state"]);
            Assert.AreEqual("no_modules_completed", (string)Request("status")["data"]["reason"]);
        }

        private static JObject Issue(int index) =>
            new JObject
            {
                ["Category"] = "Code",
                ["Severity"] = "Major",
                ["Areas"] = "CPU, Memory",
                ["Description"] = "Allocation " + index,
                ["RelativePath"] = "Assets/Scripts/Probe.cs",
                ["Line"] = index + 1,
                ["DescriptorId"] = "PAC0001",
                ["Recommendation"] = "Cache the result.",
            };

        [Test]
        public void PagesAreBoundedStableDetachedAndForeignCursorIsRejected()
        {
            Start();
            for (int index = 0; index < 105; index++)
                backend.Incoming(Issue(index));
            backend.Module("Success");
            backend.Completed();
            var first = JObject.FromObject(
                ManageProjectAuditor.HandleCommand(
                    new JObject
                    {
                        ["action"] = "issues",
                        ["audit_id"] = Id,
                        ["page_size"] = 100,
                    }
                )
            );
            Assert.AreEqual(100, ((JArray)first["data"]["items"]).Count);
            string cursor = (string)first["data"]["next_cursor"];
            first["data"]["items"][0]["Description"] = "caller mutation";
            var second = JObject.FromObject(
                ManageProjectAuditor.HandleCommand(
                    new JObject
                    {
                        ["action"] = "issues",
                        ["audit_id"] = Id,
                        ["page_size"] = 100,
                        ["cursor"] = cursor,
                    }
                )
            );
            Assert.AreEqual(5, ((JArray)second["data"]["items"]).Count);
            Assert.AreEqual("Allocation 0", (string)Request("issues")["data"]["items"][0]["Description"]);
            Assert.AreEqual(105, (int)second["data"]["diagnostic_count"]);
            Assert.IsFalse(second["data"].Value<bool>("has_more"));
            var foreign = JObject.FromObject(
                ManageProjectAuditor.HandleCommand(
                    new JObject
                    {
                        ["action"] = "issues",
                        ["audit_id"] = Id,
                        ["cursor"] = OtherId + ":100",
                    }
                )
            );
            Assert.IsFalse(foreign.Value<bool>("success"));
            Assert.AreEqual(1, backend.Starts);
        }

        [Test]
        public void CaptureAndPageBytesStayBoundedAndTruncationIsExplicit()
        {
            Start();
            JObject issue = Issue(1);
            issue["Description"] = new string('\uD55C', 100000);
            for (int index = 0; index < ProjectAuditorJobs.MaximumIssues + 1; index++)
                backend.Incoming(issue);
            backend.Module("Success");
            backend.Completed();
            var page = JObject.FromObject(ProjectAuditorJobs.Issues(Id, 100, null));
            Assert.IsTrue(page["data"].Value<bool>("observation_truncated"));
            Assert.LessOrEqual((int)page["data"]["retained_issue_count"], ProjectAuditorJobs.MaximumIssues);
            Assert.LessOrEqual((int)page["data"]["page_bytes"], 256 * 1024);
            Assert.IsTrue(page["data"]["items"].Any(row => row.Value<bool>("fields_truncated")));
            Assert.AreEqual(JTokenType.Null, page["data"]["clean"].Type);
            ProjectAuditorJobs.Restore(Persisted(), false);
            Assert.IsTrue(JToken.DeepEquals(page, JObject.FromObject(ProjectAuditorJobs.Issues(Id, 100, null))));
            Assert.AreEqual(1, backend.Starts);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ReloadPreservesInterruptedOutcomeAndOnlyInvokedWorkRequiresRecovery(bool invoked)
        {
            Request("start");
            if (invoked)
                ProjectAuditorJobs.LaunchForTests();
            var before = Request("status")["data"];
            ProjectAuditorJobs.Restore(new JArray(before).ToString(Formatting.None), false);
            var after = Request("status")["data"];
            Assert.AreEqual("interrupted", (string)after["state"]);
            Assert.AreEqual(invoked, ProjectAuditorJobs.RecoveryRequired);
            Assert.IsFalse(ProjectAuditorJobs.HasNativeOwner);
            Request("start");
            Assert.AreEqual(invoked ? 1 : 0, backend.Starts);
        }

        [Test]
        public void ReloadPreservesCompletedDiagnosticPagesWithoutRepeatingAnalysis()
        {
            Start();
            for (int index = 0; index < 105; index++)
                backend.Incoming(Issue(index));
            backend.Module("Success");
            backend.Completed();
            var before = JObject.FromObject(ProjectAuditorJobs.Issues(Id, 100, null));
            string cursor = (string)before["data"]["next_cursor"];
            var second = JObject.FromObject(ProjectAuditorJobs.Issues(Id, 100, cursor));
            string persisted = Persisted();
            ProjectAuditorJobs.Restore(persisted, false);
            Assert.AreEqual("completed", (string)Request("status")["data"]["state"]);
            var after = JObject.FromObject(ProjectAuditorJobs.Issues(Id, 100, null));
            Assert.IsTrue(after.Value<bool>("success"), after.ToString());
            Assert.IsTrue(JToken.DeepEquals(before["data"], after["data"]));
            Assert.IsTrue(JToken.DeepEquals(second["data"], JObject.FromObject(ProjectAuditorJobs.Issues(Id, 100, cursor))["data"]));
            after["data"]["items"][0]["Description"] = "caller mutation";
            ProjectAuditorJobs.Restore(persisted, false);
            Assert.AreEqual("Allocation 0", (string)Request("issues")["data"]["items"][0]["Description"]);
            Request("start");
            Assert.AreEqual(1, backend.Starts);
        }

        [Test]
        public void NativeSessionStateRoundTripsANearCapacityOwnedDiagnosticSnapshot()
        {
            string id = Guid.NewGuid().ToString("N");
            string session = (string)ProjectAuditorJobs.Availability()["editor_session_id"];
            string ownedKey = StateKey + ".issues." + session + "." + id;
            Assert.IsEmpty(previousReader(ownedKey), "The unique native artifact key must be unowned before this test.");
            try
            {
                // Only this unique artifact uses native SessionState. Summaries and all other keys stay in the fake store.
                ProjectAuditorJobs.SessionReader = key =>
                    key == ownedKey ? previousReader(key)
                    : storage.TryGetValue(key, out string value) ? value
                    : string.Empty;
                ProjectAuditorJobs.SessionWriter = (key, value) =>
                {
                    if (key == ownedKey)
                        previousWriter(key, value);
                    else
                        storage[key] = value;
                };
                Assert.IsTrue(Request("start", id).Value<bool>("success"));
                ProjectAuditorJobs.LaunchForTests();
                JObject issue = Issue(0);
                issue["Description"] = new string('\uD55C', 2048);
                for (int index = 0; index < ProjectAuditorJobs.MaximumIssues; index++)
                    backend.Incoming(issue);
                backend.Module("Success");
                backend.Completed();
                JObject before = JObject.FromObject(ProjectAuditorJobs.Issues(id, 100, null));
                string persisted = Persisted();
                string nativeSnapshot = previousReader(ownedKey);
                Assert.Greater(Encoding.UTF8.GetByteCount(nativeSnapshot), 3 * 1024 * 1024);
                Assert.LessOrEqual(Encoding.UTF8.GetByteCount(nativeSnapshot), 4 * 1024 * 1024 + 32768);
                ProjectAuditorJobs.Restore(persisted, false);
                JObject after = JObject.FromObject(ProjectAuditorJobs.Issues(id, 100, null));
                Assert.IsTrue(after.Value<bool>("success"), after.ToString());
                Assert.IsTrue(JToken.DeepEquals(before, after));
                Assert.AreEqual(1, backend.Starts);
            }
            finally
            {
                previousEraser(ownedKey);
            }
            Assert.IsEmpty(previousReader(ownedKey));
        }

        [TestCase("Success", false, 0)]
        [TestCase("Success", false, 1)]
        [TestCase("Success", true, 1)]
        [TestCase("Failure", false, 1)]
        [TestCase("Cancelled", false, 1)]
        public void ReloadPreservesEmptyTruncatedAndAbortedTerminalObservations(string result, bool truncated, int count)
        {
            Start();
            for (int index = 0; index < count; index++)
                backend.Incoming(Issue(index));
            backend.ObservationTruncated = truncated;
            backend.Module(result);
            backend.Completed();
            JObject before = JObject.FromObject(ProjectAuditorJobs.Issues(Id, 100, null));
            string persisted = Persisted();
            ProjectAuditorJobs.Restore(persisted, false);
            JObject after = JObject.FromObject(ProjectAuditorJobs.Issues(Id, 100, null));
            Assert.IsTrue(after.Value<bool>("success"), after.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, after));
            Assert.AreEqual(1, backend.Starts);
            Assert.IsFalse(ProjectAuditorJobs.RecoveryRequired);
        }

        [TestCase("missing")]
        [TestCase("partial")]
        [TestCase("checksum")]
        [TestCase("foreign_session")]
        [TestCase("foreign_audit")]
        [TestCase("count")]
        [TestCase("bytes")]
        [TestCase("scalar")]
        [TestCase("field_limit")]
        [TestCase("duplicate")]
        [TestCase("trailing")]
        [TestCase("oversized")]
        public void ReloadRejectsMissingMalformedForeignAndIncompleteSnapshots(string fault)
        {
            Start();
            backend.Incoming(Issue(0));
            backend.Module("Success");
            backend.Completed();
            var summary = (JObject)JArray.Parse(Persisted())[0];
            string key = SnapshotKey(summary);
            string original = storage[key];
            if (fault == "missing")
                storage.Remove(key);
            else if (fault == "checksum")
                storage[key] = original.Replace("Allocation 0", "Allocation 1");
            else if (fault == "partial")
                ReplaceSnapshot(summary, original.Substring(0, original.Length / 2));
            else if (fault == "duplicate")
                ReplaceSnapshot(summary, original.Insert(1, "\"version\":1,"));
            else if (fault == "trailing")
                ReplaceSnapshot(summary, original + "{}");
            else if (fault == "oversized")
                ReplaceSnapshot(summary, new string(' ', 4 * 1024 * 1024 + 32769));
            else
            {
                JObject snapshot = JObject.Parse(original);
                if (fault == "foreign_session")
                    snapshot["summary"]["editor_session_id"] = OtherId;
                else if (fault == "foreign_audit")
                    snapshot["summary"]["audit_id"] = OtherId;
                else if (fault == "count")
                    ((JArray)snapshot["items"]).Clear();
                else if (fault == "bytes")
                    snapshot["captured_bytes"] = 0;
                else if (fault == "scalar")
                    snapshot["items"][0]["Description"] = new JObject { ["native"] = "handle" };
                else if (fault == "field_limit")
                    snapshot["items"][0]["Description"] = new string('x', 2049);
                ReplaceSnapshot(summary, snapshot.ToString(Formatting.None));
            }
            ProjectAuditorJobs.Restore(new JArray(summary).ToString(Formatting.None), false);
            JObject response = Request("issues");
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(response["data"].Value<bool>("results_available"));
            Assert.AreEqual("completed", (string)response["data"]["state"]);
            Assert.AreEqual(
                fault == "missing" ? "captured_rows_missing_on_domain_reload" : "captured_rows_corrupt_on_domain_reload",
                (string)response["data"]["results_unavailable_reason"]
            );
            Assert.IsFalse(ProjectAuditorJobs.RecoveryRequired);
            Assert.AreEqual(1, backend.Starts);
            ProjectAuditorJobs.Restore(Persisted(), false);
            JObject repeated = Request("issues");
            Assert.IsFalse(repeated.Value<bool>("success"));
            Assert.AreEqual("completed", (string)repeated["data"]["state"]);
            Assert.AreEqual((string)response["data"]["results_unavailable_reason"], (string)repeated["data"]["results_unavailable_reason"]);
            Assert.IsFalse(ProjectAuditorJobs.RecoveryRequired);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ReloadNeverPublishesIncompleteNativeObservations(bool invoked)
        {
            Request("start");
            if (invoked)
            {
                ProjectAuditorJobs.LaunchForTests();
                backend.Incoming(Issue(0));
            }
            string persisted = Persisted();
            Assert.IsFalse(storage.Keys.Any(key => key.Contains(".issues.")));
            ProjectAuditorJobs.Restore(persisted, false);
            JObject after = Request("issues");
            Assert.IsFalse(after.Value<bool>("success"));
            Assert.AreEqual("interrupted", (string)after["data"]["state"]);
            Assert.AreEqual(invoked, ProjectAuditorJobs.RecoveryRequired);
            Assert.AreEqual(invoked ? 1 : 0, backend.Starts);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SnapshotWritesAreImmutableAndFailedWritesStayUnavailableAfterReload(bool throws)
        {
            Start();
            backend.Incoming(Issue(0));
            backend.Module("Success");
            backend.Completed();
            string persisted = Persisted();
            string key = SnapshotKey((JObject)JArray.Parse(persisted)[0]);
            int writes = 0;
            ProjectAuditorJobs.SessionWriter = (name, value) =>
            {
                if (name == key)
                    writes++;
                storage[name] = value;
            };
            Assert.AreEqual(persisted, Persisted());
            Assert.AreEqual(0, writes);
            ProjectAuditorJobs.Restore("[]", false);
            Request("start", OtherId);
            ProjectAuditorJobs.LaunchForTests();
            backend.Module("Success");
            backend.Completed();
            ProjectAuditorJobs.SessionWriter = (name, value) =>
            {
                if (throws && name.Contains(".issues."))
                    throw new InvalidOperationException("Session persistence unavailable.");
                storage[name] = name.Contains(".issues.") ? value.Substring(0, value.Length / 2) : value;
            };
            string broken = Persisted();
            Assert.IsTrue(Request("issues", OtherId).Value<bool>("success"));
            ProjectAuditorJobs.Restore(broken, false);
            JObject unavailable = Request("issues", OtherId);
            Assert.IsFalse(unavailable.Value<bool>("success"));
            Assert.AreEqual("captured_rows_persistence_failed", (string)unavailable["data"]["results_unavailable_reason"]);
            Assert.AreEqual(JTokenType.Null, unavailable["data"]["clean"].Type);
            Assert.AreEqual(2, backend.Starts);
        }

        [Test]
        public void RetentionDeletesOnlyKnownAuditKeysInTheCurrentSession()
        {
            for (int index = 0; index < 8; index++)
            {
                string id = index.ToString("x32");
                Assert.IsTrue(Request("start", id).Value<bool>("success"));
                ProjectAuditorJobs.LaunchForTests();
                backend.Incoming(Issue(index));
                backend.Module("Success");
                backend.Completed();
            }
            JArray summaries = JArray.Parse(Persisted());
            Assert.AreEqual(8, storage.Keys.Count(key => key.Contains(".issues.")));
            Assert.IsFalse(Request("start").Value<bool>("success"));
            JObject foreign = (JObject)summaries[0].DeepClone();
            foreign["editor_session_id"] = OtherId;
            string foreignKey = SnapshotKey(foreign);
            storage[foreignKey] = "foreign session data";
            string unknownKey = SnapshotKey(new JObject { ["editor_session_id"] = summaries[0]["editor_session_id"], ["audit_id"] = OtherId });
            storage[unknownKey] = "untracked audit data";
            now += 10 * 60 * 1000 + 1;
            ProjectAuditorJobs.Restore(summaries.ToString(Formatting.None), false);
            foreach (JObject summary in summaries)
            {
                Assert.IsFalse(Request("status", (string)summary["audit_id"]).Value<bool>("success"));
                Assert.IsFalse(storage.ContainsKey(SnapshotKey(summary)));
            }
            Assert.AreEqual("foreign session data", storage[foreignKey]);
            Assert.AreEqual("untracked audit data", storage[unknownKey]);
            Assert.IsTrue(Request("start").Value<bool>("success"));
            Assert.AreEqual(8, backend.Starts);
        }

        [Test]
        public void FailedOwnedCleanupRetainsHistoryUntilTheArtifactCanBeErased()
        {
            Start();
            backend.Module("Success");
            backend.Completed();
            JObject summary = (JObject)JArray.Parse(Persisted())[0];
            string key = SnapshotKey(summary);
            now += 10 * 60 * 1000 + 1;
            ProjectAuditorJobs.SessionEraser = _ => throw new InvalidOperationException("Session cleanup unavailable.");
            Assert.IsTrue(Request("status").Value<bool>("success"));
            Assert.IsTrue(storage.ContainsKey(key));
            ProjectAuditorJobs.SessionEraser = name => storage.Remove(name);
            Assert.IsFalse(Request("status").Value<bool>("success"));
            Assert.IsFalse(storage.ContainsKey(key));
        }

        [TestCase("negative_count")]
        [TestCase("excess_count")]
        [TestCase("inexact_count")]
        [TestCase("wrong_type")]
        [TestCase("duplicate_identity")]
        public void MalformedSummaryNeverPublishesAvailableRows(string fault)
        {
            Start();
            backend.Incoming(Issue(0));
            backend.Module("Success");
            backend.Completed();
            JArray summaries = JArray.Parse(Persisted());
            if (fault == "negative_count")
                summaries[0]["diagnostic_count"] = -1;
            else if (fault == "excess_count")
                summaries[0]["retained_issue_count"] = ProjectAuditorJobs.MaximumIssues + 1;
            else if (fault == "inexact_count")
                summaries[0]["diagnostic_count"] = 2;
            else if (fault == "wrong_type")
                summaries[0]["diagnostic_count"] = "1";
            else
                summaries.Add(summaries[0].DeepClone());
            ProjectAuditorJobs.Restore(summaries.ToString(Formatting.None), false);
            Assert.IsTrue(ProjectAuditorJobs.RecoveryRequired);
            Assert.IsFalse(Request("issues").Value<bool>("success"));
            Assert.AreEqual(1, backend.Starts);
        }

        [Test]
        public void LegacySummaryWithoutASnapshotNeverInventsCapturedDiagnostics()
        {
            Start();
            backend.Incoming(Issue(0));
            backend.Module("Success");
            backend.Completed();
            ProjectAuditorJobs.Restore(new JArray(Request("status")["data"]).ToString(Formatting.None), false);
            Assert.AreEqual("completed", (string)Request("status")["data"]["state"]);
            Assert.IsFalse(Request("issues").Value<bool>("success"));
            Assert.AreEqual(1, backend.Starts);
        }

        [TestCase("cancel")]
        [TestCase("export")]
        public void UnsupportedControlNeverStartsOrCancelsNativeAnalysis(string action)
        {
            Assert.IsFalse(Request(action).Value<bool>("success"));
            Assert.AreEqual(0, backend.Starts);
        }
    }
}
