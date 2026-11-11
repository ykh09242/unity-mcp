using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.ScriptQuality
{
    internal static class VerificationProfileStore
    {
        private static readonly object Gate = new();
        private const string Relative = "ProjectSettings/MCPVerificationProfiles.json";
        private static readonly UTF8Encoding Utf8 = new(false, true);

        internal static JObject Read(string root)
        {
            string path = SafePathUtility.ResolveWithinRoot(root, Relative);
            if (!File.Exists(path))
                return new JObject
                {
                    ["byte_hash"] = "missing",
                    ["document"] = new JObject { ["schema_version"] = 1, ["profiles"] = new JObject() },
                };
            byte[] bytes = ScriptSelection.Read(path, 256 * 1024);
            JObject document;
            try
            {
                document = JObject.Parse(ScriptSelection.Text(bytes));
            }
            catch (JsonException)
            {
                throw new ArgumentException("invalid_profile_document");
            }
            if (
                document.Properties().Any(p => p.Name != "schema_version" && p.Name != "profiles")
                || document["schema_version"]?.Type != JTokenType.Integer
                || (int)document["schema_version"] != 1
                || document["profiles"] is not JObject profiles
                || profiles.Count > 32
            )
                throw new ArgumentException("invalid_profile_document");
            foreach (var item in profiles.Properties())
            {
                Name(item.Name);
                Validate(item.Value as JObject);
            }
            return new JObject { ["byte_hash"] = ScriptSelection.Digest(bytes), ["document"] = document };
        }

        internal static JObject Write(string root, string identity, JObject profile, string expectedHash, Action beforeReplace = null)
        {
            Name(identity);
            Validate(profile);
            if (expectedHash != "missing" && (expectedHash == null || !Regex.IsMatch(expectedHash, "^[a-f0-9]{64}$")))
                throw new ArgumentException("expected_profile_hash_required");
            lock (Gate)
            {
                var current = Read(root);
                if ((string)current["byte_hash"] != expectedHash)
                    throw new InvalidOperationException("verification_profile_conflict");
                var document = (JObject)current["document"];
                ((JObject)document["profiles"])[identity] = profile.DeepClone();
                if (((JObject)document["profiles"]).Count > 32)
                    throw new ArgumentException("verification_profile_limit");
                byte[] bytes = Utf8.GetBytes(document.ToString(Formatting.Indented) + "\n");
                if (bytes.Length > 256 * 1024)
                    throw new ArgumentException("verification_profile_bytes_limit");
                string path = SafePathUtility.ResolveWithinRoot(root, Relative);
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                string temporary = SafePathUtility.ResolveWithinRoot(root, "ProjectSettings/.MCPVerificationProfiles." + Guid.NewGuid().ToString("N") + ".tmp");
                try
                {
                    using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    {
                        stream.Write(bytes, 0, bytes.Length);
                        stream.Flush(true);
                    }
                    beforeReplace?.Invoke();
                    if ((string)Read(root)["byte_hash"] != expectedHash)
                        throw new InvalidOperationException("verification_profile_conflict");
                    SafePathUtility.ResolveWithinRoot(root, Relative);
                    if (expectedHash == "missing")
                        File.Move(temporary, path);
                    else
                        File.Replace(temporary, path, null);
                    return new JObject { ["byte_hash"] = ScriptSelection.Digest(bytes), ["document"] = document };
                }
                finally
                {
                    if (File.Exists(temporary))
                        File.Delete(temporary);
                }
            }
        }

        private static void Name(string value)
        {
            if (value == null || !Regex.IsMatch(value, "^[a-z][a-z0-9-]{0,63}$"))
                throw new ArgumentException("invalid_profile_id");
        }

        internal static void Validate(JObject profile)
        {
            string[] fields =
            {
                "schema_version",
                "refresh_policy",
                "analysis_required",
                "diagnostic_scope",
                "fail_on",
                "impact",
                "test_mode",
                "test_names",
                "assembly_names",
                "category_names",
                "deadlines",
            };
            if (profile == null || profile.Properties().Any(p => !fields.Contains(p.Name)))
                throw new ArgumentException("invalid_verification_profile");
            if (profile["schema_version"] != null && (profile["schema_version"].Type != JTokenType.Integer || (int)profile["schema_version"] != 1))
                throw new ArgumentException("invalid_profile_version");
            Choice(profile, "refresh_policy", new[] { "never", "if_needed" });
            Choice(profile, "diagnostic_scope", new[] { "all", "introduced" });
            Choice(profile, "impact", new[] { "off", "if_baseline", "required" });
            Choice(profile, "test_mode", new[] { "EditMode", "PlayMode" }, nullable: true);
            if (profile["analysis_required"] != null && profile["analysis_required"].Type != JTokenType.Boolean)
                throw new ArgumentException("invalid_analysis_required");
            if (
                profile["fail_on"] != null
                && (
                    profile["fail_on"] is not JArray fail
                    || fail.Count > 3
                    || fail.Any(v => v.Type != JTokenType.String || !new[] { "error", "warning", "info" }.Contains((string)v))
                    || fail.Values<string>().Distinct().Count() != fail.Count
                )
            )
                throw new ArgumentException("invalid_severity_policy");
            foreach (string field in new[] { "test_names", "assembly_names", "category_names" })
                if (
                    profile[field] != null
                    && (
                        profile[field] is not JArray selectors
                        || selectors.Count > 128
                        || selectors.Any(v => v.Type != JTokenType.String || ((string)v).Length < 1 || ((string)v).Length > 512)
                        || selectors.Count > 0 && (profile["test_mode"] == null || profile["test_mode"].Type == JTokenType.Null)
                    )
                )
                    throw new ArgumentException("invalid_test_selectors");
            if (
                (bool?)profile["analysis_required"] == false
                && ((string)profile["impact"] == "required" || (string)profile["diagnostic_scope"] == "introduced")
            )
                throw new ArgumentException("analysis_required_by_policy");
            if (profile["deadlines"] != null)
            {
                if (profile["deadlines"] is not JObject deadlines)
                    throw new ArgumentException("invalid_stage_deadlines");
                foreach (var item in deadlines.Properties())
                {
                    int max = item.Name switch
                    {
                        "freshness" or "analysis" => 300,
                        "impact" or "final" => 120,
                        "tests" => 600,
                        "console" => 60,
                        _ => 0,
                    };
                    if (item.Value.Type != JTokenType.Integer || !int.TryParse(item.Value.ToString(), out int number) || number < 1 || number > max)
                        throw new ArgumentException("invalid_stage_deadline");
                }
            }
        }

        private static void Choice(JObject profile, string field, string[] allowed, bool nullable = false)
        {
            var value = profile[field];
            if (value == null || nullable && value.Type == JTokenType.Null)
                return;
            if (value.Type != JTokenType.String || !allowed.Contains((string)value))
                throw new ArgumentException("invalid_" + field);
        }
    }
}
