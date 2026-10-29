using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;
using UnityEngine.TestTools;
using TestMode = UnityEditor.TestTools.TestRunner.Api.TestMode;

namespace MCPForUnityTests.Editor.Services
{
    public class TestJobActualReloadTests
    {
        private const string Prefix = "MCPForUnity.Tests.TestJobActualReload.";
        private const string HistoryKey = "MCPForUnity.TestJobsV1";
        private const string CurrentKey = "MCPForUnity.CurrentTestJobIdV1";
        private const string RetiredKey = "MCPForUnity.TestRunner.RetiredRunJobId";
        private const BindingFlags PrivateStatic = BindingFlags.NonPublic | BindingFlags.Static;
        private static readonly string DomainNonce = Guid.NewGuid().ToString("N");
        private static SemaphoreSlim heldGate;
        private static FieldInfo ServiceField => typeof(MCPServiceLocator).GetField("_testRunnerService", PrivateStatic);
        private static string JobId => SessionState.GetString(Prefix + "job", "");
        private static TestRunnerService Service => (TestRunnerService)MCPServiceLocator.Tests;

        [InitializeOnLoadMethod]
        private static void InstallOwnedExecutionProbe()
        {
            if (!SessionState.GetBool(Prefix + "active", false) || !IsOwnedProject())
                return;
            NativeTestRunControl.DisableForTests = true;
            // Only the final native boundary is replaced. Admission, queueing, persisted
            // execution fences, callback restoration, and finalization are production code.
            TestRunnerService.ExecuteForTests = settings =>
            {
                Assert.IsTrue(IsOwnedProject());
                SessionState.SetInt(Prefix + "executions", SessionState.GetInt(Prefix + "executions", 0) + 1);
                SessionState.SetString(Prefix + "executed_job", TestJobManager.CurrentJobId);
                var filter = settings.filters[0];
                SessionState.SetString(Prefix + "executed_mode", filter.testMode.ToString());
                SessionState.SetString(
                    Prefix + "executed_filters",
                    JObject
                        .FromObject(
                            new TestFilterOptions
                            {
                                TestNames = filter.testNames,
                                GroupNames = filter.groupNames,
                                CategoryNames = filter.categoryNames,
                                AssemblyNames = filter.assemblyNames,
                            }
                        )
                        .ToString()
                );
                return "owned-native-boundary-probe";
            };
            if (SessionState.GetString(Prefix + "phase", "") == "hold")
                HoldAdmission();
        }

