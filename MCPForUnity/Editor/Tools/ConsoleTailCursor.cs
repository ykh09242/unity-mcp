using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    internal sealed class ConsoleReadMetrics
    {
        internal int EntryReads;
    }

    internal sealed class ConsoleTailRow
    {
        internal long GlobalIndex;
        internal int Mode;
        internal string Message = "";
        internal string File = "";
        internal int Line;
        internal int? StackStart;
    }

    internal interface IConsoleTailSnapshot : IDisposable
    {
        int Count { get; }
        int EntryReads { get; }
        ConsoleTailRow Read(int index);
    }

    internal sealed class ConsoleTailQuery
    {
        internal string Cursor;
        internal string Identity;
        internal string Filter;
        internal int PageSize;
        internal bool IncludeMcp;
        internal bool IncludeStack;
        internal HashSet<string> Types;
        internal HashSet<string> Fields;

        internal static bool ValidCursor(string value) =>
            value != null && value.Length <= 96 && (value == "begin" || Regex.IsMatch(value, "\\Act1\\.[a-f0-9]{32}\\.[a-f0-9]{32}\\z"));

        internal static ConsoleTailQuery Parse(JObject parameters)
        {
            if (parameters == null || parameters["tail_cursor"]?.Type != JTokenType.String || !ValidCursor((string)parameters["tail_cursor"]))
                throw new ArgumentException("invalid_cursor");
            if (
                (parameters["action"] != null && (parameters["action"].Type != JTokenType.String || (string)parameters["action"] != "get"))
                || parameters["count"] != null
                || parameters["cursor"] != null
                || (parameters["format"] != null && (parameters["format"].Type != JTokenType.String || (string)parameters["format"] != "json"))
            )
                throw new ArgumentException("invalid_tail_options");
            var query = new ConsoleTailQuery
            {
                Cursor = (string)parameters["tail_cursor"],
                PageSize = 50,
                Types = new HashSet<string>(new[] { "error", "warning", "log" }, StringComparer.Ordinal),
            };
            if (parameters["pageSize"] != null)
            {
                if (
                    parameters["pageSize"].Type != JTokenType.Integer
                    || !int.TryParse(parameters["pageSize"].ToString(), out query.PageSize)
                    || query.PageSize < 1
                    || query.PageSize > 200
                )
                    throw new ArgumentException("invalid_page_size");
            }
            if (parameters["types"] != null)
            {
                if (!(parameters["types"] is JArray array) || array.Count > 4 || array.Any(t => t.Type != JTokenType.String || ((string)t).Length > 7))
                    throw new ArgumentException("invalid_types");
                query.Types = new HashSet<string>(array.Select(t => ((string)t).ToLowerInvariant()), StringComparer.Ordinal);
                if (query.Types.Any(t => t != "error" && t != "warning" && t != "log" && t != "all"))
                    throw new ArgumentException("invalid_types");
                if (query.Types.Contains("all"))
                    query.Types = new HashSet<string>(new[] { "error", "warning", "log" }, StringComparer.Ordinal);
            }
            if (parameters["filterText"] != null && parameters["filterText"].Type != JTokenType.Null && parameters["filterText"].Type != JTokenType.String)
                throw new ArgumentException("invalid_filter");
            query.Filter = (string)parameters["filterText"] ?? "";
            if (query.Filter.Length > 256)
                throw new ArgumentException("invalid_filter");
            query.IncludeMcp = Boolean(parameters, "includeMcpLogs");
            query.IncludeStack = Boolean(parameters, "includeStacktrace");
            if (parameters["fields"] != null)
            {
                if (
                    !(parameters["fields"] is JArray fields)
                    || fields.Count < 2
                    || fields.Count > 5
                    || fields.Any(t => t.Type != JTokenType.String || ((string)t).Length > 10)
                )
                    throw new ArgumentException("invalid_fields");
                query.Fields = new HashSet<string>(fields.Select(t => (string)t), StringComparer.Ordinal);
                if (
                    query.Fields.Count != fields.Count
                    || !query.Fields.Contains("type")
                    || !query.Fields.Contains("message")
                    || query.Fields.Any(f => f != "type" && f != "message" && f != "file" && f != "line" && f != "stackTrace")
                    || (query.Fields.Contains("stackTrace") && !query.IncludeStack)
                )
                    throw new ArgumentException("invalid_fields");
            }
            var canonical = new JObject
            {
                ["types"] = new JArray(query.Types.OrderBy(t => t, StringComparer.Ordinal)),
                ["filter"] = BitConverter.ToString(Encoding.UTF8.GetBytes(query.Filter)).Replace("-", "").ToLowerInvariant(),
                ["includeMcp"] = query.IncludeMcp,
                ["includeStack"] = query.IncludeStack,
                ["format"] = "json",
                ["fields"] = query.Fields == null ? JValue.CreateNull() : new JArray(query.Fields.OrderBy(f => f, StringComparer.Ordinal)),
            };
            query.Identity = ConsoleTailCursor.Digest(Encoding.UTF8.GetBytes(canonical.ToString(Formatting.None)));
            return query;
        }

        private static bool Boolean(JObject parameters, string name)
        {
            if (parameters[name] == null)
                return false;
            if (parameters[name].Type != JTokenType.Boolean)
                throw new ArgumentException("invalid_boolean");
            return (bool)parameters[name];
        }
    }

    // No console payload history: records retain two digests and bounded scalar metadata only.
    internal sealed class ConsoleTailCursor
    {
        internal const int MaxRows = 4096;
        internal const int MaxPayloadBytes = 262144;

        private sealed class Cursor
        {
            internal string Token;
            internal string Identity;
            internal int Next;
            internal int SnapshotRows;
            internal string First;
            internal string Last;
            internal double Created;
        }

        private readonly List<Cursor> records = new List<Cursor>();
        private readonly Func<double> clock;
        private string generation = Guid.NewGuid().ToString("N");
        internal string Generation => generation;
        internal int RetainedCount => records.Count;
        internal int HashBytes { get; private set; }

        internal ConsoleTailCursor(Func<double> clock = null)
        {
            this.clock = clock ?? (() => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency);
        }

        internal void Invalidate()
        {
            generation = Guid.NewGuid().ToString("N");
            records.Clear();
        }

        internal static string Digest(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private string Anchor(ConsoleTailRow row)
        {
            string text = row.Message ?? "";
            string file = row.File ?? "";
            string sample =
                string.Join(
                    "|",
                    row.GlobalIndex.ToString(CultureInfo.InvariantCulture),
                    row.Mode.ToString(CultureInfo.InvariantCulture),
                    row.Line.ToString(CultureInfo.InvariantCulture),
                    row.StackStart?.ToString(CultureInfo.InvariantCulture) ?? "",
                    text.Length.ToString(CultureInfo.InvariantCulture),
                    file.Length.ToString(CultureInfo.InvariantCulture)
                )
                + "|"
                + text.Substring(0, Math.Min(512, text.Length))
                + "|"
                + text.Substring(Math.Max(0, text.Length - 512))
                + "|"
                + file.Substring(0, Math.Min(512, file.Length));
            byte[] bytes = Encoding.UTF8.GetBytes(sample);
            if (bytes.Length > 8192)
                throw new InvalidOperationException("anchor_bound");
            HashBytes += bytes.Length;
            return Digest(bytes);
        }

        internal object Read(ConsoleTailQuery query, IConsoleTailSnapshot snapshot)
        {
            HashBytes = 0;
            double now = clock();
            records.RemoveAll(record => now - record.Created >= 600);
            Cursor previous = null;
            if (query.Cursor != "begin")
            {
                if (query.Cursor.Substring(4, 32) != generation)
                    return Failure("generation_changed");
                previous = records.Find(record => record.Token == query.Cursor);
                if (previous == null)
                    return Failure("expired_or_evicted");
                if (previous.Identity != query.Identity)
                    return Failure("filter_changed");
                if (snapshot.Count < previous.SnapshotRows)
                    return Reset("row_reset");
            }

            int next = previous?.Next ?? snapshot.Count;
            string first = previous?.First;
            string last = previous?.Last;
            if (next > 0)
            {
                ConsoleTailRow firstRow = snapshot.Read(0);
                ConsoleTailRow lastRow = next == 1 ? firstRow : snapshot.Read(next - 1);
                if (firstRow == null || lastRow == null)
                    return Reset("entry_unavailable");
                string firstHash = Anchor(firstRow);
                string lastHash = next == 1 ? firstHash : Anchor(lastRow);
                if (previous != null && (firstHash != first || lastHash != last))
                    return Reset("anchor_changed");
                first = firstHash;
                last = lastHash;
            }

            var items = new JArray();
            int scanned = 0;
            int filtered = 0;
            int textUnits = 0;
            string stop = "snapshot_end";
            ConsoleTailRow finalRow = null;
            while (next < snapshot.Count && query.Cursor != "begin")
            {
                if (items.Count >= query.PageSize)
                {
                    stop = "page_size";
                    break;
                }
                if (scanned >= MaxRows)
                {
                    stop = "row_budget";
                    break;
                }
                ConsoleTailRow row = snapshot.Read(next);
                if (row == null)
                    return Reset("entry_unavailable");
                if ((row.Message?.Length ?? 0) > 65536)
                    return Failure("tail_entry_too_large", (long)next + 1);
                if (textUnits + (row.Message?.Length ?? 0) > 1048576)
                {
                    stop = "text_budget";
                    break;
                }
                JObject item = null;
                LogType kind = ReadConsole.GetLogTypeFromMode(row.Mode);
                string type = kind == LogType.Assert || kind == LogType.Exception ? "error" : kind.ToString().ToLowerInvariant();
                bool matches =
                    !string.IsNullOrEmpty(row.Message)
                    && query.Types.Contains(type)
                    && (query.IncludeMcp || !ReadConsole.IsMcpLogMessage(row.Message))
                    && (query.Filter.Length == 0 || row.Message.IndexOf(query.Filter, StringComparison.OrdinalIgnoreCase) >= 0);
                if (matches)
                {
                    item = new JObject { ["sequence"] = (long)next + 1, ["entry"] = Project(row, kind, query) };
                    items.Add(item);
                    // Reserve the bounded envelope/token/counters; calculate escaped UTF-8 payload bytes.
                    if (Encoding.UTF8.GetByteCount(items.ToString(Formatting.None)) > MaxPayloadBytes - 2048)
                    {
                        items.RemoveAt(items.Count - 1);
                        stop = "payload_budget";
                        break;
                    }
                }
                else
                    filtered++;
                if (next == 0)
                    first = Anchor(row);
                finalRow = row;
                scanned++;
                textUnits += row.Message?.Length ?? 0;
                next++;
            }
            if (finalRow != null)
                last = next == 1 ? first : Anchor(finalRow);
            string token = "ct1." + generation + "." + Guid.NewGuid().ToString("N");
            records.Add(
                new Cursor
                {
                    Token = token,
                    Identity = query.Identity,
                    Next = next,
                    SnapshotRows = snapshot.Count,
                    First = first,
                    Last = last,
                    Created = now,
                }
            );
            if (records.Count > 64)
                records.RemoveAt(0);
            var data = new JObject
            {
                ["version"] = 1,
                ["generation"] = generation,
                ["filter_identity"] = query.Identity,
                ["items"] = items,
                ["next_cursor"] = token,
                ["snapshot_rows"] = snapshot.Count,
                ["scanned_rows"] = scanned,
                ["filtered_rows"] = filtered,
                ["native_entry_reads"] = snapshot.EntryReads,
                ["has_more"] = next < snapshot.Count,
                ["stop_reason"] = stop,
                ["loss_unknown"] = true,
                ["continuity"] = "bounded_anchors",
            };
            return new SuccessResponse("Bounded console tail; loss cannot be ruled out.", data);
        }

        private object Reset(string reason)
        {
            Invalidate();
            return Failure(reason);
        }

        internal static object Failure(string reason, long? blockedSequence = null) =>
            new ErrorResponse(
                "Console tail could not establish this continuation.",
                new
                {
                    reason,
                    restart_required = reason != "tail_entry_too_large",
                    loss_unknown = true,
                    blocked_sequence = blockedSequence,
                }
            );

        private static JObject Project(ConsoleTailRow row, LogType kind, ConsoleTailQuery query)
        {
            var split = ReadConsole.SplitMessageAndStackTrace(row.Message, row.StackStart);
            var truncated = new JArray();
            var entry = new JObject { ["type"] = kind.ToString(), ["message"] = Limit(split.body, 8192, "message", truncated) };
            if (query.Fields == null || query.Fields.Contains("file"))
                entry["file"] = Limit(row.File, 1024, "file", truncated);
            if (query.Fields == null || query.Fields.Contains("line"))
                entry["line"] = row.Line;
            if (query.Fields == null || query.Fields.Contains("stackTrace"))
                entry["stackTrace"] = query.IncludeStack ? Limit(split.stackTrace, 16384, "stackTrace", truncated) : null;
            if (truncated.Count > 0)
                entry["truncated_fields"] = truncated;
            return entry;
        }

        private static string Limit(string value, int bytes, string field, JArray truncated)
        {
            if (value == null)
                return null;
            if (value.Length <= bytes && Encoding.UTF8.GetByteCount(value) <= bytes)
                return value;
            int low = 0;
            int high = Math.Min(value.Length, bytes);
            while (low < high)
            {
                int mid = (low + high + 1) / 2;
                if (Encoding.UTF8.GetByteCount(value.Substring(0, mid)) <= bytes)
                    low = mid;
                else
                    high = mid - 1;
            }
            if (low > 0 && char.IsHighSurrogate(value[low - 1]))
                low--;
            truncated.Add(field);
            return value.Substring(0, low);
        }
    }
}
