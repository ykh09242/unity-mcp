using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.SimulationProfiles
{
    internal static class SimulationProfileStore
    {
        private const int MaximumBytes = 2 * 1024 * 1024;
        private static JObject state;
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private static string Location =>
            SafePathUtility.ResolveWithinRoot(Path.GetDirectoryName(Application.dataPath), "Library/MCPForUnity/SimulationProfiles/state.json");
        internal static string SessionId
        {
            get
            {
                const string key = "MCPForUnity.SimulationProfiles.Session.v1";
                string session = SessionState.GetString(key, "");
                if (string.IsNullOrEmpty(session))
                {
                    session = Guid.NewGuid().ToString("N");
                    SessionState.SetString(key, session);
                }
                return session;
            }
        }
        internal static JObject State => state ?? (state = Read());
        internal static JObject Profiles => (JObject)State["profiles"];
        internal static JObject Receipts => (JObject)State["receipts"];

        private static JObject Read()
        {
            string path = Location;
            JObject loaded;
            if (!File.Exists(path))
                loaded = new JObject
                {
                    ["format"] = 1,
                    ["profiles"] = new JObject(),
                    ["receipts"] = new JObject(),
                };
            else
            {
                if (new FileInfo(path).Length > MaximumBytes)
                    throw new InvalidOperationException("Simulation profile metadata exceeds the bounded storage limit; no settings changed.");
                loaded = JObject.Parse(File.ReadAllText(path, Utf8));
                if (
                    loaded["format"]?.Type != JTokenType.Integer
                    || loaded["format"].ToString() != "1"
                    || !(loaded["profiles"] is JObject profiles)
                    || profiles.Count > 32
                    || !(loaded["receipts"] is JObject receipts)
                    || receipts.Count > 64
                )
                    throw new InvalidOperationException("Simulation profile metadata is unsupported or corrupt; no settings changed.");
                foreach (JProperty profile in profiles.Properties())
                    if (
                        !(profile.Value is JObject record)
                        || !(record["settings"] is JObject settings)
                        || settings.Count < 1
                        || settings.Count > 32
                        || (string)record["name"] != profile.Name
                    )
                        throw new InvalidOperationException("Simulation profile metadata contains an invalid bounded profile record.");
                foreach (JProperty property in receipts.Properties())
                    if (!(property.Value is JObject record) || !(record["outcomes"] is JArray rows) || rows.Count > 32 || rows.Any(row => !(row is JObject)))
                        throw new InvalidOperationException("Simulation profile metadata contains an invalid bounded receipt record.");
                foreach (JObject receipt in receipts.Properties().Select(p => p.Value).OfType<JObject>())
                    if ((string)receipt["state"] == "applying" || (string)receipt["state"] == "restoring")
                    {
                        receipt["state"] = "interrupted";
                        foreach (JObject row in ((JArray)receipt["outcomes"]).OfType<JObject>())
                            if ((string)row["status"] == "in_flight" || (string)row["status"] == "restore_in_flight")
                                row["status"] = "uncertain_after_reload";
                    }
            }
            return loaded;
        }

        internal static void Reload() => state = null;

        internal static void Save()
        {
            string json = State.ToString(Formatting.None);
            byte[] bytes = Utf8.GetBytes(json);
            if (bytes.Length > MaximumBytes)
                throw new InvalidOperationException("Simulation profile metadata is full; delete an owned profile and its receipts before another write.");
            string path = Location;
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllBytes(temporary, bytes);
                if (File.Exists(path))
                    File.Replace(temporary, path, null);
                else
                    File.Move(temporary, path);
            }
            catch
            {
                state = null;
                throw;
            }
            finally
            {
                if (File.Exists(temporary))
                    File.Delete(temporary);
            }
        }

        internal static string Hash(JToken value)
        {
            JToken Canonical(JToken token)
            {
                if (token is JObject obj)
                    return new JObject(obj.Properties().OrderBy(p => p.Name, StringComparer.Ordinal).Select(p => new JProperty(p.Name, Canonical(p.Value))));
                if (token is JArray array)
                    return new JArray(array.Select(Canonical));
                return token.DeepClone();
            }
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(Utf8.GetBytes(Canonical(value).ToString(Formatting.None)))).Replace("-", "").ToLowerInvariant();
        }

        internal static string Required(JObject p, string key)
        {
            if (p[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace((string)p[key]) || ((string)p[key]).Length > 128)
                throw new ArgumentException(key + " must be a nonempty string of at most 128 characters.");
            return (string)p[key];
        }

        internal static string Name(JObject p)
        {
            string name = Required(p, "name");
            if (name.Length > 64 || name.Any(char.IsControl))
                throw new ArgumentException("name must contain 1..64 visible characters.");
            return name;
        }

        internal static JObject Profile(JObject p)
        {
            JObject profile = (JObject)Profiles[Name(p)] ?? throw new ArgumentException("Unknown simulation profile name.");
            string actual = Hash(new JObject { ["name"] = profile["name"], ["settings"] = profile["settings"] });
            if ((string)profile["revision"] != actual)
                throw new InvalidOperationException("Stored profile revision does not match its settings; no global writes attempted.");
            return profile;
        }

        internal static JObject Receipt(JObject p) =>
            (JObject)Receipts[Required(p, "receipt_id")] ?? throw new ArgumentException("Unknown or deleted simulation profile receipt.");
    }
}
