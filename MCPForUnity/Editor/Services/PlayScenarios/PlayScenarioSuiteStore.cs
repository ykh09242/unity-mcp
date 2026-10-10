using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Bounded saved suite definitions and reports in checked project-local paths.</summary>
    public sealed class PlayScenarioSuiteStore
    {
        private const string Definitions = "ProjectSettings/MCPForUnity/PlayScenarioSuites";
        internal const string Reports = "Library/MCPForUnity/PlayScenarioSuiteRuns";
        internal const int ReportLimit = 2 * 1024 * 1024;
        private const int DefinitionLimit = 64 * 1024;
        private static readonly object Gate = new object();
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private readonly string root;

        public PlayScenarioSuiteStore(string projectRoot)
        {
            if (string.IsNullOrWhiteSpace(projectRoot))
                throw new ArgumentException("Project root is required.");
            root = Path.GetFullPath(projectRoot);
            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), root);
        }

        public void Save(PlayScenarioSuiteDefinition definition)
        {
            if (definition == null)
                throw new ArgumentNullException(nameof(definition));
            PlayScenarioSuiteDefinition validated = PlayScenarioSuiteDefinition.Parse(JObject.FromObject(definition));
            byte[] bytes = Encode(JObject.FromObject(validated), DefinitionLimit);
            lock (Gate)
            {
                string path = DefinitionPath(validated.Name);
                string[] saved = Files(Definitions, 100);
                if (!File.Exists(path) && saved.Length >= 100)
                    throw new InvalidOperationException("At most 100 suites may be saved.");
                Write(path, bytes);
            }
        }

        public PlayScenarioSuiteDefinition Get(string name)
        {
            lock (Gate)
            {
                PlayScenarioSuiteDefinition suite = PlayScenarioSuiteDefinition.Parse(Read(DefinitionPath(name), DefinitionLimit));
                if (suite.Name != name)
                    throw new InvalidDataException("Stored suite name does not match its filename.");
                return suite;
            }
        }

        public IReadOnlyList<string> List()
        {
            lock (Gate)
            {
                var names = Files(Definitions, 100).Select(Path.GetFileNameWithoutExtension).ToList();
                foreach (string name in names)
                    PlayScenarioDefinition.ValidateName(name);
                names.Sort(StringComparer.Ordinal);
                return names.AsReadOnly();
            }
        }

        public bool Delete(string name)
        {
            lock (Gate)
            {
                string path = DefinitionPath(name);
                if (!File.Exists(path))
                    return false;
                File.Delete(Checked(path));
                return true;
            }
        }

        public string SaveReport(PlayScenarioSuiteReport report)
        {
            ValidateId(report?.SuiteId);
            string relative = Reports + "/" + report.SuiteId + ".json";
            report.ReportPath = relative;
            JObject value = JObject.FromObject(report);
            ValidateReport(value);
            byte[] bytes = Encode(value, ReportLimit);
            lock (Gate)
            {
                Write(Checked(relative), bytes);
                foreach (
                    string file in Files(Reports, 21)
                        .Where(file => Path.GetFileNameWithoutExtension(file) != report.SuiteId)
                        .OrderByDescending(File.GetLastWriteTimeUtc)
                        .ThenBy(file => file, StringComparer.Ordinal)
                        .Skip(19)
                )
                    File.Delete(Checked(file));
            }
            return relative;
        }

        public PlayScenarioSuiteReport GetReport(string suiteId)
        {
            ValidateId(suiteId);
            lock (Gate)
            {
                JObject value = Read(Checked(Reports + "/" + suiteId + ".json"), ReportLimit);
                ValidateReport(value);
                if ((string)value["suite_id"] != suiteId)
                    throw new InvalidDataException("Stored suite_id does not match its filename.");
                return value.ToObject<PlayScenarioSuiteReport>(Serializer());
            }
        }

        public IReadOnlyList<PlayScenarioSuiteReport> ListReports(string name = null)
        {
            if (name != null)
                PlayScenarioDefinition.ValidateName(name);
            lock (Gate)
                return Files(Reports, 20)
                    .Select(file => GetReport(Path.GetFileNameWithoutExtension(file)))
                    .Where(report => report.Status != "running" && (name == null || report.Suite.Name == name))
                    .OrderByDescending(report => report.FinishedUnixMs ?? report.StartedUnixMs)
                    .ThenBy(report => report.SuiteId, StringComparer.Ordinal)
                    .ToList()
                    .AsReadOnly();
        }

        internal static JsonSerializer Serializer() =>
            new JsonSerializer { TypeNameHandling = TypeNameHandling.None, DateParseHandling = DateParseHandling.None };

        internal static void ValidateId(string id)
        {
            if (id == null || !Regex.IsMatch(id, @"\A[a-f0-9]{32}\z"))
                throw new ArgumentException("suite_id/job_id must be 32 lowercase hexadecimal characters.");
        }

        internal static void ValidateReport(JObject value)
        {
            PlayScenarioDefinition.Fields(
                value,
                "suite_id",
                "suite",
                "status",
                "repeat_count",
                "timeout_seconds",
                "started_unix_ms",
                "finished_unix_ms",
                "scenarios",
                "error",
                "report_path",
                "report_error",
                "source_revision"
            );
            ValidateId(RequiredText(value, "suite_id", 32));
            PlayScenarioSuiteDefinition suite = PlayScenarioSuiteDefinition.Parse(value["suite"] as JObject);
            State(RequiredText(value, "status", 16), "running", "succeeded", "failed", "timed_out", "cancelled");
            if (
                PlayScenarioDefinition.Integer(value, "repeat_count", 1, 10, 0) == 0
                || PlayScenarioDefinition.Integer(value, "timeout_seconds", 1, 1800, 0) == 0
            )
                throw new ArgumentException("Suite report requires original repeat_count and timeout_seconds.");
            Timestamp(value, "started_unix_ms", false);
            Timestamp(value, "finished_unix_ms", true);
            OptionalText(value, "error", 4096);
            OptionalText(value, "report_error", 4096);
            PlayScenarioService.ValidateSourceRevision(OptionalText(value, "source_revision", 128));
            string path = OptionalText(value, "report_path", 256);
            if (path != null && path != Reports + "/" + (string)value["suite_id"] + ".json")
                throw new ArgumentException("Suite report path does not belong to its suite.");
            if (!(value["scenarios"] is JArray entries) || entries.Count < 1 || entries.Count > 16)
                throw new ArgumentException("Suite report must contain 1-16 child entries.");
            var names = new HashSet<string>(StringComparer.Ordinal);
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (JToken token in entries)
            {
                if (!(token is JObject child))
                    throw new ArgumentException("Suite child must be an object.");
                PlayScenarioDefinition.Fields(child, "name", "status", "definition_hash", "job_id", "report", "skip_reason");
                string name = RequiredText(child, "name", 64);
                PlayScenarioDefinition.ValidateName(name);
                string id = RequiredText(child, "job_id", 32);
                ValidateId(id);
                if (!names.Add(name) || !ids.Add(id))
                    throw new ArgumentException("Suite children require unique names and job IDs.");
                string hash = RequiredText(child, "definition_hash", 64);
                if (!Regex.IsMatch(hash, @"\A[a-f0-9]{64}\z"))
                    throw new ArgumentException("Child definition_hash must be 64 lowercase hexadecimal characters.");
                string status = RequiredText(child, "status", 16);
                State(status, "pending", "running", "succeeded", "failed", "timed_out", "cancelled", "skipped");
                OptionalText(child, "skip_reason", 4096);
                if (child["report"] is JObject report)
                {
                    PlayScenarioStore.ValidateReport(report);
                    if ((string)report["status"] == "running" || report.Value<bool?>("runner_resources_released") == false)
                        throw new ArgumentException("Embedded child reports must be finalized.");
                    if (
                        (string)report["job_id"] != id
                        || (string)report["scenario"]?["name"] != name
                        || (string)report["status"] != status
                        || (string)report["reproduction"]?["definition_hash"] != hash
                    )
                        throw new ArgumentException("Embedded child report does not match its suite entry.");
                }
                else if (child["report"] == null || child["report"].Type != JTokenType.Null)
                    throw new ArgumentException("Child report must be an object or null.");
            }
            if (
                (string)value["status"] != "running"
                && (
                    value["finished_unix_ms"].Type == JTokenType.Null
                    || entries.Any(child => (string)child["status"] == "running" || (string)child["status"] == "pending")
                )
            )
                throw new ArgumentException("Terminal suites require finalized children and a finished timestamp.");
            if (
                (string)value["status"] == "succeeded"
                && (entries.Any(child => (string)child["status"] != "succeeded") || value["report_error"].Type != JTokenType.Null)
            )
                throw new ArgumentException("Succeeded suites cannot contain unsuccessful children or persistence errors.");
        }

        private static void Timestamp(JObject value, string key, bool nullable)
        {
            JToken token = value[key];
            if (nullable && token?.Type == JTokenType.Null)
                return;
            if (token?.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out long number) || number < 0)
                throw new ArgumentException(key + " must be a nonnegative timestamp.");
        }

        private static string RequiredText(JObject value, string key, int maximum) => PlayScenarioDefinition.Text(value, key, maximum);

        private static string OptionalText(JObject value, string key, int maximum)
        {
            JToken token = value[key];
            if (token?.Type == JTokenType.Null)
                return null;
            if (token?.Type != JTokenType.String || ((string)token).Length > maximum)
                throw new ArgumentException(key + " must be a bounded string or null.");
            return (string)token;
        }

        private static void State(string value, params string[] allowed)
        {
            if (!allowed.Contains(value, StringComparer.Ordinal))
                throw new ArgumentException("Invalid suite report status.");
        }

        private string DefinitionPath(string name)
        {
            PlayScenarioDefinition.ValidateName(name);
            return Checked(Definitions + "/" + name + ".json");
        }

        private string Checked(string path) => SafePathUtility.ResolveWithinRoot(root, path);

        private string[] Files(string directory, int maximum)
        {
            string path = Checked(directory);
            if (!Directory.Exists(path))
                return Array.Empty<string>();
            var files = new List<string>();
            foreach (string file in Directory.EnumerateFiles(path, "*.json", SearchOption.TopDirectoryOnly))
            {
                files.Add(Checked(file));
                if (files.Count > maximum)
                    throw new InvalidDataException("Stored suite file count exceeds its limit.");
            }
            return files.ToArray();
        }

        private void Write(string path, byte[] bytes)
        {
            string directory = Checked(Path.GetDirectoryName(path));
            Directory.CreateDirectory(directory);
            Checked(directory);
            string temporary = Checked(Path.Combine(directory, "." + Guid.NewGuid().ToString("N") + ".tmp"));
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush();
                }
                Checked(path);
                Checked(temporary);
                if (File.Exists(path))
                    File.Replace(temporary, path, null);
                else
                    File.Move(temporary, path);
            }
            finally
            {
                if (File.Exists(temporary))
                    File.Delete(Checked(temporary));
            }
        }

        private JObject Read(string path, int maximum)
        {
            using (var stream = new FileStream(Checked(path), FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var output = new MemoryStream())
            {
                if (stream.Length > maximum)
                    throw new InvalidDataException("Stored suite JSON exceeds its byte limit.");
                var buffer = new byte[8192];
                int count;
                while ((count = stream.Read(buffer, 0, Math.Min(buffer.Length, maximum - (int)output.Length + 1))) != 0)
                {
                    if (output.Length + count > maximum)
                        throw new InvalidDataException("Stored suite JSON exceeds its byte limit.");
                    output.Write(buffer, 0, count);
                }
                using (
                    var reader = new JsonTextReader(new StringReader(Utf8.GetString(output.GetBuffer(), 0, (int)output.Length)))
                    {
                        DateParseHandling = DateParseHandling.None,
                        MaxDepth = 48,
                    }
                )
                {
                    JObject value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                    if (reader.Read())
                        throw new InvalidDataException("Stored suite JSON has trailing content.");
                    return value;
                }
            }
        }

        private static byte[] Encode(JObject value, int maximum)
        {
            string json = value.ToString(Formatting.None);
            if (Utf8.GetByteCount(json) > maximum)
                throw new ArgumentException("Suite JSON exceeds its byte limit.");
            return Utf8.GetBytes(json);
        }
    }
}
