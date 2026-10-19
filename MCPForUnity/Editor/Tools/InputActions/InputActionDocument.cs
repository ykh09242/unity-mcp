using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.InputActions
{
    internal static class InputActionDocument
    {
        internal const int MaxBytes = 256 * 1024;
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        internal static byte[] ReadBytes(string path)
        {
            using var stream = File.Open(AssetPathUtility.GetFullAssetPath(path), FileMode.Open, FileAccess.Read, FileShare.Read);
            return ReadStream(stream);
        }

        internal static byte[] ReadStream(Stream stream)
        {
            if (stream.Length > MaxBytes)
                throw new InvalidOperationException("InputAction source exceeds 256 KiB.");
            var bytes = new byte[(int)stream.Length];
            int offset = 0;
            while (offset < bytes.Length)
            {
                int count = stream.Read(bytes, offset, bytes.Length - offset);
                if (count == 0)
                    throw new IOException("InputAction source changed during read.");
                offset += count;
            }
            if (stream.ReadByte() != -1)
                throw new IOException("InputAction source grew during read.");
            return bytes;
        }

        internal static string Digest(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        internal static JObject Parse(byte[] bytes)
        {
            using var reader = new JsonTextReader(new StringReader(Utf8.GetString(bytes).TrimStart('\uFEFF')))
            {
                MaxDepth = 16,
                DateParseHandling = DateParseHandling.None,
            };
            var document = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (reader.Read())
                throw new ArgumentException("Only one JSON document is supported.");
            return document;
        }

        internal static byte[] Encode(JObject document, byte[] before)
        {
            Validate(document);
            byte[] body = Utf8.GetBytes(document.ToString(Formatting.Indented) + "\n");
            bool bom = before.Length >= 3 && before[0] == 239 && before[1] == 187 && before[2] == 191;
            byte[] bytes = bom
                ? new byte[] { 239, 187, 191 }
                    .Concat(body)
                    .ToArray()
                : body;
            if (bytes.Length > MaxBytes)
                throw new InvalidOperationException("The candidate InputAction source exceeds 256 KiB.");
            return bytes;
        }

        internal static void ValidateNative(byte[] bytes)
        {
            UnityEngine.Object asset = null;
            try
            {
                Type type = ManageInputActions.AssetType;
                var fromJson = type.GetMethod("FromJson", new[] { typeof(string) });
                var toJson = type.GetMethod("ToJson", Type.EmptyTypes);
                if (fromJson == null || toJson == null)
                    throw new InvalidOperationException("Installed Input System lacks the supported JSON API.");
                asset = (UnityEngine.Object)fromJson.Invoke(null, new object[] { Utf8.GetString(bytes).TrimStart('\uFEFF') });
                asset.hideFlags = HideFlags.HideAndDontSave;
                var normalized = JObject.Parse((string)toJson.Invoke(asset, null));
                if (!Identities(Parse(bytes)).SequenceEqual(Identities(normalized)))
                    throw new InvalidOperationException("Native JSON loading would regenerate or reorder existing identities.");
            }
            finally
            {
                if (asset != null)
                    UnityEngine.Object.DestroyImmediate(asset);
            }
        }

        internal static void Validate(JObject document)
        {
            var ids = new HashSet<Guid>();
            var mapNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            JArray maps = Array(document, "maps");
            if (maps.Count > 32 || Array(document, "controlSchemes").Count > 32)
                throw new InvalidOperationException("InputAction maps/control schemes exceed 32.");
            int actions = 0,
                bindings = 0;
            foreach (JObject map in Objects(maps))
            {
                Id(map, ids);
                if (!mapNames.Add(Name(map)))
                    throw new ArgumentException("Duplicate map names are not supported.");
                var actionNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (JObject action in Objects(Array(map, "actions")))
                {
                    Id(action, ids);
                    if (!actionNames.Add(Name(action)))
                        throw new ArgumentException("Duplicate action names are not supported.");
                    actions++;
                }
                foreach (JObject binding in Objects(Array(map, "bindings")))
                {
                    Id(binding, ids);
                    foreach (string flag in new[] { "isComposite", "isPartOfComposite" })
                        if (binding[flag] != null && binding[flag].Type != JTokenType.Boolean)
                            throw new ArgumentException("Binding flags must be JSON booleans.");
                    string reference = ManageInputActions.Text(binding, "action", true, 128);
                    ResolveActionIdentity(map, reference);
                    bindings++;
                }
            }
            if (
                actions > 256
                || bindings > 1024
                || document.Descendants().OfType<JValue>().Any(value => value.Type == JTokenType.String && value.ToString().Length > 1024)
            )
                throw new InvalidOperationException("InputAction document exceeds action/binding/string bounds.");
        }

        internal static string Edit(JObject document, string operation, JObject request)
        {
            if (operation == "add_map")
            {
                string name = RequestName(request);
                JArray maps = Array(document, "maps");
                if (Objects(maps).Any(map => string.Equals((string)map["name"], name, StringComparison.OrdinalIgnoreCase)))
                    throw new ArgumentException("A map with this name already exists.");
                string id = Guid.NewGuid().ToString("D");
                maps.Add(
                    new JObject
                    {
                        ["name"] = name,
                        ["id"] = id,
                        ["actions"] = new JArray(),
                        ["bindings"] = new JArray(),
                    }
                );
                return id;
            }
            JObject selectedMap = Select(Array(document, "maps"), ManageInputActions.Text(request, "map_id", true, 36));
            if (operation == "add_action")
            {
                string name = RequestName(request);
                JArray actions = Array(selectedMap, "actions");
                if (Objects(actions).Any(action => string.Equals((string)action["name"], name, StringComparison.OrdinalIgnoreCase)))
                    throw new ArgumentException("An action with this name already exists.");
                string type = ManageInputActions.Text(request, "action_type", true, 16);
                if (type != "Button" && type != "Value" && type != "PassThrough")
                    throw new ArgumentException("action_type must be Button, Value or PassThrough.");
                string id = Guid.NewGuid().ToString("D");
                var action = new JObject
                {
                    ["name"] = name,
                    ["id"] = id,
                    ["type"] = type,
                    ["initialStateCheck"] = type == "Value",
                };
                Copy(request, action, "expected_control_type", "expectedControlType");
                Copy(request, action, "processors", "processors");
                Copy(request, action, "interactions", "interactions");
                actions.Add(action);
                return id;
            }
            JArray bindings = Array(selectedMap, "bindings");
            JObject selected;
            if (operation == "add_binding")
            {
                JObject action = Select(Array(selectedMap, "actions"), ManageInputActions.Text(request, "action_id", true, 36));
                selected = new JObject
                {
                    ["name"] = "",
                    ["id"] = Guid.NewGuid().ToString("D"),
                    ["action"] = (string)action["name"],
                    ["isComposite"] = false,
                    ["isPartOfComposite"] = false,
                };
            }
            else if (operation == "set_binding" || operation == "remove_binding")
            {
                selected = Select(bindings, ManageInputActions.Text(request, "binding_id", true, 36));
                if ((bool?)selected["isComposite"] == true || (bool?)selected["isPartOfComposite"] == true)
                    throw new ArgumentException("Composite bindings are preserved but cannot be edited or removed by this tool.");
                if (operation == "remove_binding")
                {
                    selected.Remove();
                    return null;
                }
                selected = (JObject)selected.DeepClone();
            }
            else
                throw new ArgumentException("Unknown InputAction authoring operation.");
            Copy(request, selected, "binding_path", "path", true);
            Copy(request, selected, "groups", "groups");
            Copy(request, selected, "processors", "processors");
            Copy(request, selected, "interactions", "interactions");
            if (Objects(bindings).Any(binding => (string)binding["id"] != (string)selected["id"] && SameBinding(selectedMap, binding, selected)))
                throw new ArgumentException("An equivalent action/path/group binding already exists.");
            if (operation == "add_binding")
                bindings.Add(selected);
            else
                Select(bindings, (string)selected["id"]).Replace(selected);
            return operation == "add_binding" ? (string)selected["id"] : null;
        }

        internal static List<JObject> Rows(JObject document)
        {
            var rows = new List<JObject>();
            foreach (JObject map in Objects(Array(document, "maps")))
            {
                rows.Add(
                    new JObject
                    {
                        ["kind"] = "map",
                        ["id"] = map["id"],
                        ["name"] = map["name"],
                    }
                );
                foreach (JObject action in Objects(Array(map, "actions")))
                    rows.Add(
                        new JObject
                        {
                            ["kind"] = "action",
                            ["map_id"] = map["id"],
                            ["id"] = action["id"],
                            ["name"] = action["name"],
                            ["type"] = action["type"],
                        }
                    );
                foreach (JObject binding in Objects(Array(map, "bindings")))
                {
                    var row = (JObject)binding.DeepClone();
                    row["kind"] = "binding";
                    row["map_id"] = map["id"];
                    rows.Add(row);
                }
            }
            return rows;
        }

        private static IEnumerable<string> Identities(JObject document)
        {
            // Input System omits empty arrays in its normalized JSON. Source validation remains strict.
            foreach (JObject map in Objects(NormalizedArray(document, "maps")))
            {
                yield return Guid.Parse((string)map["id"]).ToString("D");
                foreach (JObject action in Objects(NormalizedArray(map, "actions")))
                    yield return Guid.Parse((string)action["id"]).ToString("D");
                foreach (JObject binding in Objects(NormalizedArray(map, "bindings")))
                    yield return Guid.Parse((string)binding["id"]).ToString("D");
            }
        }

        private static JArray NormalizedArray(JObject value, string key) =>
            value[key] == null || value[key].Type == JTokenType.Null ? new JArray() : Array(value, key);

        private static JArray Array(JObject value, string key) => value[key] as JArray ?? throw new ArgumentException(key + " must be an array.");

        private static IEnumerable<JObject> Objects(JArray values) =>
            values.Select(value => value as JObject ?? throw new ArgumentException("Expected a JSON object."));

        private static JObject Select(JArray values, string id)
        {
            if (!Guid.TryParse(id, out Guid parsed))
                throw new ArgumentException("Selectors must be exact GUIDs returned by read.");
            return Objects(values).SingleOrDefault(value => Guid.Parse((string)value["id"]) == parsed)
                ?? throw new ArgumentException("The requested GUID was not found.");
        }

        private static void Id(JObject value, HashSet<Guid> ids)
        {
            if (value["id"]?.Type != JTokenType.String || !Guid.TryParse((string)value["id"], out Guid id) || id == Guid.Empty || !ids.Add(id))
                throw new ArgumentException("Every map, action and binding needs a unique nonempty GUID; implicit identity repair is refused.");
        }

        private static string Name(JObject value) => RequestName(value);

        private static string RequestName(JObject value)
        {
            string name = ManageInputActions.Text(value, "name", true, 128);
            if (name.Contains("/"))
                throw new ArgumentException("Map/action names cannot contain '/'.");
            return name;
        }

        private static void Copy(JObject source, JObject target, string key, string destination, bool required = false)
        {
            string value = ManageInputActions.Text(source, key, required, 512);
            if (value != null)
                target[destination] = value;
        }

        private static Guid ResolveActionIdentity(JObject map, string reference)
        {
            bool isId = Guid.TryParse(reference, out Guid id);
            JObject action = Objects(Array(map, "actions"))
                .SingleOrDefault(candidate =>
                    string.Equals((string)candidate["name"], reference, StringComparison.OrdinalIgnoreCase)
                    || (isId && Guid.Parse((string)candidate["id"]) == id)
                );
            return action == null ? throw new ArgumentException("A binding references an unavailable action in its map.") : Guid.Parse((string)action["id"]);
        }

        private static bool SameBinding(JObject map, JObject first, JObject second) =>
            ResolveActionIdentity(map, (string)first["action"]) == ResolveActionIdentity(map, (string)second["action"])
            && string.Equals((string)first["path"], (string)second["path"], StringComparison.OrdinalIgnoreCase)
            && string.Equals((string)first["groups"] ?? "", (string)second["groups"] ?? "", StringComparison.OrdinalIgnoreCase);
    }
}
