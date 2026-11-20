using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Services.ScriptQuality;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    public static class VerificationEnumValue
    {
        private const int MaxBytes = 4 * 1024 * 1024;

        // Inspect the imported prefab and retain the exact serialized files used for comparison.
        public static void CaptureFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string Arg(string key)
            {
                int i = Array.IndexOf(args, key);
                if (i < 0 || i + 1 >= args.Length || Array.LastIndexOf(args, key) != i)
                    throw new ArgumentException("Provide exactly one " + key);
                return args[i + 1];
            }
            string path = Arg("--enum-prefab");
            string field = Arg("--enum-field");
            string type = Arg("--enum-component");
            string output = PlayScenarioPlayerFiles.CheckedAbsolute(Path.GetFullPath(Arg("--enum-output")));
            string project = Path.GetFullPath(Path.GetDirectoryName(Application.dataPath));
            if (
                !Application.isBatchMode
                || EditorApplication.isCompiling
                || EditorApplication.isUpdating
                || EditorUtility.scriptCompilationFailed
                || !path.StartsWith("Assets/", StringComparison.Ordinal)
                || !path.EndsWith(".prefab", StringComparison.Ordinal)
                || path.IndexOfAny(new[] { '\\', ':' }) >= 0
                || path.Split('/').Any(s => s.StartsWith(".", StringComparison.Ordinal) || s == "")
                || path.IndexOf("/Resources/GameData/", StringComparison.OrdinalIgnoreCase) >= 0
                || !Regex.IsMatch(field, @"^[A-Za-z_][A-Za-z0-9_]*$")
                || PlayScenarioPlayerFiles.IsWithin(project, output)
                || Directory.Exists(output)
                || File.Exists(output)
            )
                throw new ArgumentException("Select a saved prefab, direct field, and new evidence directory outside the project");
            ScriptSelection.Physical(path);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (prefab == null)
                throw new ArgumentException("Prefab could not be imported");
            var components = prefab.GetComponentsInChildren<MonoBehaviour>(true).Where(c => c != null && c.GetType().FullName == type);
            int idOption = Array.IndexOf(args, "--enum-file-id");
            if (idOption >= 0)
            {
                long selected = long.Parse(Arg("--enum-file-id"), System.Globalization.CultureInfo.InvariantCulture);
                components = components.Where(c => AssetDatabase.TryGetGUIDAndLocalFileIdentifier(c, out string _, out long id) && id == selected);
            }
            var component = components.Single();
            if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(component, out string guid, out long localId) || guid != AssetDatabase.AssetPathToGUID(path))
                throw new ArgumentException("Select a component stored directly in this prefab");
            var matches = new List<FieldInfo>();
            for (Type owner = component.GetType(); owner != typeof(MonoBehaviour) && owner != null; owner = owner.BaseType)
            {
                var candidate = owner.GetField(field, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
                if (candidate != null)
                    matches.Add(candidate);
            }
            var info = matches.Single();
            Type enumType = info.FieldType;
            if (!enumType.IsEnum || Enum.GetUnderlyingType(enumType) != typeof(int))
                throw new ArgumentException("Select a serialized Int32 enum");
            using var serialized = new SerializedObject(component);
            var property = serialized.FindProperty(field);
            int value = Convert.ToInt32(info.GetValue(component));
            if (property == null || property.propertyType != SerializedPropertyType.Enum || property.intValue != value)
                throw new InvalidOperationException("Serialized and deserialized values disagree");
            JObject mapping = new();
            foreach (string name in Enum.GetNames(enumType))
                mapping.Add(name, Convert.ToInt32(Enum.Parse(enumType, name)));

            string script = ScriptSelection.Canonical(AssetDatabase.GetAssetPath(MonoScript.FromMonoBehaviour(component)));
            if (!script.StartsWith("Assets/", StringComparison.Ordinal) || !script.EndsWith(".cs", StringComparison.Ordinal))
                throw new ArgumentException("Select a project-owned prefab component script");
            var raw = new Dictionary<string, byte[]>();
            foreach (string input in new[] { path, path + ".meta", script, script + ".meta" })
                raw.Add(input, ScriptSelection.Read(ScriptSelection.Physical(input), MaxBytes));
            JObject hashes = new();
            Directory.CreateDirectory(output);
            foreach (var item in raw)
            {
                string destination = Path.Combine(output, item.Key);
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                using var file = new FileStream(destination, FileMode.CreateNew, FileAccess.Write);
                file.Write(item.Value, 0, item.Value.Length);
                hashes[item.Key] = ScriptSelection.Digest(item.Value);
            }
            var result = new JObject
            {
                ["schema"] = "prefab_enum_read@1",
                ["capture_id"] = Guid.NewGuid().ToString("N"),
                ["unity_version"] = Application.unityVersion,
                ["prefab"] = path,
                ["prefab_guid"] = guid,
                ["file_id"] = localId,
                ["script"] = script,
                ["script_guid"] = AssetDatabase.AssetPathToGUID(script),
                ["component"] = type,
                ["field"] = field,
                ["enum_type"] = enumType.FullName,
                ["assembly"] = enumType.Assembly.GetName().Name,
                ["mapping"] = mapping,
                ["value"] = value,
                ["inputs"] = hashes,
            };
            using var stream = new FileStream(Path.Combine(output, "enum.json"), FileMode.CreateNew, FileAccess.Write);
            using var writer = new StreamWriter(stream);
            writer.WriteLine(result);
        }
    }
}