        [UnityTest, Explicit("Owned disposable batch Editor only; requires exact UNITY_MCP_TEST_RELOAD_PROJECT and performs real domain reloads.")]
        public IEnumerator QueuedProductionAdmissionSurvivesRepeatedReloadAndExecutesOriginalJobOnlyOnce()
        {
            Prepare("normal");
            try
            {
                QueueAtReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                AssertQueuedOriginal();

                RememberDomain();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                AssertQueuedOriginal();
                SessionState.SetString(Prefix + "phase", "execute");
                Assert.AreSame(heldGate, AdmissionGate(Service), "Release must target the service that owns the restored production waiter.");
                ReleaseAdmission();
                // Batch EditMode updates are not rendered frames: hundreds can elapse
                // before a delayed callback or captured async continuation receives time.
                // Stop immediately on a terminal job; the bound does not extend its deadline.
                var admissionWait = System.Diagnostics.Stopwatch.StartNew();
                while (
                    admissionWait.Elapsed < TimeSpan.FromSeconds(5)
                    && SessionState.GetInt(Prefix + "executions", 0) == 0
                    && TestJobManager.GetJob(JobId)?.Status == TestJobStatus.Running
                )
                    yield return null;
                Assert.AreEqual(1, SessionState.GetInt(Prefix + "executions", 0), QueuedInvocationDiagnostics(admissionWait.Elapsed));
                Assert.AreEqual(JobId, SessionState.GetString(Prefix + "executed_job", ""));
                Assert.AreEqual("EditMode", SessionState.GetString(Prefix + "executed_mode", ""));
                Assert.IsTrue(JToken.DeepEquals(ExpectedFilters(), JObject.Parse(SessionState.GetString(Prefix + "executed_filters", ""))));
                Assert.AreEqual("executing", (string)TestJobManager.GetJob(JobId).RunAdmission["phase"]);

                // The injected native call has returned without a terminal callback. A real
                // reload must restore that owner, never invoke Execute for it a second time.
                RememberDomain();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                Assert.AreEqual(JobId, TestJobManager.CurrentJobId);
                Assert.IsTrue(TestRunnerService.HasOwnedNativeRun);
                Assert.IsNull(TestJobManager.GetJob(JobId).Control);
                for (int i = 0; i < 5; i++)
                    yield return null;
                Assert.AreEqual(1, SessionState.GetInt(Prefix + "executions", 0));
                Assert.Throws<InvalidOperationException>(() => TestJobManager.StartJob(TestMode.EditMode));
                Assert.IsTrue(Service.RunTestsAsync(TestMode.EditMode).IsFaulted);
                Assert.Throws<NotSupportedException>(() => TestJobManager.CancelJob(JobId));

                Service.OnError("owned terminal callback after actual reload");
                Assert.IsFalse(TestRunnerService.HasOwnedNativeRun);
                Assert.IsFalse(TestJobManager.HasRunningJob);
                Assert.AreEqual("owned terminal callback after actual reload", TestJobManager.GetJob(JobId).Error);
                RememberDomain();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                Assert.AreEqual(TestJobStatus.Failed, TestJobManager.GetJob(JobId).Status);
                Assert.AreEqual(1, SessionState.GetInt(Prefix + "executions", 0));
                Assert.IsFalse(TestRunnerService.HasOwnedNativeRun);
            }
            finally
            {
                Cleanup();
            }
        }

        [UnityTest, Explicit("Owned disposable batch Editor only; verifies explicit queued cancellation across a real domain reload without native execution.")]
        public IEnumerator ExplicitCancellationAfterReloadCannotResumeTheQueuedJob()
        {
            Prepare("normal");
            try
            {
                QueueAtReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                AssertQueuedOriginal();
                TestJobManager.CancelJob(JobId);
                Assert.AreEqual(TestJobStatus.Cancelled, TestJobManager.GetJob(JobId).Status);
                SessionState.SetString(Prefix + "phase", "execute");
                ReleaseAdmission();
                for (int i = 0; i < 5; i++)
                    yield return null;
                RememberDomain();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                Assert.AreEqual(TestJobStatus.Cancelled, TestJobManager.GetJob(JobId).Status);
                Assert.AreEqual(0, SessionState.GetInt(Prefix + "executions", 0));
                Assert.IsFalse(TestRunnerService.HasOwnedNativeRun);
                Assert.IsFalse(TestJobManager.HasRunningJob);
            }
            finally
            {
                Cleanup();
            }
        }

        [UnityTest, Explicit("Owned disposable batch Editor only; verifies the original startup deadline across a real domain reload.")]
        public IEnumerator ExpiredQueuedAdmissionKeepsOriginalDeadlineAndDoesNotAcquireUnknownOwnership()
        {
            Prepare("expired");
            try
            {
                QueueAtReload();
                yield return new WaitForDomainReload();
                AssertReloaded();
                var job = TestJobManager.GetJob(JobId);
                Assert.AreEqual(long.Parse(SessionState.GetString(Prefix + "started", "0")), job.StartedUnixMs);
                Assert.AreEqual(1, job.InitTimeoutMs);
                Assert.AreEqual(TestJobStatus.Failed, job.Status);
                Assert.AreEqual(0, SessionState.GetInt(Prefix + "executions", 0));
                Assert.IsFalse(TestRunnerService.HasOwnedNativeRun);
                Assert.IsFalse(TestJobManager.HasRunningJob);
            }
            finally
            {
                Cleanup();
            }
        }

