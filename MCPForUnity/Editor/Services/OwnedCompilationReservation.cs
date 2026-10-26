using System;

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
                };

        internal OwnedCompilationReservation(Checkpoint restored = null)
        {
            if (restored != null)
            {
                bool finished =
                    restored.PipelineFinished
                    && (restored.Phase == "finished" || restored.Phase == "recovery_required")
                    && Guid.TryParseExact(restored.Generation, "N", out _)
                    && !string.IsNullOrEmpty(restored.JobId);
                state = new Checkpoint
                {
                    JobId = restored.JobId,
                    Generation = restored.Generation,
                    Deadline = restored.Deadline,
                    Phase = finished ? "finished" : "recovery_required",
                    PipelineFinished = finished,
                    RefreshCycleObserved = restored.RefreshCycleObserved,
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
            if (state.Phase != "recovery_required" && now >= state.Deadline)
            {
                state.Phase = "recovery_required";
                Changed?.Invoke();
            }
            return state.Phase == "recovery_required" ? "recovery_required" : "native_job_active";
        }
    }
}
