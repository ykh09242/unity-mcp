using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Builds bounded observations independently of the outcome of the preceding mutation.</summary>
    internal static class PostEditVerification
    {
        internal const int MaxProperties = 64;
        internal const int MaxFindings = 32;

        internal static JObject Summary(string scope, JToken observedValues, JArray findings = null, bool complete = true)
        {
            findings ??= new JArray();
            bool truncated = findings.Count > MaxFindings;
            var retained = new JArray();
            for (int i = 0; i < findings.Count && i < MaxFindings; i++)
                retained.Add(findings[i].DeepClone());
            complete &= !truncated;
            return new JObject
            {
                ["status"] =
                    findings.Count > 0 ? "issues_found"
                    : complete ? "passed"
                    : "incomplete",
                ["scope"] = scope,
                ["complete"] = complete,
                ["observed_values"] = observedValues ?? new JArray(),
                ["findings"] = retained,
                ["findings_truncated"] = truncated,
                ["further_fixes_required"] =
                    findings.Count > 0 ? new JValue(true)
                    : complete ? new JValue(false)
                    : JValue.CreateNull(),
            };
        }

        internal static JObject Unavailable(string scope, string reason)
        {
            JObject report = Summary(scope, new JArray(), complete: false);
            report["status"] = "unavailable";
            report["reason"] = reason;
            return report;
        }

        internal static JObject Readback(Object target, IEnumerable<string> propertyPaths) =>
            SerializedObjectInspection.ReadRetainedProperties(target, propertyPaths);

        internal static JObject Properties(Object target, JObject expected) =>
            CompareProperties(target, expected?.Properties().Select(property => new KeyValuePair<string, JToken>(property.Name, property.Value)));

        internal static JObject CompareProperties(Object target, IEnumerable<KeyValuePair<string, JToken>> requests, bool enumNumbersAreIndices = false)
        {
            var expected = new Dictionary<string, JToken>(StringComparer.Ordinal);
            int requestedCount = 0;
            foreach (var request in (requests ?? Enumerable.Empty<KeyValuePair<string, JToken>>()).Take(MaxProperties + 1))
            {
                requestedCount++;
                expected[request.Key] = request.Value;
            }
            JObject report = Readback(target, expected.Keys);
            if ((string)report["status"] == "unavailable")
                return report;
            var findings = (JArray)report["findings"];
            bool complete = (bool)report["complete"] && requestedCount <= MaxProperties;
            foreach (JObject record in ((JArray)report["observed_values"]).OfType<JObject>())
            {
                string name = (string)record["requested_property"];
                if (!expected.ContainsKey(name) || (string)record["value_status"] != "available")
                    continue;
                bool? matches;
                try
                {
                    matches = Matches(expected[name], record, enumNumbersAreIndices);
                }
                catch (Exception)
                {
                    matches = null;
                }
                record["matches_requested"] = matches.HasValue ? new JValue(matches.Value) : JValue.CreateNull();
                if (!matches.HasValue)
                    complete = false;
                else if (!matches.Value && findings.Count < MaxFindings)
                    findings.Add(new JObject { ["code"] = "retained_value_differs", ["property_path"] = record["property_path"] });
            }
            JObject summary = Summary((string)report["scope"], report["observed_values"], findings, complete);
            foreach (string key in new[] { "status", "complete", "findings", "findings_truncated", "further_fixes_required" })
                report[key] = summary[key];
            return report;
        }

        private static bool? Matches(JToken expected, JObject record, bool enumNumbersAreIndices)
        {
            if ((bool?)record["value_truncated"] == true)
                return null;
            JToken actual = record["value"];
            if (
                actual is JContainer value
                && value.Descendants().OfType<JProperty>().Any(field => field.Name == "value_status" && (string)field.Value == "nonfinite")
            )
                return null;
            if ((string)record["property_type"] == "ObjectReference")
            {
                string id = (string)actual?["raw_id"];
                if (expected.Type == JTokenType.Null)
                    return id == "0";
                if ((string)actual?["id_kind"] != "instance_id_raw32")
                    return null;
                JToken requestedId = expected is JObject reference ? reference["instanceID"] ?? reference["instance_id"] : expected;
                return requestedId?.Type == JTokenType.Integer ? id == requestedId.ToString() : (bool?)null;
            }
            if ((string)record["property_type"] == "Enum")
            {
                if (expected.Type == JTokenType.String)
                    return actual?["name"]?.Type == JTokenType.String
                        ? string.Equals((string)expected, (string)actual["name"], StringComparison.OrdinalIgnoreCase)
                        : (bool?)null;
                actual = actual?[enumNumbersAreIndices ? "index" : "raw32"];
            }
            if (actual is JArray values)
            {
                JArray requested = expected as JArray;
                if (expected is JObject vector)
                {
                    string[] fields = (string)record["property_type"] == "Color" ? new[] { "r", "g", "b", "a" } : new[] { "x", "y", "z", "w" };
                    requested = new JArray(fields.Take(values.Count).Select(field => vector[field]));
                    if (fields.Take(values.Count).Any(field => vector[field] == null))
                        return null;
                }
                if (requested == null || requested.Count != values.Count)
                    return null;
                bool? result = true;
                for (int index = 0; index < values.Count; index++)
                {
                    bool? equal = ScalarMatches(requested[index], values[index]);
                    if (equal == false)
                        return false;
                    if (!equal.HasValue)
                        result = null;
                }
                return result;
            }
            return actual is JValue && expected is JValue ? ScalarMatches(expected, actual) : (bool?)null;
        }

        private static bool? ScalarMatches(JToken expected, JToken actual)
        {
            bool numericExpected = expected.Type == JTokenType.Integer || expected.Type == JTokenType.Float;
            bool numericActual = actual.Type == JTokenType.Integer || actual.Type == JTokenType.Float;
            if (numericExpected && numericActual)
            {
                if (expected.Type == JTokenType.Integer && actual.Type == JTokenType.Integer)
                    return expected.ToString() == actual.ToString();
                double first = expected.Value<double>(),
                    second = actual.Value<double>();
                return Math.Abs(first - second) <= 0.000001 * Math.Max(1, Math.Abs(first));
            }
            return expected.Type == actual.Type ? JToken.DeepEquals(expected, actual) : (bool?)null;
        }

        internal static JObject Removal(Object original, int originalId) =>
            Summary(
                "original_component_removal",
                new JObject { ["component_instance_id"] = originalId, ["removed"] = original == null },
                original == null ? null : new JArray(new JObject { ["code"] = "original_component_retained" })
            );

        internal sealed class Batch
        {
            private readonly List<(Component target, int id, IEnumerable<KeyValuePair<string, JToken>> expected, bool removed)> entries = new();
            private bool truncated;

            internal void Capture(Component target, JObject expected = null, bool removed = false) =>
                CaptureProperties(target, expected?.Properties().Select(property => new KeyValuePair<string, JToken>(property.Name, property.Value)), removed);

            internal void CaptureProperties(Component target, IEnumerable<KeyValuePair<string, JToken>> expected, bool removed = false)
            {
                if (entries.Count >= 16)
                {
                    truncated = true;
                    return;
                }
                entries.Add((target, target != null ? target.GetInstanceIDCompat() : 0, expected, removed));
            }

            internal JObject Observe()
            {
                var reports = new JArray();
                var findings = new JArray();
                bool complete = !truncated;
                int bytes = 2;
                foreach (var entry in entries)
                {
                    JObject report = entry.removed ? Removal(entry.target, entry.id) : CompareProperties(entry.target, entry.expected);
                    report["original_instance_id"] = entry.id;
                    int size = Encoding.UTF8.GetByteCount(report.ToString(Newtonsoft.Json.Formatting.None));
                    if (bytes + size > 16384)
                    {
                        complete = false;
                        break;
                    }
                    bytes += size;
                    reports.Add(report);
                    complete &= (bool)report["complete"];
                    foreach (JToken finding in (JArray)report["findings"])
                        if (findings.Count < MaxFindings)
                            findings.Add(finding.DeepClone());
                        else
                            complete = false;
                }
                return Summary("exact_touched_components; max16_components_16KiB", reports, findings, complete);
            }
        }

        internal static List<Component> Components(GameObject target, out bool complete)
        {
            using var serialized = new SerializedObject(target);
            serialized.UpdateIfRequiredOrScript();
            SerializedProperty components = serialized.FindProperty("m_Component");
            var result = new List<Component>();
            complete = components != null && components.isArray && components.arraySize <= 16;
            if (components == null || !components.isArray)
                return result;
            for (int index = 0; index < Math.Min(16, components.arraySize); index++)
                result.Add(components.GetArrayElementAtIndex(index).FindPropertyRelative("component")?.objectReferenceValue as Component);
            return result;
        }

        internal static object Attach(object response, JObject verification)
        {
            if (response is SuccessResponse success)
            {
                JObject data = success.Data == null ? new JObject() : JObject.FromObject(success.Data);
                data["verification"] = verification;
                return new SuccessResponse(success.Message, data);
            }
            if (response is ErrorResponse error)
            {
                JObject data = error.Data == null ? new JObject() : JObject.FromObject(error.Data);
                data["verification"] = verification;
                return new ErrorResponse(error.Error, data);
            }
            return response;
        }
    }
}
