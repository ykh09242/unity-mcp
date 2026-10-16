using System;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Resources.Project
{
    /// <summary>
    /// Provides static project configuration information.
    /// </summary>
    [McpForUnityResource("get_project_info")]
    public static class ProjectInfo
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                string assetsPath = Application.dataPath.Replace('\\', '/');
                string projectRoot = Directory.GetParent(assetsPath)?.FullName.Replace('\\', '/');
                string projectName = Path.GetFileName(projectRoot);

                var info = new
                {
                    projectRoot = projectRoot ?? "",
                    projectName = projectName ?? "",
                    unityVersion = Application.unityVersion,
                    platform = EditorUserBuildSettings.activeBuildTarget.ToString(),
                    assetsPath = assetsPath,
                    renderPipeline = RenderPipelineUtility.GetActivePipeline().ToString(),
                    activeInputHandler = GetActiveInputHandler(),
                    mcpPackage = CaptureDoctorPackageMetadata(),
                    packages = new
                    {
                        ugui = IsPackageInstalled("com.unity.ugui"),
                        textmeshpro = IsPackageInstalled("com.unity.textmeshpro"),
                        inputsystem = IsPackageInstalled("com.unity.inputsystem"),
                        uiToolkit = true,
                        screenCapture = true,
                    },
                };

                return new SuccessResponse("Retrieved project info.", info);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error getting project info: {e.Message}");
            }
        }

        private static JObject CaptureDoctorPackageMetadata()
        {
            string sourceOverride = EditorPrefs.GetString(EditorPrefKeys.GitUrlOverride, string.Empty);
            JObject package = null;
            try
            {
                // Scope identity to the package owning this assembly. No AssetDatabase or filesystem search.
                var info = PackageInfo.FindForAssembly(typeof(ProjectInfo).Assembly);
                if (info != null && info.name == "com.ykh09242.unity-mcp")
                    package = ReadDoctorPackageMetadata(info.resolvedPath);
            }
            catch (Exception)
            {
                // Optional diagnostics must not break the established project-info read or expose local paths.
            }
            return BuildDoctorPackageMetadata(package, sourceOverride);
        }

        private static JObject ReadDoctorPackageMetadata(string packageRoot)
        {
            const int maximumBytes = 65536;
            if (string.IsNullOrEmpty(packageRoot) || !Path.IsPathRooted(packageRoot))
                return null;
            try
            {
                using var stream = new FileStream(
                    Path.Combine(packageRoot, "package.json"),
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete
                );
                if (stream.Length > maximumBytes)
                    return null;
                var bytes = new byte[maximumBytes + 1];
                int count = 0;
                while (count < bytes.Length)
                {
                    int read = stream.Read(bytes, count, bytes.Length - count);
                    if (read == 0)
                        break;
                    count += read;
                }
                if (count > maximumBytes)
                    return null;
                string json = new UTF8Encoding(false, true).GetString(bytes, 0, count);
                if (json.Length > 0 && json[0] == '\uFEFF')
                    json = json.Substring(1);
                using var text = new StringReader(json);
                using var reader = new JsonTextReader(text) { MaxDepth = 8, DateParseHandling = DateParseHandling.None };
                var result = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                return reader.Read() ? null : result;
            }
            catch (Exception error)
                when (error is IOException
                    || error is UnauthorizedAccessException
                    || error is JsonException
                    || error is DecoderFallbackException
                    || error is ArgumentException
                )
            {
                return null;
            }
        }

        // Project-info runs on the Editor main thread. Do not use the source resolver:
        // it normalizes and persists local overrides. This projection never changes them.
        private static JObject BuildDoctorPackageMetadata(JObject package, string sourceOverride)
        {
            bool knownPackage = package?["name"]?.Type == JTokenType.String && (string)package["name"] == "com.ykh09242.unity-mcp";
            string version = knownPackage && package?["version"]?.Type == JTokenType.String ? (string)package["version"] : null;
            if (version == null || version.Length > 80 || !Regex.IsMatch(version, @"\A[0-9][0-9A-Za-z+_.-]{0,79}\z"))
                version = null;
            string declared =
                knownPackage && package?["mcpServerSource"]?.Type == JTokenType.String ? DoctorSourceCommit((string)package["mcpServerSource"]) : null;
            bool hasOverride = !string.IsNullOrEmpty(sourceOverride);
            string effective = hasOverride ? DoctorSourceCommit(sourceOverride) : declared;
            string skill = knownPackage && package?["mcpSkillRevision"]?.Type == JTokenType.String ? DoctorCommit((string)package["mcpSkillRevision"]) : null;
            return new JObject
            {
                ["name"] = knownPackage ? "com.ykh09242.unity-mcp" : null,
                ["version"] = version,
                ["packageServerCommit"] = declared,
                ["effectiveSourceKind"] = hasOverride
                    ? (effective == null ? "override_unpinned" : "override_pin")
                    : (declared == null ? "unavailable" : "package_pin"),
                ["effectiveServerCommit"] = effective,
                ["skillRevision"] = skill,
            };
        }

        private static string DoctorCommit(string value)
        {
            if (value == null || value.Length != 40 || !Regex.IsMatch(value, @"\A[0-9a-fA-F]{40}\z") || value == new string('0', 40))
                return null;
            return value.ToLowerInvariant();
        }

        private static string DoctorSourceCommit(string source)
        {
            if (source == null || source.Length > 256)
                return null;
            var archive = Regex.Match(source, @"\Ahttps://github\.com/ykh09242/unity-mcp/archive/([0-9a-fA-F]{40})\.zip#subdirectory=Server\z");
            var vcs = Regex.Match(source, @"\Agit\+https://github\.com/ykh09242/unity-mcp\.git@([0-9a-fA-F]{40})#subdirectory=Server\z");
            return DoctorCommit(
                archive.Success ? archive.Groups[1].Value
                : vcs.Success ? vcs.Groups[1].Value
                : null
            );
        }

        /// <summary>
        /// Reads PlayerSettings.activeInputHandler via reflection to avoid
        /// compile-time dependency on the Input System package.
        /// Returns "Old" (0), "New" (1), or "Both" (2).
        /// </summary>
        private static string GetActiveInputHandler()
        {
            try
            {
                var prop = typeof(PlayerSettings).GetProperty("activeInputHandler", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);

                if (prop == null)
                    return "Old";

                int value = (int)prop.GetValue(null);
                return value switch
                {
                    0 => "Old",
                    1 => "New",
                    2 => "Both",
                    _ => "Old",
                };
            }
            catch
            {
                return "Old";
            }
        }

        private static bool IsPackageInstalled(string packageName)
        {
            try
            {
                return PackageInfo.FindForAssetPath("Packages/" + packageName) != null;
            }
            catch
            {
                return false;
            }
        }
    }
}
