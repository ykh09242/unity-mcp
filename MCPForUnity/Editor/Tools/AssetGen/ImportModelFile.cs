using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Import;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.AssetGen
{
    /// <summary>
    /// Import a local 3D model file already within Assets (e.g. exported from Blender/Maya) into the
    /// Unity project. DCC-agnostic and key-free: the file is copied under Assets/ and run through
    /// the shared ModelImportPipeline (glTFast/FBX/OBJ/zip handling, scale-normalize, material
    /// settings). Placement into the scene is the caller's job (kept single-purpose).
    /// </summary>
    [McpForUnityTool("import_model_file", AutoRegister = false, Group = "asset_gen")]
    public static class ImportModelFile
    {
        private static readonly string[] SupportedExt = { ".fbx", ".obj", ".glb", ".gltf", ".zip" };

        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");
            var p = new ToolParams(@params);
            try
            {
                string source = p.Get("sourcePath");
                if (string.IsNullOrWhiteSpace(source))
                    return new ErrorResponse("'source_path' is required.");

                string srcAbs = ResolveSource(source);
                if (!File.Exists(srcAbs))
                    return new ErrorResponse($"Source file not found: {source}");

                string ext = Path.GetExtension(srcAbs).ToLowerInvariant();
                if (Array.IndexOf(SupportedExt, ext) < 0)
                    return new ErrorResponse($"Unsupported model extension '{ext}'. Supported: .fbx, .obj, .glb, .gltf, .zip.");
                if ((ext == ".glb" || ext == ".gltf") && !ModelImportPipeline.IsGltfastAvailable())
                    return new ErrorResponse("GLB import requires glTFast. Install it from the MCP for Unity → Dependencies tab, or choose FBX output.");

                string baseName = p.Get("name");
                if (string.IsNullOrWhiteSpace(baseName))
                    baseName = Path.GetFileNameWithoutExtension(srcAbs);

                string destRel = StageUnderAssets(srcAbs, baseName, ext, p.Get("outputFolder"));

                var job = new AssetGenJob { TargetSize = p.GetFloat("targetSize", 1f) ?? 1f, AnimationType = p.Get("animationType") };
                AssetGenJob result = ModelImportPipeline.ImportInto(job, destRel);

                if (result == null || result.State == AssetGenJobState.Failed)
                    return new ErrorResponse(result?.Error ?? "Import failed.");

                return new SuccessResponse($"Imported model: {result.AssetPath}", new { asset_path = result.AssetPath, asset_guid = result.AssetGuid });
            }
            catch (Exception e)
            {
                return new ErrorResponse(SecretRedactor.Scrub(e.Message));
            }
        }

        private static string ResolveSource(string source)
        {
            string s = source.Replace('\\', '/');
            if (
                Array.IndexOf(s.Split('/'), "..") >= 0
                || (s.Length >= 2 && s[1] == ':' && (s.Length < 3 || s[2] != '/'))
                || !AssetGenPaths.TryGetAssetsRelativePath(s, out string relative)
            )
                throw new ArgumentException("'source_path' must resolve under the project's Assets folder without traversal or links.");

            // Validate the source's physical boundary before existence checks or staging.
            // Contained absolute paths retain the same contract as Assets-relative paths.
            return AssetGenPaths.ToAbsolute(relative);
        }

        private static string StageUnderAssets(string srcAbs, string baseName, string ext, string outputFolder)
        {
            string root = !string.IsNullOrWhiteSpace(outputFolder) ? outputFolder : AssetGenPrefs.OutputRoot + "/Imported";
            if (!AssetGenPaths.TryGetAssetsFolder(root, out root))
            {
                if (!string.IsNullOrWhiteSpace(outputFolder))
                    throw new ArgumentException("'output_folder' must resolve under the project's Assets folder.");
                root = AssetGenPrefs.DefaultOutputRoot + "/Imported";
            }

            using var folders = new AssetFolderScope();
            folders.EnsureFolder(root);

            string safe = SanitizeName(baseName);
            string fileName = safe + ext;
            string abs = AssetGenPaths.ToAbsolute(root.TrimEnd('/') + "/" + fileName);
            int n = 1;
            while (File.Exists(abs))
            {
                fileName = safe + "_" + n++ + ext;
                abs = AssetGenPaths.ToAbsolute(root.TrimEnd('/') + "/" + fileName);
            }

            File.Copy(AssetGenPaths.ToAbsolute(srcAbs), AssetGenPaths.ToAbsolute(abs));
            folders.Complete();
            return (root.TrimEnd('/') + "/" + fileName).Replace('\\', '/');
        }

        private static string SanitizeName(string raw)
        {
            if (string.IsNullOrWhiteSpace(raw))
                return "model";
            foreach (char c in Path.GetInvalidFileNameChars())
                raw = raw.Replace(c, '_');
            return raw.Trim();
        }
    }
}
