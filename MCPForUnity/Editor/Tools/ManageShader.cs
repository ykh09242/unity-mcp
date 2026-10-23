using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Rendering;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Handles CRUD operations for shader files within the Unity project.
    /// </summary>
    [McpForUnityTool("manage_shader", AutoRegister = false, Group = "vfx")]
    public static class ManageShader
    {
        private static readonly System.Text.UTF8Encoding StrictUtf8 = new System.Text.UTF8Encoding(false, true);

        /// <summary>
        /// Main handler for shader management actions.
        /// </summary>
        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");

            // Extract parameters
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            if (action == "inspect_graph")
                return ShaderGraphInspection.Inspect(@params);
            string name = @params["name"]?.ToString();
            string path = @params["path"]?.ToString(); // Relative to Assets/
            string contents = null;

            // Check if we have base64 encoded contents
            bool contentsEncoded;
            try
            {
                JToken encodingFlag = @params["contentsEncoded"];
                if (encodingFlag?.Type == JTokenType.Null)
                    return new ErrorResponse("Invalid contentsEncoded value: explicit null is not a boolean flag.");
                contentsEncoded = encodingFlag?.ReadScalar<bool?>() ?? false;
            }
            catch (Exception e) when (e is ArgumentException || e is FormatException || e is InvalidCastException || e is Newtonsoft.Json.JsonException)
            {
                return new ErrorResponse($"Invalid contentsEncoded value: {e.Message}");
            }
            if (contentsEncoded && @params["encodedContents"] != null)
            {
                try
                {
                    contents = DecodeBase64(@params["encodedContents"].ToString());
                }
                catch (Exception e)
                {
                    return new ErrorResponse($"Failed to decode shader contents: {e.Message}");
                }
            }
            else
            {
                contents = @params["contents"]?.ToString();
            }

            // Validate required parameters
            if (string.IsNullOrEmpty(action))
            {
                return new ErrorResponse("Action parameter is required.");
            }
            if (string.IsNullOrEmpty(name))
            {
                return new ErrorResponse("Name parameter is required.");
            }
            // Basic name validation (alphanumeric, underscores, cannot start with number)
            if (!Regex.IsMatch(name, @"^[a-zA-Z_][a-zA-Z0-9_]*$"))
            {
                return new ErrorResponse($"Invalid shader name: '{name}'. Use only letters, numbers, underscores, and don't start with a number.");
            }

            // Ensure path is relative to Assets/, removing any leading "Assets/"
            // Set default directory to "Shaders" if path is not provided
            string relativeDir = path ?? "Shaders"; // Default to "Shaders" if path is null
            if (!string.IsNullOrEmpty(relativeDir))
            {
                relativeDir = AssetPathUtility.NormalizeSeparators(relativeDir);
                if (
                    Path.IsPathRooted(relativeDir)
                    || relativeDir.Contains(":")
                    || relativeDir.Split('/').Any(segment => segment == ".." || segment.EndsWith(".") || segment.EndsWith(" "))
                )
                    return new ErrorResponse("Shader path must be a relative directory inside Assets.");
                relativeDir = relativeDir.TrimEnd('/');
                if (string.Equals(relativeDir, "Assets", StringComparison.OrdinalIgnoreCase))
                {
                    relativeDir = "";
                }
                else if (relativeDir.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
                {
                    relativeDir = relativeDir.Substring("Assets/".Length);
                }
            }
            // Only omitted or empty input selects the default; explicit Assets/ denotes the root.
            if (string.IsNullOrEmpty(relativeDir) && string.IsNullOrEmpty(path))
            {
                relativeDir = "Shaders";
            }

            // Construct paths
            string shaderFileName = $"{name}.shader";
            string fullPath;
            try
            {
                fullPath = SafePathUtility.ResolveWithinRoot(Application.dataPath, Path.Combine(relativeDir, shaderFileName));
            }
            catch (Exception ex)
                when (ex is ArgumentException
                    || ex is IOException
                    || ex is InvalidOperationException
                    || ex is UnauthorizedAccessException
                    || ex is NotSupportedException
                )
            {
                return new ErrorResponse($"Invalid shader path: {ex.Message}");
            }
            string relativePath = AssetPathUtility.NormalizeSeparators(Path.Combine("Assets", relativeDir, shaderFileName)); // Ensure "Assets/" prefix and forward slashes

            // Route to specific action handlers
            switch (action)
            {
                case "create":
                    return CreateShader(fullPath, relativePath, name, contents);
                case "read":
                    return ReadShader(fullPath, relativePath, @params);
                case "update":
                    return UpdateShader(fullPath, relativePath, name, contents);
                case "delete":
                    return DeleteShader(fullPath, relativePath);
                default:
                    return new ErrorResponse($"Unknown action: '{action}'. Valid actions are: create, read, update, delete.");
            }
        }

        /// <summary>
        /// Decode base64 string to normal text
        /// </summary>
        private static string DecodeBase64(string encoded)
        {
            byte[] data = Convert.FromBase64String(encoded);
            return StrictUtf8.GetString(data);
        }

        /// <summary>
        /// Encode text to base64 string
        /// </summary>
        private static string EncodeBase64(string text)
        {
            byte[] data = System.Text.Encoding.UTF8.GetBytes(text);
            return Convert.ToBase64String(data);
        }

        private static object CreateShader(string fullPath, string relativePath, string name, string contents)
        {
            // Check if shader already exists
            if (File.Exists(fullPath))
            {
                return new ErrorResponse($"Shader already exists at '{relativePath}'. Use 'update' action to modify.");
            }

            // Add validation for shader name conflicts in Unity
            if (Shader.Find(name) != null)
            {
                return new ErrorResponse($"A shader with name '{name}' already exists in the project. Choose a different name.");
            }

            // Generate default content if none provided
            if (string.IsNullOrEmpty(contents))
            {
                contents = GenerateDefaultShaderContent(name);
            }

            bool saved = false;
            bool imported = false;
            try
            {
                // Validate before preparing directories or opening a file, which could truncate existing content.
                StrictUtf8.GetByteCount(contents);
                using var folders = new AssetFolderScope();
                folders.EnsureParentDirectory(relativePath);
                fullPath = AssetPathUtility.GetFullAssetPath(relativePath);
                AtomicAssetFile.Create(fullPath, StrictUtf8.GetBytes(contents));
                saved = true;
                AssetDatabase.ImportAsset(relativePath, ImportAssetOptions.ForceSynchronousImport);
                imported = true;
                var response = ShaderWriteResponse(relativePath, "created");
                folders.Complete();
                return response;
            }
            catch (Exception e)
            {
                return new ErrorResponse(
                    $"Failed to create shader '{relativePath}': {e.Message}",
                    new
                    {
                        path = relativePath,
                        saved,
                        imported,
                    }
                );
            }
        }

        private static object ReadShader(string fullPath, string relativePath, JObject parameters)
        {
            if (!File.Exists(fullPath))
                return new ErrorResponse($"Shader not found at '{relativePath}'.");
            try
            {
                long offset = ReadInteger(parameters, "readOffset", 0, 0, long.MaxValue);
                int limit = (int)ReadInteger(parameters, "readLimit", ShaderSourceReader.DefaultLimit, 4, ShaderSourceReader.MaximumLimit);
                JToken encodingToken = parameters["readEncoding"];
                string outputEncoding =
                    encodingToken == null ? "legacy"
                    : encodingToken.Type == JTokenType.String ? (string)encodingToken
                    : null;
                if (outputEncoding != "text" && outputEncoding != "base64" && outputEncoding != "legacy")
                    throw new ArgumentException("readEncoding must be text, base64, or legacy.");
                var page = ShaderSourceReader.Read(fullPath, offset, limit);
                bool encoded = outputEncoding == "base64" || (outputEncoding == "legacy" && page.Text.Length > 10000);
                var responseData = new
                {
                    path = relativePath,
                    contents = outputEncoding == "base64" ? null : page.Text,
                    encodedContents = encoded ? EncodeBase64(page.Text) : null,
                    contentsEncoded = encoded,
                    sourceEncoding = page.EncodingName,
                    sourceSizeBytes = page.Size,
                    readOffset = offset,
                    readLimit = limit,
                    returnedBytes = page.Next - offset,
                    hasMore = page.Next < page.Size,
                    nextOffset = page.Next < page.Size ? (long?)page.Next : null,
                };
                return new SuccessResponse($"Shader '{Path.GetFileName(relativePath)}' page read successfully.", responseData);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to read shader '{relativePath}': {e.Message}");
            }
        }

        private static long ReadInteger(JObject parameters, string key, long fallback, long minimum, long maximum)
        {
            JToken value = parameters[key];
            if (value == null)
                return fallback;
            if (
                (value.Type != JTokenType.Integer && value.Type != JTokenType.String)
                || !long.TryParse(value.ToString(), System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out long parsed)
                || parsed < minimum
                || parsed > maximum
            )
                throw new ArgumentException($"{key} must be an integer from {minimum} to {maximum}.");
            return parsed;
        }

        private static object UpdateShader(string fullPath, string relativePath, string name, string contents)
        {
            if (!File.Exists(fullPath))
            {
                return new ErrorResponse($"Shader not found at '{relativePath}'. Use 'create' action to add a new shader.");
            }
            if (string.IsNullOrEmpty(contents))
            {
                return new ErrorResponse("Content is required for the 'update' action.");
            }

            bool saved = false;
            bool imported = false;
            try
            {
                string expected = AtomicAssetFile.DigestFile(fullPath);
                AtomicAssetFile.Replace(fullPath, StrictUtf8.GetBytes(contents), expected);
                saved = true;
                AssetDatabase.ImportAsset(relativePath, ImportAssetOptions.ForceSynchronousImport);
                imported = true;
                return ShaderWriteResponse(relativePath, "updated");
            }
            catch (Exception e)
            {
                return new ErrorResponse(
                    $"Failed to update shader '{relativePath}': {e.Message}",
                    new
                    {
                        path = relativePath,
                        saved,
                        imported,
                    }
                );
            }
        }

        private static object ShaderWriteResponse(string relativePath, string operation)
        {
            var compilation = CollectCompilationEvidence(relativePath);
            string status = (string)compilation["status"];
            string observation =
                status == "errors_reported" ? "Unity reported shader compilation errors."
                : status == "pending" ? "Asynchronous shader compilation is pending in the Editor."
                : status == "unavailable" ? "Shader compilation diagnostics are unavailable."
                : "No shader compilation errors were reported at the time of observation.";
            // Success describes saving/importing. Diagnostic errors retain the source for repair.
            return new SuccessResponse(
                $"Shader '{Path.GetFileName(relativePath)}' {operation}, saved and imported. {observation}",
                new
                {
                    path = relativePath,
                    saved = true,
                    imported = true,
                    compilation,
                }
            );
        }

        internal static Dictionary<string, object> CollectCompilationEvidence(string relativePath)
        {
            try
            {
                var shader = AssetDatabase.LoadAssetAtPath<Shader>(relativePath);
                if (shader == null)
                    return BuildCompilationEvidence(Array.Empty<ShaderMessage>(), false, false, "Unity did not expose an imported Shader object.");
                // These APIs report observed messages, not exhaustive variant/platform compilation.
                // ShaderHasError also fetches Unity's cached compiler messages in supported Editors.
                bool hasErrors = ShaderUtil.ShaderHasError(shader);
                return BuildCompilationEvidence(ShaderUtil.GetShaderMessages(shader), hasErrors, ShaderUtil.anythingCompiling, null);
            }
            catch (Exception ex)
            {
                return BuildCompilationEvidence(Array.Empty<ShaderMessage>(), false, false, ex.GetBaseException().Message);
            }
        }

        internal static Dictionary<string, object> BuildCompilationEvidence(
            ShaderMessage[] messages,
            bool shaderHasError,
            bool globallyCompiling,
            string unavailableReason
        )
        {
            const int messageLimit = 100;
            const int textLimit = 2048;
            const int fileLimit = 512;
            int errors = messages.Count(message => message.severity == ShaderCompilerMessageSeverity.Error);
            int warnings = messages.Count(message => message.severity == ShaderCompilerMessageSeverity.Warning);
            bool hasErrors = shaderHasError || errors > 0;
            bool truncated = messages.Length > messageLimit;

            string Bound(string value, int limit)
            {
                if (value == null)
                    return "";
                if (value.Length <= limit)
                    return value;
                truncated = true;
                if (char.IsHighSurrogate(value[limit - 1]))
                    limit--;
                return value.Substring(0, limit);
            }

            // Prioritize actual errors before bounding the payload, while counting all observations.
            var diagnostics = messages
                .OrderBy(message => message.severity == ShaderCompilerMessageSeverity.Error ? 0 : 1)
                .Take(messageLimit)
                .Select(message => new
                {
                    severity = message.severity.ToString().ToLowerInvariant(),
                    message = Bound(message.message, textLimit),
                    message_details = Bound(message.messageDetails, textLimit),
                    file = Bound(message.file, fileLimit),
                    line = message.line,
                    platform = message.platform.ToString(),
                })
                .ToArray();
            string boundedReason = Bound(unavailableReason, textLimit);
            return new Dictionary<string, object>
            {
                ["status"] =
                    unavailableReason != null ? "unavailable"
                    : hasErrors ? "errors_reported"
                    : globallyCompiling ? "pending"
                    : "no_errors_reported",
                ["has_errors"] = unavailableReason == null ? (object)hasErrors : null,
                ["global_compilation_pending"] = unavailableReason == null ? (object)globallyCompiling : null,
                ["scope"] = "observed_shader_messages",
                ["active_build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString(),
                ["all_variants_checked"] = false,
                ["all_platforms_checked"] = false,
                ["error_count"] = unavailableReason == null ? (object)errors : null,
                ["warning_count"] = unavailableReason == null ? (object)warnings : null,
                ["total_messages"] = unavailableReason == null ? (object)messages.Length : null,
                ["returned_messages"] = diagnostics.Length,
                ["messages"] = diagnostics,
                ["truncated"] = truncated,
                ["unavailable_reason"] = boundedReason,
                ["limitations"] =
                    "Only Unity's currently reported messages are observed after synchronous asset import. Other variants/platforms may remain uncompiled; absence of errors is not proof of compilation. Pending compilation is an Editor-wide signal and may belong to another shader.",
            };
        }

        private static object DeleteShader(string fullPath, string relativePath)
        {
            if (!File.Exists(fullPath))
            {
                return new ErrorResponse($"Shader not found at '{relativePath}'.");
            }

            try
            {
                // Delete the asset through Unity's AssetDatabase first
                bool success = AssetDatabase.DeleteAsset(relativePath);
                if (!success)
                {
                    return new ErrorResponse($"Failed to delete shader through Unity's AssetDatabase: '{relativePath}'");
                }

                // If the file still exists (rare case), try direct deletion
                if (File.Exists(fullPath))
                {
                    File.Delete(fullPath);
                }

                return new SuccessResponse($"Shader '{Path.GetFileName(relativePath)}' deleted successfully.");
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to delete shader '{relativePath}': {e.Message}");
            }
        }

        //This is a CGProgram template
        //TODO: making a HLSL template as well?
        private static string GenerateDefaultShaderContent(string name)
        {
            return @"Shader """
                + name
                + @"""
        {
            Properties
            {
                _MainTex (""Texture"", 2D) = ""white"" {}
            }
            SubShader
            {
                Tags { ""RenderType""=""Opaque"" }
                LOD 100

                Pass
                {
                    CGPROGRAM
                    #pragma vertex vert
                    #pragma fragment frag
                    #include ""UnityCG.cginc""

                    struct appdata
                    {
                        float4 vertex : POSITION;
                        float2 uv : TEXCOORD0;
                    };

                    struct v2f
                    {
                        float2 uv : TEXCOORD0;
                        float4 vertex : SV_POSITION;
                    };

                    sampler2D _MainTex;
                    float4 _MainTex_ST;

                    v2f vert (appdata v)
                    {
                        v2f o;
                        o.vertex = UnityObjectToClipPos(v.vertex);
                        o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                        return o;
                    }

                    fixed4 frag (v2f i) : SV_Target
                    {
                        fixed4 col = tex2D(_MainTex, i.uv);
                        return col;
                    }
                    ENDCG
                }
            }
        }";
        }
    }
}
