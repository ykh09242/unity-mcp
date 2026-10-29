using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services
{
    // Session receipts retain identity and observed outcomes, never native Request handles.
    internal static class PackageQueryJobStore
    {
        private const string SessionKey = "MCPForUnity.PackageQueryJobsV1";
        private const int MaxPending = 64;
        private const int MaxHistory = 10;
        private const int MaxResultBytes = 128 * 1024;
        private const int MaxStorageBytes = 2 * 1024 * 1024;
        private static readonly Dictionary<string, JObject> Receipts = new();
        private static long sequence;
        internal static Func<string> SessionReader = () => SessionState.GetString(SessionKey, string.Empty);
        internal static Action<string> SessionWriter = value => SessionState.SetString(SessionKey, value);
        internal static Func<long> Clock = () => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        static PackageQueryJobStore() => Restore();

        internal static string Begin(string operation)
        {
            if (
                operation is not ("list_packages" or "search_packages")
                || Receipts.Values.Count(r => (string)r["data"]?["status"] is "running" or "unknown") >= MaxPending
            )
                return null;
            string id = Guid.NewGuid().ToString("N");
            Receipts[id] = new JObject
            {
                ["success"] = true,
                ["message"] = "Package query admitted.",
                ["data"] = new JObject
                {
                    ["job_id"] = id,
                    ["operation"] = operation,
                    ["status"] = "running",
                    ["started_unix_ms"] = Clock(),
                },
            };
            if (Persist())
                return id;
            Receipts.Remove(id);
            return null;
        }

        internal static JObject Get(string id) => Receipts.TryGetValue(id, out var receipt) ? Project(receipt) : null;

        private static JObject Project(JObject receipt)
        {
            var result = (JObject)receipt.DeepClone();
            (result["data"] as JObject)?.Remove("retention_sequence");
            return result;
        }

        internal static JObject Complete(string id, string operation, JObject response)
        {
            var result = (JObject)response.DeepClone();
            var data = result["data"] as JObject ?? new JObject();
            data["job_id"] = id;
            data["operation"] = operation;
            data["status"] = (bool?)result["success"] == true ? "succeeded" : "failed";
            data["started_unix_ms"] = Receipts.TryGetValue(id, out var previous) ? previous["data"]?["started_unix_ms"]?.DeepClone() : new JValue(Clock());
            data["finished_unix_ms"] = Clock();
            data["retention_sequence"] = ++sequence;
            data["result_availability"] = "available";
            result["data"] = data;
            // Discard oversized native payloads before scanning their strings for credentials.
            bool oversized = Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) > MaxResultBytes;
            if (!oversized)
                result = (JObject)PackageSourceSecurity.RedactResponse(result);
            if (oversized || Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) > MaxResultBytes)
            {
                // Completion is proved even when its result exceeds bounded retention.
                result = new JObject
                {
                    ["success"] = result["success"],
                    ["message"] = "Package query completed; its result exceeded the retention limit.",
                    ["data"] = new JObject
                    {
                        ["job_id"] = id,
                        ["operation"] = operation,
                        ["status"] = data["status"],
                        ["started_unix_ms"] = data["started_unix_ms"],
                        ["finished_unix_ms"] = data["finished_unix_ms"],
                        ["result_availability"] = "size_limit",
                        ["retention_sequence"] = data["retention_sequence"],
                    },
                };
                result = (JObject)PackageSourceSecurity.RedactResponse(result);
            }
            Receipts[id] = result;
            Prune();
            Persist();
            return Project(result);
        }

        private static void Prune()
        {
            foreach (
                string id in Receipts
                    .Where(p => (string)p.Value["data"]?["status"] is not ("running" or "unknown"))
                    .OrderByDescending(p => (long?)p.Value["data"]?["retention_sequence"] ?? 0)
                    .Skip(MaxHistory)
                    .Select(p => p.Key)
                    .ToArray()
            )
                Receipts.Remove(id);
        }

        internal static bool Persist()
        {
            try
            {
                string text = new JObject { ["version"] = 1, ["jobs"] = new JArray(Receipts.Values) }.ToString(Formatting.None);
                if (Encoding.UTF8.GetByteCount(text) > MaxStorageBytes)
                    return false;
                SessionWriter(text);
                return true;
            }
            catch
            {
                return false;
            }
        }

        internal static void Restore()
        {
            try
            {
                string text = SessionReader();
                if (string.IsNullOrEmpty(text) || text.Length > MaxStorageBytes || Encoding.UTF8.GetByteCount(text) > MaxStorageBytes)
                    return;
                JObject state;
                using (var reader = new JsonTextReader(new System.IO.StringReader(text)) { MaxDepth = 32, DateParseHandling = DateParseHandling.None })
                {
                    state = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                    if (reader.Read())
                        return;
                }
                if ((int?)state["version"] != 1 || state["jobs"] is not JArray jobs || jobs.Count > MaxPending + MaxHistory)
                    return;
                var restored = new Dictionary<string, JObject>();
                foreach (var token in jobs)
                {
                    if (token is not JObject receipt || receipt["success"]?.Type != JTokenType.Boolean || receipt["data"] is not JObject data)
                        return;
                    string id = (string)data["job_id"],
                        operation = (string)data["operation"],
                        status = (string)data["status"];
                    if (
                        data["job_id"]?.Type != JTokenType.String
                        || data["operation"]?.Type != JTokenType.String
                        || data["status"]?.Type != JTokenType.String
                        || string.IsNullOrWhiteSpace(id)
                        || id.Length > 128
                        || operation is not ("list_packages" or "search_packages")
                        || status is not ("running" or "unknown" or "succeeded" or "failed")
                        || data["started_unix_ms"]?.Type != JTokenType.Integer
                        || (long)data["started_unix_ms"] < 0
                        || restored.ContainsKey(id)
                        || Encoding.UTF8.GetByteCount(receipt.ToString(Formatting.None)) > MaxResultBytes
                    )
                        return;
                    if (status is "succeeded" or "failed")
                    {
                        if (
                            data["finished_unix_ms"]?.Type != JTokenType.Integer
                            || data["retention_sequence"]?.Type != JTokenType.Integer
                            || (long)data["retention_sequence"] < 1
                            || (long)data["retention_sequence"] > 1_000_000_000_000
                            || (status == "succeeded") != (bool)receipt["success"]
                        )
                            return;
                    }
                    if (status == "running" || status == "unknown")
                    {
                        if (Encoding.UTF8.GetByteCount(receipt.ToString(Formatting.None)) > 2048)
                            return;
                        receipt = new JObject
                        {
                            ["success"] = true,
                            ["message"] = "Package query completion is unknown after losing the native request; it was not replayed.",
                            ["data"] = new JObject
                            {
                                ["job_id"] = id,
                                ["operation"] = operation,
                                ["status"] = "unknown",
                                ["started_unix_ms"] = data["started_unix_ms"],
                                ["recovery"] = new JObject
                                {
                                    ["reason"] = "request_lost_after_reload",
                                    ["native_completion"] = "unobserved",
                                    ["retry_safe"] = false,
                                },
                            },
                        };
                    }
                    restored.Add(id, receipt);
                }
                sequence = restored.Values.Select(r => (long?)r["data"]?["retention_sequence"] ?? 0).DefaultIfEmpty(0).Max();
                Receipts.Clear();
                foreach (var pair in restored)
                    Receipts.Add(pair.Key, pair.Value);
                Prune();
            }
            catch
            {
                // Malformed storage is never admission or authority to issue a Client call.
            }
        }
    }
}
