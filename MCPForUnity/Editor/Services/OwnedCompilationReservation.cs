using System;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;

namespace MCPForUnity.Editor.Services
{
    // A requested compilation can be idle until a later Editor tick. Public job outcome and
    // idle samples cannot retire that ownership. Completion needs paired pipeline events
    // or terminal cancellation/failure of the exact native task supplied at its start.
    internal sealed class OwnedCompilationReservation
    {
        internal sealed class Checkpoint
        {
            public string JobId;
            public string Generation;
            public string Phase;
            public double Deadline;
            public bool RefreshDecisionPending;
            public bool RefreshCycleObserved;
            public bool PipelineFinished;
            public string NativeTaskTermination;
            public string RecoveryReason;
            public bool InvocationUncertain;
            public bool? NativeInvocationPending;
        }

        private Checkpoint state;
        private object context;
        private Task nativeTask;
        private readonly Func<object, Task> readTask;
        private bool finishTurnObserved;
        private bool failedTriggerCanStart;
        private bool compilationDuringPreparation;
        private bool checkpointNeedsRetry;
        internal Action Changed;
        private bool HasCompletion => state != null && !state.InvocationUncertain && (state.PipelineFinished || state.NativeTaskTermination != null);
        internal bool AwaitingQuiescence => HasCompletion && !state.RefreshDecisionPending && state.NativeInvocationPending != true;
        internal Checkpoint State =>
            state == null
                ? null
                : new Checkpoint
                {
                    JobId = state.JobId,
                    Generation = state.Generation,
                    Phase = state.Phase,
                    Deadline = state.Deadline,
                    RefreshDecisionPending = state.RefreshDecisionPending,
                    RefreshCycleObserved = state.RefreshCycleObserved,
                    PipelineFinished = state.PipelineFinished,
                    NativeTaskTermination = state.NativeTaskTermination,
                    RecoveryReason = state.RecoveryReason,
                    InvocationUncertain = state.InvocationUncertain,
                    NativeInvocationPending = state.NativeInvocationPending,
                };

        internal OwnedCompilationReservation(Checkpoint restored = null, Func<object, Task> readTask = null, Action<string> interruptedBeforeDispatch = null)
        {
            this.readTask = readTask ?? CompilationTaskObservation.Read;
            if (restored != null)
            {
                bool terminated = restored.NativeTaskTermination == "canceled" || restored.NativeTaskTermination == "faulted";
                bool identified =
                    Guid.TryParseExact(restored.Generation, "N", out _)
                    && !string.IsNullOrEmpty(restored.JobId)
                    && !double.IsNaN(restored.Deadline)
                    && !double.IsInfinity(restored.Deadline);
                if (
                    identified
                    && restored.Phase == "preparing"
                    && !restored.RefreshCycleObserved
                    && !restored.PipelineFinished
                    && restored.NativeTaskTermination == null
                    && restored.RecoveryReason == null
                    && !restored.InvocationUncertain
                    && restored.NativeInvocationPending != true
                )
                {
                    // The old domain's continuation cannot run. Every native entry
                    // point persists "pending" before it can import or request a compile.
                    interruptedBeforeDispatch?.Invoke(restored.JobId);
                    return;
                }
                bool valid =
                    identified
                    && (restored.NativeTaskTermination == null || terminated && !restored.PipelineFinished)
                    && (
                        ((restored.Phase == "pending" || restored.Phase == "started") && !restored.PipelineFinished && !terminated)
                        || (restored.Phase == "finished" && (restored.PipelineFinished || terminated))
                        || restored.Phase == "recovery_required"
                    );
                bool finished =
                    valid
                    && !restored.InvocationUncertain
                    // In older checkpoints a pending refresh decision could still
                    // be inside the native import that reported the pipeline finish.
                    && !(restored.NativeInvocationPending ?? restored.RefreshDecisionPending)
                    && (restored.PipelineFinished || terminated)
                    && (restored.Phase == "finished" || restored.Phase == "recovery_required");
                string recoveryReason =
                    finished ? null
                    : !valid ? "invalid_compilation_checkpoint"
                    : restored.RecoveryReason == "compilation_context_unavailable" ? "compilation_context_unavailable"
                    : "compilation_context_lost_on_reload";
                state = new Checkpoint
                {
                    JobId = valid ? restored.JobId : null,
                    Generation = valid ? restored.Generation : null,
                    Deadline = restored.Deadline,
                    Phase = finished ? "finished" : "recovery_required",
                    PipelineFinished = finished && restored.PipelineFinished,
                    NativeTaskTermination = finished ? restored.NativeTaskTermination : null,
                    RefreshCycleObserved = restored.RefreshCycleObserved,
                    RecoveryReason = recoveryReason,
                    InvocationUncertain = restored.InvocationUncertain,
                };
            }
        }

        internal string Reserve(string jobId, double deadline, bool refreshing = false)
        {
            if (state != null)
                throw new InvalidOperationException("An owned compilation is pending or requires recovery.");
            compilationDuringPreparation = false;
            state = new Checkpoint
            {
                JobId = jobId,
                Generation = Guid.NewGuid().ToString("N"),
                Phase = "preparing",
                NativeInvocationPending = false,
                Deadline = deadline,
                RefreshDecisionPending = refreshing,
            };
            try
            {
                Persist();
            }
            catch
            {
                // The native trigger has not been invoked yet.
                state = null;
                throw;
            }
            return state.Generation;
        }

