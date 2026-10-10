using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Runtime;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Bounded clock-driven execution; evaluation, cleanup and sampling never replay a dispatched step.</summary>
    public sealed partial class PlayScenarioEngine
    {
        private readonly IPlayScenarioHost _host;
        private readonly Action<PlayScenarioRun, PlayScenarioStep, long> _onFailure;
        private readonly Action _onEvaluationCompleted;
        private readonly IPlayScenarioResourceHost _resources;
        private PlayScenarioRegisteredResources _resourceBaseline;
        private long _resourceBaselineAt;
        private int _resourceIteration;
        private PlayScenarioFailure _observationFailure;
        private bool _released;
        private bool _failureCaptured;
        private int? _completingIteration;
        public PlayScenarioRun State { get; }
        public int Revision { get; private set; }
        public bool Running => State.Status == "running";

        public PlayScenarioEngine(
            PlayScenarioRun state,
            IPlayScenarioHost host,
            Action<PlayScenarioRun, PlayScenarioStep, long> onFailure = null,
            Action onEvaluationCompleted = null,
            IPlayScenarioResourceHost resources = null
        )
        {
            State = state ?? throw new ArgumentNullException(nameof(state));
            _host = host ?? throw new ArgumentNullException(nameof(host));
            _onFailure = onFailure;
            _onEvaluationCompleted = onEvaluationCompleted;
            _resources = resources ?? host as IPlayScenarioResourceHost;
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
                IterationResultsVersion = 1,
                IterationResults = new List<PlayScenarioIterationResult>(repeatCount),
                StartedUnixMs = now,
                DeadlineUnixMs = now + timeoutSeconds * 1000L,
                Reproduction = new PlayScenarioReproduction { DefinitionHash = PlayScenarioReproduction.Hash(scenario) },
            };
            for (int iteration = 1; iteration <= repeatCount; iteration++)
            {
                run.IterationResults.Add(new PlayScenarioIterationResult { Iteration = iteration });
                AddSteps(run, scenario.SetupSteps, "setup", iteration);
                AddSteps(run, scenario.Steps, "main", iteration);
                AddSteps(run, scenario.CleanupSteps, "cleanup", iteration);
            }
            RecordTimeline(run, now, null, "run_started", "Scenario execution created.");
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

        public PlayScenarioStep CurrentStep
        {
            get
            {
                PlayScenarioStepResult result = State.Cursor >= 0 && State.Cursor < State.Steps.Count ? State.Steps[State.Cursor] : null;
                if (
                    _completingIteration.HasValue
                    || (State.IterationResults?.Any(entry => entry.Status == "running") == true && (result == null || result.Iteration != ActiveIteration))
                )
                    result = State.Steps.LastOrDefault(entry => entry.Iteration == ActiveIteration);
                return result == null ? null : DefinitionStep(result);
            }
        }

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
            Timeline(State.StartedUnixMs, "run_entered_play", "Execution host is ready.", null);
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
                    CleanupFailure(
                        "Cleanup exceeded its wall-clock timeout.",
                        now,
                        true,
                        Failure("cleanup_timeout", "Cleanup exceeded its wall-clock timeout.")
                    );
                    return;
                }
                if (now >= State.DeadlineUnixMs && State.PendingStatus == null)
                    RequestOutcome(
                        "timed_out",
                        "Run exceeded its wall-clock timeout during cleanup.",
                        now,
                        Failure("run_timeout", "Run exceeded its wall-clock timeout during cleanup.")
                    );
            }
            else if (now >= State.DeadlineUnixMs)
            {
                RequestOutcome("timed_out", "Run exceeded its wall-clock timeout.", now, Failure("run_timeout", "Run exceeded its wall-clock timeout."));
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
                    CleanupFailure(error, now, false, _observationFailure ?? Failure("step_timeout", error), true);
                else
                    RequestOutcome("timed_out", error, now, _observationFailure ?? Failure("step_timeout", error));
                return;
            }
            if (!ready || now < State.NextPollUnixMs)
                return;
            BeginIteration(result.Iteration, now);
            if (!BeginResourceIteration(result.Iteration, now))
                return;
            bool firstPoll = result.Status == "pending";
            if (firstPoll)
            {
                result.Status = "running";
                result.StartedUnixMs = now;
                _observationFailure = null;
                _resetSnapshot = null;
                ClearStateProbe();
                _lastObservation = null;
                _hasObservation = false;
                Timeline(now, "step_started", result.Name, result);
                Revision++;
            }
            State.NextPollUnixMs = now + State.Scenario.PollIntervalMs;
            result.PollCount++;
            int cursor = State.Cursor;
            string phase = State.Phase;
            try
            {
                PlayScenarioObservation observation = EvaluateCounted(step, firstPoll, result);
                // Apply logs from this evaluation before attributing effects to a later iteration
                // or sampling a purportedly successful cleanup.
                _onEvaluationCompleted?.Invoke();
                if (ObserveQueryBudget(now))
                    return;
                // A UI listener may cancel, fail or interrupt synchronously during dispatch.
                if (!Running || State.Cursor != cursor || State.Phase != phase)
                    return;
                ObserveTimeline(now, result, observation);
                result.Detail = Bounded(observation.Detail, 2048);
                _observationFailure = observation.Failure == null ? null : Attribute(observation.Failure);
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
                _observationFailure = null;
                result.Status = "passed";
                result.FinishedUnixMs = now;
                Timeline(now, "step_passed", result.Detail, result);
                _resetSnapshot = null;
                ClearStateProbe();
                State.Cursor++;
                State.NextPollUnixMs = now;
                Revision++;
                Advance(result, now);
            }
            catch (Exception exception)
            {
                // Query work is counted even when the evaluation throws.
                if (ObserveQueryBudget(now))
                    return;
                // A partially dispatched click must never be retried.
                string error = exception.GetType().Name + ": " + exception.Message;
                if (!Running || State.Cursor != cursor || State.Phase != phase)
                    return;
                if (State.Phase == "cleaning")
                    CleanupFailure(error, now, false, (exception as PlayScenarioException)?.Failure ?? Failure("action_exception", error));
                else
                    RequestOutcome("failed", error, now, (exception as PlayScenarioException)?.Failure ?? Failure("action_exception", error));
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

        public void Cancel(long now) =>
            RequestOutcome(
                "cancelled",
                "Cancelled by caller. Play Mode and any in-flight scene load are unchanged.",
                now,
                Failure("cancelled", "Cancelled by caller.")
            );

        public void RequestFailure(string reason, long now)
        {
            if (Running && State.Phase == "cleaning")
            {
                AddCleanupFailure(Failure("unexpected_log", reason));
                AppendCleanupError(reason);
                if (State.PendingStatus == null)
                {
                    State.PendingStatus = "failed";
                    State.PendingError = Bounded(reason, 4096);
                    State.Failure = Attribute(Failure("unexpected_log", reason));
                }
                CaptureFailure(now);
                Revision++;
                return;
            }
            RequestOutcome("failed", reason, now, Failure("unexpected_log", reason));
        }

        /// <summary>Consumes final-evaluation logs before a terminal report is published.</summary>
        public void ObserveUnexpectedError(string reason, long now)
        {
            if (Running)
                RequestFailure(reason, now);
            else if (State.Status == "succeeded")
            {
                State.Error = Bounded(reason, 4096);
                State.Failure = Attribute(Failure("unexpected_log", reason));
                CaptureFailure(now);
                State.Status = "failed";
                State.FinishedUnixMs = now;
                PlayScenarioIterationResult iteration = State.IterationResults?.LastOrDefault(result => result.Status == "passed");
                if (iteration != null)
                    FinishIteration(iteration.Iteration, "failed", now);
                Revision++;
            }
        }

        public void Interrupt(string reason, long now)
        {
            if (!Running)
                return;
            State.Failure = State.Failure ?? Attribute(Failure("interrupted", reason));
            State.PendingStatus = State.PendingStatus ?? "failed";
            State.PendingError = State.PendingError ?? Bounded(reason, 4096);
            CaptureFailure(now);
            if (
                IterationStarted(ActiveIteration)
                && State.Steps.Any(result =>
                    result.Iteration == ActiveIteration && result.Stage == "cleanup" && (result.Status == "pending" || result.Status == "running")
                )
            )
            {
                AddCleanupFailure(Failure("interrupted", "Cleanup unavailable after interruption; effects were not replayed."));
                AppendCleanupError("Cleanup unavailable after interruption; effects were not replayed.");
            }
            VerifyResources(now, "Resource verification unavailable after interruption.");
            Finish(State.PendingStatus ?? "failed", State.PendingError ?? reason, now);
        }

        private void RequestOutcome(string status, string error, long now, PlayScenarioFailure failure = null)
        {
            if (!Running || State.PendingStatus != null)
                return;
            State.Failure = State.Failure ?? Attribute(failure ?? Failure("action_exception", error));
            State.PendingStatus = status;
            State.PendingError = Bounded(error, 4096);
            Timeline(now, "outcome_requested", status + ": " + State.PendingError);
            _resetSnapshot = null;
            if (State.Phase != "cleaning")
                ClearStateProbe();
            CaptureFailure(now);
            Revision++;
            if (_completingIteration.HasValue)
                return;
            if (State.Phase == "cleaning")
                return;
            int iteration = ActiveIteration;
            bool began = IterationStarted(iteration);
            if (!began || State.Scenario.CleanupSteps.Count == 0)
            {
                if (began)
                    VerifyResources(now);
                Finish(status, error, now);
                return;
            }
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
            _resetSnapshot = null;
            ClearStateProbe();
            Timeline(now, "cleanup_started", "Best-effort cleanup started.", State.Steps[cleanup]);
            State.Cursor = cleanup;
            State.Phase = "cleaning";
            State.CleanupDeadlineUnixMs = null;
            State.NextPollUnixMs = now;
            Revision++;
        }

        private void CleanupFailure(string error, long now, bool all, PlayScenarioFailure failure = null, bool timedOut = false)
        {
            failure = Attribute(failure ?? Failure("action_exception", error));
            AddCleanupFailure(failure);
            AppendCleanupError(error);
            if (State.PendingStatus == null)
            {
                State.PendingStatus = "failed";
                State.PendingError = Bounded(error, 4096);
                State.Failure = State.Failure ?? failure;
            }
            CaptureFailure(now);
            int iteration = State.Steps[State.Cursor].Iteration;
            PlayScenarioStepResult result = State.Steps[State.Cursor];
            result.Status = all || timedOut ? "timed_out" : "failed";
            result.Detail = Bounded(error, 2048);
            result.FinishedUnixMs = now;
            Timeline(now, "cleanup_failed", result.Detail, result);
            _resetSnapshot = null;
            ClearStateProbe();
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
            // The cursor can already point at the next repetition while resource/metric
            // callbacks still belong to the iteration whose cleanup just completed.
            _completingIteration = iteration;
            try
            {
                State.CleanupDeadlineUnixMs = null;
                VerifyResources(now);
                if (State.PendingStatus != null)
                {
                    Finish(State.PendingStatus, State.PendingError, now);
                    return;
                }
                CaptureMetrics(iteration, now);
                if (!Running)
                    return;
                if (State.PendingStatus != null)
                {
                    Finish(State.PendingStatus, State.PendingError, now);
                    return;
                }
                FinishIteration(iteration, "passed", now);
                if (iteration == State.RepeatCount)
                {
                    Finish("succeeded", null, now);
                    return;
                }
                State.Phase = "executing";
                State.NextPollUnixMs = now;
                Revision++;
            }
            finally
            {
                _completingIteration = null;
            }
        }

        private int ActiveIteration =>
            _completingIteration
            ?? State.IterationResults?.FirstOrDefault(result => result.Status == "running")?.Iteration
            ?? (
                State.Phase == "settling" && State.Cursor > 0 ? State.Steps[State.Cursor - 1].Iteration
                : State.Cursor < State.Steps.Count ? State.Steps[State.Cursor].Iteration
                : State.Steps.Last().Iteration
            );

        private bool IterationStarted(int iteration) =>
            State.IterationResults?.Any(result => result.Iteration == iteration && result.StartedUnixMs.HasValue)
            ?? State.Steps.Any(result => result.Iteration == iteration && result.StartedUnixMs.HasValue);

        private void BeginIteration(int iteration, long now)
        {
            PlayScenarioIterationResult result = State.IterationResults?.FirstOrDefault(entry => entry.Iteration == iteration);
            if (result == null || result.Status != "pending")
                return;
            // A ready iteration starts with resource initialization, immediately before
            // its first evaluation. Unready ticks and cancellation do not start it.
            result.Status = "running";
            result.StartedUnixMs = now;
            Revision++;
        }

        private void FinishIteration(int iteration, string status, long now)
        {
            PlayScenarioIterationResult result = State.IterationResults?.FirstOrDefault(entry => entry.Iteration == iteration);
            if (result == null || !result.StartedUnixMs.HasValue)
                return;
            result.Status = status;
            result.FinishedUnixMs = now;
            Revision++;
        }

        private static PlayScenarioFailure Failure(string code, string message) => new PlayScenarioFailure { Code = code, Message = message };

        private PlayScenarioFailure Attribute(PlayScenarioFailure failure)
        {
            PlayScenarioStepResult result = State.Cursor < State.Steps.Count ? State.Steps[State.Cursor] : State.Steps.LastOrDefault();
            if (_completingIteration.HasValue)
                result = State.Steps.LastOrDefault(entry => entry.Iteration == _completingIteration.Value);
            else if (State.Phase == "settling" && State.Cursor > 0)
                result = State.Steps[State.Cursor - 1];
            else if (result != null && result.Iteration != ActiveIteration)
                result = State.Steps.LastOrDefault(entry => entry.Iteration == ActiveIteration);
            bool resourceFailure = failure.Code == "resource_assertion_failed" || failure.Code == "resource_measurement_failed";
            PlayScenarioStep step = result == null || resourceFailure ? null : DefinitionStep(result);
            bool fallbackCondition = failure.Code == "step_timeout" || failure.Code == "action_exception";
            string expected =
                fallbackCondition && step != null
                    ? (step.Action == "load_scene" || step.Action == "wait_scene" ? step.Scene + " loaded and active" : step.Action + " completed successfully")
                    : null;
            string actual = fallbackCondition ? (failure.Code == "action_exception" ? failure.Message : result?.Detail ?? failure.Message) : null;
            return new PlayScenarioFailure
            {
                Code = Bounded(failure.Code, 64),
                Stage = failure.Stage ?? result?.Stage,
                Iteration = failure.Iteration ?? result?.Iteration,
                StepIndex = resourceFailure ? null : failure.StepIndex ?? result?.StepIndex,
                Target = Bounded(failure.Target ?? step?.Target ?? step?.TargetId ?? step?.StateId, 4096),
                Component = Bounded(failure.Component ?? step?.Component, 256),
                PropertyPath = Bounded(failure.PropertyPath ?? step?.Property?.Path, 256),
                Expected = Bounded(failure.Expected ?? expected, 2048),
                Actual = Bounded(failure.Actual ?? actual, 2048),
                Message = Bounded(failure.Message, 4096),
            };
        }

        private void AddCleanupFailure(PlayScenarioFailure failure)
        {
            if (State.CleanupFailures.Count < 16)
                State.CleanupFailures.Add(Attribute(failure));
        }

        private bool BeginResourceIteration(int iteration, long now)
        {
            if (State.Scenario.Resources?.Enabled != true || _resourceIteration == iteration)
                return true;
            _resourceIteration = iteration;
            _resourceBaselineAt = now;
            Timeline(now, "resource_baseline", "Resource baseline requested.");
            try
            {
                _resourceBaseline = CaptureResources();
                return true;
            }
            catch (Exception error)
            {
                string message = "Resource baseline unavailable: " + error.Message;
                State.ResourceChecks.Add(
                    new PlayScenarioResourceCheck
                    {
                        Iteration = iteration,
                        BaselineUnixMs = now,
                        CheckedUnixMs = now,
                        Error = Bounded(message, 2048),
                    }
                );
                _resourceBaseline = null;
                RequestOutcome("failed", message, now, Failure("resource_measurement_failed", message));
                return false;
            }
        }

        private PlayScenarioRegisteredResources CaptureResources()
        {
            PlayScenarioRegisteredResources snapshot = _resources?.CaptureResources();
            if (snapshot == null || snapshot.Error != null || snapshot.RegistrationFailureCount != 0)
                throw new InvalidOperationException(
                    snapshot?.Error
                        ?? (snapshot?.RegistrationFailureCount > 0 ? "Resource registration capacity was exceeded." : "Resource host is unavailable.")
                );
            long[][] groups = { snapshot.ScriptableObjectIds, snapshot.SubscriptionIds, snapshot.HandleIds };
            if (
                groups.Any(group => group == null)
                || groups.Sum(group => group.Length) > PlayScenarioResourceTracker.RegistrationLimit
                || groups.Any(group => group.Any(id => id <= 0) || group.Distinct().Count() != group.Length)
                || groups.SelectMany(group => group).Distinct().Count() != groups.Sum(group => group.Length)
            )
                throw new InvalidOperationException("Registered resource identities are incomplete or exceed their bound.");
            var identities = new HashSet<long>(groups.SelectMany(group => group));
            PlayScenarioRegisteredResourceInfo[] details = snapshot.ResourceDetails;
            if (
                details == null
                || details.Length > PlayScenarioResourceTracker.RegistrationLimit
                || details.Any(info => info == null || !identities.Contains(info.Id))
                || details.Select(info => info.Id).Distinct().Count() != details.Length
            )
                throw new InvalidOperationException("Registered resource metadata is invalid or exceeds its bound.");
            return new PlayScenarioRegisteredResources
            {
                ScriptableObjectIds = (long[])snapshot.ScriptableObjectIds.Clone(),
                SubscriptionIds = (long[])snapshot.SubscriptionIds.Clone(),
                HandleIds = (long[])snapshot.HandleIds.Clone(),
                ResourceDetails = details,
            };
        }

        private void VerifyResources(long now, string unavailable = null)
        {
            if (State.Scenario.Resources?.Enabled != true)
                return;
            if (_resourceBaseline == null)
            {
                if (unavailable == null)
                    return;
                int? interruptedIteration = State.Steps.LastOrDefault(result => result.StartedUnixMs.HasValue)?.Iteration;
                if (!interruptedIteration.HasValue || State.ResourceChecks.Any(check => check.Iteration == interruptedIteration.Value))
                    return;
                _resourceIteration = interruptedIteration.Value;
                _resourceBaselineAt = now;
            }
            var check = new PlayScenarioResourceCheck
            {
                Iteration = _resourceIteration,
                BaselineUnixMs = _resourceBaselineAt,
                CheckedUnixMs = now,
            };
            try
            {
                if (unavailable != null)
                    throw new InvalidOperationException(unavailable);
                PlayScenarioRegisteredResources snapshot = CaptureResources();
                check.NewScriptableObjects = snapshot.ScriptableObjectIds.Except(_resourceBaseline.ScriptableObjectIds).Count();
                check.NewSubscriptions = snapshot.SubscriptionIds.Except(_resourceBaseline.SubscriptionIds).Count();
                check.NewHandles = snapshot.HandleIds.Except(_resourceBaseline.HandleIds).Count();
                CaptureRetainedResources(check, snapshot, _resourceBaseline);
                PlayScenarioResourceOptions options = State.Scenario.Resources;
                check.Passed =
                    check.NewScriptableObjects <= options.MaxScriptableObjects
                    && check.NewSubscriptions <= options.MaxSubscriptions
                    && check.NewHandles <= options.MaxHandles;
                if (!check.Passed)
                    check.Error = "Registered resources remain alive after cleanup.";
            }
            catch (Exception error)
            {
                check.Error = Bounded(error.Message, 2048);
            }
            finally
            {
                _resourceBaseline = null;
            }
            State.ResourceChecks.Add(check);
            Timeline(
                now,
                "resource_checked",
                check.Passed ? "Resource check passed." : check.Error,
                State.Steps.LastOrDefault(result => result.Iteration == check.Iteration && result.StartedUnixMs.HasValue)
            );
            Revision++;
            if (check.Passed)
                return;
            var failure = new PlayScenarioFailure
            {
                Code = check.NewScriptableObjects.HasValue ? "resource_assertion_failed" : "resource_measurement_failed",
                Stage = "cleanup",
                Iteration = check.Iteration,
                Message = check.Error,
                Expected =
                    "new scriptable_objects <= "
                    + State.Scenario.Resources.MaxScriptableObjects
                    + ", subscriptions <= "
                    + State.Scenario.Resources.MaxSubscriptions
                    + ", handles <= "
                    + State.Scenario.Resources.MaxHandles,
                Actual = check.NewScriptableObjects.HasValue
                    ? "scriptable_objects=" + check.NewScriptableObjects + ", subscriptions=" + check.NewSubscriptions + ", handles=" + check.NewHandles
                    : "unavailable",
            };
            AddCleanupFailure(failure);
            AppendCleanupError(check.Error);
            if (State.PendingStatus == null)
            {
                State.PendingStatus = "failed";
                State.PendingError = check.Error;
                State.Failure = Attribute(failure);
                CaptureFailure(now);
            }
        }

        private void CaptureFailure(long now)
        {
            if (_failureCaptured)
                return;
            _failureCaptured = true;
            Timeline(now, "failure", State.Failure?.Code + ": " + State.Failure?.Message);
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
            Timeline(now, "run_finished", status + (error == null ? "" : ": " + error), null);
            if (State.IterationResults != null)
            {
                foreach (PlayScenarioIterationResult iteration in State.IterationResults)
                {
                    if (iteration.Status == "running")
                        FinishIteration(iteration.Iteration, status == "succeeded" ? "passed" : status, now);
                    else if (iteration.Status == "pending")
                    {
                        iteration.Status = "skipped";
                        iteration.FinishedUnixMs = now;
                    }
                }
            }
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
            _resourceBaseline = null;
            _observationFailure = null;
            _resetSnapshot = null;
            ClearStateProbe();
            _lastObservation = null;
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
