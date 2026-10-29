using System;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Navigation
{
    // Contains only declarative session metadata. Native ownership is fenced before entry,
    // so a reload can resume a reservation but can never repeat an uncertain invocation.
    internal static class NavigationBakeCheckpoint
    {
        internal const int MaxJobs = 32;
        internal const int MaxBytes = 262144;

        internal static JArray Restore(string raw)
        {
            try
            {
                if (raw == null || raw.Length > MaxBytes)
                    throw new JsonException("Navigation checkpoint exceeds its bound.");
                using var reader = new JsonTextReader(new StringReader(raw)) { DateParseHandling = DateParseHandling.None, MaxDepth = 32 };
                JArray jobs = JArray.Load(reader);
                if (reader.Read())
                    throw new JsonException("Unexpected trailing navigation checkpoint content.");
                if (
                    jobs.Count > MaxJobs
                    || jobs.Any(j => !(j is JObject))
                    || jobs.Select(j => (string)j["job_id"]).Any(string.IsNullOrEmpty)
                    || jobs.Select(j => (string)j["job_id"]).Distinct().Count() != jobs.Count
                )
                    throw new JsonException("Invalid navigation checkpoint entries.");
                foreach (JObject job in jobs)
                {
                    if ((string)job["state"] != "queued" && (string)job["state"] != "running")
                        continue;
                    if ((string)job["state"] == "queued" && ValidReservation(job))
                        job["restored"] = true;
                    else
                    {
                        job["state"] = "interrupted";
                        job["detail"] =
                            job["resume_refusal"]?.ToString()
                            ?? "Native completion or safe unstarted target identity is unconfirmed; no bake resumed. Inspect surface data before another bake.";
                    }
                }
                return jobs;
            }
            catch (Exception ex)
                when (ex is JsonException || ex is ArgumentException || ex is InvalidCastException || ex is FormatException || ex is OverflowException)
            {
                return new JArray(
                    new JObject
                    {
                        ["job_id"] = "recovery",
                        ["state"] = "unavailable",
                        ["detail"] = "Bake job metadata was corrupt or exceeded limits; no work resumed.",
                    }
                );
            }
        }

        private static bool ValidReservation(JObject job) =>
            job["native_invoked"]?.Type == JTokenType.Boolean
            && (bool)job["native_invoked"] == false
            && DateTime.TryParseExact((string)job["deadline_utc"], "O", CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out _)
            && job["checkpoint"] is JObject checkpoint
            && checkpoint["version"]?.Type == JTokenType.Integer
            && (int?)checkpoint["version"] == 1
            && new[] { "target", "scene_guid", "scene_hash", "configuration_hash", "backend" }.All(k =>
                checkpoint[k]?.Type == JTokenType.String && !string.IsNullOrEmpty((string)checkpoint[k]) && ((string)checkpoint[k]).Length <= 4096
            );

        internal static bool Advance(JObject job, DateTime now, Action validate, Action invoke, Func<string> blocker, Action save)
        {
            if ((string)job["state"] != "queued")
                return false;
            try
            {
                if (job["native_invoked"]?.Type != JTokenType.Boolean || (bool)job["native_invoked"])
                    throw new InvalidOperationException("Native invocation is uncertain; no bake resumed.");
                if (
                    !DateTime.TryParse((string)job["deadline_utc"], CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out DateTime deadline)
                    || now >= deadline.ToUniversalTime()
                )
                    throw new InvalidOperationException("Navigation queue deadline expired before native entry; no bake performed.");
                string reason = blocker();
                if (reason != null)
                {
                    job["detail"] = "Waiting before native bake: " + reason;
                    save();
                    return false;
                }
                validate();
                if ((string)job["state"] != "queued")
                    return false;
                // Validation/asset resolution may expose a newly admitted foreign owner.
                reason = blocker();
                if (reason != null)
                {
                    job["detail"] = "Waiting before native bake: " + reason;
                    save();
                    return false;
                }
                if (DateTime.UtcNow >= deadline.ToUniversalTime())
                    throw new InvalidOperationException("Navigation queue deadline expired during validation; no bake performed.");
                job["state"] = "running";
                job["native_invoked"] = true;
                job["started_utc"] = now.ToString("O");
                save();
                invoke();
                job["state"] = "completed";
                job["finished_utc"] = DateTime.UtcNow.ToString("O");
                save();
                return true;
            }
            catch (Exception ex)
            {
                job["state"] = "failed";
                job["detail"] = ex.InnerException?.Message ?? ex.Message;
                job["finished_utc"] = DateTime.UtcNow.ToString("O");
                save();
                return false;
            }
        }
    }
}
