using System;
using System.Linq;
using System.Reflection;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine.Events;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Version-scoped passive termination evidence. Only public CancelTestRun mutates Unity execution.</summary>
    internal sealed class NativeTestRunControl : IDisposable
    {
        internal static bool DisableForTests = false;
        internal static Func<string, long, NativeTestRunControl> FactoryOverride = null;
        private readonly Func<double> clock;
        private readonly Func<JObject> snapshot;
        private readonly Func<string, bool> cancel;
        private Binding binding;
        private long tick;
        private long finalTick = -1;
        private long editorUpdates;
        private long finalEditorUpdates = -1;
        private double? finalWitnessUptime;
        private double? lastEditorUpdateUptime;
        private double? lastSnapshotUptime;
        private int? registeredCount;
        private int? activeCount;
        private int? serializedActiveCount;
        private bool? globalActive;
        private string lastSnapshotError;
        private bool restoredAfterReload;
        private bool observerUnavailable;
        private bool laterTurn;
        private bool disposed;
        private EditorApplication.CallbackFunction scheduledTurn;
        private Action<Action> defer = action => action();
        internal Func<bool> Persist;
        internal JObject State { get; private set; }
        internal bool CanRequest => !disposed && !Released && (binding != null || FactoryOverride != null);
        internal bool Released => (bool?)State["released"] == true;
        internal bool Requested => State["reason"]?.Type == JTokenType.String;
        internal bool CancelAccepted => (string)State["request_state"] == "accepted";
        internal string NativeGuid => (string)State["native_guid"];

        internal static bool SupportsVersion(string version) => version == "1.6.0";

        // Injected snapshots never invoke Unity's runner or change Editor preferences/settings.
        internal NativeTestRunControl(string jobId, double timeoutMs, Func<double> clock, Func<JObject> snapshot, Func<string, bool> cancel)
        {
            this.clock = clock;
            this.snapshot = snapshot;
            this.cancel = cancel;
            double started = clock();
            State = new JObject
            {
                ["version"] = 1,
                ["job_id"] = jobId,
                ["framework"] = "1.6.0",
                ["editor_session"] = "injected",
                ["started_uptime"] = started,
                ["deadline_uptime"] = timeoutMs > 0 ? started + timeoutMs / 1000 : JValue.CreateNull(),
                ["termination"] = "not_started",
                ["request_state"] = "none",
                ["released"] = false,
                ["final_witness"] = false,
                ["native_invoked"] = false,
            };
        }

        internal static bool Available
        {
            get
            {
                if (DisableForTests)
                    return false;
                if (FactoryOverride != null)
                    return true;
                try
                {
                    using var probe = new Binding();
                    return true;
                }
                catch
                {
                    return false;
                }
            }
        }

        private static string Session()
        {
            const string key = "MCPForUnity.NativeTestControlSessionV1";
            string value = SessionState.GetString(key, "");
            if (string.IsNullOrEmpty(value))
            {
                value = Guid.NewGuid().ToString("N");
                SessionState.SetString(key, value);
            }
            return value;
        }

        internal static NativeTestRunControl Create(string jobId, long timeoutMs)
        {
            if (DisableForTests)
                return null;
            if (FactoryOverride != null)
                return FactoryOverride(jobId, timeoutMs);
            Binding native = null;
            try
            {
                native = new Binding();
                var control = new NativeTestRunControl(
                    jobId,
                    timeoutMs,
                    () => EditorApplication.timeSinceStartup,
                    native.Snapshot,
                    TestRunnerApi.CancelTestRun
                );
                control.binding = native;
                control.defer = control.ScheduleLaterEditorUpdate;
                control.State["editor_session"] = Session();
                native.Subscribe(control.ObserveFinal);
                return control;
            }
            catch
            {
                native?.Dispose();
                return null;
            }
        }

        internal static NativeTestRunControl Restore(string jobId, JObject state)
        {
            if (!ValidState(state, jobId))
                return null;
            var control = Create(jobId, 0);
            if (control == null)
            {
                control = new NativeTestRunControl(jobId, 0, () => EditorApplication.timeSinceStartup, () => null, _ => false);
                control.State = (JObject)state.DeepClone();
                control.State["termination"] = "unknown";
                control.observerUnavailable = true;
                return control;
            }
            control.State = (JObject)state.DeepClone();
            control.restoredAfterReload = true;
            if (FactoryOverride == null && (string)state["editor_session"] != Session())
                control.State["observation_lost"] = true;
            if ((bool?)state["final_witness"] == true)
            {
                control.finalTick = 0;
                control.finalEditorUpdates = 0;
                control.defer(() => control.laterTurn = true);
            }
            return control;
        }

        internal static bool ValidState(JToken token, string jobId)
        {
            try
            {
                if (
                    token is not JObject value
                    || Encoding.UTF8.GetByteCount(value.ToString(Newtonsoft.Json.Formatting.None)) > 1536
                    || value["version"]?.Type != JTokenType.Integer
                    || (int?)value["version"] != 1
                    || value["job_id"]?.Type != JTokenType.String
                    || (string)value["job_id"] != jobId
                    || value["framework"]?.Type != JTokenType.String
                    || (string)value["framework"] != "1.6.0"
                )
                    return false;
                if (
                    value["editor_session"]?.Type != JTokenType.String
                    || string.IsNullOrWhiteSpace((string)value["editor_session"])
                    || ((string)value["editor_session"]).Length > 64
                    || value["released"]?.Type != JTokenType.Boolean
                    || value["final_witness"]?.Type != JTokenType.Boolean
                    || value["native_invoked"]?.Type != JTokenType.Boolean
                )
                    return false;
                if (value["started_uptime"] == null || value["started_uptime"].Type == JTokenType.Null)
                    return false;
                foreach (string key in new[] { "started_uptime", "deadline_uptime", "requested_uptime" })
                    if (
                        value[key] != null
                        && value[key].Type != JTokenType.Null
                        && (
                            value[key].Type != JTokenType.Integer && value[key].Type != JTokenType.Float
                            || double.IsNaN((double)value[key])
                            || double.IsInfinity((double)value[key])
                            || (double)value[key] < 0
                        )
                    )
                        return false;
                double started = (double)value["started_uptime"];
                if (value["deadline_uptime"] != null && value["deadline_uptime"].Type != JTokenType.Null)
                {
                    double deadline = (double)value["deadline_uptime"];
                    if (deadline < started || deadline - started > 600.000001)
                        return false;
                }
                bool hasReason = value["reason"] != null && value["reason"].Type != JTokenType.Null;
                bool hasRequestTime = value["requested_uptime"] != null && value["requested_uptime"].Type != JTokenType.Null;
                if (hasReason != hasRequestTime || hasRequestTime && (double)value["requested_uptime"] < started)
                    return false;
                if (
                    value["native_guid"] != null
                    && value["native_guid"].Type != JTokenType.Null
                    && (value["native_guid"].Type != JTokenType.String || !Guid.TryParse((string)value["native_guid"], out _))
                )
                    return false;
                foreach (string key in new[] { "ambiguous", "observation_lost", "settled" })
                    if (value[key] != null && value[key].Type != JTokenType.Boolean)
                        return false;
                string termination = (string)value["termination"],
                    request = (string)value["request_state"];
                if (
                    !new[] { "not_started", "running", "cleanup_pending", "confirmed", "unknown" }.Contains(termination)
                    || !new[] { "none", "pending", "accepted", "not_accepted_or_already_pending", "persistence_failed", "unknown" }.Contains(request)
                )
                    return false;
                if (
                    value["reason"] != null
                    && value["reason"].Type != JTokenType.Null
                    && (value["reason"].Type != JTokenType.String || (string)value["reason"] != "user" && (string)value["reason"] != "deadline")
                )
                    return false;
                if (hasReason && request == "none" || !hasReason && request != "none" && (request != "unknown" || (bool?)value["observation_lost"] != true))
                    return false;
                bool invoked = (bool)value["native_invoked"],
                    released = (bool)value["released"],
                    final = (bool)value["final_witness"];
                bool hasGuid = value["native_guid"] != null && value["native_guid"].Type != JTokenType.Null;
                if ((request == "accepted" || request == "not_accepted_or_already_pending") && !hasGuid)
                    return false;
                if (hasGuid && !invoked || final && !hasGuid || (bool?)value["settled"] == true && !released)
                    return false;
                if (released && (bool?)value["observation_lost"] == true || released && (bool?)value["ambiguous"] == true)
                    return false;
                if (released && (termination == "not_started" ? invoked || hasGuid || final : termination != "confirmed" || !final || !hasGuid))
                    return false;
                if (termination == "confirmed" && !released || termination == "running" && (!invoked || !hasGuid))
                    return false;
                return true;
            }
            catch
            {
                return false;
            }
        }

        private bool Save()
        {
            try
            {
                return Persist == null || Persist();
            }
            catch
            {
                return false;
            }
        }

        internal void BindGuid(string guid)
        {
            if (Requested || Released)
                throw new InvalidOperationException("Test job stopped before native admission.");
            if (!Guid.TryParse(guid, out _))
            {
                State["observation_lost"] = true;
                State["termination"] = "unknown";
                Save();
                return;
            }
            State["native_invoked"] = true;
            State["native_guid"] = guid;
            State["termination"] = "running";
            if (!Save())
            {
                State["observation_lost"] = true;
                State["termination"] = "unknown";
            }
        }

        internal void CheckAdmission()
        {
            Tick();
            if (Requested || Released)
                throw new InvalidOperationException("Test job stopped before native admission.");
            if ((bool?)State["observation_lost"] == true || (bool?)State["native_invoked"] == true)
                throw new InvalidOperationException("Native ownership is already executing or unknown.");
            if (NativeGuid == null)
            {
                JObject value = View();
                if ((bool)value["global_active"] || new[] { "registered", "active", "serialized_active" }.Any(key => ((JArray)value[key]).Count > 0))
                    throw new InvalidOperationException("A foreign Unity test run is already in progress.");
            }
        }

        internal void MarkExecuting()
        {
            State["native_invoked"] = true;
            if (!Save())
            {
                State["observation_lost"] = true;
                throw new InvalidOperationException("Native ownership could not be persisted before execution.");
            }
        }

        internal void AbortBeforeExecute()
        {
            State["native_invoked"] = false;
            State["released"] = true;
            State["termination"] = "not_started";
            Save();
        }

        internal static JObject CorruptState(string jobId) =>
            new JObject
            {
                ["version"] = 1,
                ["job_id"] = jobId,
                ["framework"] = "1.6.0",
                ["editor_session"] = "unknown",
                ["started_uptime"] = 0,
                ["deadline_uptime"] = null,
                ["released"] = false,
                ["final_witness"] = false,
                ["native_invoked"] = true,
                ["observation_lost"] = true,
                ["termination"] = "unknown",
                ["request_state"] = "unknown",
            };

        internal void RetireQueued()
        {
            if (NativeGuid != null || (bool?)State["native_invoked"] == true || (bool?)State["observation_lost"] == true)
                return;
            State["released"] = true;
            State["termination"] = "not_started";
            Save();
        }

        internal void Request(string reason)
        {
            if (Released || Requested)
                return;
            State["reason"] = reason;
            State["requested_uptime"] = clock();
            State["request_state"] = "pending";
            if (!Save())
            {
                State["request_state"] = "persistence_failed";
                State["termination"] = "unknown";
                return;
            }
            if (NativeGuid == null)
            {
                if ((bool?)State["native_invoked"] == true || (bool?)State["observation_lost"] == true)
                {
                    State["termination"] = "unknown";
                    State["request_state"] = "unknown";
                    Save();
                    return;
                }
                State["termination"] = "not_started";
                State["released"] = true;
                if (!Save())
                {
                    State["released"] = false;
                    State["termination"] = "unknown";
                }
                return;
            }
            try
            {
                State["request_state"] = cancel(NativeGuid) ? "accepted" : "not_accepted_or_already_pending";
            }
            catch
            {
                State["request_state"] = "unknown";
            }
            State["termination"] = "cleanup_pending";
            Save();
        }

        internal void ObserveFinal(string guid)
        {
            if (disposed || Released || (bool?)State["final_witness"] == true || NativeGuid == null || guid != NativeGuid)
                return;
            State["final_witness"] = true;
            State["termination"] = "cleanup_pending";
            finalTick = tick;
            finalEditorUpdates = editorUpdates;
            finalWitnessUptime = clock();
            if (!Save())
            {
                State["final_witness"] = false;
                State["observation_lost"] = true;
                return;
            }
            laterTurn = false;
            defer(() => laterTurn = true);
        }

        private void ScheduleLaterEditorUpdate(Action callback)
        {
            if (scheduledTurn != null)
                EditorApplication.update -= scheduledTurn;
            EditorApplication.CallbackFunction scheduled = null;
            scheduled = () =>
            {
                EditorApplication.update -= scheduled;
                if (scheduledTurn == scheduled)
                    scheduledTurn = null;
                if (!disposed)
                    callback();
            };
            scheduledTurn = scheduled;
            EditorApplication.update += scheduled;
        }

        // All values are cached from the existing monitor. Status reads never probe UTF.
        internal Transport.NativeRecoveryDetails RecoveryDetails()
        {
            if (Released)
                return null;
            string reason;
            string mode;
            string message;
            if ((bool?)State["ambiguous"] == true)
            {
                reason = "test_callback_attribution_ambiguous";
                mode = "restart_required";
                message = "A foreign or incoherent test runner made callback attribution ambiguous. The retained owner cannot safely release native work.";
            }
            else if ((bool?)State["observation_lost"] == true)
            {
                reason = "test_ownership_evidence_lost";
                mode = "restart_required";
                message = "The persisted test ownership or completion evidence was lost. An idle Editor cannot prove that this native run stopped.";
            }
            else if (NativeGuid == null && (bool?)State["native_invoked"] == true)
            {
                reason = "test_native_guid_missing";
                mode = "restart_required";
                message = "Native test execution was invoked without a retained run GUID. Completion cannot be attributed safely to this owner.";
            }
            else if ((string)State["termination"] != "unknown")
                return null;
            else if (observerUnavailable || disposed)
            {
                reason = "test_native_observer_unavailable";
                mode = "unknown";
                message =
                    "The test run is retained, but its native observer is unavailable. Inspect Test Runner and restore supported observation before retrying.";
            }
            else
            {
                reason = lastSnapshotError != null ? "test_native_snapshot_unavailable" : "test_final_witness_pending";
                mode = "recoverable";
                message =
                    lastSnapshotError != null
                        ? "The retained test observer could not read a native snapshot. It will reconcile later observations; keep waiting for native cleanup."
                        : "The retained test observer has not received this run's final completion witness. Idle snapshots alone cannot release the owner.";
            }
            return new Transport.NativeRecoveryDetails(
                "tests",
                "native_cleanup",
                reason,
                mode,
                mode == "recoverable" ? "wait_for_test_cleanup"
                    : mode == "restart_required" ? "inspect_native_work_then_restart_editor"
                    : "inspect_native_work",
                message,
                (string)State["job_id"]
            );
        }

        internal JObject Diagnostics() =>
            new JObject
            {
                ["editor_update_count"] = editorUpdates,
                ["editor_updates_since_final"] = finalEditorUpdates < 0 ? JValue.CreateNull() : editorUpdates - finalEditorUpdates,
                ["last_editor_update_uptime"] = lastEditorUpdateUptime.HasValue ? new JValue(lastEditorUpdateUptime.Value) : JValue.CreateNull(),
                ["tick_count"] = tick,
                ["ticks_since_final"] = finalTick < 0 ? JValue.CreateNull() : tick - finalTick,
                ["final_witness_uptime"] = finalWitnessUptime.HasValue ? new JValue(finalWitnessUptime.Value) : JValue.CreateNull(),
                ["deferred_turn_seen"] = laterTurn,
                ["last_snapshot_uptime"] = lastSnapshotUptime.HasValue ? new JValue(lastSnapshotUptime.Value) : JValue.CreateNull(),
                ["registered_count"] = registeredCount.HasValue ? new JValue(registeredCount.Value) : JValue.CreateNull(),
                ["active_count"] = activeCount.HasValue ? new JValue(activeCount.Value) : JValue.CreateNull(),
                ["serialized_active_count"] = serializedActiveCount.HasValue ? new JValue(serializedActiveCount.Value) : JValue.CreateNull(),
                ["global_active"] = globalActive.HasValue ? new JValue(globalActive.Value) : JValue.CreateNull(),
                ["last_snapshot_error"] = lastSnapshotError == null ? JValue.CreateNull() : new JValue(lastSnapshotError),
                ["restored_after_reload"] = restoredAfterReload,
            };

        internal void NoteEditorUpdate()
        {
            editorUpdates++;
            lastEditorUpdateUptime = clock();
        }

        private JObject View()
        {
            JObject value = snapshot();
            if (value == null || value["global_active"]?.Type != JTokenType.Boolean)
                throw new InvalidOperationException();
            foreach (string key in new[] { "registered", "active", "serialized_active" })
                if (value[key] is not JArray list || list.Count > 32 || list.Any(item => item.Type != JTokenType.String || ((string)item).Length > 64))
                    throw new InvalidOperationException();
            return value;
        }

        internal bool AcceptsCallback()
        {
            if (NativeGuid == null || (bool?)State["ambiguous"] == true || (bool?)State["observation_lost"] == true)
                return false;
            try
            {
                JObject value = View();
                bool coherent =
                    new[] { "registered", "active", "serialized_active" }.All(key => ((JArray)value[key]).Count == 1 && (string)value[key][0] == NativeGuid)
                    && (bool)value["global_active"];
                if (!coherent)
                {
                    State["ambiguous"] = true;
                    State["termination"] = "unknown";
                    Save();
                }
                return coherent;
            }
            catch
            {
                State["termination"] = "unknown";
                return false;
            }
        }

        internal void Tick() => TickCore(fromEditorUpdate: false);

        internal void TickFromEditorUpdate() => TickCore(fromEditorUpdate: true);

        private void TickCore(bool fromEditorUpdate)
        {
            if (disposed || Released)
                return;
            tick++;
            if (
                !Requested
                && State["deadline_uptime"]?.Type != JTokenType.Null
                && State["deadline_uptime"] != null
                && clock() >= (double)State["deadline_uptime"]
            )
                Request("deadline");
            if (Released || NativeGuid == null)
                return;
            try
            {
                JObject value = View();
                lastSnapshotUptime = clock();
                registeredCount = ((JArray)value["registered"]).Count;
                activeCount = ((JArray)value["active"]).Count;
                serializedActiveCount = ((JArray)value["serialized_active"]).Count;
                globalActive = (bool)value["global_active"];
                lastSnapshotError = null;
                bool foreign = new[] { "registered", "active", "serialized_active" }.Any(key => ((JArray)value[key]).Any(id => (string)id != NativeGuid));
                if (foreign)
                    State["ambiguous"] = true;
                bool idle = !(bool)value["global_active"] && new[] { "registered", "active", "serialized_active" }.All(key => ((JArray)value[key]).Count == 0);
                if ((bool?)State["ambiguous"] == true || (bool?)State["observation_lost"] == true || idle && (bool?)State["final_witness"] != true)
                {
                    State["termination"] = "unknown";
                    return;
                }
                // A temporary failed read does not destroy GUID ownership. Restore only
                // an exact coherent running observation; partial snapshots remain unknown.
                bool coherentOwned =
                    (bool)value["global_active"]
                    && new[] { "registered", "active", "serialized_active" }.All(key => ((JArray)value[key]).Count == 1 && (string)value[key][0] == NativeGuid);
                if (coherentOwned && (string)State["termination"] == "unknown")
                    State["termination"] = Requested || (bool?)State["final_witness"] == true ? "cleanup_pending" : "running";
                if (idle && (bool?)State["final_witness"] == true && laterTurn && fromEditorUpdate && editorUpdates > finalEditorUpdates && tick > finalTick)
                {
                    State["released"] = true;
                    State["termination"] = "confirmed";
                    if (!Save())
                    {
                        State["released"] = false;
                        State["termination"] = "unknown";
                    }
                }
            }
            catch (Exception error)
            {
                lastSnapshotError = error.GetType().Name;
                registeredCount = null;
                activeCount = null;
                serializedActiveCount = null;
                globalActive = null;
                State["termination"] = "unknown";
            }
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            if (scheduledTurn != null)
            {
                EditorApplication.update -= scheduledTurn;
                scheduledTurn = null;
            }
            binding?.Dispose();
        }

        private sealed class ProgressBridge<T>
        {
            internal Action<object> Handler = null;

            public void Invoke(T value) => Handler(value);
        }

        private sealed class Binding : IDisposable
        {
            private readonly FieldInfo eventField;
            private readonly FieldInfo guidField;
            private readonly FieldInfo finishedField;
            private readonly PropertyInfo holderProperty;
            private readonly MethodInfo globalActive;
            private readonly Type progressType;
            private object progressEvent;
            private MethodInfo remove;
            private Delegate listener;
            private object bridge;

            internal Binding()
            {
                var api = typeof(TestRunnerApi);
                if (!SupportsVersion(UnityEditor.PackageManager.PackageInfo.FindForAssembly(api.Assembly)?.version))
                    throw new NotSupportedException();
                const BindingFlags flags = BindingFlags.Static | BindingFlags.NonPublic;
                eventField = api.GetField("runProgressChanged", flags) ?? throw new NotSupportedException();
                progressType = api.Assembly.GetType("UnityEditor.TestTools.TestRunner.Api.TestRunProgress") ?? throw new NotSupportedException();
                guidField = progressType.GetField("RunGuid");
                finishedField = progressType.GetField("HasFinished");
                holderProperty = api.GetProperty("m_testJobDataHolder", flags);
                globalActive = api.GetMethod("IsRunActive", flags, null, Type.EmptyTypes, null);
                if (
                    guidField?.FieldType != typeof(string)
                    || finishedField?.FieldType != typeof(bool)
                    || holderProperty == null
                    || globalActive?.ReturnType != typeof(bool)
                )
                    throw new NotSupportedException();
                progressEvent = eventField.GetValue(null) ?? throw new NotSupportedException();
                Type listenerType = typeof(UnityAction<>).MakeGenericType(progressType);
                if (
                    progressEvent.GetType().GetMethod("AddListener", new[] { listenerType }) == null
                    || progressEvent.GetType().GetMethod("RemoveListener", new[] { listenerType }) == null
                )
                    throw new NotSupportedException();
                Type bridgeType = typeof(ProgressBridge<>).MakeGenericType(progressType);
                bridge = Activator.CreateInstance(bridgeType, true);
                listener = Delegate.CreateDelegate(listenerType, bridge, bridgeType.GetMethod("Invoke"));
                // Validate holder shape and bounded passive reads before admission.
                Snapshot();
            }

            internal void Subscribe(Action<string> handler)
            {
                bridge
                    .GetType()
                    .GetField("Handler", BindingFlags.Instance | BindingFlags.NonPublic)
                    .SetValue(
                        bridge,
                        new Action<object>(value =>
                        {
                            try
                            {
                                if ((bool)finishedField.GetValue(value))
                                    handler((string)guidField.GetValue(value));
                            }
                            catch { }
                        })
                    );
                Type delegateType = listener.GetType();
                progressEvent.GetType().GetMethod("AddListener", new[] { delegateType }).Invoke(progressEvent, new object[] { listener });
                remove = progressEvent.GetType().GetMethod("RemoveListener", new[] { delegateType });
            }

            internal JObject Snapshot()
            {
                object holder = holderProperty.GetValue(null);
                var holderType = holder.GetType();
                var runners = (Array)holderType.GetMethod("GetAllRunners").Invoke(holder, null);
                var data = (System.Collections.IEnumerable)holderType.GetField("TestRuns").GetValue(holder);
                var registered = new JArray();
                var active = new JArray();
                var serialized = new JArray();
                if (runners.Length > 32)
                    throw new NotSupportedException();
                foreach (object runner in runners)
                {
                    object row = runner.GetType().GetMethod("GetData").Invoke(runner, null);
                    string id = (string)row.GetType().GetField("guid").GetValue(row);
                    registered.Add(id);
                    if ((bool)row.GetType().GetField("isRunning").GetValue(row))
                        active.Add(id);
                }
                int count = 0;
                foreach (object row in data)
                {
                    if (++count > 32)
                        throw new NotSupportedException();
                    if ((bool)row.GetType().GetField("isRunning").GetValue(row))
                        serialized.Add((string)row.GetType().GetField("guid").GetValue(row));
                }
                return new JObject
                {
                    ["registered"] = registered,
                    ["active"] = active,
                    ["serialized_active"] = serialized,
                    ["global_active"] = (bool)globalActive.Invoke(null, null),
                };
            }

            public void Dispose()
            {
                if (remove != null)
                {
                    try
                    {
                        remove.Invoke(progressEvent, new object[] { listener });
                    }
                    catch { }
                    remove = null;
                }
            }
        }
    }
}
