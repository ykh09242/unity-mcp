using System;
using System.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Bounded, clock-driven execution. The host is touched only for a due condition.</summary>
    public sealed class PlayScenarioEngine
    {
        private readonly IPlayScenarioHost _host;
        private bool _released;
        public PlayScenarioRun State { get; }
        public int Revision { get; private set; }
        public bool Running => State.Status == "running";

        public PlayScenarioEngine(PlayScenarioRun state, IPlayScenarioHost host)
        {
            State = state ?? throw new ArgumentNullException(nameof(state));
            _host = host ?? throw new ArgumentNullException(nameof(host));
        }

        public static PlayScenarioRun Create(PlayScenarioDefinition scenario, string jobId, int repeatCount, int timeoutSeconds, long now)
        {
            if (repeatCount < 1 || repeatCount > 10 || timeoutSeconds < 1 || timeoutSeconds > 1800)
                throw new ArgumentException("repeat_count must be 1–10 and timeout_seconds must be 1–1800.");
            var run = new PlayScenarioRun
            {
                JobId = jobId,
                Scenario = scenario,
                RepeatCount = repeatCount,
                StartedUnixMs = now,
                DeadlineUnixMs = now + timeoutSeconds * 1000L,
            };
            for (int iteration = 1; iteration <= repeatCount; iteration++)
            for (int index = 0; index < scenario.Steps.Count; index++)
                run.Steps.Add(
                    new PlayScenarioStepResult
                    {
                        Iteration = iteration,
                        StepIndex = index,
                        Name = scenario.Steps[index].Name,
                        Action = scenario.Steps[index].Action,
                    }
                );
            return run;
        }

        public void EnteredPlayMode()
        {
            if (!Running || State.Phase != "starting")
                return;
            State.Phase = "executing";
            Revision++;
        }

        public void Tick(long now, bool ready)
        {
            if (!Running)
                return;
            if (now >= State.DeadlineUnixMs)
            {
                Finish("timed_out", "Run exceeded its wall-clock timeout.", now);
                return;
            }
            if (State.Phase != "executing")
                return;

            PlayScenarioStepResult result = State.Steps[State.Cursor];
            PlayScenarioStep step = State.Scenario.Steps[result.StepIndex];
            if (result.StartedUnixMs.HasValue && now >= result.StartedUnixMs.Value + step.TimeoutSeconds * 1000L)
            {
                Finish("timed_out", "Step timed out: " + result.Name + ". Last observation: " + result.Detail, now);
                return;
            }
            if (!ready || now < State.NextPollUnixMs)
                return;

            bool firstPoll = result.Status == "pending";
            if (firstPoll)
            {
                result.Status = "running";
                result.StartedUnixMs = now;
                Revision++;
            }
            State.NextPollUnixMs = now + State.Scenario.PollIntervalMs;
            result.PollCount++;
            try
            {
                PlayScenarioObservation observation = _host.Evaluate(step, firstPoll);
                // UI listeners may cancel or stop the job synchronously during dispatch.
                if (!Running)
                    return;
                result.Detail = Bounded(observation.Detail, 2048);
                if (!observation.Ready)
                    return;
                result.Status = "passed";
                result.FinishedUnixMs = now;
                State.Cursor++;
                State.NextPollUnixMs = now;
                Revision++;
                if (State.Cursor == State.Steps.Count)
                    Finish("succeeded", null, now);
            }
            catch (Exception exception)
            {
                // This is the host boundary: a partially dispatched click must never be retried.
                Finish("failed", exception.GetType().Name + ": " + exception.Message, now);
            }
        }

        public void Cancel(long now) => Finish("cancelled", "Cancelled by caller. Play Mode and any in-flight scene load are unchanged.", now);

        public void Interrupt(string reason, long now) => Finish("failed", reason, now);

        private void Finish(string status, string error, long now)
        {
            if (!Running)
                return;
            State.Status = status;
            State.Phase = "finished";
            State.Error = Bounded(error, 4096);
            State.FinishedUnixMs = now;
            foreach (PlayScenarioStepResult result in State.Steps.Where(result => result.Status == "running" || result.Status == "pending"))
            {
                result.Status = result.Status == "running" ? status : "skipped";
                result.FinishedUnixMs = now;
                if (result.Status != "skipped")
                    result.Detail = State.Error;
            }
            Revision++;
            Release();
        }

        public void Release()
        {
            if (_released)
                return;
            _released = true;
            _host.Release();
        }

        internal static string Bounded(string value, int maxLength) => value == null || value.Length <= maxLength ? value : value.Substring(0, maxLength);
    }
}