        internal void BeginNative(string generation)
        {
            if (state == null || state.Generation != generation || state.NativeInvocationPending == true || state.Phase == "recovery_required")
                throw new InvalidOperationException("Compilation preparation no longer owns this reservation.");
            if (compilationDuringPreparation)
                throw new InvalidOperationException("An independent compilation started during preparation.");
            string previousPhase = state.Phase;
            if (state.Phase == "preparing")
                state.Phase = "pending";
            // Every import in a batch can queue more work. Its call must return
            // before even a matching in-call finish can certify this handoff on reload.
            state.NativeInvocationPending = true;
            try
            {
                Persist();
            }
            catch
            {
                // No caller may invoke Unity when the handoff checkpoint fails.
                state.Phase = previousPhase;
                state.NativeInvocationPending = false;
                throw;
            }
        }

        internal void NativeReturned(string generation)
        {
            if (state == null || state.Generation != generation || state.NativeInvocationPending != true)
                throw new InvalidOperationException("The native compilation call no longer owns this reservation.");
            state.NativeInvocationPending = false;
            PersistObservation();
        }

        internal bool AbortBeforeStart(string generation)
        {
            if (state == null || state.Generation != generation || state.Phase != "preparing")
                return false;
            state = null;
            Persist();
            return true;
        }

        internal void InvocationFailed(string generation)
        {
            if (state == null || state.Generation != generation)
                return;
            // A native call that threw may already have queued compilation. Keep a
            // separate uncertainty fence: a cycle observed before this failure cannot
            // prove the later invocation settled. Only a subsequent start in this
            // domain can restore pairing; a reload cannot adopt a replacement cycle.
            state.InvocationUncertain = true;
            state.NativeInvocationPending = false;
            state.RefreshDecisionPending = false;
            state.Phase = "recovery_required";
            state.RecoveryReason = "compilation_trigger_outcome_unknown";
            failedTriggerCanStart = true;
            Persist();
        }

        internal void Started(object value)
        {
            if (state?.Phase == "preparing")
            {
                compilationDuringPreparation = true;
                return;
            }
            ObserveNativeCompletion();
            if (
                state == null
                || (
                    state.Phase != "pending"
                    && !failedTriggerCanStart
                    && !(state.RefreshDecisionPending && state.NativeInvocationPending == true && HasCompletion)
                )
            )
                return;
            failedTriggerCanStart = false;
            state.InvocationUncertain = false;
            context = value;
            nativeTask = readTask(value);
            state.RefreshCycleObserved |= state.RefreshDecisionPending;
            state.PipelineFinished = false;
            state.NativeTaskTermination = null;
            finishTurnObserved = false;
            state.Phase = value == null ? "recovery_required" : "started";
            state.RecoveryReason = value == null ? "compilation_context_unavailable" : null;
            PersistObservation();
        }

        internal void Finished(object value)
        {
            if (state == null || context == null || !ReferenceEquals(context, value))
                return;
            context = null;
            nativeTask = null;
            state.PipelineFinished = true;
            state.Phase = state.InvocationUncertain ? "recovery_required" : "finished";
            finishTurnObserved = false;
            PersistObservation();
        }

        internal void ObserveNativeCompletion()
        {
            string termination = CompilationTaskObservation.Termination(nativeTask);
            if (state == null || context == null || termination == null)
                return;
            // Unity's Bee pipeline omits compilationFinished for canceled/faulted
            // tasks. Retain that exact task's witness before a later reload loses it.
            context = null;
            nativeTask = null;
            state.NativeTaskTermination = termination;
            state.Phase = state.InvocationUncertain ? "recovery_required" : "finished";
            if (!state.InvocationUncertain)
                state.RecoveryReason = null;
            finishTurnObserved = false;
            PersistObservation();
        }

        internal bool CompleteRefreshDecision(string generation)
        {
            if (state?.Generation != generation)
                return false;
            bool changed = state.RefreshDecisionPending;
            state.RefreshDecisionPending = false;
            if (changed)
                PersistObservation();
            return state.RefreshCycleObserved;
        }

        internal void ObserveQuiescence(bool quiescent)
        {
            if (!AwaitingQuiescence)
                return;
            // Observe a later Editor update before considering raw native idle. This
            // cannot run from the pipeline Finished callback itself.
            if (!finishTurnObserved)
            {
                finishTurnObserved = true;
                return;
            }
            if (!quiescent)
                return;
            state = null;
            PersistObservation();
        }

        private void Persist()
        {
            checkpointNeedsRetry = true;
            Changed?.Invoke();
            checkpointNeedsRetry = false;
        }

        private void PersistObservation()
        {
            try
            {
                Persist();
            }
            catch (Exception)
            {
                // An observed native event still belongs to the ledger and Editor
                // snapshot. The verified handoff remains the conservative fallback.
            }
        }

