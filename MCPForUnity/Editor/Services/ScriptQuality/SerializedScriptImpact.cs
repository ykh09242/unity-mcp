using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services.ScriptQuality
{
    // A deliberately limited text reader. Never loads an asset, scene, prefab or project assembly.
    internal static class SerializedScriptImpact
    {
        private static Match Match(string value, string pattern) => Regex.Match(value, pattern, RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100));

        internal static JObject ObserveIdentity(JObject request)
        {
            string path = ScriptSelection.Canonical((string)request["script_path"]);
            string guid = (string)request["script_guid"];
            if (guid == null || !Match(guid, "^[a-f0-9]{32}$").Success)
                throw new ArgumentException("invalid_script_guid");
            string physical = ScriptSelection.Physical(path);
            string state;
            bool Exists(string candidate)
            {
                try
                {
                    File.GetAttributes(candidate);
                    return true;
                }
                catch (FileNotFoundException)
                {
                    return false;
                }
                catch (DirectoryNotFoundException)
                {
                    return false;
                }
            }
            try
            {
                if (Exists(physical))
                    state = "present";
                else if (Exists(SafePathUtility.ResolveWithinRoot(Path.GetDirectoryName(physical), physical + ".meta")))
                    state = "indeterminate";
                else
                {
                    string mapped = AssetDatabase.GUIDToAssetPath(guid);
                    // Unity can retain the old path for a deleted asset until Editor exit.
                    // A different mapping must never be classified as a deletion.
                    state = !string.IsNullOrEmpty(mapped) && !string.Equals(mapped, path, StringComparison.Ordinal) ? "relocated" : "absent";
                }
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException || error is System.Security.SecurityException)
            {
                state = "indeterminate";
            }
            return new JObject
            {
                ["path"] = path,
                ["guid"] = guid,
                ["state"] = state,
                ["basis"] = "disk_absence_and_asset_database_identity",
            };
        }

        internal static JObject Read(JObject request)
        {
            if (
                request["assets"] is not JArray assets
                || assets.Count > 10
                || assets.Any(a => a.Type != JTokenType.String)
                || request["members"] is not JArray members
                || members.Count > 128
            )
                throw new ArgumentException("impact_scope_limit");
            foreach (var member in members)
            {
                if (
                    member is not JObject item
                    || item.Properties().Any(p => !new[] { "name", "kind", "container" }.Contains(p.Name))
                    || item["name"]?.Type != JTokenType.String
                    || ((string)item["name"]).Length > 256
                    || !Match((string)item["name"], @"^[\p{L}_][\p{L}\p{N}_]*$").Success
                    || !new[] { "field", "method" }.Contains((string)item["kind"])
                    || item["container"]?.Type != JTokenType.String
                    || ((string)item["container"]).Length > 4096
                )
                    throw new ArgumentException("invalid_impact_member");
            }
            if (request["expected_missing"] != null && request["expected_missing"].Type != JTokenType.Boolean)
                throw new ArgumentException("expected_missing_must_be_boolean");
            bool missing = (bool?)request["expected_missing"] == true;
            ScriptSelection.Source source;
            if (missing)
            {
                if ((string)ObserveIdentity(request)["state"] != "absent")
                    throw new InvalidOperationException("impact_source_changed");
                source = new ScriptSelection.Source { Path = (string)request["script_path"], Guid = (string)request["script_guid"] };
            }
            else
            {
                source = ScriptSelection.Resolve(new JObject { ["paths"] = new JArray(request["script_path"]) }).Files.Single();
                if (source.Hash != (string)request["source_hash"] || source.Guid == null || source.Guid != (string)request["script_guid"])
                    throw new InvalidOperationException("impact_source_changed");
            }
            var rows = new JArray();
            var gaps = new HashSet<string>(StringComparer.Ordinal);
            long total = 0;
            foreach (string path in assets.Values<string>().Distinct(StringComparer.Ordinal))
            {
                var row = new JObject { ["path"] = path, ["members"] = new JArray() };
                try
                {
                    // Reuse source admission for path components, then restrict extensions to serialized assets.
                    ScriptSelection.Canonical(path + ".cs");
                    if (!new[] { ".unity", ".prefab", ".asset" }.Contains(Path.GetExtension(path).ToLowerInvariant()))
                        throw new InvalidOperationException("unsupported_serialized_asset");
                    string physical = ScriptSelection.Physical(path);
                    byte[] bytes = ScriptSelection.Read(physical, 2 * 1024 * 1024);
                    total += bytes.Length;
                    if (total > 16 * 1024 * 1024)
                        throw new InvalidOperationException("impact_asset_limit");
                    string metaPath = SafePathUtility.ResolveWithinRoot(Path.GetDirectoryName(physical), physical + ".meta");
                    byte[] meta = File.Exists(metaPath) ? ScriptSelection.Read(metaPath, 64 * 1024) : Array.Empty<byte>();
                    row = InspectText(
                        ScriptSelection.Utf8.GetString(bytes).TrimStart('\uFEFF'),
                        source.Guid,
                        Path.GetFileNameWithoutExtension(source.Path),
                        members
                    );
                    row["path"] = path;
                    row["bytes_sha256"] = ScriptSelection.Digest(bytes);
                    if (
                        !bytes.SequenceEqual(ScriptSelection.Read(physical, 2 * 1024 * 1024))
                        || !meta.SequenceEqual(File.Exists(metaPath) ? ScriptSelection.Read(metaPath, 64 * 1024) : Array.Empty<byte>())
                    )
                        throw new InvalidOperationException("serialized_asset_changed");
                    foreach (string gap in row["coverage_gaps"].Values<string>())
                        gaps.Add(gap);
                }
                catch (Exception error)
                    when (error is IOException
                        || error is ArgumentException
                        || error is InvalidOperationException
                        || error is DecoderFallbackException
                        || error is RegexMatchTimeoutException
                    )
                {
                    row = new JObject
                    {
                        ["path"] = path,
                        ["members"] = new JArray(),
                        ["coverage_gaps"] = new JArray(error.Message.Length < 256 ? error.Message : "serialized_observation_unavailable"),
                    };
                    gaps.Add("serialized_observation_incomplete");
                }
                rows.Add(row);
            }
            if (missing)
            {
                if ((string)ObserveIdentity(request)["state"] != "absent")
                    throw new InvalidOperationException("impact_source_changed");
            }
            else
            {
                var after = ScriptSelection.Resolve(new JObject { ["paths"] = new JArray(source.Path) }).Files.Single();
                if (source.Hash != after.Hash || source.Guid != after.Guid)
                    throw new InvalidOperationException("impact_source_changed");
            }
            var result = new JObject
            {
                ["assets"] = rows,
                ["coverage_gaps"] = new JArray(gaps),
                ["source_hash"] = source.Hash,
                ["source_state"] = missing ? "absent" : "present",
                ["basis"] = missing ? "script_absence_and_serialized_bytes_rechecked" : "selected_source_and_serialized_bytes_rechecked",
                ["observed_at_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            };
            if (Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) > 480 * 1024)
                throw new InvalidOperationException("impact_page_limit");
            return result;
        }

        internal static JObject InspectText(string text, string scriptGuid, string scriptClass, JArray members)
        {
            var found = new JArray();
            var gaps = new HashSet<string>(StringComparer.Ordinal);
            var timer = Stopwatch.StartNew();
            void Budget()
            {
                if (found.Count > 512 || timer.ElapsedMilliseconds > 2000)
                    throw new InvalidOperationException("impact_observation_limit");
            }
            JObject Result() => new JObject { ["members"] = found, ["coverage_gaps"] = new JArray(gaps) };
            if (!text.StartsWith("%YAML", StringComparison.Ordinal) || text.IndexOf('\0') >= 0)
            {
                gaps.Add("binary_or_unsupported_serialization");
                return Result();
            }
            string[] lines = text.Replace("\r\n", "\n").Split('\n');
            if (lines.Length > 100000 || lines.Any(line => Match(line, @":\s*[|>][+-]?\s*$").Success))
            {
                gaps.Add("unsupported_yaml_structure");
                return Result();
            }
            var documents = new List<(string id, int start, int end)>();
            var allIds = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < lines.Length; i++)
            {
                Budget();
                var anyHeader = Match(lines[i], @"^--- !u!\d+ &(-?\d+)(?: stripped)?$");
                if (anyHeader.Success && !allIds.Add(anyHeader.Groups[1].Value))
                {
                    gaps.Add("ambiguous_file_id");
                    return Result();
                }
                var header = Match(lines[i], @"^--- !u!114 &(-?\d+)$");
                if (!header.Success)
                    continue;
                int end = i + 1;
                while (end < lines.Length && !lines[end].StartsWith("--- ", StringComparison.Ordinal))
                    end++;
                string script = lines.Skip(i + 1).Take(end - i - 1).FirstOrDefault(line => line.StartsWith("  m_Script:", StringComparison.Ordinal));
                if (script != null && Match(script, @"fileID:\s*0\s*}").Success)
                    gaps.Add("missing_script_reference");
                if (script != null && Match(script, @"^  m_Script:\s*\{fileID:\s*11500000,\s*guid:\s*" + scriptGuid + @",\s*type:\s*3\s*}$").Success)
                    documents.Add((header.Groups[1].Value, i + 1, end));
            }
            if (documents.Select(d => d.id).Distinct().Count() != documents.Count)
            {
                gaps.Add("ambiguous_file_id");
                return Result();
            }
            foreach (var member in members)
            {
                Budget();
                string name = (string)member["name"],
                    kind = (string)member["kind"],
                    container = (string)member["container"];
                // MonoScript class resolution is intentionally not executed; nested/generic/other classes are uncertain.
                if (container != "global::" + scriptClass && !container.EndsWith("." + scriptClass, StringComparison.Ordinal))
                {
                    gaps.Add("script_class_binding_unknown");
                    continue;
                }
                if (kind == "field")
                {
                    foreach (var document in documents)
                        if (
                            lines
                                .Skip(document.start)
                                .Take(document.end - document.start)
                                .Any(line => line.StartsWith("  " + name + ":", StringComparison.Ordinal))
                        )
                            found.Add(
                                new JObject
                                {
                                    ["name"] = name,
                                    ["kind"] = kind,
                                    ["file_id"] = document.id,
                                    ["certainty"] = "confirmed_reference",
                                    ["basis"] = "serialized_top_level_field",
                                }
                            );
                    continue;
                }
                // Only the standard persistent-call list and targets in this very file are confirmed.
                for (int i = 0; i < lines.Length; i++)
                {
                    Budget();
                    var persistent = Match(lines[i], @"^( +)m_PersistentCalls:\s*$");
                    if (!persistent.Success || i + 1 >= lines.Length || lines[i + 1].Trim() != "m_Calls:")
                        continue;
                    int indent = persistent.Groups[1].Length;
                    string target = null;
                    for (int j = i + 2; j < lines.Length && lines[j].TakeWhile(c => c == ' ').Count() > indent; j++)
                    {
                        Budget();
                        string line = lines[j].Trim();
                        if (line.StartsWith("- m_Target:", StringComparison.Ordinal))
                        {
                            var local = Match(line, @"^- m_Target:\s*\{fileID:\s*(-?\d+)\s*}$");
                            target = local.Success ? local.Groups[1].Value : null;
                        }
                        if (line == "m_MethodName: " + name)
                        {
                            bool confirmed = target != null && documents.Any(d => d.id == target);
                            found.Add(
                                new JObject
                                {
                                    ["name"] = name,
                                    ["kind"] = kind,
                                    ["file_id"] = target,
                                    ["certainty"] = confirmed ? "confirmed_reference" : "unknown",
                                    ["basis"] = confirmed ? "persistent_call_local_target" : "persistent_target_unresolved",
                                }
                            );
                            if (!confirmed)
                                gaps.Add("persistent_target_unresolved");
                        }
                    }
                }
            }
            if (found.Count > 512)
                throw new InvalidOperationException("impact_member_limit");
            return Result();
        }
    }
}
