using System;
using System.Collections;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class RefreshUnityTests
    {
        [TestCase("force")]
        [TestCase("if_dirty")]
        public void BusyCompilationLeavesPendingSourcesForAnExplicitRefreshAfterIdle(string mode)
        {
            var flags = BindingFlags.NonPublic | BindingFlags.Static;
            var running = typeof(TestRunStatus).GetField("_isRunning", flags);
            object previousRun = running.GetValue(null);
            var compiling = RefreshUnity.IsCompiling;
            var refresh = RefreshUnity.RefreshAssets;
            var request = RefreshUnity.RequestCompilation;
            var ledger = RefreshUnity.LedgerOverride;
            bool pendingSource = true;
            bool pipelineBusy = true;
            int imports = 0,
                requests = 0;
            try
            {
                RefreshUnity.RefreshAssets = _ =>
                {
                    imports++;
                    pendingSource = false;
                };
                RefreshUnity.RequestCompilation = () => requests++;
                RefreshUnity.IsCompiling = () => pipelineBusy;
                RefreshUnity.LedgerOverride = new CompilationLedger("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "C:/fixture", _ => { });
                running.SetValue(null, false);
                var parameters = new JObject
                {
                    ["mode"] = mode,
                    ["scope"] = "all",
                    ["compile"] = "request",
                    ["wait_for_ready"] = false,
                };
                var busy = ToJObject(RefreshUnity.HandleCommand(parameters).GetAwaiter().GetResult());
                Assert.IsFalse((bool)busy["success"]);
                Assert.AreEqual("preexisting_compile", (string)busy["data"]["reason"]);
                Assert.IsTrue((bool)busy["data"]["retryable"]);
                Assert.IsFalse((bool)busy["data"]["refresh_triggered"]);
                Assert.IsFalse((bool)busy["data"]["compile_requested"]);
                Assert.IsTrue(pendingSource);
                Assert.AreEqual(0, imports);
                Assert.AreEqual(0, requests);
                StringAssert.Contains("explicitly refresh again", (string)busy["data"]["hint"]);

                pipelineBusy = false;
                var refreshed = ToJObject(RefreshUnity.HandleCommand(parameters).GetAwaiter().GetResult());
                Assert.IsTrue((bool)refreshed["success"]);
                Assert.IsTrue((bool)refreshed["data"]["refresh_triggered"]);
                Assert.IsTrue((bool)refreshed["data"]["compile_requested"]);
                Assert.IsFalse(pendingSource);
                Assert.AreEqual(1, imports);
                Assert.AreEqual(1, requests);
                // Asset-only refresh remains available even when the script pipeline is busy.
                pipelineBusy = true;
                parameters["compile"] = "none";
                var assets = ToJObject(RefreshUnity.HandleCommand(parameters).GetAwaiter().GetResult());
                Assert.IsTrue((bool)assets["success"]);
                Assert.IsTrue((bool)assets["data"]["refresh_triggered"]);
                Assert.IsFalse((bool)assets["data"]["compile_requested"]);
                Assert.AreEqual(2, imports);
                Assert.AreEqual(1, requests);
            }
            finally
            {
                RefreshUnity.RefreshAssets = refresh;
                RefreshUnity.RequestCompilation = request;
                RefreshUnity.LedgerOverride = ledger;
                RefreshUnity.IsCompiling = compiling;
                running.SetValue(null, previousRun);
            }
        }

        [TestCase("timeout_seconds", "0")]
        [TestCase("timeout_seconds", "-1")]
        [TestCase("timeout_seconds", "86401")]
        [TestCase("timeout_seconds", "true")]
        [TestCase("timeout_seconds", "\"900\"")]
        [TestCase("timeoutSeconds", "[]")]
        [TestCase("compile_id", "42")]
        [TestCase("compile_id", "\"not-an-id\"")]
        [TestCase("mode", "\"bogus\"")]
        [TestCase("mode", "42")]
        [TestCase("mode", "[]")]
        [TestCase("scope", "\"bogus\"")]
        [TestCase("scope", "false")]
        [TestCase("scope", "{}")]
        [TestCase("compile", "\"bogus\"")]
        [TestCase("compile", "true")]
        [TestCase("compile", "[]")]
        [TestCase("wait_for_ready", "\"invalid\"")]
        [TestCase("wait_for_ready", "2")]
        [TestCase("wait_for_ready", "{}")]
        [TestCase("waitForReady", "\"invalid\"")]
        [TestCase("waitForReady", "[]")]
        public void InvalidInput_IsRejectedBeforeRefreshCompileOrReadinessChecks(string field, string json)
        {
            bool alreadyRunning = TestRunStatus.IsRunning;
            if (!alreadyRunning)
                TestRunStatus.MarkStarted(UnityEditor.TestTools.TestRunner.Api.TestMode.EditMode);
            try
            {
                var parameters = new JObject
                {
                    ["mode"] = "force",
                    ["scope"] = "all",
                    ["compile"] = "request",
                    ["wait_for_ready"] = false,
                };
                parameters[field] = JToken.Parse(json);
                var task = RefreshUnity.HandleCommand(parameters);

                Assert.IsTrue(task.IsCompleted, "Invalid inputs must fail before any refresh, compilation, or editor update wait.");
                var response = ToJObject(task.Result);
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains(field, response.Value<string>("error"), response.ToString());
                Assert.AreNotEqual("tests_running", response.Value<string>("error"), "Pure validation must precede the runtime guard.");
            }
            finally
            {
                if (!alreadyRunning)
                    TestRunStatus.MarkFinished();
            }
        }

        [TestCase("\"if_dirty\"", "\"scripts\"", "\"none\"", "false")]
        [TestCase("\"FORCE\"", "\"SCRIPTS\"", "\"NONE\"", "\"false\"")]
        [TestCase("null", "null", "null", "null")]
        public void SupportedOptionForms_ReachRuntimeGuard(string mode, string scope, string compile, string wait)
        {
            bool alreadyRunning = TestRunStatus.IsRunning;
            if (!alreadyRunning)
                TestRunStatus.MarkStarted(UnityEditor.TestTools.TestRunner.Api.TestMode.EditMode);
            try
            {
                var task = RefreshUnity.HandleCommand(
                    new JObject
                    {
                        ["mode"] = JToken.Parse(mode),
                        ["scope"] = JToken.Parse(scope),
                        ["compile"] = JToken.Parse(compile),
                        ["wait_for_ready"] = JToken.Parse(wait),
                    }
                );
                Assert.IsTrue(task.IsCompleted);
                Assert.AreEqual("tests_running", ToJObject(task.Result).Value<string>("error"));
            }
            finally
            {
                if (!alreadyRunning)
                    TestRunStatus.MarkFinished();
            }
        }

        [TestCase(0.5)]
        [TestCase(900)]
        [TestCase(7200)]
        [TestCase(86400)]
        public void ExplicitLargeAndRemainingBudgets_AreNotTruncated(double seconds)
        {
            Assert.AreEqual(seconds, OperationTimeouts.ReadSeconds(new JObject { ["timeout_seconds"] = seconds }, 300));
            Assert.AreEqual(seconds, OperationTimeouts.ReadSeconds(new JObject { ["timeoutSeconds"] = seconds }, 300));
        }

        [Test]
        public void InvalidShadowedTimeoutAlias_IsRejected()
        {
            Assert.Throws<ArgumentException>(() => OperationTimeouts.ReadSeconds(new JObject { ["timeout_seconds"] = 900, ["timeoutSeconds"] = true }, 300));
            Assert.Throws<ArgumentException>(() => OperationTimeouts.ReadSeconds(new JObject { ["timeout_seconds"] = double.NaN }, 300));
            Assert.Throws<ArgumentException>(() => OperationTimeouts.ReadSeconds(new JObject { ["timeout_seconds"] = double.PositiveInfinity }, 300));
        }

        [TestCase(null, 300)]
        [TestCase("900", 900)]
        [TestCase("NaN", 300)]
        [TestCase("Infinity", 300)]
        [TestCase("-1", 300)]
        [TestCase("0", 300)]
        [TestCase("1000000", 86400)]
        public void EnvironmentBudget_IsFiniteAndConfigurable(string raw, double expected)
        {
            const string name = "UNITY_MCP_OWNED_TIMEOUT_TEST";
            string previous = Environment.GetEnvironmentVariable(name);
            try
            {
                Environment.SetEnvironmentVariable(name, raw);
                Assert.AreEqual(expected, OperationTimeouts.EnvironmentSeconds(name, 300));
            }
            finally
            {
                Environment.SetEnvironmentVariable(name, previous);
            }
        }

        [UnityTest]
        public IEnumerator HandleCommand_BatchNormalizedWaitForReady_WaitsForEditorUpdate()
        {
            if (TestRunStatus.IsRunning)
                Assert.Ignore("Refresh is intentionally blocked during bridge-managed test runs.");

            var parameters = (JObject)
                typeof(BatchExecute)
                    .GetMethod("NormalizeParameterKeys", BindingFlags.NonPublic | BindingFlags.Static)
                    .Invoke(
                        null,
                        new object[]
                        {
                            new JObject
                            {
                                ["scope"] = "scripts",
                                ["compile"] = "none",
                                ["wait_for_ready"] = true,
                            },
                        }
                    );
            var task = RefreshUnity.HandleCommand(parameters);

            Assert.IsFalse(task.IsCompleted, "Batch normalization must preserve the readiness wait.");
            double deadline = EditorApplication.timeSinceStartup + 5.0;
            while (!task.IsCompleted)
            {
                if (EditorApplication.timeSinceStartup > deadline)
                    Assert.Fail("Readiness wait did not finish in an idle editor.");
                yield return null;
            }
            Assert.IsTrue(ToJObject(task.Result).Value<bool>("success"));
        }

        [TestCase(null)]
        [TestCase("waitForReady")]
        [TestCase("wait_for_ready")]
        public void HandleCommand_DefaultOrFalseWait_CompletesSynchronously(string waitKey)
        {
            if (TestRunStatus.IsRunning)
                Assert.Ignore("Refresh is intentionally blocked during bridge-managed test runs.");

            var parameters = new JObject { ["scope"] = "scripts", ["compile"] = "none" };
            if (waitKey != null)
                parameters[waitKey] = false;
            var task = RefreshUnity.HandleCommand(parameters);
            Assert.IsTrue(task.IsCompleted, "The default and false values must not wait.");
            Assert.IsTrue(ToJObject(task.Result).Value<bool>("success"));
        }

        [Test]
        public void HandleCommand_SnakeCaseWaitTakesPrecedenceOverCamelCase()
        {
            if (TestRunStatus.IsRunning)
                Assert.Ignore("Refresh is intentionally blocked during bridge-managed test runs.");

            var task = RefreshUnity.HandleCommand(
                new JObject
                {
                    ["scope"] = "scripts",
                    ["compile"] = "none",
                    ["wait_for_ready"] = false,
                    ["waitForReady"] = true,
                }
            );
            Assert.IsTrue(task.IsCompleted, "The established snake_case parameter must take precedence.");
            Assert.IsTrue(ToJObject(task.Result).Value<bool>("success"));
        }

        [Test]
        public void HandleCommand_CompileNone_NoWait_CompletesSynchronously()
        {
            // scope=scripts skips AssetDatabase.Refresh, compile=none skips the
            // request, wait_for_ready=false skips both waits: nothing on this path
            // yields, so the task must already be complete when it is handed back.
            var task = RefreshUnity.HandleCommand(
                new JObject
                {
                    ["mode"] = "if_dirty",
                    ["scope"] = "scripts",
                    ["compile"] = "none",
                    ["wait_for_ready"] = false,
                }
            );

            Assert.IsTrue(task.IsCompleted, "compile=none with wait_for_ready=false must not defer");

            var result = ToJObject(task.Result);
            if (TestRunStatus.IsRunning)
            {
                // Under the bridge's run_tests the handler short-circuits before the
                // refresh logic; that exit is synchronous too, and is all we can check.
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual("tests_running", result["data"]?["reason"]?.ToString(), result.ToString());
                return;
            }

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.IsFalse(data.Value<bool>("refresh_triggered"), result.ToString());
            Assert.IsFalse(data.Value<bool>("compile_requested"), result.ToString());
            Assert.AreEqual(JTokenType.Null, data["compile_started"].Type, "compile_started must be null when no compile was waited for");
        }

        [Test]
        public void WaitForCompilationToStart_CounterAlreadyMoved_CompletesSynchronouslyTrue()
        {
            // A compile that began and ended inside AssetDatabase.Refresh leaves only
            // the counter behind. Presenting a stale "before" value reproduces that
            // state without triggering a compile.
            var task = RefreshUnity.WaitForCompilationToStartAsync(EditorStateCache.CompileCount - 1, TimeSpan.FromSeconds(10));

            Assert.IsTrue(task.IsCompleted, "counter already moved must resolve without a tick");
            Assert.IsTrue(task.Result, "a moved counter is a start, not a grace expiry");
        }

        [UnityTest]
        public IEnumerator WaitForCompilationToStart_GraceElapsed_ResolvesFalse()
        {
            // No compile is requested here, so with the counter current the only way
            // out is the grace. A zero grace expires on the first update tick.
            var task = RefreshUnity.WaitForCompilationToStartAsync(EditorStateCache.CompileCount, TimeSpan.Zero);

            Assert.IsFalse(task.IsCompleted, "nothing has started, so the wait must actually wait");

            double deadline = EditorApplication.timeSinceStartup + 5.0;
            while (!task.IsCompleted)
            {
                if (EditorApplication.timeSinceStartup > deadline)
                {
                    Assert.Fail("grace expiry never resolved the wait");
                }
                yield return null;
            }

            Assert.IsFalse(task.Result, "grace expiry must report that no compile started");
        }
    }
}
