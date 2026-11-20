using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.ScriptQuality;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEditor.PackageManager;
using UnityEditorInternal;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    public static class VerificationConfiguration
    {
        private static string Required(JObject options, string field, int limit)
        {
            string value = options[field]?.Type == JTokenType.String ? (string)options[field] : null;
            if (string.IsNullOrWhiteSpace(value) || value.Length > limit)
                throw new ArgumentException("invalid_configuration_" + field);
            return value;
        }

        private static string AssetPath(string path, string extension = null)
        {
            if (
                !(path == "Assets" || path.StartsWith("Assets/", StringComparison.Ordinal))
                || path.Any(c => char.IsControl(c) || "\\:*?\"<>|".Contains(c))
                || path.Split('/').Any(p => p.Length == 0 || p.StartsWith(".", StringComparison.Ordinal) || p.EndsWith("~", StringComparison.Ordinal))
                || (path + "/").IndexOf("/Resources/GameData/", StringComparison.OrdinalIgnoreCase) >= 0
                || (extension != null && !path.EndsWith(extension, StringComparison.OrdinalIgnoreCase))
            )
                throw new ArgumentException("invalid_configuration_asset_path");
            return AssetPathUtility.GetContainedAssetPath(path);
        }

        private static string Digest(string path) => ScriptSelection.Digest(ScriptSelection.Read(ScriptSelection.Physical(path), 32 * 1024 * 1024));

        public static JObject Observe(JObject options)
        {
            if (EditorApplication.isCompiling || EditorApplication.isUpdating || EditorUtility.scriptCompilationFailed)
                throw new InvalidOperationException("configuration_import_or_compilation_unsettled");
            string path = Required(options, "path", 512);
            JObject observation;
            switch ((string)options["action"])
            {
                case "observe_assembly":
                    observation = ObserveAssembly(AssetPath(path, ".asmdef"), AssetPath(Required(options, "source", 512), ".cs"));
                    break;
                case "observe_texture":
                    observation = ObserveTexture(
                        AssetPath(path, ".meta"),
                        AssetPath(Required(options, "search_folder", 512)),
                        Required(options, "platform", 64)
                    );
                    break;
                default:
                    throw new ArgumentException("invalid_configuration_action");
            }
            observation["schema_version"] = 1;
            observation["unity_version"] = Application.unityVersion;
            observation["target"] = EditorUserBuildSettings.activeBuildTarget.ToString();
            observation["project_identity"] = ScriptSelection.Digest(Encoding.UTF8.GetBytes(ScriptAnalysisHost.ProjectRoot));
            observation["path"] = path;
            observation["observation_revision"] = ScriptSelection.Digest(Encoding.UTF8.GetBytes(FingerprintInput(observation)));
            return observation;
        }

        // Length-independent ASCII encoding shared with the offline reader. Keys and scalar strings use UTF-8 base64.
        private static string FingerprintInput(JToken value)
        {
            string Encode(string text) => Convert.ToBase64String(Encoding.UTF8.GetBytes(text));
            if (value is JObject obj)
                return "{"
                    + string.Concat(
                        obj.Properties().OrderBy(p => Encode(p.Name), StringComparer.Ordinal).Select(p => Encode(p.Name) + ":" + FingerprintInput(p.Value))
                    )
                    + "}";
            if (value is JArray array)
                return "[" + string.Concat(array.Select(FingerprintInput)) + "]";
            return value.Type == JTokenType.String ? "s" + Encode((string)value) + ";" : value.ToString(Newtonsoft.Json.Formatting.None) + ";";
        }

        private static JObject ObserveAssembly(string path, string source)
        {
            var imported = AssetDatabase.LoadAssetAtPath<AssemblyDefinitionAsset>(path);
            if (imported == null)
                throw new ArgumentException("assembly_definition_unavailable");
            byte[] bytes = ScriptSelection.Read(ScriptSelection.Physical(path), 65536);
            string text = ScriptSelection.Text(bytes);
            if (imported.text.Replace("\r\n", "\n") != text.Replace("\r\n", "\n"))
                throw new InvalidOperationException("assembly_definition_import_unsettled");
            JObject definition = JObject.Parse(text);
            string name = (string)definition["name"];
            string ownerPath = CompilationPipeline.GetAssemblyDefinitionFilePathFromScriptPath(source);
            if (
                string.IsNullOrEmpty(ownerPath)
                || !string.Equals(
                    Path.GetFullPath(ownerPath),
                    Path.GetFullPath(path),
                    Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal
                )
            )
                throw new ArgumentException("source_does_not_belong_to_selected_assembly_definition");
            var conditions = new JObject();
            foreach (string key in new[] { "includePlatforms", "excludePlatforms", "defineConstraints", "versionDefines" })
                conditions[key] = definition[key]?.DeepClone() ?? new JArray();
            var packages = PackageInfo.GetAllRegisteredPackages().ToDictionary(p => p.name, p => p.version, StringComparer.Ordinal);
            var versions = new JObject();
            var versionSymbols = new JObject();
            string[] editorDefines = CompilationPipeline.GetDefinesFromAssemblyName(name);
            foreach (JToken rule in conditions["versionDefines"])
            {
                string resource = (string)rule["name"];
                if (resource != null)
                    versions[resource] =
                        resource == "Unity" ? Application.unityVersion
                        : packages.TryGetValue(resource, out string version) ? version
                        : null;
                string symbol = (string)rule["define"];
                if (symbol != null && editorDefines != null)
                    versionSymbols[symbol] = editorDefines.Contains(symbol);
            }
            JObject Compiler(AssembliesType kind)
            {
                var assembly = CompilationPipeline.GetAssemblies(kind).SingleOrDefault(a => a.name == name);
                string absolute = ScriptSelection.Physical(source);
                var pathComparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
                return new JObject
                {
                    ["assemblies_type"] = kind.ToString(),
                    ["included"] = assembly != null,
                    ["source_included"] = assembly != null && assembly.sourceFiles.Any(file => string.Equals(Path.GetFullPath(file), absolute, pathComparison)),
                    ["defines"] = assembly == null ? null : new JArray(assembly.defines.Distinct().OrderBy(d => d, StringComparer.Ordinal)),
                };
            }
            return new JObject
            {
                ["kind"] = "assembly",
                ["guid"] = AssetDatabase.AssetPathToGUID(path),
                ["assembly"] = name,
                ["source"] = source,
                ["source_sha256"] = Digest(source),
                ["asmdef_sha256"] = ScriptSelection.Digest(bytes),
                ["conditions"] = conditions,
                ["resource_versions"] = versions,
                ["editor_version_symbols"] = versionSymbols,
                ["target_platforms"] = new JArray(
                    CompilationPipeline
                        .GetAssemblyDefinitionPlatforms()
                        .Where(p => p.BuildTarget == EditorUserBuildSettings.activeBuildTarget)
                        .Select(p => p.Name)
                ),
                ["editor"] = Compiler(AssembliesType.Editor),
                ["player"] = Compiler(AssembliesType.PlayerWithoutTestAssemblies),
            };
        }

        private static JObject ObserveTexture(string meta, string folder, string platform)
        {
            string path = AssetPath(meta.Substring(0, meta.Length - ".meta".Length));
            if (!(AssetImporter.GetAtPath(path) is TextureImporter importer) || !AssetDatabase.IsValidFolder(folder))
                throw new ArgumentException("texture_or_reference_search_folder_unavailable");
            string[] Find(string type, string suffix) =>
                AssetDatabase
                    .FindAssets("t:" + type, new[] { folder })
                    .Select(AssetDatabase.GUIDToAssetPath)
                    .Where(p => p.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
                    .Distinct()
                    .OrderBy(p => p, StringComparer.Ordinal)
                    .ToArray();
            string[] materials = Find("Material", ".mat");
            string[] scenes = Find("Scene", ".unity");
            if (materials.Length + scenes.Length > 512)
                throw new InvalidOperationException("reference_search_limit_choose_a_smaller_folder");

            var edges = new JArray();
            var affected = new HashSet<string>(StringComparer.Ordinal);
            void Edge(string owner, string target)
            {
                if (edges.Count == 256)
                    throw new InvalidOperationException("reference_edge_limit_choose_a_smaller_folder");
                edges.Add(new JObject { ["owner"] = owner, ["target"] = target });
            }
            foreach (string material in materials)
                if (AssetDatabase.GetDependencies(AssetPath(material), false).Contains(path))
                {
                    affected.Add(material);
                    Edge(material, path);
                }
            foreach (string scene in scenes)
            foreach (string material in AssetDatabase.GetDependencies(AssetPath(scene), false).Where(affected.Contains).OrderBy(p => p, StringComparer.Ordinal))
                Edge(scene, material);
            string[] owners = edges.Select(e => (string)e["owner"]).Distinct().OrderBy(p => p, StringComparer.Ordinal).ToArray();
            var ownerInputs = new JObject();
            long remaining = 32 * 1024 * 1024;
            foreach (string input in owners.SelectMany(p => new[] { p, p + ".meta" }))
            {
                string physical = ScriptSelection.Physical(input);
                var info = new FileInfo(physical);
                if (info.Exists && info.Length <= remaining)
                {
                    byte[] content = ScriptSelection.Read(physical, (int)remaining);
                    remaining -= content.Length;
                    ownerInputs[input] = ScriptSelection.Digest(content);
                }
                else
                    ownerInputs[input] = JValue.CreateNull();
            }
            var settings = importer.GetPlatformTextureSettings(platform);
            return new JObject
            {
                ["kind"] = "texture",
                ["guid"] = AssetDatabase.AssetPathToGUID(path),
                ["source_sha256"] = Digest(path),
                ["meta_sha256"] = Digest(meta),
                ["search_folder"] = folder,
                ["platform"] = platform,
                ["importer"] = new JObject
                {
                    ["sRGBTexture"] = importer.sRGBTexture,
                    ["textureCompression"] = importer.textureCompression.ToString(),
                    ["overridden"] = settings.overridden,
                    ["platformFormat"] = settings.format.ToString(),
                    ["platformTextureCompression"] = settings.textureCompression.ToString(),
                    ["platformMaxSize"] = settings.maxTextureSize,
                    ["platformCompressionQuality"] = settings.compressionQuality,
                },
                ["references"] = edges,
                ["owner_inputs"] = ownerInputs,
                ["dependency_hashes"] = JObject.FromObject(owners.ToDictionary(p => p, p => AssetDatabase.GetAssetDependencyHash(p).ToString())),
                ["coverage"] = "direct_material_and_saved_scene_dependencies_in_selected_folder",
            };
        }
    }
}
