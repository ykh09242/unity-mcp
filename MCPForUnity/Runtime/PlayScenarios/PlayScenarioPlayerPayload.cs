using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Build-time payload inventory. Runtime report hashes are launcher admission echoes.</summary>
    public static class PlayScenarioPlayerPayload
    {
        public const int FileLimit = 1024;
        public const int InventoryJsonLimit = 1024 * 1024;
        public const int PathLimit = 512;
        public const long ByteLimit = 16L * 1024 * 1024 * 1024;
        private const int DirectoryLimit = 4096;

        public static string HashText(string text)
        {
            using (SHA256 hash = SHA256.Create())
                return Hex(hash.ComputeHash(Encoding.UTF8.GetBytes(text)));
        }

        private static string Hex(byte[] hash) => string.Concat(hash.Select(value => value.ToString("x2")));

        public static JArray Inventory(string root)
        {
            root = PlayScenarioPlayerFiles.CheckedAbsolute(root);
            if (!Directory.Exists(root))
                throw new DirectoryNotFoundException("Player build root is unavailable.");
            var paths = new List<string>();
            var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var pending = new Stack<string>();
            pending.Push(root);
            int directories = 0;
            int discoveredDirectories = 1;
            while (pending.Count > 0)
            {
                string directory = pending.Pop();
                if (++directories > DirectoryLimit)
                    throw new IOException("Player payload exceeds its directory bound.");
                foreach (string entry in Directory.EnumerateFileSystemEntries(directory))
                {
                    PlayScenarioPlayerFiles.CheckedAbsolute(entry);
                    string relative = entry.Substring(root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar).Length + 1).Replace('\\', '/');
                    ValidatePath(relative);
                    if ((File.GetAttributes(entry) & FileAttributes.Directory) != 0)
                    {
                        if (++discoveredDirectories > DirectoryLimit)
                            throw new IOException("Player payload exceeds its discovered directory bound.");
                        pending.Push(entry);
                        continue;
                    }
                    if (relative == PlayScenarioPlayerBundle.ManifestName)
                        continue;
                    if (!names.Add(relative))
                        throw new IOException("Player payload contains case-ambiguous paths.");
                    if (paths.Count == FileLimit)
                        throw new IOException("Player payload exceeds its file bound.");
                    paths.Add(relative);
                }
            }
            paths.Sort(StringComparer.Ordinal);
            long bytes = 0;
            var inventory = new JArray();
            foreach (string relative in paths)
            {
                string path = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
                using (var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    long length = file.Length;
                    if (length > ByteLimit - bytes)
                        throw new IOException("Player payload exceeds its byte bound.");
                    bytes += length;
                    using (SHA256 hash = SHA256.Create())
                        inventory.Add(
                            new JObject
                            {
                                ["path"] = relative,
                                ["size_bytes"] = length,
                                ["sha256"] = Hex(hash.ComputeHash(file)),
                            }
                        );
                    if (file.Length != length)
                        throw new IOException("Player payload changed while hashing.");
                }
            }
            if (inventory.Count == 0)
                throw new IOException("Player payload must contain at least one file.");
            return inventory;
        }

        public static void Attach(JObject manifest, string root)
        {
            JArray inventory = Inventory(root);
            string json = PlayScenarioPlayerBundle.CanonicalJson(inventory);
            if (Encoding.UTF8.GetByteCount(json) > InventoryJsonLimit)
                throw new IOException("Player payload inventory exceeds its JSON byte bound.");
            manifest["payload_inventory"] = inventory;
            manifest["payload_inventory_json"] = json;
            manifest["payload_hash"] = HashText(json);
        }

        public static void ValidateManifest(JObject manifest)
        {
            bool present =
                manifest.Property("payload_inventory") != null
                || manifest.Property("payload_inventory_json") != null
                || manifest.Property("payload_hash") != null;
            if (!present)
                return; // Embedded metadata is compiled before the actual payload exists.
            if (
                !(manifest["payload_inventory"] is JArray inventory)
                || inventory.Count == 0
                || inventory.Count > FileLimit
                || manifest["payload_inventory_json"]?.Type != JTokenType.String
                || manifest["payload_hash"]?.Type != JTokenType.String
            )
                throw new ArgumentException("Incomplete or oversized Player payload inventory.");
            string json = (string)manifest["payload_inventory_json"];
            if (Encoding.UTF8.GetByteCount(json) > InventoryJsonLimit)
                throw new ArgumentException("Player payload inventory exceeds its JSON byte bound.");
            if (PlayScenarioPlayerBundle.CanonicalJson(inventory) != json || HashText(json) != (string)manifest["payload_hash"])
                throw new ArgumentException("Player payload inventory identity does not match.");
            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string previous = null;
            long total = 0;
            foreach (JToken token in inventory)
            {
                if (!(token is JObject item))
                    throw new ArgumentException("Player inventory entries must be objects.");
                PlayScenarioPlayerBundle.Fields(item, "path", "size_bytes", "sha256");
                string path = PlayScenarioPlayerBundle.Text(item, "path", PathLimit);
                ValidatePath(path);
                if (
                    string.Equals(path, PlayScenarioPlayerBundle.ManifestName, StringComparison.OrdinalIgnoreCase)
                    || !paths.Add(path)
                    || (previous != null && StringComparer.Ordinal.Compare(previous, path) >= 0)
                )
                    throw new ArgumentException("Player inventory paths must be unique, sorted and exclude their own manifest.");
                if (
                    item["size_bytes"]?.Type != JTokenType.Integer
                    || !long.TryParse(item["size_bytes"].ToString(), out long size)
                    || size < 0
                    || size > ByteLimit - total
                )
                    throw new ArgumentException("Player inventory exceeds its byte bound.");
                total += size;
                previous = path;
                PlayScenarioPlayerBundle.ValidateHex(PlayScenarioPlayerBundle.Text(item, "sha256", 64), 64, "payload sha256");
            }
        }

        public static void Verify(string root, JObject manifest)
        {
            ValidateManifest(manifest);
            if (!(manifest["payload_inventory"] is JArray expected))
                throw new ArgumentException("An external verified manifest requires its payload inventory.");
            JArray actual = Inventory(root);
            if (!JToken.DeepEquals(actual, expected))
                throw new IOException("Player payload contains changed, extra or missing files.");
        }

        private static void ValidatePath(string relative)
        {
            string[] segments = relative?.Split('/');
            if (
                string.IsNullOrWhiteSpace(relative)
                || relative.Length > PathLimit
                || relative.Any(char.IsControl)
                || relative.IndexOfAny(new[] { '\\', ':', '*', '?', '"', '<', '>', '|' }) >= 0
                || segments.Any(part =>
                    part.Length == 0
                    || part == "."
                    || part == ".."
                    || part.EndsWith(".", StringComparison.Ordinal)
                    || part.EndsWith(" ", StringComparison.Ordinal)
                    || part.StartsWith(".env", StringComparison.OrdinalIgnoreCase)
                    || Regex.IsMatch(part, @"\A(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|\z)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)
                )
                || Enumerable
                    .Range(0, Math.Max(0, segments.Length - 2))
                    .Any(index =>
                        segments[index].Equals("Assets", StringComparison.OrdinalIgnoreCase)
                        && segments[index + 1].Equals("Resources", StringComparison.OrdinalIgnoreCase)
                        && segments[index + 2].Equals("GameData", StringComparison.OrdinalIgnoreCase)
                    )
            )
                throw new ArgumentException("Player payload path must be a bounded exact permitted Windows relative path.");
        }
    }
}
