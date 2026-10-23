using System;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Windows
{
    internal static class McpDiagnosticBundle
    {
        internal const int Versions = 1,
            Connection = 2,
            Capabilities = 4,
            Graph = 8,
            Operation = 16,
            All = 31;

        private static JToken Count(JToken token)
        {
            if (token?.Type != JTokenType.Integer)
                return JValue.CreateNull();
            try
            {
                long value = token.Value<long>();
                return value >= 0 ? new JValue(value) : JValue.CreateNull();
            }
            catch (OverflowException)
            {
                return JValue.CreateNull();
            }
            catch (InvalidCastException)
            {
                return JValue.CreateNull();
            }
        }

        private static JToken UnixSeconds(JToken token)
        {
            if (token?.Type == JTokenType.Integer)
                return Count(token);
            if (token?.Type != JTokenType.Float)
                return JValue.CreateNull();
            double value = token.Value<double>();
            return !double.IsNaN(value) && !double.IsInfinity(value) && value > 0 && value <= 253402300799d ? new JValue((long)value) : JValue.CreateNull();
        }

        private static JToken Boolean(JToken token) => token?.Type == JTokenType.Boolean ? new JValue(token.Value<bool>()) : JValue.CreateNull();

        private static string Allowed(JToken token, params string[] values)
        {
            string value = token?.Type == JTokenType.String ? token.Value<string>() : null;
            return value != null && values.Contains(value, StringComparer.Ordinal) ? value : "unknown";
        }

        private static string Version(JToken token, bool unity)
        {
            string value = token?.Type == JTokenType.String ? token.Value<string>() : null;
            string pattern = unity
                ? @"\A[0-9]{1,8}\.[0-9]{1,8}\.[0-9]{1,8}[abfp][0-9]{1,8}\z"
                : @"\A[0-9]{1,8}\.[0-9]{1,8}\.[0-9]{1,8}(-(alpha|beta|rc)(\.[0-9]{1,8})?)?\z";
            return value != null && value.Length <= 64 && Regex.IsMatch(value, pattern) ? value : "unknown";
        }

        internal static JObject Build(JObject snapshot, int categories, DateTimeOffset now)
        {
            snapshot ??= new JObject();
            categories &= All;
            var result = new JObject { ["schema_version"] = 1, ["created_at"] = now.ToUniversalTime().ToString("O") };
            if ((categories & Versions) != 0)
            {
                var source = snapshot["versions"] as JObject;
                result["versions"] = new JObject { ["unity"] = Version(source?["unity"], true), ["package"] = Version(source?["package"], false) };
            }
            if ((categories & Connection) != 0)
            {
                var source = snapshot["connection"] as JObject;
                result["connection"] = new JObject
                {
                    ["available"] = source != null,
                    ["selected_transport"] = Allowed(source?["selected_transport"], "http", "stdio"),
                    ["selected_scope"] = Allowed(source?["selected_scope"], "local", "remote", "unspecified"),
                    ["existing_client_connected"] = Boolean(source?["existing_client_connected"]),
                    ["observation"] = "existing_client_only",
                };
            }
            if ((categories & Capabilities) != 0)
            {
                var source = snapshot["capabilities"] as JObject;
                result["capabilities"] = new JObject
                {
                    ["tools"] = Catalog(source?["tools"] as JObject, true),
                    ["resources"] = Catalog(source?["resources"] as JObject, false),
                };
            }
            if ((categories & Graph) != 0)
                result["graph"] = GraphSummary(snapshot["graph"] as JObject) ?? new JObject { ["available"] = false };
            if ((categories & Operation) != 0)
                result["operation"] = OperationSummary(snapshot["operation"] as JObject) ?? new JObject { ["available"] = false };
            return result;
        }

        private static JObject Catalog(JObject source, bool tools)
        {
            var result = new JObject
            {
                ["available"] = source != null,
                ["known"] = Count(source?["known"]),
                ["enabled"] = Count(source?["enabled"]),
                ["selection"] = "saved_local",
            };
            if (!tools)
            {
                result["server_confirmation"] = "unavailable";
                return result;
            }
            var application = source?["application"] as JObject;
            result["application"] = new JObject
            {
                ["available"] = application != null,
                ["stage"] = Allowed(
                    application?["stage"],
                    "saved",
                    "applied",
                    "pending",
                    "sending",
                    "awaiting_ack",
                    "rejected",
                    "disconnected",
                    "preparation_failed",
                    "send_failed",
                    "unconfirmed"
                ),
                ["desired_revision"] = Count(application?["desired_revision"]),
                ["confirmed_revision"] = Count(application?["confirmed_revision"]),
            };
            return result;
        }

        internal static JObject GraphSummary(JObject graph)
        {
            if (graph == null)
                return null;
            var result = new JObject
            {
                ["available"] = true,
                ["revision"] = Count(graph["revision"]),
                ["ready"] = Count(graph["ready"]),
                ["pending"] = Count(graph["pending"]),
                ["errors"] = Count(graph["errors"]),
                ["native_current"] = Boolean(graph["native_current"]),
                ["coverage"] = Allowed(graph["coverage"], "partial_static", "partial_runtime", "partial_static_and_runtime"),
                ["provider_count"] = Count(graph["provider_count"]),
                ["providers_failed"] = Count(graph["providers_failed"]),
                ["oldest_verification_unix_seconds"] = Count(graph["oldest_verification_unix_seconds"]),
            };
            if (graph["providers"] is JObject providers)
            {
                int count = 0,
                    failed = 0;
                long? oldest = null;
                foreach (var property in providers.Properties())
                {
                    count++;
                    if (property.Value is not JObject provider)
                        continue;
                    if (Allowed(provider["state"], "failed") == "failed")
                        failed++;
                    var verified = UnixSeconds(provider["last_verified_at"]);
                    if (verified.Type == JTokenType.Integer)
                    {
                        long value = verified.Value<long>();
                        if (value > 0 && (!oldest.HasValue || value < oldest))
                            oldest = value;
                    }
                }
                result["provider_count"] = count;
                result["providers_failed"] = failed;
                result["oldest_verification_unix_seconds"] = oldest;
            }
            return result;
        }

        private static JObject OperationSummary(JObject row)
        {
            if (row == null)
                return null;
            string rawId = row["job_id"]?.Type == JTokenType.String ? row.Value<string>("job_id") : null;
            return new JObject
            {
                ["available"] = true,
                ["owner"] = Allowed(row["owner"], "test", "build", "batch", "scenario", "package", "generation"),
                ["job_id"] = Guid.TryParse(rawId, out var id) ? id.ToString("N") : null,
                ["state"] = Allowed(
                    row["state"],
                    "queued",
                    "pending",
                    "running",
                    "starting",
                    "preparing",
                    "importing",
                    "cancelling",
                    "succeeded",
                    "failed",
                    "error",
                    "cancelled",
                    "canceled",
                    "unknown",
                    "interrupted",
                    "timed_out",
                    "completed",
                    "rejected",
                    "aborted"
                ),
                ["active"] = Boolean(row["active"]),
                ["can_cancel"] = Boolean(row["can_cancel"]),
                ["evidence_available"] = Boolean(row["evidence_available"]),
            };
        }

        internal static JObject OperationSummary(OperationActivityRow row) =>
            row == null
                ? null
                : OperationSummary(
                    new JObject
                    {
                        ["owner"] = row.Owner,
                        ["job_id"] = row.Id,
                        ["state"] = row.State,
                        ["active"] = row.Active,
                        ["can_cancel"] = row.CanCancel,
                        ["evidence_available"] = row.EvidenceId != null,
                    }
                );
    }
}