        private static JObject ExpectedFilters() =>
            JObject.FromObject(
                new TestFilterOptions
                {
                    TestNames = new[] { "OwnedProbe.NeverActuallyExecuted" },
                    GroupNames = new[] { "OwnedProbe.*" },
                    CategoryNames = new[] { "OwnedReload" },
                    AssemblyNames = new[] { "OwnedProbeAssembly" },
                }
            );

        private static bool IsOwnedProject() =>
            Application.isBatchMode
            && Path.GetFullPath(Path.GetDirectoryName(Application.dataPath)) == Environment.GetEnvironmentVariable("UNITY_MCP_TEST_RELOAD_PROJECT");

        private static void Prepare(string scenario)
        {
            Assert.IsTrue(IsOwnedProject(), "The caller must identify the exact disposable batch project with UNITY_MCP_TEST_RELOAD_PROJECT.");
            Assert.IsFalse(SessionState.GetBool(Prefix + "active", false), "Preserve any unfinished owned probe.");
            Assert.IsFalse(TestJobManager.HasRunningJob || TestRunnerService.HasOwnedNativeRun, "Preserve existing test ownership.");
            Assert.IsEmpty(SessionState.GetString("MCPForUnity.TestAdmissionWriteV1", ""));
            SessionState.SetString(Prefix + "previous_history", SessionState.GetString(HistoryKey, ""));
            SessionState.SetString(Prefix + "previous_current", SessionState.GetString(CurrentKey, ""));
            SessionState.SetBool(Prefix + "previous_control_disabled", NativeTestRunControl.DisableForTests);
            SessionState.SetBool(Prefix + "active", true);
            SessionState.SetString(Prefix + "scenario", scenario);
            SessionState.SetString(Prefix + "phase", "hold");
            SessionState.SetInt(Prefix + "executions", 0);
            RememberDomain();
            InstallOwnedExecutionProbe();
        }

        private static void HoldAdmission()
        {
            if (heldGate != null)
                return;
            heldGate = AdmissionGate(Service);
            Assert.IsTrue(heldGate.Wait(0), "The owned probe must acquire an idle service admission gate.");
        }

        private static SemaphoreSlim AdmissionGate(TestRunnerService service) =>
            (SemaphoreSlim)typeof(TestRunnerService).GetField("_operationLock", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(service);

        private static string QueuedInvocationDiagnostics(TimeSpan waited)
        {
            var job = TestJobManager.GetJob(JobId);
            var service = ServiceField.GetValue(null) as TestRunnerService;
            object ServiceValue(string field) =>
                service == null ? null : typeof(TestRunnerService).GetField(field, BindingFlags.NonPublic | BindingFlags.Instance).GetValue(service);
            return JObject
                .FromObject(
                    new
                    {
                        waited_seconds = waited.TotalSeconds,
                        job_id = JobId,
                        current_job_id = TestJobManager.CurrentJobId,
                        status = job?.Status.ToString(),
                        error = job?.Error,
                        admission = job?.RunAdmission,
                        native_control = job?.NativeControl,
                        started_unix_ms = job?.StartedUnixMs,
                        init_timeout_ms = job?.InitTimeoutMs,
                        owned_native_run = TestRunnerService.HasOwnedNativeRun,
                        retired_owner = SessionState.GetString(RetiredKey, ""),
                        service_tracked_job = ServiceValue("_trackedJobId"),
                        service_disposed = ServiceValue("_disposed"),
                        service_api_released = ServiceValue("_apiReleased"),
                        gate_count = service == null ? (int?)null : AdmissionGate(service).CurrentCount,
                        resume_claimed = ((HashSet<string>)typeof(TestJobManager).GetField("ResumingJobs", PrivateStatic).GetValue(null)).Contains(JobId),
                        invocation_count = SessionState.GetInt(Prefix + "executions", 0),
                        is_compiling = EditorStateCache.GetActualIsCompiling(),
                        is_updating = EditorApplication.isUpdating,
                        reload_pending = EditorStateCache.IsDomainReloadPending,
                    }
                )
                .ToString();
        }

        private static void ReleaseAdmission()
        {
            heldGate?.Release();
            heldGate = null;
        }

        private static void QueueAtReload()
        {
            AssemblyReloadEvents.beforeAssemblyReload += AdmitBeforeReload;
            EditorUtility.RequestScriptReload();
        }

        private static void AdmitBeforeReload()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= AdmitBeforeReload;
            try
            {
                string id = TestJobManager.StartJob(
                    TestMode.EditMode,
                    ExpectedFilters().ToObject<TestFilterOptions>(),
                    initTimeoutMs: SessionState.GetString(Prefix + "scenario", "") == "expired" ? 1 : 600000
                );
                SessionState.SetString(Prefix + "job", id);
                // Read the public checkpoint instead of polling a one-millisecond job.
                var history = JObject.Parse(SessionState.GetString(HistoryKey, ""));
                foreach (var row in history["jobs"])
                    if ((string)row["job_id"] == id)
                        SessionState.SetString(Prefix + "started", (string)row["started_unix_ms"]);
                SessionState.SetBool(Prefix + "admitted", true);
            }
            catch (Exception error)
            {
                SessionState.SetString(Prefix + "error", error.ToString());
            }
        }

