using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Runtime;
using MCPForUnity.Runtime.PlayScenarios;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed partial class PlayScenarioEngine
    {
        private PlayScenarioResetRegistry.Snapshot _resetSnapshot;
        private bool _queryBudgetExceeded;
        private string _lastObservation;
        private string _lastObservationCode;
        private string _lastObservationExpected;
        private string _lastObservationActual;
        private bool _lastObservationReady;
        private bool _hasObservation;

        private PlayScenarioObservation EvaluateCounted(PlayScenarioStep step, bool firstPoll, PlayScenarioStepResult result)
        {
            if (step.Action == "reset_state")
            {
                if (firstPoll)
                {
                    _resetSnapshot = PlayScenarioResetRegistry.Resolve(step.ResetIds);
                    _resetSnapshot.Begin();
                }
                if (_resetSnapshot == null)
                    throw new PlayScenarioException(
                        Failure("reset_participant_unavailable", "A started reset cannot be resumed or replayed after losing its snapshot.")
                    );
                bool complete = _resetSnapshot.Complete();
                return new PlayScenarioObservation(
                    complete,
                    complete ? "All requested reset participants completed." : "Waiting for requested reset participants."
                );
            }
            var queryHost = _host as IPlayScenarioQueryHost;
            if (queryHost == null && State.Scenario.QueryBudget?.Enabled == true)
                throw new PlayScenarioException(Failure("capability_unavailable", "Enabled query budgets require a host with actual query instrumentation."));
            PlayScenarioQueryCounts before = queryHost?.CaptureQueryCounts() ?? default;
            try
            {
                return _host.Evaluate(step, firstPoll);
            }
            finally
            {
                if (queryHost != null)
                {
                    PlayScenarioQueryCounts after = queryHost.CaptureQueryCounts();
                    long searches = Delta(before.TargetSearches, after.TargetSearches);
                    long visits = Delta(before.HierarchyVisits, after.HierarchyVisits);
                    State.QueryCounts.TargetSearches = Add(State.QueryCounts.TargetSearches, searches);
                    State.QueryCounts.HierarchyVisits = Add(State.QueryCounts.HierarchyVisits, visits);
                    result.QueryCounts.TargetSearches = Add(result.QueryCounts.TargetSearches, searches);
                    result.QueryCounts.HierarchyVisits = Add(result.QueryCounts.HierarchyVisits, visits);
                }
            }
        }

        private static long Delta(long before, long after) => before >= 0 && after >= before ? after - before : 0;

        private static long Add(long value, long increment) => value > long.MaxValue - increment ? long.MaxValue : value + increment;

        /// <summary>A first overflow requests failure once; subsequent cleanup evaluations remain executable.</summary>
        private bool ObserveQueryBudget(long now)
        {
            PlayScenarioQueryBudgetOptions budget = State.Scenario.QueryBudget;
            if (
                _queryBudgetExceeded
                || budget?.Enabled != true
                || (State.QueryCounts.TargetSearches <= budget.MaxTargetSearches && State.QueryCounts.HierarchyVisits <= budget.MaxHierarchyVisits)
            )
                return false;
            _queryBudgetExceeded = true;
            var failure = new PlayScenarioFailure
            {
                Code = "query_budget_exceeded",
                Expected = "target_searches <= " + budget.MaxTargetSearches + "; hierarchy_visits <= " + budget.MaxHierarchyVisits,
                Actual = "target_searches=" + State.QueryCounts.TargetSearches + "; hierarchy_visits=" + State.QueryCounts.HierarchyVisits,
                Message = "Scenario evaluation exceeded its configured query budget.",
            };
            Timeline(now, "query_budget_exceeded", failure.Actual);
            if (!Running)
                return false;
            if (State.Phase == "cleaning")
            {
                AddCleanupFailure(failure);
                AppendCleanupError(failure.Message);
                if (State.PendingStatus == null)
                    RequestOutcome("failed", failure.Message, now, failure);
                return false;
            }
            RequestOutcome("failed", failure.Message, now, failure);
            return true;
        }

        private void ObserveTimeline(long now, PlayScenarioStepResult result, PlayScenarioObservation observation)
        {
            if (State.Scenario.Diagnostics?.RecordTimeline != true)
                return;
            string detail = Bounded(observation.Detail, 512);
            string code = Bounded(observation.Failure?.Code, 64);
            string expected = Bounded(observation.Failure?.Expected, 512);
            string actual = Bounded(observation.Failure?.Actual, 512);
            if (
                _hasObservation
                && _lastObservationReady == observation.Ready
                && _lastObservation == detail
                && _lastObservationCode == code
                && _lastObservationExpected == expected
                && _lastObservationActual == actual
            )
                return;
            _hasObservation = true;
            _lastObservation = detail;
            _lastObservationCode = code;
            _lastObservationExpected = expected;
            _lastObservationActual = actual;
            _lastObservationReady = observation.Ready;
            Timeline(now, "observation_changed", (observation.Ready ? "ready: " : "waiting: ") + detail, result);
        }

        private void Timeline(long now, string eventName, string detail) =>
            Timeline(now, eventName, detail, State.Cursor >= 0 && State.Cursor < State.Steps.Count ? State.Steps[State.Cursor] : null);

        private void Timeline(long now, string eventName, string detail, PlayScenarioStepResult result) =>
            RecordTimeline(State, now, result, eventName, detail);

        private static void RecordTimeline(PlayScenarioRun run, long now, PlayScenarioStepResult result, string eventName, string detail)
        {
            if (run.Scenario.Diagnostics?.RecordTimeline != true)
                return;
            long sequence = run.Timeline.Count == 0 ? 1 : Add(run.Timeline[run.Timeline.Count - 1].Sequence, 1);
            if (run.Timeline.Count == 128)
            {
                run.Timeline.RemoveAt(0);
                if (run.DroppedTimelineCount < int.MaxValue)
                    run.DroppedTimelineCount++;
            }
            run.Timeline.Add(
                new PlayScenarioTimelineEvent
                {
                    Sequence = sequence,
                    TimestampUnixMs = now,
                    Stage = result?.Stage ?? "run",
                    Iteration = result?.Iteration ?? 0,
                    StepIndex = result?.StepIndex ?? -1,
                    Event = Bounded(eventName, 64),
                    Detail = Bounded(detail, 512),
                }
            );
        }

        private static void CaptureRetainedResources(
            PlayScenarioResourceCheck check,
            PlayScenarioRegisteredResources snapshot,
            PlayScenarioRegisteredResources baseline
        )
        {
            var newKinds = new Dictionary<long, string>();
            foreach (long id in snapshot.ScriptableObjectIds.Except(baseline.ScriptableObjectIds))
                newKinds.Add(id, "scriptable_object");
            foreach (long id in snapshot.SubscriptionIds.Except(baseline.SubscriptionIds))
                newKinds.Add(id, "subscription");
            foreach (long id in snapshot.HandleIds.Except(baseline.HandleIds))
                newKinds.Add(id, "handle");
            var descriptions = snapshot.ResourceDetails.ToDictionary(info => info.Id);
            foreach (var pair in newKinds.OrderBy(pair => pair.Key))
            {
                if (check.RetainedResources.Count == 32)
                {
                    check.OmittedResourceCount++;
                    continue;
                }
                descriptions.TryGetValue(pair.Key, out PlayScenarioRegisteredResourceInfo info);
                check.RetainedResources.Add(
                    new PlayScenarioRetainedResource
                    {
                        Id = pair.Key,
                        Kind = pair.Value,
                        Owner = Bounded(info?.Owner, 128),
                        TypeName = Bounded(info?.TypeName, 256),
                        ResourceName = Bounded(info?.ResourceName, 128),
                        SourceFile = Bounded(info?.SourceFile, 128),
                        SourceMember = Bounded(info?.SourceMember, 128),
                        SourceLine = info?.SourceLine ?? 0,
                    }
                );
            }
        }
    }
}
