using System;
using MCPForUnity.Editor.Services.Transport;

namespace MCPForUnity.Editor.Services
{
    // A requested compilation can be idle until a later Editor tick. Public job outcome and
    // idle samples cannot retire that ownership; only its paired pipeline events can.
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
            public string RecoveryReason;
        }

        private Checkpoint state;
        private object context;
        private bool finishTurnObserved;
        internal Action Changed;
        internal bool AwaitingQuiescence => state?.PipelineFinished == true && !state.RefreshDecisionPending;
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
                    RecoveryReason = state.RecoveryReason,
                };

        internal OwnedCompilationReservation(Checkpoint restored = null)
        {
            if (restored != null)
            {
                bool valid =
                    Guid.TryParseExact(restored.Generation, "N", out _)
                    && !string.IsNullOrEmpty(restored.JobId)
                    && !double.IsNaN(restored.Deadline)
                    && !double.IsInfinity(restored.Deadline)
                    && (
                        ((restored.Phase == "pending" || restored.Phase == "started") && !restored.PipelineFinished)
                        || (restored.Phase == "finished" && restored.PipelineFinished)
                        || restored.Phase == "recovery_required"
                    );
                bool finished = valid && restored.PipelineFinished && (restored.Phase == "finished" || restored.Phase == "recovery_required");
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
                    PipelineFinished = finished,
                    RefreshCycleObserved = restored.RefreshCycleObserved,
                    RecoveryReason = recoveryReason,
                };
            }
        }

        internal string Reserve(string jobId, double deadline, bool refreshing = false)
        {
            if (state != null)
                throw new InvalidOperationException("An owned compilation is pending or requires recovery.");
            state = new Checkpoint
            {
                JobId = jobId,
                Generation = Guid.NewGuid().ToString("N"),
                Phase = "pending",
                Deadline = deadline,
                RefreshDecisionPending = refreshing,
            };
            try
            {
                Changed?.Invoke();
            }
            catch
            {
                // The native trigger has not been invoked yet.
                state = null;
                throw;
            }
            return state.Generation;
        }

        internal bool AbortBeforeStart(string generation)
        {
            if (state?.Generation != generation || state.Phase != "pending" || state.RefreshCycleObserved)
                return false;
            state = null;
            Changed?.Invoke();
            return true;
        }

        internal void Started(object value)
        {
            if (state == null || (state.Phase != "pending" && !(state.RefreshDecisionPending && state.PipelineFinished)))
                return;
            context = value;
            state.RefreshCycleObserved |= state.RefreshDecisionPending;
            state.PipelineFinished = false;
            finishTurnObserved = false;
            state.Phase = value == null ? "recovery_required" : "started";
            state.RecoveryReason = value == null ? "compilation_context_unavailable" : null;
            Changed?.Invoke();
        }

        internal void Finished(object value)
        {
            if (state == null || context == null || !ReferenceEquals(context, value))
                return;
            context = null;
            state.PipelineFinished = true;
            state.Phase = "finished";
            finishTurnObserved = false;
            Changed?.Invoke();
        }

        internal bool CompleteRefreshDecision(string generation)
        {
            if (state?.Generation != generation)
                return false;
            bool changed = state.RefreshDecisionPending;
            state.RefreshDecisionPending = false;
            if (changed)
                Changed?.Invoke();
            return state.RefreshCycleObserved;
        }

        internal void ObserveQuiescence(bool quiescent)
        {
            if (state == null || !state.PipelineFinished || state.RefreshDecisionPending)
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
            Changed?.Invoke();
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
            if (state.PipelineFinished)
            {
                reason = state.RefreshDecisionPending ? "refresh_trigger_decision_pending" : "compilation_finished_awaiting_quiescence";
                action = "wait_for_editor_quiescence";
                detail = state.RefreshDecisionPending
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
}
