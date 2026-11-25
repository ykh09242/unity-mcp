using System;
using System.Collections;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources.Editor;
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
        [TestCase(false, TestName = "FailedHandoffAndFailedCleanupReturnANonDispatchedJob")]
        [TestCase(true, TestName = "PlayTransitionDuringSourcePreparationRefusesNativeDispatch")]
        public void PreparationFailureReturnsTerminalJobEvidence(bool playTransition)
        {
            const string first = "11111111111111111111111111111111";
            const string second = "22222222222222222222222222222222";
            const string session = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
            const string playKey = "MCPForUnity.EditorState.PlayTransition";
            var running = typeof(TestRunStatus).GetField("_isRunning", BindingFlags.NonPublic | BindingFlags.Static);
            var originalRunning = running.GetValue(null);
            var originalTransition = SessionState.GetBool(playKey, false);
            var originalPrepare = RefreshUnity.PrepareSourceOverride;
            var originalLedger = RefreshUnity.LedgerOverride;
            var originalReservation = RefreshUnity.ReservationOverride;
            var originalRequest = RefreshUnity.RequestCompilation;
            var originalCompiling = RefreshUnity.IsCompiling;
            var writes = new System.Collections.Generic.List<string>();
            string checkpoint = "";
            string savedLedger = null;
            bool writable = false;
            int requests = 0;
            var ledger = new CompilationLedger(session, "C:/fixture", value => savedLedger = value);
            var owner = CompilationJobTracker.RestoreReservation(
                () => checkpoint,
                value =>
                {
                    string phase = value == "" ? "cleared" : (string)JObject.Parse(value)["Phase"];
                    writes.Add(phase);
                    if (phase == "preparing" || writable || playTransition)
                        checkpoint = value;
                },
                ledger.InterruptedBeforeDispatch
            );
            JObject Command(string id) =>
                new JObject
                {
                    ["compile_id"] = id,
                    ["scope"] = "scripts",
                    ["compile"] = "request",
                };
            try
            {
                running.SetValue(null, false);
                SessionState.SetBool(playKey, false);
                RefreshUnity.LedgerOverride = ledger;
                RefreshUnity.ReservationOverride = owner;
                RefreshUnity.RequestCompilation = () => requests++;
                RefreshUnity.IsCompiling = () => false;
                RefreshUnity.PrepareSourceOverride = (_, _) =>
                {
                    if (playTransition)
                        SessionState.SetBool(playKey, true);
                    return Task.CompletedTask;
                };
                var response = ToJObject(RefreshUnity.HandleCommand(Command(first)).GetAwaiter().GetResult());
                Assert.IsFalse((bool)response["success"]);
                Assert.AreEqual(0, requests);
                Assert.IsNull(owner.State);
                Assert.AreEqual("finished", (string)response["data"]["compilation"]["state"]);
                Assert.AreEqual("not_dispatched", (string)response["data"]["compilation"]["reason"]);
                Assert.AreEqual("not_dispatched", (string)new CompilationLedger(session, "C:/fixture", _ => { }, savedLedger).Query(first)["reason"]);
                CollectionAssert.AreEqual(playTransition ? new[] { "preparing", "cleared" } : new[] { "preparing", "pending", "cleared" }, writes);

                SessionState.SetBool(playKey, false);
                writable = true;
                RefreshUnity.PrepareSourceOverride = null;
                var duplicate = ToJObject(RefreshUnity.HandleCommand(Command(first)).GetAwaiter().GetResult());
                Assert.AreEqual("not_dispatched", (string)duplicate["data"]["compilation"]["reason"]);
                var retry = ToJObject(RefreshUnity.HandleCommand(Command(second)).GetAwaiter().GetResult());
                Assert.IsTrue((bool)retry["success"]);
                Assert.AreEqual(second, owner.State.JobId);
                Assert.AreEqual(1, requests);
            }
            finally
            {
                SessionState.SetBool(playKey, originalTransition);
                running.SetValue(null, originalRunning);
                RefreshUnity.PrepareSourceOverride = originalPrepare;
                RefreshUnity.LedgerOverride = originalLedger;
                RefreshUnity.ReservationOverride = originalReservation;
                RefreshUnity.RequestCompilation = originalRequest;
                RefreshUnity.IsCompiling = originalCompiling;
            }
        }

        [TestCase("scripts")]
        [TestCase("all")]
        public async Task ReloadDuringSourcePreparationAdmitsAnotherCallerWithoutRepeatingTheInterruptedJob(string scope)
        {
            const string first = "11111111111111111111111111111111";
            const string second = "22222222222222222222222222222222";
            const string session = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
            JObject Command(string id) =>
                new JObject
                {
                    ["compile_id"] = id,
                    ["scope"] = scope,
                    ["compile"] = "request",
                    ["wait_for_ready"] = false,
                };
            var running = typeof(TestRunStatus).GetField("_isRunning", BindingFlags.NonPublic | BindingFlags.Static);
            var originalRunning = running.GetValue(null);
            var originalPrepare = RefreshUnity.PrepareSourceOverride;
            var originalLedger = RefreshUnity.LedgerOverride;
            var originalReservation = RefreshUnity.ReservationOverride;
            var originalRequest = RefreshUnity.RequestCompilation;
            var originalRefresh = RefreshUnity.RefreshAssets;
            var originalCompiling = RefreshUnity.IsCompiling;
            var prepare = new TaskCompletionSource<bool>();
            string saved = null;
            var ledger = new CompilationLedger(session, "C:/fixture", encoded => saved = encoded);
            var owner = new OwnedCompilationReservation();
            int nativeRequests = 0;
            int nativeImports = 0;
            try
            {
                running.SetValue(null, false);
                RefreshUnity.PrepareSourceOverride = (_, _) => prepare.Task;
                RefreshUnity.LedgerOverride = ledger;
                RefreshUnity.ReservationOverride = owner;
                RefreshUnity.IsCompiling = () => false;
                RefreshUnity.RequestCompilation = () => nativeRequests++;
                RefreshUnity.RefreshAssets = _ => nativeImports++;
                var firstCommand = RefreshUnity.HandleCommand(Command(first));
                Assert.IsFalse(firstCommand.IsCompleted);
                Assert.AreEqual("preparing", owner.State.Phase);

                var blocked = ToJObject(await RefreshUnity.HandleCommand(Command(second)));
                Assert.AreEqual("native_job_active", (string)blocked["data"]["reason"]);
                Assert.AreEqual(first, (string)blocked["data"]["native_coordination"]["recovery"]["job_id"]);
                Assert.IsFalse(ledger.Contains(second));
                var duplicate = ToJObject(await RefreshUnity.HandleCommand(Command(first)));
                Assert.IsTrue((bool)duplicate["success"]);
                Assert.AreEqual("requested", (string)duplicate["data"]["compilation"]["state"]);

                // An independently started cycle belongs to the Editor even when
                // its full lifetime fits inside the source capture.
                var unrelated = new object();
                owner.Started(unrelated);
                ledger.Started(unrelated);
                owner.Finished(unrelated);
                ledger.Finished(unrelated);
                Assert.IsEmpty((JArray)ledger.Query(first)["candidate_generations"]);
                var interrupted = owner.State;
                var restoredLedger = new CompilationLedger(session, "C:/fixture", encoded => saved = encoded, saved);
                var restoredOwner = new OwnedCompilationReservation(interrupted, interruptedBeforeDispatch: restoredLedger.InterruptedBeforeDispatch);

                // Let the original domain continuation finish independently of the
                // restored state. It must refuse dispatch after the foreign cycle.
                prepare.SetResult(true);
                var originalFailure = ToJObject(await firstCommand);
                Assert.IsFalse((bool)originalFailure["success"]);
                Assert.AreEqual("not_dispatched", (string)originalFailure["data"]["compilation"]["reason"]);
                Assert.IsNull(owner.State);

                RefreshUnity.PrepareSourceOverride = null;
                RefreshUnity.ReservationOverride = restoredOwner;
                RefreshUnity.LedgerOverride = restoredLedger;
                var prior = ToJObject(await RefreshUnity.HandleCommand(Command(first)));
                Assert.AreEqual("finished", (string)prior["data"]["compilation"]["state"]);
                Assert.AreEqual("interrupted_before_native_dispatch", (string)prior["data"]["compilation"]["reason"]);
                Assert.AreEqual("unconfirmed", (string)prior["data"]["compilation"]["attribution"]);
                Assert.AreEqual(0, nativeRequests);
                Assert.AreEqual(0, nativeImports);
                var admitted = ToJObject(await RefreshUnity.HandleCommand(Command(second)));
                Assert.IsTrue((bool)admitted["success"]);
                Assert.AreEqual(1, nativeRequests);
                Assert.AreEqual(scope == "all" ? 1 : 0, nativeImports);
                Assert.AreEqual(second, restoredOwner.State.JobId);
            }
            finally
            {
                prepare.TrySetCanceled();
                RefreshUnity.PrepareSourceOverride = originalPrepare;
                RefreshUnity.LedgerOverride = originalLedger;
                RefreshUnity.ReservationOverride = originalReservation;
                RefreshUnity.RequestCompilation = originalRequest;
                RefreshUnity.RefreshAssets = originalRefresh;
                RefreshUnity.IsCompiling = originalCompiling;
                running.SetValue(null, originalRunning);
            }
        }

        [TestCase("all", false, false)]
        [TestCase("all", true, false)]
        [TestCase("assets", false, false)]
        [TestCase("assets", true, false)]
        [TestCase("all", false, true)]
        [TestCase("all", true, true)]
        public void OwnedRefreshCycleIsTrackedWithoutIssuingARedundantVoidRequest(string scope, bool finishesInsideRefresh, bool refreshThrows)
        {
            var running = typeof(TestRunStatus).GetField("_isRunning", BindingFlags.NonPublic | BindingFlags.Static);
            object previousRun = running.GetValue(null);
            var request = RefreshUnity.RequestCompilation;
            var refresh = RefreshUnity.RefreshAssets;
            var compiling = RefreshUnity.IsCompiling;
            var ledger = RefreshUnity.LedgerOverride;
            var reservation = RefreshUnity.ReservationOverride;
            var tracked = new CompilationLedger("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "C:/fixture", _ => { });
            string savedOwner = "";
            bool rejectWrites = false;
            var owner = CompilationJobTracker.RestoreReservation(
                () => savedOwner,
                encoded =>
                {
                    if (!rejectWrites)
                        savedOwner = encoded;
                },
                tracked.InterruptedBeforeDispatch
            );
            const string id = "11111111111111111111111111111111";
            object context = new object();
            try
            {
                running.SetValue(null, false);
                RefreshUnity.LedgerOverride = tracked;
                RefreshUnity.ReservationOverride = owner;
                RefreshUnity.IsCompiling = () => false;
                RefreshUnity.RequestCompilation = () =>
                    Assert.Fail("The already observed owned refresh cycle must satisfy this goal without a second void trigger.");
                RefreshUnity.RefreshAssets = _ =>
                {
                    Assert.AreEqual("native_job_active", owner.Blocker(EditorApplication.timeSinceStartup));
                    owner.Started(context);
                    tracked.Started(context);
                    if (finishesInsideRefresh)
                    {
                        owner.Finished(context);
                        tracked.Finished(context);
                        owner.ObserveQuiescence(true);
                        owner.ObserveQuiescence(true);
                        Assert.IsNotNull(owner.State, "Early finish must not retire before the explicit-trigger decision.");
                    }
                    if (refreshThrows)
                    {
                        rejectWrites = finishesInsideRefresh;
                        throw new InvalidOperationException("controlled refresh failure after native start");
                    }
                };
                var acknowledgement = RefreshUnity.HandleCommand(
                    new JObject
                    {
                        ["scope"] = scope,
                        ["compile"] = "request",
                        ["compile_id"] = id,
                        ["wait_for_ready"] = true,
                    }
                );
                Assert.IsTrue(acknowledgement.IsCompleted, "Return the observed compile before a domain reload can unload the native readiness waiter.");
                var response = ToJObject(acknowledgement.Result);
                Assert.AreEqual(!refreshThrows, (bool)response["success"]);
                if (!refreshThrows)
                {
                    var target = (JObject)response["data"]["editor_target"];
                    Assert.AreEqual(ProjectIdentityUtility.GetProjectName() + "@" + ProjectIdentityUtility.GetProjectHash(), (string)target["instance"]);
                    Assert.That((string)target["generation"], Does.Match("^[0-9a-f]{32}$"));
                    var observed = ToJObject(EditorState.HandleCommand(new JObject { ["expected_editor_generation"] = (string)target["generation"] }));
                    Assert.IsTrue(JToken.DeepEquals(target, observed["data"]["editor_target"]));
                    var wrongProcess = ToJObject(EditorState.HandleCommand(new JObject { ["expected_editor_generation"] = "other" }));
                    Assert.AreEqual("editor_generation_mismatch", (string)wrongProcess["error"]);
                    Assert.IsFalse((bool)response["data"]["compile_requested"]);
                    Assert.AreEqual("refresh", (string)response["data"]["compilation_trigger"]);
                    Assert.IsTrue((bool)response["data"]["compilation_goal_satisfied"]);
                    Assert.IsTrue((bool)response["data"]["compile_started"]);
                    Assert.AreEqual(id, (string)response["data"]["compilation"]["compile_id"]);
                    Assert.AreEqual(finishesInsideRefresh ? "finished" : "running", (string)response["data"]["compilation"]["state"]);
                }
                Assert.IsNotNull(owner.State, "A public return/failure cannot retire native ownership before quiescence.");
                if (!finishesInsideRefresh)
                {
                    owner.Finished(context);
                    tracked.Finished(context);
                }
                owner.ObserveQuiescence(true);
                Assert.IsNotNull(owner.State);
                owner.ObserveQuiescence(true);
                if (refreshThrows)
                {
                    Assert.AreEqual("compilation_trigger_outcome_unknown", owner.Recovery(0).Reason);
                    Assert.AreEqual("compilation_trigger_outcome_unknown", (string)response["data"]["compilation"]["reason"]);
                    if (rejectWrites)
                    {
                        var saved = JObject.Parse(savedOwner);
                        Assert.IsTrue((bool)saved["PipelineFinished"]);
                        Assert.IsTrue((bool)saved["RefreshDecisionPending"]);
                        Assert.IsTrue((bool)saved["NativeInvocationPending"]);
                        var restored = CompilationJobTracker.RestoreReservation(
                            () => savedOwner,
                            _ => Assert.Fail("Unexpected restore write"),
                            tracked.InterruptedBeforeDispatch
                        );
                        restored.ObserveQuiescence(true);
                        restored.ObserveQuiescence(true);
                        Assert.AreEqual("recovery_required", restored.Blocker(0), "A finish inside a throwing import cannot settle its unreturned call.");
                    }
                }
                else
                    Assert.IsNull(owner.State);
            }
            finally
            {
                RefreshUnity.RequestCompilation = request;
                RefreshUnity.RefreshAssets = refresh;
                RefreshUnity.IsCompiling = compiling;
                RefreshUnity.LedgerOverride = ledger;
                RefreshUnity.ReservationOverride = reservation;
                running.SetValue(null, previousRun);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExplicitCompilePersistsHandoffBeforeTriggerAndRetainsUncertainThrow(bool fail)
        {
            var running = typeof(TestRunStatus).GetField("_isRunning", BindingFlags.NonPublic | BindingFlags.Static);
            object previousRun = running.GetValue(null);
            var request = RefreshUnity.RequestCompilation;
            var compiling = RefreshUnity.IsCompiling;
            var ledger = RefreshUnity.LedgerOverride;
            var reservation = RefreshUnity.ReservationOverride;
            var owner = new OwnedCompilationReservation();
            try
            {
                running.SetValue(null, false);
                RefreshUnity.LedgerOverride = new CompilationLedger("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "C:/fixture", _ => { });
                RefreshUnity.ReservationOverride = owner;
                RefreshUnity.IsCompiling = () => false;
                RefreshUnity.RequestCompilation = () =>
                {
                    Assert.AreEqual("native_job_active", owner.Blocker(EditorApplication.timeSinceStartup));
                    Assert.AreEqual("pending", owner.State.Phase);
                    Assert.AreEqual("recovery_required", new OwnedCompilationReservation(owner.State).Blocker(0));
                    if (fail)
                        throw new InvalidOperationException("controlled exception from native trigger");
                };
                var response = ToJObject(RefreshUnity.HandleCommand(new JObject { ["scope"] = "scripts", ["compile"] = "request" }).GetAwaiter().GetResult());
                Assert.AreEqual(!fail, (bool)response["success"]);
                Assert.AreEqual(fail ? "recovery_required" : "native_job_active", owner.Blocker(EditorApplication.timeSinceStartup));
                if (fail)
                    Assert.AreEqual("compilation_trigger_outcome_unknown", (string)response["data"]["compilation"]["reason"]);
            }
            finally
            {
                RefreshUnity.RequestCompilation = request;
                RefreshUnity.IsCompiling = compiling;
                RefreshUnity.LedgerOverride = ledger;
                RefreshUnity.ReservationOverride = reservation;
                running.SetValue(null, previousRun);
            }
        }

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
            var reservation = RefreshUnity.ReservationOverride;
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
                RefreshUnity.ReservationOverride = new OwnedCompilationReservation();
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
                RefreshUnity.ReservationOverride = reservation;
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
                            "refresh_unity",
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
