using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    /// <summary>Read-only consumer of the explicit local Python baseline registry contract.</summary>
    internal static class PlayScenarioSoakBaselineRegistry
    {
        internal const int RegistryLimit = 2 * 1024 * 1024;
        private const int EvidenceLimit = 16384 * 1000;
        private static readonly string[] RequiredFiles =
        {
            "request.json",
            "definition.json",
            "process.json",
            "run.json",
            "soak.json",
            "client-soak.json",
            "iterations.jsonl",
        };
        private static readonly Regex NamePattern = new Regex("^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$");
        private static readonly Regex DigestPattern = new Regex("^[a-f0-9]{64}$");
        private static readonly Regex FilePattern = new Regex(
            "^(request|definition|process|run|soak|client-soak)\\.json$|^iterations\\.jsonl$|^iteration-[0-9]{4}\\.json$"
        );

        private sealed class RegistryJsonReader : JsonTextReader
        {
            private readonly CancellationToken cancellation;

            internal RegistryJsonReader(TextReader text, CancellationToken cancellation)
                : base(text) => this.cancellation = cancellation;

            public override bool Read()
            {
                cancellation.ThrowIfCancellationRequested();
                bool result = base.Read();
                if (TokenType == JsonToken.Comment)
                    throw new InvalidDataException("Baseline registry comments are unsupported.");
                return result;
            }
        }

        internal sealed class Entry
        {
            internal string Name;
            internal string Directory;
            internal string Snapshot;
            internal string Digest;
            internal bool Pinned;
            internal readonly SortedDictionary<string, string> Files = new SortedDictionary<string, string>(StringComparer.Ordinal);
        }

        internal static Entry[] Read(string selected, CancellationToken cancellation)
        {
            string path = Path.GetFullPath(selected);
            RequireUnlinked(path);
            if (System.IO.Directory.Exists(path))
                path = Path.Combine(path, "baselines.json");
            byte[] bytes = ReadBytes(path, RegistryLimit, cancellation);
            JObject registry;
            using (var text = new StringReader(new UTF8Encoding(false, true).GetString(bytes)))
            using (
                var reader = new RegistryJsonReader(text, cancellation)
                {
                    MaxDepth = 16,
                    DateParseHandling = DateParseHandling.None,
                    FloatParseHandling = FloatParseHandling.Decimal,
                }
            )
            {
                registry = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if (reader.Read())
                    throw new InvalidDataException("Registry contains trailing JSON.");
            }
            Fields(registry, "schema_version", "revision", "entries", "pins", "history");
            if (Integer(registry, "schema_version", 1, 1) != 1)
                throw new InvalidDataException("Unsupported baseline registry version.");
            long revision = Integer(registry, "revision", 0, 4096);
            JArray entries = Array(registry, "entries", 256);
            JArray pins = Array(registry, "pins", 256);
            JArray history = Array(registry, "history", 4096);
            if (revision != history.Count)
                throw new InvalidDataException("Registry revision disagrees with its audit history.");
            var result = new List<Entry>();
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (JToken token in entries)
            {
                cancellation.ThrowIfCancellationRequested();
                JObject value = Object(token);
                Fields(value, "name", "directory", "snapshot_file", "registered_utc", "evidence_sha256", "files", "provenance");
                var entry = new Entry
                {
                    Name = Text(value, "name", 64, NamePattern),
                    Directory = Text(value, "directory", 4096),
                    Snapshot = Text(value, "snapshot_file", 4096),
                    Digest = Text(value, "evidence_sha256", 64, DigestPattern),
                };
                Text(value, "registered_utc", 64);
                Object(value["provenance"]);
                if (
                    !names.Add(entry.Name)
                    || !Path.IsPathRooted(entry.Directory)
                    || entry.Directory != Path.GetFullPath(entry.Directory)
                    || entry.Snapshot != Path.Combine(entry.Directory, "soak.json")
                )
                    throw new InvalidDataException("Registry identity or explicit snapshot path is invalid.");
                JArray files = Array(value, "files", 71);
                string previousFile = null;
                foreach (JToken file in files)
                {
                    JObject manifest = Object(file);
                    Fields(manifest, "name", "sha256");
                    string name = Text(manifest, "name", 64, FilePattern);
                    if (previousFile != null && string.CompareOrdinal(previousFile, name) >= 0)
                        throw new InvalidDataException("Registry manifest must contain sorted unique filenames.");
                    entry.Files.Add(name, Text(manifest, "sha256", 64, DigestPattern));
                    previousFile = name;
                }
                if (files.Count < 8 || RequiredFiles.Any(name => !entry.Files.ContainsKey(name)) || ManifestDigest(entry) != entry.Digest)
                    throw new InvalidDataException("Registry manifest lacks required evidence or has an invalid digest.");
                result.Add(entry);
            }
            var pinned = new List<string>();
            foreach (JToken token in pins)
            {
                JObject pin = Object(token);
                Fields(pin, "name");
                string name = Text(pin, "name", 64, NamePattern);
                if (!names.Contains(name) || pinned.Contains(name))
                    throw new InvalidDataException("Registry pins must be unique registered names.");
                pinned.Add(name);
            }
            var registered = new HashSet<string>(StringComparer.Ordinal);
            var prior = new List<string>();
            int index = 0;
            foreach (JToken token in history)
            {
                cancellation.ThrowIfCancellationRequested();
                JObject audit = Object(token);
                Fields(audit, "revision", "action", "name", "previous_pins", "pins", "at_utc", "reason");
                if (Integer(audit, "revision", 1, 4096) != ++index || !Names(audit, "previous_pins").SequenceEqual(prior))
                    throw new InvalidDataException("Registry audit history has a broken chain.");
                string name = Text(audit, "name", 64, NamePattern);
                string action = Text(audit, "action", 16);
                Text(audit, "at_utc", 64);
                Text(audit, "reason", 512);
                switch (action)
                {
                    case "register":
                        if (!registered.Add(name))
                            throw new InvalidDataException("Registry audit repeats a registration.");
                        break;
                    case "pin":
                        if (!registered.Contains(name) || prior.Contains(name))
                            throw new InvalidDataException("Registry audit has an invalid pin.");
                        prior.Add(name);
                        prior.Sort(StringComparer.Ordinal);
                        break;
                    case "unpin":
                        if (!prior.Remove(name))
                            throw new InvalidDataException("Registry audit has an invalid unpin.");
                        break;
                    default:
                        throw new InvalidDataException("Registry audit action is unsupported.");
                }
                if (!Names(audit, "pins").SequenceEqual(prior))
                    throw new InvalidDataException("Registry audit pin outcome is invalid.");
            }
            if (!registered.SetEquals(names) || !prior.SequenceEqual(pinned))
                throw new InvalidDataException("Registry current state disagrees with its history.");
            foreach (Entry entry in result)
                entry.Pinned = pinned.Contains(entry.Name);
            return result.ToArray();
        }

        internal static string ValidateSnapshot(Entry entry, CancellationToken cancellation)
        {
            RequireUnlinked(entry.Directory);
            foreach (var file in entry.Files)
            {
                string path = Path.Combine(entry.Directory, file.Key);
                if (Hash(ReadBytes(path, EvidenceLimit, cancellation)) != file.Value)
                    throw new InvalidDataException("Registered baseline evidence changed: " + entry.Name);
            }
            return entry.Snapshot;
        }

        private static string ManifestDigest(Entry entry) =>
            Hash(Encoding.UTF8.GetBytes(string.Concat(entry.Files.Select(file => file.Key + ":" + file.Value + "\n"))));

        private static string Hash(byte[] bytes)
        {
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static byte[] ReadBytes(string path, int limit, CancellationToken cancellation)
        {
            RequireUnlinked(path);
            var stamp = PlayScenarioImportFileStamp.Capture(path);
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var output = new MemoryStream())
            {
                if (stream.Length > limit)
                    throw new InvalidDataException("Baseline artifact exceeds its byte bound.");
                var buffer = new byte[8192];
                int count;
                while ((count = stream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    cancellation.ThrowIfCancellationRequested();
                    if (output.Length + count > limit)
                        throw new InvalidDataException("Baseline artifact exceeds its byte bound.");
                    output.Write(buffer, 0, count);
                }
                stamp.Verify(path);
                return output.ToArray();
            }
        }

        private static void RequireUnlinked(string path)
        {
            for (string current = Path.GetFullPath(path); !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
                if ((File.Exists(current) || System.IO.Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("Baseline paths must have unlinked ancestors.");
        }

        private static JObject Object(JToken token) => token as JObject ?? throw new InvalidDataException("Registry object required.");

        private static void Fields(JObject value, params string[] fields)
        {
            if (
                !value
                    .Properties()
                    .Select(property => property.Name)
                    .OrderBy(name => name, StringComparer.Ordinal)
                    .SequenceEqual(fields.OrderBy(name => name, StringComparer.Ordinal))
            )
                throw new InvalidDataException("Registry object fields disagree with schema version 1.");
        }

        private static JArray Array(JObject value, string field, int limit)
        {
            var result = value[field] as JArray;
            if (result == null || result.Count > limit)
                throw new InvalidDataException("Registry array exceeds its bound: " + field);
            return result;
        }

        private static string Text(JObject value, string field, int maximum, Regex pattern = null)
        {
            if (value[field]?.Type != JTokenType.String)
                throw new InvalidDataException("Registry string required: " + field);
            string text = (string)value[field];
            if (
                text.Length < 1
                || text.Length > maximum
                || text.Any(character => character < 32 || character == 127)
                || pattern != null && !pattern.IsMatch(text)
            )
                throw new InvalidDataException("Registry string exceeds its bounds: " + field);
            return text;
        }

        private static long Integer(JObject value, string field, long minimum, long maximum)
        {
            if (value[field]?.Type != JTokenType.Integer || (long)value[field] < minimum || (long)value[field] > maximum)
                throw new InvalidDataException("Registry integer exceeds its bounds: " + field);
            return (long)value[field];
        }

        private static List<string> Names(JObject value, string field) =>
            Array(value, field, 256).Select(token => Text(new JObject { ["name"] = token.DeepClone() }, "name", 64, NamePattern)).ToList();
    }
}
