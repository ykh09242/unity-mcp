using System;
using System.Linq;
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
        private Func<IProjectAuditorBackend> previousFactory;
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
            backend = new Backend();
            ProjectAuditorJobs.BackendFactory = () => backend;
            ProjectAuditorJobs.Restore("[]", false);
        }

        [TearDown]
        public void TearDown()
        {
            ProjectAuditorJobs.Restore("[]", false);
            ProjectAuditorJobs.BackendFactory = previousFactory;
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
        public void ReloadNeverInventsCapturedRowsForCompletedDiagnostics()
        {
            Start();
            backend.Incoming(Issue(0));
            backend.Module("Success");
            backend.Completed();
            ProjectAuditorJobs.Restore(new JArray(Request("status")["data"]).ToString(Formatting.None), false);
            Assert.AreEqual("completed", (string)Request("status")["data"]["state"]);
            Assert.IsFalse(Request("issues").Value<bool>("success"));
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
