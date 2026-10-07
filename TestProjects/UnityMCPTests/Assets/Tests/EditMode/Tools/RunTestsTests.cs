using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// Tests for RunTests tool functionality.
    /// Injects a test service so request validation never launches recursive test runs.
    /// </summary>
    public class RunTestsTests
    {
        private const BindingFlags StaticPrivate = BindingFlags.NonPublic | BindingFlags.Static;
        private static readonly FieldInfo ServiceField = typeof(MCPServiceLocator).GetField("_testRunnerService", StaticPrivate);
        private static readonly FieldInfo CurrentField = typeof(TestJobManager).GetField("_currentJobId", StaticPrivate);
        private static readonly FieldInfo PersistField = typeof(TestJobManager).GetField("_lastPersistUnixMs", StaticPrivate);
        private static Dictionary<string, TestJob> Jobs => (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", StaticPrivate).GetValue(null);
        private Dictionary<string, TestJob> originalJobs;
        private object originalService;
        private object originalCurrent;
        private object originalPersist;
        private string originalJobsSession;
        private string originalCurrentSession;
        private CapturingTestService service;

        [SetUp]
        public void SetUp()
        {
            originalJobs = new Dictionary<string, TestJob>(Jobs);
            originalCurrent = CurrentField.GetValue(null);
            originalPersist = PersistField.GetValue(null);
            originalService = ServiceField.GetValue(null);
            originalJobsSession = SessionState.GetString("MCPForUnity.TestJobsV1", "");
            originalCurrentSession = SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", "");
            Jobs.Clear();
            CurrentField.SetValue(null, null);
            service = new CapturingTestService();
            ServiceField.SetValue(null, service);
        }

        [TearDown]
        public void TearDown()
        {
            Jobs.Clear();
            foreach (var pair in originalJobs)
                Jobs.Add(pair.Key, pair.Value);
            CurrentField.SetValue(null, originalCurrent);
            PersistField.SetValue(null, originalPersist);
            ServiceField.SetValue(null, originalService);
            SessionState.SetString("MCPForUnity.TestJobsV1", originalJobsSession);
            SessionState.SetString("MCPForUnity.CurrentTestJobIdV1", originalCurrentSession);
        }

        private static readonly string[] SelectorKeys =
        {
            "testNames",
            "test_names",
            "groupNames",
            "group_names",
            "categoryNames",
            "category_names",
            "assemblyNames",
            "assembly_names",
        };

        private static IEnumerable<TestCaseData> MalformedSelectors()
        {
            foreach (string key in SelectorKeys)
            foreach (
                string json in new[]
                {
                    "42",
                    "true",
                    "{}",
                    "[\"Valid\",42]",
                    "[\"Valid\",true]",
                    "[\"Valid\",{}]",
                    "[\"Valid\",null]",
                    "\"[\\\"Valid\\\",42]\"",
                    "[\"[\\\"Valid\\\",42]\"]",
                    "[[\"Valid\",42]]",
                }
            )
                yield return new TestCaseData(key, json);
        }

        [TestCaseSource(nameof(MalformedSelectors))]
        public void MalformedSelector_RejectsBeforeJobReservationOrServiceCall(string key, string json)
        {
            string jobsSession = SessionState.GetString("MCPForUnity.TestJobsV1", "");
            string currentSession = SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", "");
            var response = MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject { [key] = JToken.Parse(json) }).GetAwaiter().GetResult();
            Assert.AreEqual(0, service.Calls, "Malformed selector reached the test runner before rejection.");
            Assert.IsInstanceOf<ErrorResponse>(response, "Malformed selectors must not start an unfiltered or altered test run.");
            StringAssert.Contains(key.Replace("_", "").ToLowerInvariant(), ((ErrorResponse)response).Error.Replace("_", "").ToLowerInvariant());
            Assert.AreEqual(0, Jobs.Count);
            Assert.IsNull(TestJobManager.CurrentJobId);
            Assert.AreEqual(jobsSession, SessionState.GetString("MCPForUnity.TestJobsV1", ""));
            Assert.AreEqual(currentSession, SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", ""));
        }

        private static IEnumerable<TestCaseData> ValidSelectors()
        {
            foreach (string key in SelectorKeys)
            foreach (
                string json in new[] { "\"Valid\"", "[\"Valid\"]", "\"[\\\"Valid\\\"]\"", "[\"[\\\"Valid\\\"]\"]", "[[\"Valid\"]]", "[\"\",\"Valid\",\" \"]" }
            )
                yield return new TestCaseData(key, json);
        }

        [TestCaseSource(nameof(ValidSelectors))]
        public void ValidSelector_PreservesSupportedEncodingsAndAliases(string key, string json)
        {
            var response = MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject { [key] = JToken.Parse(json) }).GetAwaiter().GetResult();
            Assert.IsInstanceOf<SuccessResponse>(response);
            Assert.AreEqual(1, service.Calls);
            string[] selected =
                key.StartsWith("test") ? service.Options.TestNames
                : key.StartsWith("group") ? service.Options.GroupNames
                : key.StartsWith("category") ? service.Options.CategoryNames
                : service.Options.AssemblyNames;
            CollectionAssert.AreEqual(new[] { "Valid" }, selected);
        }

        [TestCase(null)]
        [TestCase("null")]
        [TestCase("[]")]
        [TestCase("\"\"")]
        [TestCase("\" \"")]
        public void OptionalSelectorDefaults_RemainUnfiltered(string json)
        {
            var request = new JObject();
            if (json != null)
                foreach (string key in new[] { "testNames", "groupNames", "categoryNames", "assemblyNames" })
                    request[key] = JToken.Parse(json);
            var response = MCPForUnity.Editor.Tools.RunTests.HandleCommand(request).GetAwaiter().GetResult();
            Assert.IsInstanceOf<SuccessResponse>(response);
            Assert.AreEqual(1, service.Calls);
            Assert.IsNull(service.Options);
        }

        private sealed class CapturingTestService : ITestRunnerService
        {
            public int Calls;
            public TestFilterOptions Options;
            private readonly TaskCompletionSource<TestRunResult> completion = new TaskCompletionSource<TestRunResult>();

            public Task<TestRunResult> RunTestsAsync(TestMode mode, TestFilterOptions filterOptions = null)
            {
                Calls++;
                Options = filterOptions;
                return completion.Task;
            }

            public Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode) => throw new NotSupportedException();
        }

        [Test]
        public void HandleCommand_WhenTestsAlreadyRunning_ReturnsBusyError()
        {
            // Arrange: Force TestJobManager into a "busy" state without starting a real run.
            // We do this via reflection because TestJobManager is internal.
            var asm = typeof(MCPForUnity.Editor.Services.MCPServiceLocator).Assembly;
            var testJobManagerType = asm.GetType("MCPForUnity.Editor.Services.TestJobManager");
            Assert.NotNull(testJobManagerType, "Could not locate TestJobManager type via reflection");

            var currentJobIdField = testJobManagerType.GetField("_currentJobId", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.NotNull(currentJobIdField, "Could not locate TestJobManager._currentJobId field");

            var originalJobId = currentJobIdField.GetValue(null) as string;
            currentJobIdField.SetValue(null, "busy-test-job-id");

            try
            {
                var resultObj = MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject()).GetAwaiter().GetResult();

                Assert.IsInstanceOf<ErrorResponse>(resultObj);
                var err = (ErrorResponse)resultObj;
                Assert.AreEqual(false, err.Success);
                Assert.AreEqual("tests_running", err.Code);

                var data = err.Data != null ? JObject.FromObject(err.Data) : null;
                Assert.NotNull(data, "Expected data payload on tests_running error");
                Assert.AreEqual("tests_running", data["reason"]?.ToString());
                Assert.GreaterOrEqual(data["retry_after_ms"]?.Value<int>() ?? 0, 500);
            }
            finally
            {
                currentJobIdField.SetValue(null, originalJobId);
            }
        }

        [Test]
        public void HandleCommand_WithInvalidMode_ReturnsError()
        {
            var resultObj = MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject { ["mode"] = "NotARealMode" }).GetAwaiter().GetResult();

            Assert.IsInstanceOf<ErrorResponse>(resultObj);
            var err = (ErrorResponse)resultObj;
            Assert.AreEqual(false, err.Success);
            Assert.IsTrue(err.Error.Contains("Unknown test mode", StringComparison.OrdinalIgnoreCase));
        }
    }
}
