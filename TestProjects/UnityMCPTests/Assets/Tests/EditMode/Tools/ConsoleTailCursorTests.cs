using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class ConsoleTailCursorTests
    {
        [Test]
        public void TailHasDistinctBackendCommandWithoutChangingNumericCursorRoute()
        {
            // Given the real native command registry.
            CommandRegistry.Initialize();
            // When the opt-in route is resolved.
            // Then old peers cannot silently serve a full scan as a tail.
            Assert.IsTrue(CommandRegistry.HasHandler("tail_console"));
            Assert.IsTrue(CommandRegistry.HasHandler("read_console"));
        }

        private sealed class Snapshot : IConsoleTailSnapshot
        {
            internal readonly List<ConsoleTailRow> Rows;
            internal int Unavailable = -1;
            public int Count => Rows.Count;
            public int EntryReads { get; private set; }

            internal Snapshot(List<ConsoleTailRow> rows)
            {
                Rows = rows;
            }

            public ConsoleTailRow Read(int index)
            {
                EntryReads++;
                return index == Unavailable ? null : Rows[index];
            }

            public void Dispose() { }
        }

        private static List<ConsoleTailRow> Rows(int count, string text = "Synthetic") =>
            Enumerable
                .Range(0, count)
                .Select(i => new ConsoleTailRow
                {
                    GlobalIndex = i + 1,
                    Message = text,
                    StackStart = 0,
                })
                .ToList();

        private static ConsoleTailQuery Query(string token = "begin", string filter = "", int page = 50) =>
            ConsoleTailQuery.Parse(
                new JObject
                {
                    ["tail_cursor"] = token,
                    ["filterText"] = filter,
                    ["pageSize"] = page,
                }
            );

        private static JObject Read(ConsoleTailCursor state, List<ConsoleTailRow> rows, string token = "begin", string filter = "", int page = 50) =>
            JObject.FromObject(state.Read(Query(token, filter, page), new Snapshot(rows)));

        private static string Token(JObject response) => (string)response["data"]["next_cursor"];

        [TestCase(false)]
        [TestCase(true)]
        public void TailSourceDiagnosticsPreserveExplicitProjection(bool projected)
        {
            const string stack = "Fixture.Method () (at Fixture.cs:7)";
            var row = new ConsoleTailRow
            {
                GlobalIndex = 1,
                Message = "body\n" + stack,
                StackStart = 5,
                File = "Fixture.cs",
                Line = 7,
            };
            var parameters = new JObject { ["tail_cursor"] = "begin", ["includeStacktrace"] = true };
            if (projected)
                parameters["fields"] = new JArray("type", "message", "stackTrace");
            var state = new ConsoleTailCursor();
            var begin = JObject.FromObject(state.Read(ConsoleTailQuery.Parse(parameters), new Snapshot(new List<ConsoleTailRow>())));
            parameters["tail_cursor"] = Token(begin);
            var result = JObject.FromObject(state.Read(ConsoleTailQuery.Parse(parameters), new Snapshot(new List<ConsoleTailRow> { row })));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var entry = (JObject)result["data"]["items"][0]["entry"];
            Assert.AreEqual(stack, (string)entry["stackTrace"]);
            if (projected)
                CollectionAssert.AreEqual(new[] { "type", "message", "stackTrace" }, entry.Properties().Select(p => p.Name));
            else
            {
                Assert.AreEqual("available", (string)entry["diagnostics"]["location"]);
                Assert.AreEqual("available", (string)entry["diagnostics"]["stack_trace"]);
            }
        }

        private static void Reason(JObject response, string reason)
        {
            Assert.IsFalse((bool)response["success"]);
            Assert.AreEqual(reason, (string)response["data"]["reason"]);
            Assert.IsTrue((bool)response["data"]["loss_unknown"]);
        }

        [TestCase(2000)]
        [TestCase(20000)]
        public void ThreeAppendsReadOnlyTwoAnchorsAndThreeNewRows(int prefix)
        {
            // Given an existing prefix and a tail positioned after it.
            var state = new ConsoleTailCursor();
            var rows = Rows(prefix);
            JObject begin = Read(state, rows);
            Assert.AreEqual(0, ((JArray)begin["data"]["items"]).Count);
            rows.AddRange(Rows(3, "Synthetic new"));
            // When three native rows arrive.
            JObject page = Read(state, rows, Token(begin));
            // Then the prefix is not scanned; every new raw sequence appears exactly once.
            Assert.IsTrue((bool)page["success"]);
            Assert.AreEqual(5, (int)page["data"]["native_entry_reads"]);
            CollectionAssert.AreEqual(new long[] { prefix + 1, prefix + 2, prefix + 3 }, page["data"]["items"].Select(t => (long)t["sequence"]));
            Assert.IsTrue((bool)page["data"]["loss_unknown"]);
            Assert.LessOrEqual(state.HashBytes, 24576);
            JObject empty = Read(state, rows, Token(page));
            Assert.AreEqual(0, ((JArray)empty["data"]["items"]).Count);
            Assert.AreEqual(2, (int)empty["data"]["native_entry_reads"]);
        }

        [Test]
        public void SparseFilterConsumesRawBudgetAndContinuesWithoutPrefixRescan()
        {
            // Given an empty tail followed by more than one scan budget of filtered rows.
            var state = new ConsoleTailCursor();
            var rows = Rows(0);
            string token = Token(Read(state, rows, filter: "Match"));
            rows.AddRange(Rows(4100, "Other"));
            rows[4099].Message = "Match";
            // When each bounded page is consumed.
            JObject first = Read(state, rows, token, "Match");
            JObject second = Read(state, rows, Token(first), "Match");
            // Then an empty page advances and only the remainder plus anchors is read.
            Assert.AreEqual(4096, (int)first["data"]["scanned_rows"]);
            Assert.AreEqual(4096, (int)first["data"]["filtered_rows"]);
            Assert.IsTrue((bool)first["data"]["has_more"]);
            Assert.AreEqual(6, (int)second["data"]["native_entry_reads"]);
            Assert.AreEqual(4100, (long)second["data"]["items"][0]["sequence"]);
        }

        [TestCase("owned_clear")]
        [TestCase("reload")]
        [TestCase("row_reset")]
        [TestCase("anchor_changed")]
        [TestCase("entry_unavailable")]
        [TestCase("filter_changed")]
        public void KnownDiscontinuityNeverSilentlyReplays(string cause)
        {
            // Given a valid cursor before a known or observed discontinuity.
            var state = new ConsoleTailCursor();
            var rows = Rows(3);
            string token = Token(Read(state, rows));
            string expected = cause;
            if (cause == "owned_clear")
            {
                state.Invalidate();
                expected = "generation_changed";
            }
            if (cause == "reload")
            {
                state = new ConsoleTailCursor();
                expected = "generation_changed";
            }
            if (cause == "row_reset")
                rows.RemoveAt(2);
            if (cause == "anchor_changed")
                rows[0].Message = "Changed";
            var snapshot = new Snapshot(rows) { Unavailable = cause == "entry_unavailable" ? 0 : -1 };
            // When continuation is attempted.
            JObject result = JObject.FromObject(state.Read(Query(token, cause == "filter_changed" ? "new filter" : ""), snapshot));
            // Then no item page or automatic restart is returned.
            Reason(result, expected);
            Assert.IsNull(result["data"]["items"]);
            Assert.IsTrue((bool)result["data"]["restart_required"]);
        }

        [Test]
        public void IndistinguishableRefillStillDisclosesUnknownLoss()
        {
            // Given a source replacement with identical sampled rows and native indices.
            var state = new ConsoleTailCursor();
            string token = Token(Read(state, Rows(3)));
            // When the native API cannot distinguish the replacement.
            JObject response = Read(state, Rows(3), token);
            // Then matching anchors never certify loss-free history.
            Assert.IsTrue((bool)response["success"]);
            Assert.IsTrue((bool)response["data"]["loss_unknown"]);
            Assert.AreEqual("bounded_anchors", (string)response["data"]["continuity"]);
        }

        [Test]
        public void CursorRetentionIsBoundedAndExpiresWithoutPayloadHistory()
        {
            // Given many clients and an injected monotonic clock.
            double now = 0;
            var state = new ConsoleTailCursor(() => now);
            string oldest = Token(Read(state, Rows(0)));
            string latest = oldest;
            for (int i = 0; i < 70; i++)
                latest = Token(Read(state, Rows(0)));
            // When retention capacity and TTL are exceeded.
            Reason(Read(state, Rows(0), oldest), "expired_or_evicted");
            Assert.AreEqual(64, state.RetainedCount);
            now = 600;
            Reason(Read(state, Rows(0), latest), "expired_or_evicted");
            // Then no historic strings or expired cursor survive.
            Assert.AreEqual(0, state.RetainedCount);
        }

        [Test]
        public void OversizedRowBlocksWithoutSkippingEarlierOrBlockedSequence()
        {
            // Given a cursor before one valid and one oversized raw row.
            var state = new ConsoleTailCursor();
            var rows = Rows(0);
            string token = Token(Read(state, rows));
            rows.AddRange(Rows(2));
            rows[1].Message = new string('x', 65537);
            // When the row limit blocks the page.
            JObject response = Read(state, rows, token);
            // Then no partial success/next cursor hides the omitted item.
            Reason(response, "tail_entry_too_large");
            Assert.AreEqual(2, (long)response["data"]["blocked_sequence"]);
            Assert.IsNull(response["data"]["next_cursor"]);
            rows[1].Message = "Repaired";
            Assert.AreEqual(2, ((JArray)Read(state, rows, token)["data"]["items"]).Count);
        }

        [Test]
        public void PayloadAndUnicodeTruncationStopBeforeUnconsumedRow()
        {
            // Given repeated bounded raw rows whose projected stack/message fill one payload.
            var state = new ConsoleTailCursor();
            var rows = Rows(0);
            string token = Token(Read(state, rows));
            rows.AddRange(Rows(100, string.Concat(Enumerable.Repeat("\U0001F600", 10000))));
            // When a bounded page is materialized.
            JObject page = Read(state, rows, token, page: 200);
            // Then serialization is bounded and the next cursor returns the next raw sequence.
            Assert.IsTrue((bool)page["success"]);
            Assert.LessOrEqual(Encoding.UTF8.GetByteCount(page["data"].ToString(Formatting.None)), 262144);
            var items = (JArray)page["data"]["items"];
            Assert.Greater(items.Count, 0);
            Assert.IsTrue((bool)page["data"]["has_more"]);
            string message = (string)items[0]["entry"]["message"];
            Assert.LessOrEqual(Encoding.UTF8.GetByteCount(message), 8192);
            Assert.IsFalse(char.IsHighSurrogate(message[message.Length - 1]));
            CollectionAssert.Contains((JArray)items[0]["entry"]["truncated_fields"], new JValue("message"));
            JObject next = Read(state, rows, Token(page), page: 200);
            Assert.AreEqual((long)items.Last["sequence"] + 1, (long)next["data"]["items"][0]["sequence"]);
        }

        [Test]
        public void CanonicalViewTreatsTypeOrderAndProjectionOrderAsEquivalent()
        {
            // Given two spellings of the same query and one changed predicate.
            JObject a = new JObject
            {
                ["tail_cursor"] = "begin",
                ["types"] = new JArray("warning", "log", "error"),
                ["fields"] = new JArray("message", "type"),
            };
            JObject b = new JObject
            {
                ["tail_cursor"] = "begin",
                ["types"] = new JArray("all"),
                ["fields"] = new JArray("type", "message"),
            };
            // When identities are computed.
            string first = ConsoleTailQuery.Parse(a).Identity;
            // Then only the actual view change invalidates the cursor.
            Assert.AreEqual(first, ConsoleTailQuery.Parse(b).Identity);
            b["filterText"] = " ";
            Assert.AreNotEqual(first, ConsoleTailQuery.Parse(b).Identity);
        }

        [Test]
        public void TextBudgetAndLargeFileKeepWorkAndHashingBounded()
        {
            // Given messages at the raw bound and an arbitrarily large native file string.
            var state = new ConsoleTailCursor();
            var rows = Rows(0);
            string token = Token(Read(state, rows, filter: "Absent"));
            rows.AddRange(Rows(20, new string('x', 65536)));
            rows[0].File = new string('f', 100000);
            // When a sparse-filter poll reaches its cumulative text bound.
            JObject page = Read(state, rows, token, "Absent");
            // Then one deferred row is counted but not consumed; all text/hash work is bounded.
            Assert.AreEqual(16, (int)page["data"]["scanned_rows"]);
            Assert.AreEqual(17, (int)page["data"]["native_entry_reads"]);
            Assert.AreEqual("text_budget", (string)page["data"]["stop_reason"]);
            Assert.LessOrEqual(state.HashBytes, 24576);
            Assert.IsTrue((bool)page["data"]["has_more"]);
            JObject second = Read(state, rows, Token(page), "Absent");
            Assert.AreEqual(4, (int)second["data"]["scanned_rows"]);
            Assert.AreEqual(6, (int)second["data"]["native_entry_reads"]);
        }

        [Test]
        public void ReusingOldCursorReplaysSameSequenceWhileAdvancingAvoidsDuplicates()
        {
            // Given two identical rows after an empty tail.
            var state = new ConsoleTailCursor();
            var rows = Rows(0);
            string token = Token(Read(state, rows));
            rows.AddRange(Rows(2, "identical"));
            // When a client retries the old token or follows the new one.
            JObject page = Read(state, rows, token);
            JObject replay = Read(state, rows, token);
            JObject advanced = Read(state, rows, Token(page));
            // Then replay identity is explicit and advancing has no duplicates.
            CollectionAssert.AreEqual(page["data"]["items"].Select(i => (long)i["sequence"]), replay["data"]["items"].Select(i => (long)i["sequence"]));
            Assert.AreEqual(2, ((JArray)page["data"]["items"]).Count);
            Assert.AreEqual(0, ((JArray)advanced["data"]["items"]).Count);
        }

        [Test]
        public void PartialPageDetectsRegressionFromPriorSnapshotWatermark()
        {
            // Given a partially consumed snapshot much larger than the next unread row.
            var state = new ConsoleTailCursor();
            var rows = Rows(0);
            string token = Token(Read(state, rows));
            rows.AddRange(Rows(5000));
            JObject first = Read(state, rows, token, page: 1);
            Assert.AreEqual(5000, (int)first["data"]["snapshot_rows"]);
            Assert.AreEqual(1, (long)first["data"]["items"][0]["sequence"]);
            string generation = state.Generation;
            rows.RemoveRange(2, rows.Count - 2); // First-row/last-consumed anchors are unchanged.
            // When the same continuation observes count2 after count5000.
            JObject response = Read(state, rows, Token(first), page: 1);
            // Then observable loss is stale even though Count remains above Next1.
            Reason(response, "row_reset");
            Assert.AreNotEqual(generation, state.Generation);
            Assert.IsNull(response["data"]["items"]);
        }
    }
}
