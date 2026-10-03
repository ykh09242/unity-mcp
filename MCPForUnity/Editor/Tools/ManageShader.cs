using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
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
            if (@params == null) return new ErrorResponse("Parameters cannot be null.");

            // Extract parameters
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            string name = @params["name"]?.ToString();
            string path = @params["path"]?.ToString(); // Relative to Assets/
            string contents = null;

            // Check if we have base64 encoded contents
            bool contentsEncoded;
            try
            {
                contentsEncoded = @params["contentsEncoded"]?.ToObject<bool>() ?? false;
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
                return new ErrorResponse(
                    $"Invalid shader name: '{name}'. Use only letters, numbers, underscores, and don't start with a number."
                );
            }

            // Ensure path is relative to Assets/, removing any leading "Assets/"
            // Set default directory to "Shaders" if path is not provided
            string relativeDir = path ?? "Shaders"; // Default to "Shaders" if path is null
            if (!string.IsNullOrEmpty(relativeDir))
            {
                relativeDir = AssetPathUtility.NormalizeSeparators(relativeDir);
                if (Path.IsPathRooted(relativeDir) || relativeDir.Contains(":") ||
                    relativeDir.Split('/').Any(segment => segment == ".." || segment.EndsWith(".") || segment.EndsWith(" ")))
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
            // Handle empty string case explicitly after processing
            if (string.IsNullOrEmpty(relativeDir))
            {
                relativeDir = "Shaders"; // Ensure default if path was provided as "" or only "/" or "Assets/"
            }

            // Construct paths
            string shaderFileName = $"{name}.shader";
            string fullPath;
            try
            {
                fullPath = SafePathUtility.ResolveWithinRoot(Application.dataPath, Path.Combine(relativeDir, shaderFileName));
            }
            catch (Exception ex) when (ex is ArgumentException || ex is IOException || ex is InvalidOperationException || ex is UnauthorizedAccessException || ex is NotSupportedException)
            {
                return new ErrorResponse($"Invalid shader path: {ex.Message}");
            }
            string relativePath = AssetPathUtility.NormalizeSeparators(
                Path.Combine("Assets", relativeDir, shaderFileName)
            ); // Ensure "Assets/" prefix and forward slashes

            // Route to specific action handlers
            switch (action)
            {
                case "create":
                    return CreateShader(fullPath, relativePath, name, contents);
                case "read":
                    return ReadShader(fullPath, relativePath);
                case "update":
                    return UpdateShader(fullPath, relativePath, name, contents);
                case "delete":
                    return DeleteShader(fullPath, relativePath);
                default:
                    return new ErrorResponse(
                        $"Unknown action: '{action}'. Valid actions are: create, read, update, delete."
                    );
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

        private static object CreateShader(
            string fullPath,
            string relativePath,
            string name,
            string contents
        )
        {
            // Check if shader already exists
            if (File.Exists(fullPath))
            {
                return new ErrorResponse(
                    $"Shader already exists at '{relativePath}'. Use 'update' action to modify."
                );
            }

            // Add validation for shader name conflicts in Unity
            if (Shader.Find(name) != null)
            {
                return new ErrorResponse(
                    $"A shader with name '{name}' already exists in the project. Choose a different name."
                );
            }

            // Generate default content if none provided
            if (string.IsNullOrEmpty(contents))
            {
                contents = GenerateDefaultShaderContent(name);
            }

            try
            {
                // Validate before preparing directories or opening a file, which could truncate existing content.
                StrictUtf8.GetByteCount(contents);
                string fullPathDir = Path.GetDirectoryName(fullPath);
                if (!Directory.Exists(fullPathDir))
                {
                    Directory.CreateDirectory(fullPathDir);
                    AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                }
                File.WriteAllText(fullPath, contents, StrictUtf8);
                AssetDatabase.ImportAsset(relativePath);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport); // Ensure Unity recognizes the new shader
                return new SuccessResponse(
                    $"Shader '{name}.shader' created successfully at '{relativePath}'.",
                    new { path = relativePath }
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to create shader '{relativePath}': {e.Message}");
            }
        }

        private static object ReadShader(string fullPath, string relativePath)
        {
            if (!File.Exists(fullPath))
            {
                return new ErrorResponse($"Shader not found at '{relativePath}'.");
            }

            try
            {
                string contents = ReadShaderContents(fullPath);

                // Return both normal and encoded contents for larger files
                //TODO: Consider a threshold for large files
                bool isLarge = contents.Length > 10000; // If content is large, include encoded version
                var responseData = new
                {
                    path = relativePath,
                    contents = contents,
                    // For large files, also include base64-encoded version
                    encodedContents = isLarge ? EncodeBase64(contents) : null,
                    contentsEncoded = isLarge,
                };

                return new SuccessResponse(
                    $"Shader '{Path.GetFileName(relativePath)}' read successfully.",
                    responseData
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to read shader '{relativePath}': {e.Message}");
            }
        }

        private static string ReadShaderContents(string fullPath)
        {
            byte[] bytes = File.ReadAllBytes(fullPath);
            System.Text.Encoding encoding = StrictUtf8;
            int offset = 0;
            // Match ReadAllText's Unicode BOM detection, with strict decoding for every encoding.
            // UTF32 LE must precede UTF16 LE because their BOM prefixes overlap.
            if (bytes.Length >= 4 && bytes[0] == 0xff && bytes[1] == 0xfe && bytes[2] == 0 && bytes[3] == 0)
            {
                encoding = new System.Text.UTF32Encoding(false, false, true);
                offset = 4;
            }
            else if (bytes.Length >= 4 && bytes[0] == 0 && bytes[1] == 0 && bytes[2] == 0xfe && bytes[3] == 0xff)
            {
                encoding = new System.Text.UTF32Encoding(true, false, true);
                offset = 4;
            }
            else if (bytes.Length >= 3 && bytes[0] == 0xef && bytes[1] == 0xbb && bytes[2] == 0xbf)
                offset = 3;
            else if (bytes.Length >= 2 && bytes[0] == 0xff && bytes[1] == 0xfe)
            {
                encoding = new System.Text.UnicodeEncoding(false, false, true);
                offset = 2;
            }
            else if (bytes.Length >= 2 && bytes[0] == 0xfe && bytes[1] == 0xff)
            {
                encoding = new System.Text.UnicodeEncoding(true, false, true);
                offset = 2;
            }
            return encoding.GetString(bytes, offset, bytes.Length - offset);
        }

        private static object UpdateShader(
            string fullPath,
            string relativePath,
            string name,
            string contents
        )
        {
            if (!File.Exists(fullPath))
            {
                return new ErrorResponse(
                    $"Shader not found at '{relativePath}'. Use 'create' action to add a new shader."
                );
            }
            if (string.IsNullOrEmpty(contents))
            {
                return new ErrorResponse("Content is required for the 'update' action.");
            }

            try
            {
                StrictUtf8.GetByteCount(contents);
                File.WriteAllText(fullPath, contents, StrictUtf8);
                AssetDatabase.ImportAsset(relativePath);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                return new SuccessResponse(
                    $"Shader '{Path.GetFileName(relativePath)}' updated successfully.",
                    new { path = relativePath }
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to update shader '{relativePath}': {e.Message}");
            }
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
            return @"Shader """ + name + @"""
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
