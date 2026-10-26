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
        }

        private Checkpoint state;
        private object context;
        internal Action Changed;
        internal Checkpoint State =>
            state == null
                ? null
                : new Checkpoint
                {
                    JobId = state.JobId,
                    Generation = state.Generation,
                    Phase = state.Phase,
                    Deadline = state.Deadline,
                };

        internal OwnedCompilationReservation(Checkpoint restored = null)
        {
            if (restored != null)
                state = new Checkpoint
                {
                    JobId = restored.JobId,
                    Generation = restored.Generation,
                    Deadline = restored.Deadline,
                    Phase = "recovery_required",
                };
        }

        internal string Reserve(string jobId, double deadline)
        {
            if (state != null)
                throw new InvalidOperationException("An owned compilation is pending or requires recovery.");
            state = new Checkpoint
            {
                JobId = jobId,
                Generation = Guid.NewGuid().ToString("N"),
                Phase = "pending",
                Deadline = deadline,
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
            if (state?.Generation != generation || state.Phase != "pending")
                return false;
            state = null;
            Changed?.Invoke();
            return true;
        }

        internal void Started(object value)
        {
            if (state?.Phase != "pending")
                return;
            context = value;
            state.Phase = value == null ? "recovery_required" : "started";
            Changed?.Invoke();
        }

        internal void Finished(object value)
        {
            if (state == null || context == null || !ReferenceEquals(context, value))
                return;
            context = null;
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
