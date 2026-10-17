using System;
using System.Collections.Generic;
using System.Text;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Fixed-shape event proof. Sequence lists exist only for ordered matches, never the raw journal.</summary>
    public sealed class PlayScenarioEventEvidence
    {
        [JsonProperty("schema_version")]
        public int SchemaVersion = 1;

        [JsonProperty("generation")]
        public string Generation;

        [JsonProperty("since_sequence")]
        public long SinceSequence;

        [JsonProperty("retained_from_sequence")]
        public long RetainedFromSequence;

        [JsonProperty("observed_through_sequence")]
        public long ObservedThroughSequence;

        [JsonProperty("consumed_through_sequence")]
        public long ConsumedThroughSequence;

        [JsonProperty("match_count")]
        public int MatchCount;

        [JsonProperty("dropped_total")]
        public long DroppedTotal;

        [JsonProperty("overflow")]
        public bool Overflow;

        [JsonProperty("first_matched_sequence", NullValueHandling = NullValueHandling.Ignore)]
        public long? FirstMatchedSequence;

        [JsonProperty("last_matched_sequence", NullValueHandling = NullValueHandling.Ignore)]
        public long? LastMatchedSequence;

        [JsonProperty("matched_sequences", NullValueHandling = NullValueHandling.Ignore)]
        public List<long> MatchedSequences;

        internal void CheckSize()
        {
            if (
                !Guid.TryParseExact(Generation, "N", out _)
                || SchemaVersion != 1
                || SinceSequence < 0
                || RetainedFromSequence < 1
                || ObservedThroughSequence < SinceSequence
                || ConsumedThroughSequence < 0
                || ConsumedThroughSequence > ObservedThroughSequence
                || DroppedTotal < 0
                || MatchCount < 0
                || MatchCount > 128
                || (MatchedSequences != null && MatchedSequences.Count > 16)
                || Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(this, Formatting.None)) > 1024
            )
                throw new InvalidOperationException("Event proof exceeded its fixed bounded shape.");
            if (DroppedTotal != RetainedFromSequence - 1 || DroppedTotal > ObservedThroughSequence || ObservedThroughSequence - DroppedTotal > 128)
                throw new InvalidOperationException("Event ring bounds are inconsistent.");
            if (MatchCount == 0)
            {
                if (FirstMatchedSequence.HasValue || LastMatchedSequence.HasValue)
                    throw new InvalidOperationException("Empty event proof cannot contain matched endpoints.");
            }
            else if (
                !FirstMatchedSequence.HasValue
                || !LastMatchedSequence.HasValue
                || FirstMatchedSequence <= SinceSequence
                || LastMatchedSequence < FirstMatchedSequence
                || LastMatchedSequence > ObservedThroughSequence
                || LastMatchedSequence.Value - FirstMatchedSequence.Value < MatchCount - 1
            )
                throw new InvalidOperationException("Matched event endpoints cannot prove distinct occurrences.");
            long previous = SinceSequence;
            if (MatchedSequences != null)
            {
                if (MatchedSequences.Count != MatchCount)
                    throw new InvalidOperationException("Event order proof has inconsistent positions.");
                foreach (long position in MatchedSequences)
                {
                    if (position <= previous || position > ObservedThroughSequence)
                        throw new InvalidOperationException("Event order positions must be distinct and increasing.");
                    previous = position;
                }
                if (MatchCount > 0 && (FirstMatchedSequence != MatchedSequences[0] || LastMatchedSequence != MatchedSequences[MatchedSequences.Count - 1]))
                    throw new InvalidOperationException("Ordered event proof endpoints are inconsistent.");
            }
        }
    }

    public sealed partial class PlayScenarioEngine
    {
        private sealed class EventFence
        {
            internal readonly PlayScenarioEventRegistry.Binding Binding;
            internal readonly long Sequence;

            internal EventFence(PlayScenarioEventRegistry.Binding binding, long sequence)
            {
                Binding = binding;
                Sequence = sequence;
            }
        }

        private readonly Dictionary<string, EventFence> eventMarks = new Dictionary<string, EventFence>(StringComparer.Ordinal);
        private readonly Dictionary<string, long> eventConsumed = new Dictionary<string, long>(StringComparer.Ordinal);
        private string eventStage = null;
        private int eventIteration;
        private EventFence eventFence = null;
        private long eventSince;
        private long eventScanned;
        private int eventMatches;
        private long? eventFirst = null;
        private long? eventLast = null;
        private List<long> eventPositions = null;
        private string pendingEventMark = null;

        private void ClearEventProbe()
        {
            eventFence = null;
            eventSince = eventScanned = 0;
            eventMatches = 0;
            eventFirst = eventLast = null;
            eventPositions = null;
            pendingEventMark = null;
        }

        private void EventStage(PlayScenarioStepResult result)
        {
            if (eventStage == result.Stage && eventIteration == result.Iteration)
                return;
            eventMarks.Clear();
            eventConsumed.Clear();
            ClearEventProbe();
            eventStage = result.Stage;
            eventIteration = result.Iteration;
        }

        private static PlayScenarioException EventFailure(string code, string target, string message) =>
            new PlayScenarioException(
                new PlayScenarioFailure
                {
                    Code = code,
                    Target = target,
                    Message = message,
                }
            );

        private PlayScenarioObservation EvaluateEvents(PlayScenarioStep step, bool firstPoll, PlayScenarioStepResult result)
        {
            if (step.Action == "mark_events")
            {
                if (!PlayScenarioTarget.IsValidTargetId(step.EventId) || !PlayScenarioEventRegistry.ValidName(step.EventMark))
                    throw new ArgumentException("Invalid event fence identifiers.");
            }
            else
            {
                if (!PlayScenarioEventRegistry.ValidName(step.EventSince))
                    throw new ArgumentException("Invalid event fence reference.");
                if (step.EventOrder == null)
                {
                    if (!step.EventCount.HasValue || step.EventCount < 1 || step.EventCount > 128 || !PlayScenarioEventRegistry.ValidName(step.EventName))
                        throw new ArgumentException("Event count requires one to128 occurrences and a valid name.");
                }
                else
                {
                    if (step.EventOrder.Count < 1 || step.EventOrder.Count > 16)
                        throw new ArgumentException("Event order requires one to16 names.");
                    foreach (string name in step.EventOrder)
                        if (!PlayScenarioEventRegistry.ValidName(name))
                            throw new ArgumentException("Invalid ordered event name.");
                }
            }
            EventStage(result);
            if (step.Action == "mark_events")
            {
                if (firstPoll)
                    pendingEventMark = step.EventMark;
                else if (pendingEventMark != step.EventMark)
                    throw EventFailure("event_history_unavailable", step.EventId, "A started fence lost its transient ownership state.");
                if (!firstPoll && eventMarks.ContainsKey(step.EventMark))
                    throw EventFailure("event_history_unavailable", step.EventId, "An event fence cannot be replayed after partial completion.");
                PlayScenarioEventRegistry.Binding binding = PlayScenarioEventRegistry.Bind(step.EventId);
                if (binding == null)
                    return EventWaiting(step.EventId, "Waiting for an explicitly registered event stream.");
                if (eventMarks.Count >= 16 || eventMarks.ContainsKey(step.EventMark))
                    throw EventFailure("event_history_unavailable", step.EventId, "Event fence capacity or identity is invalid.");
                PlayScenarioEventRegistry.Page mark = binding.Capture();
                eventMarks.Add(step.EventMark, new EventFence(binding, mark.Head));
                // Every distinct consumed generation must correspond to one of at most sixteen marks.
                if (!eventConsumed.ContainsKey(binding.Generation))
                    eventConsumed.Add(binding.Generation, 0);
                result.EventEvidence = Proof(mark, mark.Head, 0, 0, null, null, null);
                return new PlayScenarioObservation(true, "Captured event fence at sequence " + mark.Head + ".");
            }
            if (firstPoll)
            {
                if (!eventMarks.TryGetValue(step.EventSince, out eventFence))
                    throw EventFailure(
                        "event_history_unavailable",
                        step.EventSince,
                        "The original stage's transient event fence is unavailable; it cannot be reconstructed or replayed."
                    );
                eventSince = Math.Max(eventFence.Sequence, eventConsumed[eventFence.Binding.Generation]);
                eventScanned = eventSince;
                eventMatches = 0;
                eventFirst = eventLast = null;
                eventPositions = step.EventOrder == null ? null : new List<long>(16);
            }
            if (eventFence == null)
                throw EventFailure("event_history_unavailable", step.EventSince, "A started event wait lost its transient accumulator.");
            PlayScenarioEventRegistry.Page page = eventFence.Binding.Read(eventScanned);
            if (page.Overflow)
            {
                result.EventEvidence = Proof(page, eventSince, eventConsumed[page.Generation], eventMatches, eventFirst, eventLast, eventPositions);
                throw EventFailure(
                    "event_journal_overflow",
                    step.EventSince,
                    "An unobserved sequence gap invalidates the event proof; oldest="
                        + page.Floor
                        + ", last_scanned="
                        + eventScanned
                        + ", head="
                        + page.Head
                        + ", dropped="
                        + page.Dropped
                        + "."
                );
            }
            int required = step.EventOrder?.Count ?? step.EventCount.Value;
            foreach (PlayScenarioEventRegistry.Record record in page.Records)
            {
                if (eventMatches >= required)
                    break;
                string expected = step.EventOrder == null ? step.EventName : step.EventOrder[eventMatches];
                if (!string.Equals(record.Name, expected, StringComparison.Ordinal))
                    continue;
                eventMatches++;
                if (!eventFirst.HasValue)
                    eventFirst = record.Sequence;
                eventLast = record.Sequence;
                eventPositions?.Add(record.Sequence);
            }
            eventScanned = page.Head;
            bool complete = eventMatches >= required;
            if (complete)
                eventConsumed[page.Generation] = page.Head;
            result.EventEvidence = Proof(page, eventSince, eventConsumed[page.Generation], eventMatches, eventFirst, eventLast, eventPositions);
            return complete
                ? new PlayScenarioObservation(true, "Event evidence matched distinct retained occurrences.")
                : EventWaiting(step.EventSince, "Waiting for event evidence: matched " + eventMatches + " of " + required + ".");
        }

        private static PlayScenarioObservation EventWaiting(string target, string message) =>
            new PlayScenarioObservation(
                false,
                message,
                new PlayScenarioFailure
                {
                    Code = "condition_unmet",
                    Target = target,
                    Expected = "the authored bounded event evidence",
                    Actual = message,
                    Message = message,
                }
            );

        private static PlayScenarioEventEvidence Proof(
            PlayScenarioEventRegistry.Page page,
            long since,
            long consumed,
            int matches,
            long? first,
            long? last,
            List<long> positions
        )
        {
            var proof = new PlayScenarioEventEvidence
            {
                Generation = page.Generation,
                SinceSequence = since,
                RetainedFromSequence = page.Floor,
                ObservedThroughSequence = page.Head,
                ConsumedThroughSequence = consumed,
                MatchCount = matches,
                DroppedTotal = page.Dropped,
                Overflow = page.Overflow,
                FirstMatchedSequence = first,
                LastMatchedSequence = last,
                MatchedSequences = positions == null ? null : new List<long>(positions),
            };
            proof.CheckSize();
            return proof;
        }
    }
}