        private static void RememberDomain() => SessionState.SetString(Prefix + "domain", DomainNonce);

        private static void AssertReloaded()
        {
            Assert.AreNotEqual(SessionState.GetString(Prefix + "domain", ""), DomainNonce, "The native managed domain must actually change.");
            Assert.IsTrue(SessionState.GetBool(Prefix + "admitted", false));
            Assert.IsEmpty(SessionState.GetString(Prefix + "error", ""));
            LogAssert.NoUnexpectedReceived();
        }

        private static void AssertQueuedOriginal()
        {
            var job = TestJobManager.GetJob(JobId);
            Assert.AreEqual(JobId, TestJobManager.CurrentJobId);
            Assert.AreEqual(TestJobStatus.Running, job.Status);
            Assert.AreEqual("queued", (string)job.RunAdmission["phase"]);
            Assert.AreEqual(long.Parse(SessionState.GetString(Prefix + "started", "0")), job.StartedUnixMs);
            Assert.AreEqual(600000, job.InitTimeoutMs);
            Assert.IsTrue(JToken.DeepEquals(ExpectedFilters(), job.RunAdmission["filters"]));
            Assert.AreEqual(0, SessionState.GetInt(Prefix + "executions", 0));
            Assert.IsFalse(TestRunnerService.HasRetiredRun, "Reload waiting is not cancellation or uncertain native execution.");
        }

        private static void Cleanup()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= AdmitBeforeReload;
            if (!SessionState.GetBool(Prefix + "active", false))
                return;
            // This fixture never calls native Execute, so its injected run can be settled
            // explicitly before disposing callbacks and restoring the previous history.
            if (ServiceField.GetValue(null) is TestRunnerService service)
            {
                service.OnError("owned probe cleanup");
                service.Dispose();
                ServiceField.SetValue(null, null);
            }
            ReleaseAdmission();
            TestRunnerService.ExecuteForTests = null;
            NativeTestRunControl.DisableForTests = SessionState.GetBool(Prefix + "previous_control_disabled", false);
            ((Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", PrivateStatic).GetValue(null)).Remove(JobId);
            typeof(TestJobManager).GetField("_currentJobId", PrivateStatic).SetValue(null, null);
            SessionState.SetString(HistoryKey, SessionState.GetString(Prefix + "previous_history", ""));
            SessionState.SetString(CurrentKey, SessionState.GetString(Prefix + "previous_current", ""));
            SessionState.SetString(RetiredKey, "");
            TestRunStatus.MarkFinished();
            foreach (
                string key in new[]
                {
                    "job",
                    "phase",
                    "scenario",
                    "started",
                    "domain",
                    "error",
                    "executed_job",
                    "executed_mode",
                    "executed_filters",
                    "previous_history",
                    "previous_current",
                }
            )
                SessionState.EraseString(Prefix + key);
            foreach (string key in new[] { "active", "admitted", "previous_control_disabled" })
                SessionState.EraseBool(Prefix + key);
            SessionState.EraseInt(Prefix + "executions");
        }
    }
}