        internal void RetryCheckpointBeforeReload()
        {
            if (checkpointNeedsRetry)
                PersistObservation();
        }

        internal string Blocker(double now)
        {
            if (state == null)
                return null;
            // This is the caller's observation budget, not a native cancellation or
            // lifetime deadline. Late start/finish events must still be pairable.
            return state.Phase == "recovery_required" ? "recovery_required" : "native_job_active";
        }

        internal NativeRecoveryDetails Recovery(double now)
        {
            if (state == null)
                return null;
            if (state.Phase == "recovery_required")
            {
                string message = state.RecoveryReason switch
                {
                    "invalid_compilation_checkpoint" =>
                        "The saved compilation reservation is invalid, so its ownership and completion evidence cannot be trusted.",
                    "compilation_context_unavailable" =>
                        "Unity reported a compilation start without a usable pipeline context, so its matching finish cannot be identified.",
                    "compilation_trigger_outcome_unknown" =>
                        "A script import or compilation trigger threw after native invocation. It may have queued compilation; a later start in this domain can still be paired with its finish.",
                    _ => "A compilation reservation survived an assembly reload without a matching finish witness; its pipeline context cannot be restored.",
                };
                return new NativeRecoveryDetails(
                    "owned_compilation",
                    state.Phase,
                    state.RecoveryReason ?? "compilation_context_lost_on_reload",
                    "restart_required",
                    "inspect_native_work_then_restart_editor",
                    message
                        + " Idle samples or unrelated pipeline events cannot release this ownership. Inspect Unity's native work and restart the Editor when safe.",
                    state.JobId,
                    state.Generation
                );
            }

            bool waitExpired = now >= state.Deadline;
            string reason;
            string action;
            string detail;
            if (state.Phase == "preparing")
            {
                reason = "preparing_compilation";
                action = "wait_for_source_preparation";
                detail = "The caller holds the reservation while preparing source inputs. It has not invoked a native import or compilation request.";
            }
            else if (HasCompletion && state.NativeInvocationPending == true)
            {
                reason = "compilation_trigger_return_pending";
                action = "wait_for_native_work";
                detail = "The matching compilation finished inside a native call. Wait for the call to return before treating that finish as completion.";
            }
            else if (HasCompletion)
            {
                reason =
                    state.RefreshDecisionPending ? "refresh_trigger_decision_pending"
                    : state.NativeTaskTermination != null ? "compilation_" + state.NativeTaskTermination + "_awaiting_quiescence"
                    : "compilation_finished_awaiting_quiescence";
                action = "wait_for_editor_quiescence";
                detail =
                    state.NativeTaskTermination != null
                        ? "The exact native compilation task "
                            + state.NativeTaskTermination
                            + " without a pipeline finish event. Its termination was recorded; ownership will release after the refresh decision and later Editor quiescence. This does not establish compilation success."
                    : state.RefreshDecisionPending
                        ? "The matching pipeline finish was observed; wait for the owned refresh trigger decision and later editor quiescence before ownership can be released."
                    : "The matching pipeline finish was observed; ownership will release after later Editor updates confirm compilation, import and assembly reload are idle.";
            }
            else if (state.Phase == "started")
            {
                reason = waitExpired ? "compilation_finish_wait_expired" : "compilation_running";
                action = "wait_for_pipeline_finish";
                detail = waitExpired
                    ? "The caller's wait deadline elapsed while the owned compilation still has a usable pipeline context. Keep waiting for its matching finish; the deadline does not require an Editor restart."
                    : "The owned compilation is running; wait for its matching pipeline finish and later editor quiescence.";
            }
            else
            {
                reason = waitExpired ? "compilation_start_wait_expired" : "compilation_start_pending";
                action = "wait_for_compilation_start";
                detail = waitExpired
                    ? "The caller's wait deadline elapsed before a pipeline start was observed. The native request remains owned and late events can still complete it; elapsed time or idle samples do not prove cancellation."
                    : "The native compilation request is queued; wait for its pipeline start and matching finish. An idle sample does not prove the queued request was canceled.";
            }
            return new NativeRecoveryDetails("owned_compilation", state.Phase, reason, "recoverable", action, detail, state.JobId, state.Generation);
        }
    }

    internal static class CompilationTaskObservation
    {
        internal static Task Read(object context)
        {
            // This is an optional, read-only compatibility adapter for Unity's Bee
            // context, not a general TaskObject convention. Unknown versions/types
            // retain event-only ownership rather than guessing that work ended.
            var type = context?.GetType();
            if (type?.FullName != "Bee.BeeDriver.ActiveBuild" || type.Assembly.GetName().Name != "Bee.BeeDriver2")
                return null;
            try
            {
                return type.GetProperty("TaskObject", BindingFlags.Instance | BindingFlags.Public)?.GetValue(context) as Task;
            }
            catch (Exception)
            {
                return null;
            }
        }

        // Success is deliberately excluded: assembly diagnostics and the public
        // finish callback may still be pending after the Bee task has completed.
        internal static string Termination(Task task) =>
            task?.IsCanceled == true ? "canceled"
            : task?.IsFaulted == true ? "faulted"
            : null;
    }
}
