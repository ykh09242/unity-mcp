using System;
using System.Collections.Generic;
using System.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Bounded clock-driven execution; evaluation, cleanup and sampling never replay a dispatched step.</summary>
    public sealed class PlayScenarioEngine
    {
        private readonly IPlayScenarioHost _host;
        private readonly Action<PlayScenarioRun, PlayScenarioStep, long> _onFailure;
        private readonly Action _onEvaluationCompleted;
        private bool _released;
        private bool _failureCaptured;
        public PlayScenarioRun State { get; }
        public int Revision { get; private set; }
        public bool Running => State.Status == "running";

        public PlayScenarioEngine(
            PlayScenarioRun state,
            IPlayScenarioHost host,
            Action<PlayScenarioRun, PlayScenarioStep, long> onFailure = null,
            Action onEvaluationCompleted = null
        )
        {
            State = state ?? throw new ArgumentNullException(nameof(state));
            _host = host ?? throw new ArgumentNullException(nameof(host));
            _onFailure = onFailure;
            _onEvaluationCompleted = onEvaluationCompleted;
        }

        public static PlayScenarioRun Create(PlayScenarioDefinition scenario, string jobId, int repeatCount, int timeoutSeconds, long now)
        {
            if (scenario == null)
                throw new ArgumentNullException(nameof(scenario));
            if (repeatCount < 1 || repeatCount > 10 || timeoutSeconds < 1 || timeoutSeconds > 1800)
                throw new ArgumentException("repeat_count must be 1-10 and timeout_seconds must be 1-1800.");
            var run = new PlayScenarioRun
            {
                JobId = jobId,
                Scenario = scenario,
                RepeatCount = repeatCount,
                StartedUnixMs = now,
                DeadlineUnixMs = now + timeoutSeconds * 1000L,
            };
            for (int iteration = 1; iteration <= repeatCount; iteration++)
            {
                AddSteps(run, scenario.SetupSteps, "setup", iteration);
                AddSteps(run, scenario.Steps, "main", iteration);
                AddSteps(run, scenario.CleanupSteps, "cleanup", iteration);
            }
            return run;
        }

        private static void AddSteps(PlayScenarioRun run, List<PlayScenarioStep> steps, string stage, int iteration)
        {
            for (int index = 0; index < steps.Count; index++)
                run.Steps.Add(
                    new PlayScenarioStepResult
                    {
                        Iteration = iteration,
                        Stage = stage,
                        StepIndex = index,
                        Name = steps[index].Name,
                        Action = steps[index].Action,
                    }
                );
        }

        public PlayScenarioStep CurrentStep => State.Cursor >= 0 && State.Cursor < State.Steps.Count ? DefinitionStep(State.Steps[State.Cursor]) : null;

        private PlayScenarioStep DefinitionStep(PlayScenarioStepResult result)
        {
            List<PlayScenarioStep> steps =
                result.Stage == "setup" ? State.Scenario.SetupSteps
                : result.Stage == "cleanup" ? State.Scenario.CleanupSteps
                : State.Scenario.Steps;
            return steps[result.StepIndex];
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
            if (State.Phase == "cleaning")
            {
                // Failure capture may pause service ticks before cleanup can begin. Grant its
                // separate budget once execution resumes, including an unready first tick.
                if (!State.CleanupDeadlineUnixMs.HasValue)
                {
                    State.CleanupDeadlineUnixMs = now + State.Scenario.CleanupTimeoutSeconds * 1000L;
                    Revision++;
                }
                if (now >= State.CleanupDeadlineUnixMs.Value)
                {
                    CleanupFailure("Cleanup exceeded its wall-clock timeout.", now, true);
                    return;
                }
                if (now >= State.DeadlineUnixMs && State.PendingStatus == null)
                    RequestOutcome("timed_out", "Run exceeded its wall-clock timeout during cleanup.", now);
            }
            else if (now >= State.DeadlineUnixMs)
            {
                RequestOutcome("timed_out", "Run exceeded its wall-clock timeout.", now);
                return;
            }
            if (!Running || State.Phase == "starting")
                return;
            if (State.Phase == "settling")
            {
                if (now < State.SettleDeadlineUnixMs)
                    return;
                int iteration = State.Cursor > 0 ? State.Steps[State.Cursor - 1].Iteration : 1;
                StartCleanup(iteration, now);
                return;
            }

            PlayScenarioStepResult result = State.Steps[State.Cursor];
            PlayScenarioStep step = DefinitionStep(result);
            if (result.StartedUnixMs.HasValue && now >= result.StartedUnixMs.Value + step.TimeoutSeconds * 1000L)
            {
                string error = "Step timed out: " + result.Name + ". Last observation: " + result.Detail;
                if (State.Phase == "cleaning")
                    CleanupFailure(error, now, false);
                else
                    RequestOutcome("timed_out", error, now);
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
            int cursor = State.Cursor;
            string phase = State.Phase;
            try
            {
                PlayScenarioObservation observation = _host.Evaluate(step, firstPoll);
                // Apply logs from this evaluation before attributing effects to a later iteration
                // or sampling a purportedly successful cleanup.
                _onEvaluationCompleted?.Invoke();
                // A UI listener may cancel, fail or interrupt synchronously during dispatch.
                if (!Running || State.Cursor != cursor || State.Phase != phase)
                    return;
                result.Detail = Bounded(observation.Detail, 2048);
                if (!observation.Ready)
                {
                    if (result.StableSinceUnixMs.HasValue)
                    {
                        result.StableSinceUnixMs = null;
                        Revision++;
                    }
                    return;
                }
                if ((step.StableForMs ?? 0) > 0)
                {
                    if (!result.StableSinceUnixMs.HasValue)
                    {
                        result.StableSinceUnixMs = now;
                        Revision++;
                    }
                    if (now - result.StableSinceUnixMs.Value < step.StableForMs.Value)
                        return;
                }
                result.Status = "passed";
                result.FinishedUnixMs = now;
                State.Cursor++;
                State.NextPollUnixMs = now;
                Revision++;
                Advance(result, now);
            }
            catch (Exception exception)
            {
                // A partially dispatched click must never be retried.
                string error = exception.GetType().Name + ": " + exception.Message;
                if (!Running || State.Cursor != cursor || State.Phase != phase)
                    return;
                if (State.Phase == "cleaning")
                    CleanupFailure(error, now, false);
                else
                    RequestOutcome("failed", error, now);
            }
        }

        private void Advance(PlayScenarioStepResult completed, long now)
        {
            if (completed.Stage == "cleanup")
            {
                if (State.Cursor == State.Steps.Count || State.Steps[State.Cursor].Iteration != completed.Iteration)
                    CompleteIteration(completed.Iteration, now);
                return;
            }
            if (completed.Stage == "main" && completed.StepIndex == State.Scenario.Steps.Count - 1)
            {
                State.Phase = "settling";
                State.SettleDeadlineUnixMs = now + State.Scenario.CompletionStableMs;
                Revision++;
                // Even a zero quiet window leaves success open until the next tick's log drain.
            }
        }

        public void Cancel(long now) => RequestOutcome("cancelled", "Cancelled by caller. Play Mode and any in-flight scene load are unchanged.", now);

        public void RequestFailure(string reason, long now)
        {
            if (Running && State.Phase == "cleaning")
            {
                AppendCleanupError(reason);
                if (State.PendingStatus == null)
                {
                    State.PendingStatus = "failed";
                    State.PendingError = Bounded(reason, 4096);
                }
                CaptureFailure(now);
                Revision++;
                return;
            }
            RequestOutcome("failed", reason, now);
        }

        /// <summary>Consumes final-evaluation logs before a terminal report is published.</summary>
        public void ObserveUnexpectedError(string reason, long now)
        {
            if (Running)
                RequestFailure(reason, now);
            else if (State.Status == "succeeded")
            {
                State.Error = Bounded(reason, 4096);
                CaptureFailure(now);
                State.Status = "failed";
                State.FinishedUnixMs = now;
                Revision++;
            }
        }

        public void Interrupt(string reason, long now)
        {
            if (!Running)
                return;
            State.PendingStatus = State.PendingStatus ?? "failed";
            State.PendingError = State.PendingError ?? Bounded(reason, 4096);
            CaptureFailure(now);
            if (State.Scenario.CleanupSteps.Count > 0 && State.Steps.Any(result => result.StartedUnixMs.HasValue))
                AppendCleanupError("Cleanup unavailable after interruption; effects were not replayed.");
            Finish(State.PendingStatus ?? "failed", State.PendingError ?? reason, now);
        }

        private void RequestOutcome(string status, string error, long now)
        {
            if (!Running || State.PendingStatus != null)
                return;
            State.PendingStatus = status;
            State.PendingError = Bounded(error, 4096);
            CaptureFailure(now);
            Revision++;
            if (State.Phase == "cleaning")
                return;
            bool began = State.Steps.Any(result => result.StartedUnixMs.HasValue);
            if (!began || State.Scenario.CleanupSteps.Count == 0)
            {
                Finish(status, error, now);
                return;
            }
            int iteration = State.Cursor < State.Steps.Count ? State.Steps[State.Cursor].Iteration : State.Steps[State.Steps.Count - 1].Iteration;
            // During settling the cursor may already point at the next iteration.
            if (State.Phase == "settling" && State.Cursor > 0)
                iteration = State.Steps[State.Cursor - 1].Iteration;
            foreach (PlayScenarioStepResult result in State.Steps.Where(result => result.Iteration == iteration && result.Stage != "cleanup"))
            {
                if (result.Status == "running")
                {
                    result.Status = status;
                    result.Detail = Bounded(State.PendingError, 2048);
                    result.FinishedUnixMs = now;
                }
                else if (result.Status == "pending")
                {
                    result.Status = "skipped";
                    result.FinishedUnixMs = now;
                }
            }
            StartCleanup(iteration, now);
        }

        private void StartCleanup(int iteration, long now)
        {
            int cleanup = State.Steps.FindIndex(result => result.Iteration == iteration && result.Stage == "cleanup" && result.Status == "pending");
            State.SettleDeadlineUnixMs = null;
            if (cleanup < 0)
            {
                CompleteIteration(iteration, now);
                return;
            }
            State.Cursor = cleanup;
            State.Phase = "cleaning";
            State.CleanupDeadlineUnixMs = null;
            State.NextPollUnixMs = now;
            Revision++;
        }

        private void CleanupFailure(string error, long now, bool all)
        {
            AppendCleanupError(error);
            if (State.PendingStatus == null)
            {
                State.PendingStatus = "failed";
                State.PendingError = Bounded(error, 4096);
            }
            CaptureFailure(now);
            int iteration = State.Steps[State.Cursor].Iteration;
            PlayScenarioStepResult result = State.Steps[State.Cursor];
            result.Status = error.StartsWith("Step timed out:", StringComparison.Ordinal) || all ? "timed_out" : "failed";
            result.Detail = Bounded(error, 2048);
            result.FinishedUnixMs = now;
            State.Cursor++;
            State.NextPollUnixMs = now;
            Revision++;
            if (all || State.Cursor == State.Steps.Count || State.Steps[State.Cursor].Iteration != iteration)
                CompleteIteration(iteration, now);
        }

        private void AppendCleanupError(string error)
        {
            State.CleanupError = Bounded(State.CleanupError == null ? error : State.CleanupError + "\n" + error, 4096);
        }

        private void CompleteIteration(int iteration, long now)
        {
            State.CleanupDeadlineUnixMs = null;
            if (State.PendingStatus != null)
            {
                Finish(State.PendingStatus, State.PendingError, now);
                return;
            }
            CaptureMetrics(iteration, now);
            if (!Running)
                return;
            if (iteration == State.RepeatCount)
            {
                Finish("succeeded", null, now);
                return;
            }
            State.Phase = "executing";
            State.NextPollUnixMs = now;
            Revision++;
        }

        private void CaptureFailure(long now)
        {
            if (_failureCaptured)
                return;
            _failureCaptured = true;
            try
            {
                _onFailure?.Invoke(State, State.Phase == "settling" && State.Cursor > 0 ? DefinitionStep(State.Steps[State.Cursor - 1]) : CurrentStep, now);
            }
            catch (Exception error)
            {
                State.ReportError = Bounded("Failure diagnostics unavailable: " + error.GetType().Name + ": " + error.Message, 4096);
            }
        }

        private void CaptureMetrics(int iteration, long now)
        {
            PlayScenarioMetricsOptions options = State.Scenario.Metrics;
            if (!options.Enabled || State.Metrics.Count >= 10)
                return;
            PlayScenarioMetricsSnapshot snapshot;
            try
            {
                snapshot = (_host as IPlayScenarioMetricsHost)?.CaptureMetrics(iteration, now);
                if (snapshot == null)
                    snapshot = new PlayScenarioMetricsSnapshot { Error = "Metrics are unavailable from this host." };
            }
            catch (Exception error)
            {
                snapshot = new PlayScenarioMetricsSnapshot { Error = error.GetType().Name + ": " + error.Message };
            }
            snapshot.Iteration = iteration;
            snapshot.TimestampUnixMs = now;
            snapshot.IgnoredForTrend = iteration <= options.WarmupIterations;
            snapshot.Error = Bounded(snapshot.Error, 2048);
            if (
                snapshot.ManagedBytes < 0
                || snapshot.AllocatedBytes < 0
                || snapshot.ObjectCount < 0
                || snapshot.RunnerSubscriptionCount < 0
                || snapshot.RunnerHandleCount < 0
            )
                snapshot.Error = "Metrics provider returned a negative measurement.";
            State.Metrics.Add(snapshot);
            UpdateTrends(options);
            Revision++;
        }

        private void UpdateTrends(PlayScenarioMetricsOptions options)
        {
            CheckTrend("managed_bytes", snapshot => snapshot.ManagedBytes, options.ManagedGrowthBytes, options);
            CheckTrend("allocated_bytes", snapshot => snapshot.AllocatedBytes, options.AllocatedGrowthBytes, options);
            CheckTrend("object_count", snapshot => snapshot.ObjectCount, options.ObjectGrowthCount, options);
            int usable = State.Metrics.Count(snapshot => !snapshot.IgnoredForTrend && snapshot.Error == null);
            int required = options.ConsecutiveIncreases + 1;
            State.MetricsSummary =
                usable < required
                    ? "Insufficient post-warmup samples for sustained growth: " + usable + " of " + required + " required. This does not establish a leak."
                    : "Sustained growth is diagnostic only; measurements do not establish a leak.";
        }

        private void CheckTrend(string name, Func<PlayScenarioMetricsSnapshot, long?> selector, long threshold, PlayScenarioMetricsOptions options)
        {
            long? previous = null;
            int increases = 0;
            foreach (PlayScenarioMetricsSnapshot snapshot in State.Metrics)
            {
                long? current = snapshot.IgnoredForTrend || snapshot.Error != null ? null : selector(snapshot);
                if (previous.HasValue && current.HasValue && current.Value > previous.Value && current.Value - previous.Value > threshold)
                    increases++;
                else
                    increases = 0;
                if (increases >= options.ConsecutiveIncreases && State.MetricWarnings.Count < 16)
                {
                    string warning =
                        name
                        + " increased by more than "
                        + threshold
                        + " for "
                        + options.ConsecutiveIncreases
                        + " consecutive post-warmup intervals. This is a diagnostic trend, not proof of a leak.";
                    if (!State.MetricWarnings.Contains(warning))
                        State.MetricWarnings.Add(warning);
                }
                previous = current;
            }
        }

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
                    result.Detail = Bounded(State.Error, 2048);
            }
            Revision++;
            Release();
        }

        public void Release()
        {
            if (_released)
                return;
            _released = true;
            try
            {
                _host.Release();
                State.RunnerResourcesReleased = true;
            }
            catch (Exception error)
            {
                State.RunnerResourcesReleased = false;
                State.ReportError = Bounded("Host release failed: " + error.GetType().Name + ": " + error.Message, 4096);
            }
        }

        internal static string Bounded(string value, int maxLength) => value == null || value.Length <= maxLength ? value : value.Substring(0, maxLength);
    }
}
