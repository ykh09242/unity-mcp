using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Profiler
{
    internal static class MemorySnapshotComparison
    {
        internal sealed class LimitException : Exception { }

        internal sealed class NativeObject
        {
            internal string Key;
            internal string Id;
            internal string Name;
            internal string Type;
            internal long Bytes;
        }

        internal sealed class Snapshot
        {
            internal uint Format;
            internal uint Session;
            internal string Path;
            internal readonly Dictionary<string, NativeObject> Objects = new Dictionary<string, NativeObject>(StringComparer.Ordinal);
        }

        internal sealed class Result
        {
            internal JArray Types;
            internal JObject Delta;
            internal long BytesA;
            internal long BytesB;
        }

        private sealed class TypeTotals
        {
            internal int CountA;
            internal int CountB;
            internal long BytesA;
            internal long BytesB;
        }

        private sealed class Selection
        {
            private readonly SortedList<string, NativeObject> _rows;
            private readonly int _limit;
            internal int Count;

            internal Selection(int limit)
            {
                _limit = limit;
                _rows = new SortedList<string, NativeObject>(limit, StringComparer.Ordinal);
            }

            internal void Add(NativeObject row)
            {
                Count++;
                if (_rows.Count == _limit)
                {
                    if (StringComparer.Ordinal.Compare(row.Key, _rows.Keys[_limit - 1]) >= 0)
                        return;
                    _rows.RemoveAt(_limit - 1);
                }
                _rows.Add(row.Key, row);
            }

            internal JArray Describe()
            {
                var result = new JArray();
                foreach (NativeObject row in _rows.Values)
                    result.Add(
                        new JObject
                        {
                            ["snapshot_identity"] = row.Key,
                            ["native_id"] = row.Id,
                            ["name"] = row.Name == null || row.Name.Length <= 512 ? row.Name : row.Name.Substring(0, 512),
                            ["type"] = row.Type,
                            ["native_size_bytes"] = row.Bytes,
                        }
                    );
                return result;
            }
        }

        internal static Result Compare(Snapshot a, Snapshot b, int objectLimit)
        {
            if (objectLimit < 1 || objectLimit > 256)
                throw new ArgumentOutOfRangeException(nameof(objectLimit));
            var totals = new Dictionary<string, TypeTotals>(StringComparer.Ordinal);
            TypeTotals GetTotals(string type)
            {
                if (!totals.TryGetValue(type, out var total))
                {
                    if (totals.Count == 4096)
                        throw new LimitException();
                    total = new TypeTotals();
                    totals.Add(type, total);
                }
                return total;
            }

            bool sameSession = a.Session != 0 && a.Session == b.Session && (a.Format >= 18) == (b.Format >= 18);
            var added = sameSession ? new Selection(objectLimit) : null;
            var removed = sameSession ? new Selection(objectLimit) : null;
            var changed = sameSession ? new Selection(objectLimit) : null;
            long bytesA = 0;
            long bytesB = 0;
            foreach (NativeObject row in a.Objects.Values)
            {
                TypeTotals total = GetTotals(row.Type);
                total.CountA++;
                total.BytesA = checked(total.BytesA + row.Bytes);
                bytesA = checked(bytesA + row.Bytes);
                if (sameSession && !b.Objects.ContainsKey(row.Key))
                    removed.Add(row);
            }
            foreach (NativeObject row in b.Objects.Values)
            {
                TypeTotals total = GetTotals(row.Type);
                total.CountB++;
                total.BytesB = checked(total.BytesB + row.Bytes);
                bytesB = checked(bytesB + row.Bytes);
                if (sameSession)
                {
                    if (!a.Objects.TryGetValue(row.Key, out var before))
                        added.Add(row);
                    else if (row.Bytes != before.Bytes || row.Name != before.Name)
                        changed.Add(row);
                }
            }

            var names = new List<string>(totals.Keys);
            names.Sort(StringComparer.Ordinal);
            var types = new JArray();
            foreach (string name in names)
            {
                TypeTotals total = totals[name];
                types.Add(
                    new JObject
                    {
                        ["type"] = name,
                        ["count_a"] = total.CountA,
                        ["count_b"] = total.CountB,
                        ["count_delta"] = total.CountB - total.CountA,
                        ["bytes_a"] = total.BytesA,
                        ["bytes_b"] = total.BytesB,
                        ["bytes_delta"] = checked(total.BytesB - total.BytesA),
                    }
                );
            }
            JObject delta;
            if (sameSession)
                delta = new JObject
                {
                    ["status"] = "same_capture_session",
                    ["identity_basis"] = "native_id_and_address_and_native_type; no_managed_or_cross_session_identity_claim",
                    ["added_count"] = added.Count,
                    ["removed_count"] = removed.Count,
                    ["changed_count"] = changed.Count,
                    ["added"] = added.Describe(),
                    ["removed"] = removed.Describe(),
                    ["changed"] = changed.Describe(),
                    ["objects_truncated"] = added.Count > objectLimit || removed.Count > objectLimit || changed.Count > objectLimit,
                };
            else
                delta = new JObject
                {
                    ["status"] = "unavailable_different_or_unknown_session_or_id_width",
                    ["added_count"] = JValue.CreateNull(),
                    ["removed_count"] = JValue.CreateNull(),
                    ["changed_count"] = JValue.CreateNull(),
                    ["added"] = JValue.CreateNull(),
                    ["removed"] = JValue.CreateNull(),
                    ["changed"] = JValue.CreateNull(),
                };
            return new Result
            {
                Types = types,
                Delta = delta,
                BytesA = bytesA,
                BytesB = bytesB,
            };
        }
    }
}
