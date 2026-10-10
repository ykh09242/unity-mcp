using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    internal sealed class PlayScenarioSuiteState
    {
        [JsonProperty("report")]
        public PlayScenarioSuiteReport Report;

        [JsonProperty("definitions")]
        public List<PlayScenarioDefinition> Definitions = new List<PlayScenarioDefinition>();

        [JsonProperty("repeat_count")]
        public int RepeatCount;

        [JsonProperty("deadline_unix_ms")]
        public long DeadlineUnixMs;

        [JsonProperty("cursor")]
        public int Cursor;

        [JsonProperty("requested_status")]
        public string RequestedStatus;

        [JsonProperty("first_failure_status")]
        public string FirstFailureStatus;

        [JsonProperty("cancel_sent")]
        public bool CancelSent;

        [JsonProperty("finalization_deadline_unix_ms")]
        public long? FinalizationDeadlineUnixMs;
    }

    /// <summary>Sequential value-only coordinator; child execution remains owned by the ordinary runner.</summary>
    internal sealed class PlayScenarioSuiteRunner
    {
        internal readonly PlayScenarioSuiteState State;
        private readonly Func<PlayScenarioDefinition, int, int, string, string, JObject> start;
        private readonly Func<string, JObject> observe;
        private readonly Func<string, JObject> cancel;
        private readonly Action checkpoint;
        private readonly Func<bool> childBusy;
        internal bool Running => State.Report.Status == "running";
        internal PlayScenarioSuiteChild Current => State.Cursor < State.Report.Scenarios.Count ? State.Report.Scenarios[State.Cursor] : null;

        internal PlayScenarioSuiteRunner(
            PlayScenarioSuiteState state,
            Func<PlayScenarioDefinition, int, int, string, string, JObject> start,
            Func<string, JObject> observe,
            Func<string, JObject> cancel,
            Action checkpoint,
            Func<bool> childBusy
        )
        {
            if (state?.Definitions == null || state.Report?.Scenarios == null || state.Definitions.Count != state.Report.Scenarios.Count)
                throw new InvalidDataException("Frozen suite queue does not match its report.");
            for (int index = 0; index < state.Definitions.Count; index++)
                if (
                    state.Definitions[index].Name != state.Report.Scenarios[index].Name
                    || PlayScenarioReproduction.Hash(state.Definitions[index]) != state.Report.Scenarios[index].DefinitionHash
                )
                    throw new InvalidDataException("Frozen suite definition does not match its admission hash.");
            State = state;
            this.start = start;
            this.observe = observe;
            this.cancel = cancel;
            this.checkpoint = checkpoint;
            this.childBusy = childBusy;
        }

        internal static PlayScenarioSuiteState Create(
            PlayScenarioSuiteDefinition suite,
            IReadOnlyList<PlayScenarioDefinition> selected,
            int repeats,
            int timeout,
            string revision,
            long now,
            string suiteId = null
        )
        {
            if (repeats < 1 || repeats > 10 || timeout < 1 || timeout > 1800)
                throw new ArgumentException("Suite repeats/timeout exceed their bounds.");
            PlayScenarioService.ValidateSourceRevision(revision);
            if (suiteId != null)
                PlayScenarioSuiteStore.ValidateId(suiteId);
            if (selected == null || selected.Count < 1 || selected.Count > 16)
                throw new ArgumentException("Suite selection must contain 1-16 scenarios.");
            var state = new PlayScenarioSuiteState
            {
                Report = new PlayScenarioSuiteReport
                {
                    SuiteId = suiteId ?? Guid.NewGuid().ToString("N"),
                    RepeatCount = repeats,
                    TimeoutSeconds = timeout,
                    Suite = PlayScenarioSuiteDefinition.Parse(JObject.FromObject(suite)),
                    StartedUnixMs = now,
                    SourceRevision = revision,
                },
                RepeatCount = repeats,
                DeadlineUnixMs = checked(now + timeout * 1000L),
            };
            foreach (PlayScenarioDefinition definition in selected)
            {
                PlayScenarioDefinition frozen = PlayScenarioDefinition.Parse(JObject.FromObject(definition));
                if (state.Definitions.Any(item => item.Name == frozen.Name))
                    throw new ArgumentException("Resolved suite scenarios must be unique.");
                state.Definitions.Add(frozen);
                state.Report.Scenarios.Add(
                    new PlayScenarioSuiteChild
                    {
                        Name = frozen.Name,
                        JobId = Guid.NewGuid().ToString("N"),
                        DefinitionHash = PlayScenarioReproduction.Hash(frozen),
                    }
                );
            }
            if (Encoding.UTF8.GetByteCount(JArray.FromObject(state.Definitions).ToString(Formatting.None)) > 1024 * 1024)
                throw new ArgumentException("Frozen suite definitions exceed the snapshot limit.");
            return state;
        }

        internal void RequestStop(string outcome, string error, long now)
        {
            if (!Running || State.RequestedStatus != null)
                return;
            State.RequestedStatus = State.FirstFailureStatus != null && State.Report.Suite.FailurePolicy == "continue" ? "failed" : outcome;
            State.Report.Error = State.Report.Error ?? Bounded(error);
            State.FinalizationDeadlineUnixMs = checked(
                now + (State.Cursor < State.Definitions.Count ? State.Definitions[State.Cursor].CleanupTimeoutSeconds : 0) * 1000L + 60000
            );
            checkpoint();
        }

        internal void Tick(long now, bool canStart)
        {
            if (!Running)
                return;
            if (now >= State.DeadlineUnixMs)
                RequestStop("timed_out", "Suite execution budget expired.", now);
            PlayScenarioSuiteChild child = Current;
            if (child?.Status == "running")
            {
                if (State.RequestedStatus != null && !State.CancelSent)
                {
                    State.CancelSent = true;
                    checkpoint();
                    try
                    {
                        cancel(child.JobId);
                    }
                    catch (Exception exception)
                    {
                        State.Report.Error = State.Report.Error ?? Bounded("Child cancellation failed: " + exception.Message);
                    }
                }
                JObject snapshot;
                try
                {
                    snapshot = observe(child.JobId);
                }
                catch (Exception exception)
                {
                    RequestStop("failed", "Could not observe child finalization: " + exception.Message, now);
                    if (childBusy() || now < State.FinalizationDeadlineUnixMs.Value)
                        return;
                    child.Status = "failed";
                    child.SkipReason = "Child finalization could not be observed; no effects were replayed.";
                    State.Cursor++;
                    Finish(now);
                    return;
                }
                string status = (string)snapshot["status"];
                if (status == "running" || snapshot.Value<bool?>("runner_resources_released") == false)
                    return;
                if (!new[] { "succeeded", "failed", "timed_out", "cancelled" }.Contains(status, StringComparer.Ordinal))
                    throw new InvalidOperationException("Child returned an invalid terminal status.");
                child.Status = status;
                child.Report = (JObject)snapshot.DeepClone();
                if (!string.IsNullOrEmpty((string)snapshot["report_error"]) || string.IsNullOrEmpty((string)snapshot["report_path"]))
                {
                    State.Report.ReportError = Bounded("Child report was not persisted: " + ((string)snapshot["report_error"] ?? child.JobId));
                    State.Report.Error = State.Report.Error ?? State.Report.ReportError;
                    RequestStop("failed", State.Report.ReportError, now);
                }
                if (Encoding.UTF8.GetByteCount(JObject.FromObject(State.Report).ToString(Formatting.None)) > PlayScenarioSuiteStore.ReportLimit - 65536)
                {
                    child.Report = null;
                    State.Report.ReportError = "Suite embedded child reports exceed the 2 MiB aggregate limit; the child report remains referenced by job_id.";
                    State.Report.Error = State.Report.Error ?? State.Report.ReportError;
                    RequestStop("failed", State.Report.ReportError, now);
                }
                if (status != "succeeded")
                {
                    State.FirstFailureStatus = State.FirstFailureStatus ?? status;
                    State.Report.Error = State.Report.Error ?? Bounded((string)snapshot["error"] ?? "Child " + child.Name + " " + status + ".");
                    if (State.Report.Suite.FailurePolicy == "stop")
                        RequestStop(status, State.Report.Error, now);
                }
                State.Cursor++;
                State.CancelSent = false;
                checkpoint();
                if (State.RequestedStatus != null || State.Cursor == State.Report.Scenarios.Count)
                    Finish(now);
                return;
            }
            if (State.RequestedStatus != null || child == null)
            {
                Finish(now);
                return;
            }
            if (!canStart)
                return;
            child.Status = "running";
            // Commit the reservation and exact frozen queue before the first possible Play-entry reload.
            checkpoint();
            try
            {
                int remaining = (int)Math.Max(1, (State.DeadlineUnixMs - now + 999) / 1000);
                start(State.Definitions[State.Cursor], State.RepeatCount, remaining, child.JobId, State.Report.SourceRevision);
            }
            catch (Exception exception)
            {
                child.Status = "failed";
                State.FirstFailureStatus = State.FirstFailureStatus ?? "failed";
                child.SkipReason = Bounded("Child admission failed: " + exception.Message);
                State.Report.Error = State.Report.Error ?? child.SkipReason;
                State.Cursor++;
                if (State.Report.Suite.FailurePolicy == "stop")
                    RequestStop("failed", child.SkipReason, now);
                checkpoint();
                if (State.RequestedStatus != null || State.Cursor == State.Report.Scenarios.Count)
                    Finish(now);
            }
        }

        private void Finish(long now)
        {
            foreach (PlayScenarioSuiteChild pending in State.Report.Scenarios.Where(child => child.Status == "pending"))
            {
                pending.Status = "skipped";
                pending.SkipReason = State.Report.Error ?? "Suite stopped before this child was admitted.";
            }
            State.Report.Status =
                State.Report.ReportError != null
                    ? "failed"
                    : State.RequestedStatus ?? (State.Report.Scenarios.All(child => child.Status == "succeeded") ? "succeeded" : "failed");
            State.Report.FinishedUnixMs = now;
            checkpoint();
        }

        private static string Bounded(string value) => value?.Length > 4096 ? value.Substring(0, 4096) : value;
    }
}
