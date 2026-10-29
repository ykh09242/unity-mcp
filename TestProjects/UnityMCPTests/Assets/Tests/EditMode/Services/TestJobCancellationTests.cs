using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>Pure injected native snapshots and backed-up SessionState; never starts a native run.</summary>
    public class TestJobCancellationTests
    {
        private const string Job = "1234567890abcdef1234567890abcdef";
        private const string Guid = "fedcba0987654321fedcba0987654321";
        private static readonly BindingFlags Members = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
        private double now;
        private JObject snapshot;
        private int calls;
        private bool accepted;
        private string requestedGuid;
        private object control;

        private static JObject Native(bool running) =>
            new JObject
            {
                ["registered"] = running ? new JArray(Guid) : new JArray(),
                ["active"] = running ? new JArray(Guid) : new JArray(),
                ["serialized_active"] = running ? new JArray(Guid) : new JArray(),
                ["global_active"] = running,
            };

        private object Create(double timeout = 0)
        {
            Type type = typeof(TestJobManager).Assembly.GetType("MCPForUnity.Editor.Services.NativeTestRunControl");
            Assert.NotNull(type, "A native GUID completion observer must exist; caller retirement is not native termination.");
            snapshot = Native(true);
            accepted = true;
            calls = 0;
            now = 10;
            return control = Activator.CreateInstance(
                type,
                Members,
                null,
                new object[]
                {
                    Job,
                    timeout,
                    new Func<double>(() => now),
                    new Func<JObject>(() => snapshot),
                    new Func<string, bool>(id =>
                    {
                        calls++;
                        requestedGuid = id;
                        return accepted;
                    }),
                },
                null
            );
        }

        private object Call(string method, params object[] args)
        {
            try
            {
                return control.GetType().GetMethod(method, Members).Invoke(control, args);
            }
            catch (TargetInvocationException error)
            {
                throw error.InnerException;
            }
        }

        private JObject State => (JObject)control.GetType().GetProperty("State", Members).GetValue(control);
        private bool Released => (bool)control.GetType().GetProperty("Released", Members).GetValue(control);

        [Test]
        public void StackFalseAndEarlyUnregisterCannotReleaseBeforeDisposeWitnessAndLaterIdle()
        {
            Create();
            Call("BindGuid", Guid);
            Call("Request", "user");
            snapshot["is_running_stack"] = false;
            Call("Tick");
            Assert.IsFalse(Released, "Empty task stack precedes StopRun.");
            snapshot = Native(false);
            Call("Tick");
            Assert.IsFalse(Released, "Unregister precedes task Dispose and final progress.");
            Call("ObserveFinal", Guid);
            Assert.IsFalse(Released, "A final event cannot reentrantly admit another run.");
            Call("Tick");
            Assert.IsFalse(Released, "A direct status tick cannot release native ownership.");
            Call("NoteEditorUpdate");
            Call("TickFromEditorUpdate");
            Assert.IsTrue(Released);
            Assert.AreEqual(1, calls);
        }

        [Test]
        public void CachedDiagnosticsDistinguishIdleNativeSnapshotFromDeferredCleanupTurn()
        {
            Create();
            Action deferred = null;
            control.GetType().GetField("defer", Members).SetValue(control, new Action<Action>(action => deferred = action));
            Call("BindGuid", Guid);
            Call("NoteEditorUpdate");
            Call("ObserveFinal", Guid);
            snapshot = Native(false);
            now = 11;
            Call("NoteEditorUpdate");
            Call("Tick");
            var diagnostics = (JObject)Call("Diagnostics");
            Assert.IsFalse(Released, "An idle snapshot and elapsed update do not replace the deferred turn witness.");
            Assert.AreEqual(1, (int)diagnostics["editor_updates_since_final"]);
            Assert.AreEqual(0, (int)diagnostics["registered_count"]);
            Assert.AreEqual(0, (int)diagnostics["active_count"]);
            Assert.AreEqual(0, (int)diagnostics["serialized_active_count"]);
            Assert.IsFalse((bool)diagnostics["global_active"]);
            Assert.IsFalse((bool)diagnostics["deferred_turn_seen"]);
            Assert.NotNull(deferred);
            deferred();
            Call("Tick");
            Assert.IsFalse(Released, "A deferred witness alone cannot turn a direct poll into cleanup completion.");
            Call("NoteEditorUpdate");
            Call("TickFromEditorUpdate");
            Assert.IsTrue(Released);
        }

        [Test]
        public void RestoredFinalWitnessNeedsALaterMonitorUpdateAndIdleSnapshot()
        {
            Create();
            Call("BindGuid", Guid);
            Call("ObserveFinal", Guid);
            var saved = (JObject)State.DeepClone();
            var oldFactory = NativeTestRunControl.FactoryOverride;
            try
            {
                NativeTestRunControl.FactoryOverride = (id, timeout) => new NativeTestRunControl(id, timeout, () => 11, () => Native(false), _ => true);
                using var restored = NativeTestRunControl.Restore(Job, saved);
                Assert.NotNull(restored);
                restored.Tick();
                Assert.IsFalse(restored.Released, "A restored final callback cannot make a direct poll release native ownership.");
                restored.NoteEditorUpdate();
                restored.TickFromEditorUpdate();
                Assert.IsTrue(restored.Released);
                Assert.IsTrue((bool)restored.Diagnostics()["restored_after_reload"]);
            }
            finally
            {
                NativeTestRunControl.FactoryOverride = oldFactory;
            }
        }

        [Test]
        public void MissingOrForeignFinalEventNeverInventsTermination()
        {
            Create();
            Call("BindGuid", Guid);
            Call("Request", "user");
            snapshot = Native(false);
            Call("ObserveFinal", "foreign");
            Call("Tick");
            Call("Tick");
            Assert.IsFalse(Released);
            Assert.AreEqual("unknown", (string)State["termination"]);
            Assert.AreEqual("recoverable", ((NativeTestRunControl)control).RecoveryDetails().Mode);
            Assert.AreEqual("test_final_witness_pending", ((NativeTestRunControl)control).RecoveryDetails().Reason);
        }

        [Test]
        public void RepeatedCancellationTargetsOnlyCapturedGuidOnce()
        {
            Create();
            Call("BindGuid", Guid);
            Call("Request", "user");
            Call("Request", "user");
            Assert.AreEqual(Guid, requestedGuid);
            Assert.AreEqual(1, calls);
            Assert.IsFalse(Released);
            Assert.AreEqual("accepted", (string)State["request_state"]);
        }

        [Test]
        public void FalseCancellationReceiptIsNotNativeIdle()
        {
            Create();
            accepted = false;
            Call("BindGuid", Guid);
            Call("Request", "user");
            Call("Tick");
            Assert.IsFalse(Released);
            Assert.AreEqual("not_accepted_or_already_pending", (string)State["request_state"]);
        }

        [Test]
        public void WaitingDeadlineStopsBeforeExecuteWithoutNativeCancel()
        {
            Create(100);
            now = 10.101;
            Call("Tick");
            Assert.IsTrue(Released);
            Assert.AreEqual(0, calls);
            Assert.AreEqual("not_started", (string)State["termination"]);
            Assert.AreEqual("deadline", (string)State["reason"]);
        }

        [Test]
        public void RunningDeadlineUsesSameGuidAndDoesNotResetOnPoll()
        {
            Create(100);
            Call("BindGuid", Guid);
            now = 10.050;
            Call("Tick");
            Assert.AreEqual(0, calls);
            now = 10.101;
            Call("Tick");
            Call("Tick");
            Assert.AreEqual(1, calls);
            Assert.IsFalse(Released);
            Assert.AreEqual("deadline", (string)State["reason"]);
        }

        [Test]
        public void ForeignRegisteredRunnerRejectsUntaggedCallbackOwnership()
        {
            Create();
            Call("BindGuid", Guid);
            ((JArray)snapshot["registered"]).Add("foreign");
            Assert.IsFalse((bool)Call("AcceptsCallback"));
            snapshot = Native(true);
            Assert.IsFalse((bool)Call("AcceptsCallback"), "Ambiguous attribution remains unavailable.");
        }

        [TestCase("1.4.6", false)]
        [TestCase("1.6.0", true)]
        [TestCase("1.6.1", false)]
        [TestCase("", false)]
        public void CapabilityVersionIsExact(string version, bool expected)
        {
            Create();
            Assert.AreEqual(expected, control.GetType().GetMethod("SupportsVersion", Members).Invoke(null, new object[] { version }));
        }

        [TestCase(-1)]
        [TestCase(0)]
        [TestCase(86400001)]
        [TestCase("not-a-number")]
        public void InvalidOptInDeadlineRejectsBeforeStartingAnyService(object timeout)
        {
            Type manager = typeof(TestJobManager);
            var jobs = (Dictionary<string, TestJob>)manager.GetField("Jobs", Members).GetValue(null);
            var backup = new Dictionary<string, TestJob>(jobs);
            object current = manager.GetField("_currentJobId", Members).GetValue(null);
            object service = typeof(MCPServiceLocator).GetField("_testRunnerService", Members).GetValue(null);
            string saved = SessionState.GetString("MCPForUnity.TestJobsV1", ""),
                savedCurrent = SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", "");
            var fake = new PendingTests();
            try
            {
                jobs.Clear();
                manager.GetField("_currentJobId", Members).SetValue(null, null);
                typeof(MCPServiceLocator).GetField("_testRunnerService", Members).SetValue(null, fake);
                JObject response = JObject.FromObject(
                    MCPForUnity
                        .Editor.Tools.RunTests.HandleCommand(new JObject { ["mode"] = "EditMode", ["runTimeoutMs"] = JToken.FromObject(timeout) })
                        .GetAwaiter()
                        .GetResult()
                );
                Assert.AreEqual(0, fake.Calls, "Invalid deadline must not start even a fake native execution.");
                Assert.IsFalse((bool)response["success"]);
                Assert.IsEmpty(jobs);
            }
            finally
            {
                typeof(MCPServiceLocator).GetField("_testRunnerService", Members).SetValue(null, service);
                manager.GetField("_currentJobId", Members).SetValue(null, current);
                jobs.Clear();
                foreach (var item in backup)
                    jobs.Add(item.Key, item.Value);
                SessionState.SetString("MCPForUnity.TestJobsV1", saved);
                SessionState.SetString("MCPForUnity.CurrentTestJobIdV1", savedCurrent);
            }
        }

        [Test]
        public void ExactInstalledBindingAddsAndRemovesOnlyItsPassiveListener()
        {
            string version = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(TestRunnerApi).Assembly)?.version;
            bool disabled = NativeTestRunControl.DisableForTests;
            var factory = NativeTestRunControl.FactoryOverride;
            const string key = "MCPForUnity.NativeTestControlSessionV1";
            string session = SessionState.GetString(key, "");
            bool hadSession = !string.IsNullOrEmpty(session);
            try
            {
                NativeTestRunControl.DisableForTests = false;
                NativeTestRunControl.FactoryOverride = null;
                Assert.AreEqual(version == "1.6.0", NativeTestRunControl.Available, "Only exact installed supported binding may admit control.");
                if (version != "1.6.0")
                    return;
                var field = typeof(TestRunnerApi).GetField("runProgressChanged", BindingFlags.NonPublic | BindingFlags.Static);
                object original = field.GetValue(null);
                NativeTestRunControl actual = NativeTestRunControl.Create(Job, 0);
                using (actual)
                {
                    Assert.NotNull(actual);
                    actual.BindGuid(Guid);
                    object binding = typeof(NativeTestRunControl).GetField("binding", Members).GetValue(actual);
                    object bridge = binding.GetType().GetField("bridge", Members).GetValue(binding);
                    var progressType = typeof(TestRunnerApi).Assembly.GetType("UnityEditor.TestTools.TestRunner.Api.TestRunProgress");
                    object progress = Activator.CreateInstance(progressType, true);
                    progressType.GetField("RunGuid").SetValue(progress, Guid);
                    progressType.GetField("HasFinished").SetValue(progress, true);
                    // Invoke only our bridge, never Unity's shared event or other observers.
                    ((Action<object>)bridge.GetType().GetField("Handler", Members).GetValue(bridge))(progress);
                    Assert.IsTrue((bool)actual.State["final_witness"]);
                    Assert.IsFalse(actual.Released);
                    Assert.NotNull(typeof(NativeTestRunControl).GetField("scheduledTurn", Members).GetValue(actual));
                }
                Assert.IsNull(
                    typeof(NativeTestRunControl).GetField("scheduledTurn", Members).GetValue(actual),
                    "Disposing the control unsubscribes its unused update witness."
                );
                Assert.AreSame(original, field.GetValue(null), "Never replace the framework event.");
            }
            finally
            {
                NativeTestRunControl.DisableForTests = disabled;
                NativeTestRunControl.FactoryOverride = factory;
                if (hadSession)
                    SessionState.SetString(key, session);
                else
                    SessionState.EraseString(key);
            }
        }

        [Test]
        public void FailedIntentPersistenceCannotCallNativeCancel()
        {
            Create();
            Call("BindGuid", Guid);
            control.GetType().GetField("Persist", Members).SetValue(control, new Func<bool>(() => false));
            Call("Request", "user");
            Assert.AreEqual(0, calls);
            Assert.IsFalse(Released);
            Assert.AreEqual("persistence_failed", (string)State["request_state"]);
        }

        [Test]
        public void ExecutedWithoutReturnedGuidCannotBecomeNeverStarted()
        {
            Create();
            Call("MarkExecuting");
            Call("Request", "user");
            Assert.AreEqual(0, calls);
            Assert.IsFalse(Released);
            Assert.AreEqual("unknown", (string)State["termination"]);
            Assert.AreEqual("restart_required", ((NativeTestRunControl)control).RecoveryDetails().Mode);
            Assert.AreEqual("test_native_guid_missing", ((NativeTestRunControl)control).RecoveryDetails().Reason);
        }

        [Test]
        public void MissingSnapshotCannotReleaseEvenMatchingFinalWitness()
        {
            Create();
            Call("BindGuid", Guid);
            Call("ObserveFinal", Guid);
            snapshot = null;
            Call("Tick");
            Assert.IsFalse(Released);
            Assert.AreEqual("unknown", (string)State["termination"]);
        }

        [TestCase(false, "running")]
        [TestCase(true, "cleanup_pending")]
        public void TemporarySnapshotLossReconcilesExactOwnedGuidWithoutReleasing(bool cancelling, string expectedTermination)
        {
            Create();
            Call("BindGuid", Guid);
            if (cancelling)
                Call("Request", "user");
            snapshot = null;
            Call("Tick");
            var details = ((NativeTestRunControl)control).RecoveryDetails();
            Assert.AreEqual("recoverable", details.Mode);
            Assert.AreEqual("test_native_snapshot_unavailable", details.Reason);
            snapshot = Native(true);
            Call("Tick");
            Assert.AreEqual(expectedTermination, (string)State["termination"]);
            Assert.IsNull(((NativeTestRunControl)control).RecoveryDetails());
            Assert.IsFalse(Released, "Recovered observation changes classification, never native ownership.");
            snapshot = Native(false);
            Call("NoteEditorUpdate");
            Call("TickFromEditorUpdate");
            Assert.IsFalse(Released, "Even a later idle Editor turn cannot replace the final witness.");
            Call("ObserveFinal", Guid);
            Call("Tick");
            Assert.IsFalse(Released);
            Call("NoteEditorUpdate");
            Call("TickFromEditorUpdate");
            Assert.IsTrue(Released);
        }

        [TestCase("ambiguous", "test_callback_attribution_ambiguous")]
        [TestCase("observation_lost", "test_ownership_evidence_lost")]
        public void CoherentSnapshotCannotRepairLostOwnershipEvidence(string flag, string expectedReason)
        {
            Create();
            Call("BindGuid", Guid);
            State[flag] = true;
            State["termination"] = "unknown";
            Call("Tick");
            Assert.AreEqual("unknown", (string)State["termination"]);
            var details = ((NativeTestRunControl)control).RecoveryDetails();
            Assert.AreEqual("restart_required", details.Mode);
            Assert.AreEqual(expectedReason, details.Reason);
            Call("ObserveFinal", Guid);
            snapshot = Native(false);
            Call("NoteEditorUpdate");
            Call("TickFromEditorUpdate");
            Assert.IsFalse(Released, "A later completion cannot repair lost attribution or persisted evidence.");
        }

        [Test]
        public void PartialOwnedSnapshotCannotRepairTemporaryObservationFailure()
        {
            Create();
            Call("BindGuid", Guid);
            snapshot = null;
            Call("Tick");
            snapshot = Native(true);
            snapshot["active"] = new JArray();
            Call("Tick");
            Assert.AreEqual("unknown", (string)State["termination"]);
            Assert.IsFalse(Released);
        }

        [Test]
        public void UnavailableRestoredObserverRequiresInspectionWithoutInventingRestartEvidence()
        {
            Create();
            Call("BindGuid", Guid);
            var saved = (JObject)State.DeepClone();
            bool disabled = NativeTestRunControl.DisableForTests;
            try
            {
                NativeTestRunControl.DisableForTests = true;
                using var restored = NativeTestRunControl.Restore(Job, saved);
                restored.Tick();
                Assert.IsFalse(restored.Released);
                Assert.AreEqual("unknown", restored.RecoveryDetails().Mode);
                Assert.AreEqual("test_native_observer_unavailable", restored.RecoveryDetails().Reason);
            }
            finally
            {
                NativeTestRunControl.DisableForTests = disabled;
            }
        }

        [Test]
        public void PersistedDeadlineRestoresWithoutStartingANewBudget()
        {
            Create(100);
            var saved = JObject.Parse(State.ToString());
            var originalFactory = NativeTestRunControl.FactoryOverride;
            try
            {
                now = 10.090;
                NativeTestRunControl.FactoryOverride = (id, timeout) =>
                    new NativeTestRunControl(
                        id,
                        timeout,
                        () => now,
                        () => Native(false),
                        _ => throw new AssertionException("Never started must not cancel native execution.")
                    );
                using var restored = NativeTestRunControl.Restore(Job, saved);
                Assert.AreEqual(10.1, (double)restored.State["deadline_uptime"], 0.00001);
                now = 10.101;
                restored.Tick();
                Assert.IsTrue(restored.Released);
                Assert.AreEqual("deadline", (string)restored.State["reason"]);
            }
            finally
            {
                NativeTestRunControl.FactoryOverride = originalFactory;
            }
        }

        [Test]
        public void LostFinalEventAfterRestoreRemainsUnknownInsteadOfInferringIdle()
        {
            Create();
            Call("BindGuid", Guid);
            var saved = JObject.Parse(State.ToString());
            var originalFactory = NativeTestRunControl.FactoryOverride;
            try
            {
                NativeTestRunControl.FactoryOverride = (id, timeout) => new NativeTestRunControl(id, timeout, () => now, () => Native(false), _ => false);
                using var restored = NativeTestRunControl.Restore(Job, saved);
                restored.Tick();
                restored.Tick();
                Assert.IsFalse(restored.Released);
                Assert.AreEqual("unknown", (string)restored.State["termination"]);
            }
            finally
            {
                NativeTestRunControl.FactoryOverride = originalFactory;
            }
        }

        [Test]
        public void UnsupportedOptInsLeaveRepeatedOrdinaryAdmissionOnLegacyPath()
        {
            WithManager(fake =>
            {
                NativeTestRunControl.DisableForTests = true;
                JObject denied = JObject.FromObject(
                    MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject { ["runTimeoutMs"] = 100 }).GetAwaiter().GetResult()
                );
                Assert.IsFalse((bool)denied["success"]);
                Assert.AreEqual(0, fake.Calls);
                denied = JObject.FromObject(MCPForUnity.Editor.Tools.CancelTestJob.HandleCommand(new JObject { ["job_id"] = Job }));
                Assert.IsFalse((bool)denied["success"]);
                Assert.AreEqual("unsupported_native_test_control", (string)denied["error"]);
                for (int index = 0; index < 2; index++)
                {
                    JObject started = JObject.FromObject(MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject()).GetAwaiter().GetResult());
                    Assert.IsTrue((bool)started["success"]);
                    Assert.IsFalse(TestJobManager.NativeAdmissionBlocked);
                    Assert.IsTrue(TestJobManager.ClearStuckJob());
                }
                Assert.AreEqual(2, fake.Calls);
            });
        }

        [Test]
        public void PublicWaitingCancellationPersistsCancelledStateWithoutNativeStop()
        {
            WithManager(fake =>
            {
                NativeTestRunControl.DisableForTests = false;
                int cancelCalls = 0;
                NativeTestRunControl.FactoryOverride = (id, timeout) =>
                    new NativeTestRunControl(
                        id,
                        timeout,
                        () => 10,
                        () => Native(false),
                        _ =>
                        {
                            cancelCalls++;
                            return true;
                        }
                    );
                JObject started = JObject.FromObject(
                    MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject { ["runTimeoutMs"] = 100 }).GetAwaiter().GetResult()
                );
                Assert.IsTrue((bool)started["success"]);
                string id = (string)started["data"]["job_id"];
                JObject cancelled = JObject.FromObject(MCPForUnity.Editor.Tools.CancelTestJob.HandleCommand(new JObject { ["job_id"] = id }));
                Assert.IsTrue((bool)cancelled["success"]);
                Assert.AreEqual("cancelled", (string)cancelled["data"]["status"]);
                Assert.AreEqual(0, cancelCalls);
                Assert.IsFalse(TestJobManager.NativeAdmissionBlocked);
                var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Members).GetValue(null);
                jobs.Clear();
                typeof(TestJobManager).GetMethod("TryRestoreFromSessionState", Members).Invoke(null, null);
                Assert.AreEqual(TestJobStatus.Cancelled, jobs[id].Status);
                Assert.IsNull(jobs[id].RetainedSummary);
            });
        }

        [TestCase("started_uptime")]
        [TestCase("termination")]
        [TestCase("request_state")]
        [TestCase("released_without_witness")]
        [TestCase("settled_without_release")]
        public void InvalidPersistedControlCannotClaimClosedOwnership(string mutation)
        {
            Create();
            Call("BindGuid", Guid);
            var saved = JObject.Parse(State.ToString());
            switch (mutation)
            {
                case "started_uptime":
                    saved.Remove("started_uptime");
                    break;
                case "termination":
                    saved["termination"] = "invented";
                    break;
                case "request_state":
                    saved["request_state"] = "invented";
                    break;
                case "released_without_witness":
                    saved["released"] = true;
                    break;
                case "settled_without_release":
                    saved["settled"] = true;
                    break;
            }
            Assert.IsFalse(NativeTestRunControl.ValidState(saved, Job));
        }

        [Test]
        public void MalformedReleasedControlRetainsSummaryAndUnknownAdmissionBarrier()
        {
            WithManager(_ =>
            {
                NativeTestRunControl.DisableForTests = false;
                NativeTestRunControl.FactoryOverride = (id, timeout) => new NativeTestRunControl(id, timeout, () => 10, () => Native(false), _ => false);
                var summary = new JObject
                {
                    ["Total"] = 1,
                    ["Passed"] = 0,
                    ["Failed"] = 1,
                    ["Skipped"] = 0,
                    ["DurationSeconds"] = 0.25,
                    ["ResultState"] = "Failed",
                };
                var row = new JObject
                {
                    ["job_id"] = Job,
                    ["status"] = "failed",
                    ["started_unix_ms"] = 1,
                    ["last_update_unix_ms"] = 2,
                    ["completed_tests"] = 1,
                    ["summary"] = summary,
                    ["native_control"] = new JObject { ["version"] = 999, ["released"] = true },
                };
                SessionState.SetString("MCPForUnity.TestJobsV1", new JObject { ["version"] = 2, ["jobs"] = new JArray(row) }.ToString());
                typeof(TestJobManager).GetMethod("TryRestoreFromSessionState", Members).Invoke(null, null);
                var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Members).GetValue(null);
                Assert.AreEqual(TestJobStatus.Failed, jobs[Job].Status);
                Assert.IsTrue(JToken.DeepEquals(summary, JObject.FromObject(jobs[Job].RetainedSummary)));
                Assert.IsNotNull(jobs[Job].NativeControl);
                Assert.AreEqual("unknown", (string)jobs[Job].NativeControl["termination"]);
                Assert.IsTrue(TestJobManager.NativeAdmissionBlocked);
            });
        }

        [Test]
        public void CompletedHistoryPressureCannotEvictAnUnreleasedNativeOwner()
        {
            WithManager(_ =>
            {
                NativeTestRunControl.DisableForTests = false;
                NativeTestRunControl.FactoryOverride = (id, timeout) => new NativeTestRunControl(id, timeout, () => 10, () => Native(true), _ => true);
                string owned = TestJobManager.StartJob(TestMode.EditMode, runTimeoutMs: 100);
                var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Members).GetValue(null);
                jobs[owned].Control.BindGuid(Guid);
                jobs[owned].Status = TestJobStatus.Succeeded;
                jobs[owned].LastUpdateUnixMs = 1;
                for (int index = 0; index < 12; index++)
                {
                    string id = System.Guid.NewGuid().ToString("N");
                    jobs[id] = new TestJob
                    {
                        JobId = id,
                        Status = TestJobStatus.Succeeded,
                        StartedUnixMs = index + 2,
                        LastUpdateUnixMs = index + 2,
                    };
                }
                Assert.IsTrue((bool)typeof(TestJobManager).GetMethod("PersistToSessionState", Members).Invoke(null, new object[] { true }));
                Assert.IsTrue(jobs.ContainsKey(owned));
                var envelope = JObject.Parse(SessionState.GetString("MCPForUnity.TestJobsV1", ""));
                Assert.IsTrue(System.Linq.Enumerable.Any((JArray)envelope["jobs"], row => (string)row["job_id"] == owned));
                Assert.IsTrue(TestJobManager.NativeAdmissionBlocked);
            });
        }

        [Test]
        public void OldObserverManagedHistoryRetainsLiveCallbackOwnershipAfterRestore()
        {
            WithManager(_ =>
            {
                Create(100);
                Call("BindGuid", Guid);
                NativeTestRunControl.DisableForTests = false;
                NativeTestRunControl.FactoryOverride = (id, timeout) => new NativeTestRunControl(id, timeout, () => 10, () => Native(true), _ => true);
                var row = new JObject
                {
                    ["job_id"] = Job,
                    ["status"] = "running",
                    ["started_unix_ms"] = 1,
                    ["last_update_unix_ms"] = 2,
                    ["completed_tests"] = 0,
                    ["native_control"] = State.DeepClone(),
                };
                SessionState.SetString(
                    "MCPForUnity.TestJobsV1",
                    new JObject
                    {
                        ["version"] = 2,
                        ["current_job_id"] = Job,
                        ["jobs"] = new JArray(row),
                    }.ToString()
                );
                typeof(TestJobManager).GetMethod("TryRestoreFromSessionState", Members).Invoke(null, null);
                var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Members).GetValue(null);
                Assert.AreEqual(TestJobStatus.Running, jobs[Job].Status, "Wall-clock history age does not prove native termination.");
                Assert.AreEqual(Job, TestJobManager.CurrentJobId);
                Assert.IsTrue(TestJobManager.NativeAdmissionBlocked);
                Assert.AreEqual(10.1, (double)jobs[Job].NativeControl["deadline_uptime"], 0.00001);
            });
        }

        [Test]
        public void FalseCancelReceiptCannotTurnAuthoritativeCanceledResultIntoSuccess()
        {
            WithManager(_ =>
            {
                NativeTestRunControl.DisableForTests = false;
                JObject view = Native(true);
                NativeTestRunControl.FactoryOverride = (id, timeout) => new NativeTestRunControl(id, timeout, () => 10, () => view, _ => false);
                string id = TestJobManager.StartJob(TestMode.EditMode, runTimeoutMs: 100);
                var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Members).GetValue(null);
                var job = jobs[id];
                job.Control.BindGuid(Guid);
                job.Control.Request("user");
                Assert.AreEqual("not_accepted_or_already_pending", (string)job.NativeControl["request_state"]);
                job.RetainedSummary = new TestRunSummary(1, 0, 0, 1, 0.25, "Canceled");
                job.CompletionKind = "cancellation_pending";
                view = Native(false);
                job.Control.ObserveFinal(Guid);
                typeof(TestJobManager).GetMethod("TickNativeControls", Members).Invoke(null, null);
                Assert.IsTrue(TestJobManager.NativeAdmissionBlocked, "A public cancellation tick cannot settle native ownership.");
                typeof(TestJobManager).GetMethod("OnNativeControlEditorUpdate", Members).Invoke(null, null);
                Assert.AreEqual(TestJobStatus.Cancelled, job.Status, "An authoritative canceled result does not depend on the API receipt bool.");
                Assert.AreEqual("cancelled", job.CompletionKind);
                Assert.AreEqual("Canceled", job.RetainedSummary.ResultState);
                Assert.IsFalse(TestJobManager.NativeAdmissionBlocked);
            });
        }

        [Test]
        public void NaturalSuccessSummaryWinsPendingStopAfterPersistedPayloadEviction()
        {
            WithManager(_ =>
            {
                NativeTestRunControl.DisableForTests = false;
                JObject view = Native(true);
                NativeTestRunControl.FactoryOverride = (id, timeout) => new NativeTestRunControl(id, timeout, () => 10, () => view, _ => true);
                string id = TestJobManager.StartJob(TestMode.EditMode, runTimeoutMs: 100);
                var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Members).GetValue(null);
                var original = jobs[id];
                original.Control.BindGuid(Guid);
                original.Control.Request("user");
                original.RetainedSummary = new TestRunSummary(1, 1, 0, 0, 0.25, "Passed");
                original.CompletionKind = "cancellation_pending";
                Assert.IsTrue((bool)typeof(TestJobManager).GetMethod("PersistToSessionState", Members).Invoke(null, new object[] { true }));
                original.Control.Dispose();
                jobs.Clear();
                typeof(TestJobManager).GetMethod("TryRestoreFromSessionState", Members).Invoke(null, null);
                var restored = jobs[id];
                Assert.IsNull(restored.Result, "Managed reload intentionally evicts the live payload.");
                Assert.AreEqual(1, restored.RetainedSummary.Passed);
                view = Native(false);
                restored.Control.ObserveFinal(Guid);
                typeof(TestJobManager).GetMethod("TickNativeControls", Members).Invoke(null, null);
                Assert.IsTrue(TestJobManager.NativeAdmissionBlocked, "A direct status tick cannot settle restored ownership.");
                typeof(TestJobManager).GetMethod("OnNativeControlEditorUpdate", Members).Invoke(null, null);
                Assert.AreEqual(TestJobStatus.Succeeded, restored.Status, "Retained authoritative success must win a racing accepted stop.");
                Assert.AreEqual("completed", restored.CompletionKind);
                Assert.AreEqual("Passed", restored.RetainedSummary.ResultState);
                Assert.IsFalse(TestJobManager.NativeAdmissionBlocked);
            });
        }

        [TestCase("version_string")]
        [TestCase("version_float")]
        [TestCase("job_id_number")]
        [TestCase("session_empty")]
        [TestCase("deadline_before_start")]
        [TestCase("deadline_after_limit")]
        [TestCase("request_time_before_start")]
        [TestCase("request_time_missing")]
        [TestCase("request_time_without_reason")]
        public void MalformedPersistedProducerFieldsCannotIssueAnOverdueNativeCancel(string mutation)
        {
            WithManager(_ =>
            {
                Create(100);
                Call("BindGuid", Guid);
                string id = Job;
                var saved = JObject.Parse(State.ToString());
                switch (mutation)
                {
                    case "version_string":
                        saved["version"] = "1";
                        break;
                    case "version_float":
                        saved["version"] = 1.0;
                        break;
                    case "job_id_number":
                        id = "12345678901234567890123456789012";
                        saved["job_id"] = JToken.Parse(id);
                        break;
                    case "session_empty":
                        saved["editor_session"] = "";
                        break;
                    case "deadline_before_start":
                        saved["deadline_uptime"] = 0;
                        break;
                    case "deadline_after_limit":
                        saved["deadline_uptime"] = 610.5;
                        break;
                    case "request_time_before_start":
                        saved["reason"] = "user";
                        saved["request_state"] = "pending";
                        saved["requested_uptime"] = 1;
                        break;
                    case "request_time_missing":
                        saved["reason"] = "user";
                        saved["request_state"] = "pending";
                        break;
                    case "request_time_without_reason":
                        saved["requested_uptime"] = 10;
                        break;
                }
                int nativeCalls = 0;
                NativeTestRunControl.DisableForTests = false;
                NativeTestRunControl.FactoryOverride = (owner, timeout) =>
                    new NativeTestRunControl(
                        owner,
                        timeout,
                        () => 1000,
                        () => Native(true),
                        _ =>
                        {
                            nativeCalls++;
                            return true;
                        }
                    );
                var summary = new TestRunSummary(1, 0, 1, 0, 0.25, "Failed");
                var row = new JObject
                {
                    ["job_id"] = id,
                    ["status"] = "running",
                    ["started_unix_ms"] = 1,
                    ["last_update_unix_ms"] = 2,
                    ["completed_tests"] = 1,
                    ["summary"] = JObject.FromObject(summary),
                    ["native_control"] = saved,
                };
                SessionState.SetString(
                    "MCPForUnity.TestJobsV1",
                    new JObject
                    {
                        ["version"] = 2,
                        ["current_job_id"] = id,
                        ["jobs"] = new JArray(row),
                    }.ToString()
                );
                typeof(TestJobManager).GetMethod("TryRestoreFromSessionState", Members).Invoke(null, null);
                typeof(TestJobManager).GetMethod("TickNativeControls", Members).Invoke(null, null);
                var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Members).GetValue(null);
                Assert.AreEqual(0, nativeCalls, "Malformed optional persisted data cannot authorize a native mutation.");
                Assert.AreEqual("unknown", (string)jobs[id].NativeControl["termination"]);
                Assert.AreEqual(TestJobStatus.Running, jobs[id].Status);
                Assert.AreEqual(1, jobs[id].RetainedSummary.Failed);
                Assert.IsTrue(TestJobManager.NativeAdmissionBlocked);
            });
        }

        [Test]
        public void PublicPersistenceFailureDoesNotClaimThatAnIntentWasRecorded()
        {
            WithManager(_ =>
            {
                int nativeCalls = 0;
                NativeTestRunControl.DisableForTests = false;
                NativeTestRunControl.FactoryOverride = (owner, timeout) =>
                    new NativeTestRunControl(
                        owner,
                        timeout,
                        () => 10,
                        () => Native(true),
                        _ =>
                        {
                            nativeCalls++;
                            return true;
                        }
                    );
                string id = TestJobManager.StartJob(TestMode.EditMode, runTimeoutMs: 100);
                var control = TestJobManager.ControlFor(id);
                control.BindGuid(Guid);
                control.Persist = () => false;
                var response = JObject.FromObject(MCPForUnity.Editor.Tools.CancelTestJob.HandleCommand(new JObject { ["job_id"] = id }));
                Assert.AreEqual("persistence_failed", (string)response["data"]["native_control"]["request_state"]);
                Assert.AreEqual(0, nativeCalls);
                StringAssert.Contains("evaluated", (string)response["message"]);
                StringAssert.DoesNotContain("recorded", (string)response["message"]);
                Assert.IsTrue(TestJobManager.NativeAdmissionBlocked);
            });
        }

        [Test]
        public void MaximumDeadlineIsMeasuredFromItsCapturedAdmissionInstant()
        {
            double clock = 0;
            using var captured = new NativeTestRunControl(
                Job,
                600000,
                () => clock += 10,
                () => Native(false),
                _ => throw new AssertionException("No native run exists.")
            );
            Assert.AreEqual(
                600,
                (double)captured.State["deadline_uptime"] - (double)captured.State["started_uptime"],
                "Clock movement while constructing the record must not extend the admitted budget."
            );
            Assert.IsTrue(NativeTestRunControl.ValidState(captured.State, Job));
        }

        private static void WithManager(Action<PendingTests> action)
        {
            var type = typeof(TestJobManager);
            var jobs = (Dictionary<string, TestJob>)type.GetField("Jobs", Members).GetValue(null);
            var backup = new Dictionary<string, TestJob>(jobs);
            object current = type.GetField("_currentJobId", Members).GetValue(null),
                service = typeof(MCPServiceLocator).GetField("_testRunnerService", Members).GetValue(null),
                persisted = type.GetField("_lastPersistUnixMs", Members).GetValue(null);
            bool disabled = NativeTestRunControl.DisableForTests,
                monitor = (bool)type.GetField("_controlMonitor", Members).GetValue(null);
            var factory = NativeTestRunControl.FactoryOverride;
            var status = new Dictionary<FieldInfo, object>();
            foreach (var field in typeof(TestRunStatus).GetFields(Members))
                if (field.IsStatic && !field.IsInitOnly)
                    status[field] = field.GetValue(null);
            string saved = SessionState.GetString("MCPForUnity.TestJobsV1", ""),
                savedCurrent = SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", ""),
                retired = SessionState.GetString("MCPForUnity.TestRunner.RetiredRunJobId", "");
            var fake = new PendingTests();
            try
            {
                jobs.Clear();
                type.GetField("_currentJobId", Members).SetValue(null, null);
                SessionState.SetString("MCPForUnity.TestRunner.RetiredRunJobId", "");
                typeof(MCPServiceLocator).GetField("_testRunnerService", Members).SetValue(null, fake);
                action(fake);
            }
            finally
            {
                foreach (var job in jobs.Values)
                    job.Control?.Dispose();
                if (!monitor)
                    EditorApplication.update -= (EditorApplication.CallbackFunction)
                        Delegate.CreateDelegate(typeof(EditorApplication.CallbackFunction), type.GetMethod("OnNativeControlEditorUpdate", Members));
                type.GetField("_controlMonitor", Members).SetValue(null, monitor);
                NativeTestRunControl.DisableForTests = disabled;
                NativeTestRunControl.FactoryOverride = factory;
                typeof(MCPServiceLocator).GetField("_testRunnerService", Members).SetValue(null, service);
                type.GetField("_currentJobId", Members).SetValue(null, current);
                type.GetField("_lastPersistUnixMs", Members).SetValue(null, persisted);
                jobs.Clear();
                foreach (var pair in backup)
                    jobs.Add(pair.Key, pair.Value);
                foreach (var pair in status)
                    pair.Key.SetValue(null, pair.Value);
                SessionState.SetString("MCPForUnity.TestJobsV1", saved);
                SessionState.SetString("MCPForUnity.CurrentTestJobIdV1", savedCurrent);
                SessionState.SetString("MCPForUnity.TestRunner.RetiredRunJobId", retired);
            }
        }

        private sealed class PendingTests : ITestRunnerService
        {
            public int Calls;

            public Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode) =>
                Task.FromResult<IReadOnlyList<Dictionary<string, string>>>(Array.Empty<Dictionary<string, string>>());

            public Task<TestRunResult> RunTestsAsync(TestMode mode, TestFilterOptions options = null)
            {
                Calls++;
                return new TaskCompletionSource<TestRunResult>().Task;
            }
        }
    }
}
