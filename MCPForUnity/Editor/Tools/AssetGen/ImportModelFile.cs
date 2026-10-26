using System;
using System.IO;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Import;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.AssetGen
{
    /// <summary>
    /// Import a local 3D model file already within Assets (e.g. exported from Blender/Maya) into the
    /// Unity project. DCC-agnostic and key-free: the file is copied under Assets/ and run through
    /// the shared ModelImportPipeline (glTFast/FBX/OBJ/zip handling, scale-normalize, material
    /// settings). A failed import can retain the staged file; response evidence distinguishes saved
    /// bytes, registered GUID, and usable mesh hierarchy. The original source is preserved.
    /// Usable does not certify materials, rigs or rendering. Placement into the scene is the caller's job.
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
                string archiveModelPath = ModelArchiveSelection.ReadSelector(p.GetRaw("archiveModelPath"));
                string source = p.Get("sourcePath");
                if (string.IsNullOrWhiteSpace(source))
                    return new ErrorResponse("'source_path' is required.");

                string srcAbs = ResolveSource(source);
                if (!File.Exists(srcAbs))
                    return new ErrorResponse($"Source file not found: {source}");

                string ext = Path.GetExtension(srcAbs).ToLowerInvariant();
                if (Array.IndexOf(SupportedExt, ext) < 0)
                    return new ErrorResponse($"Unsupported model extension '{ext}'. Supported: .fbx, .obj, .glb, .gltf, .zip.");

                if (archiveModelPath != null && ext != ".zip")
                    throw new ArchiveSelectionException("archive_selector_requires_zip", "archive_model_path applies only to ZIP archives.");

                string baseName = p.Get("name");
                if (string.IsNullOrWhiteSpace(baseName))
                    baseName = Path.GetFileNameWithoutExtension(srcAbs);

                var job = new AssetGenJob
                {
                    TargetSize = p.GetFloat("targetSize", 1f) ?? 1f,
                    AnimationType = p.Get("animationType"),
                    ArchiveModelPath = archiveModelPath,
                };
                string destRel;
                string bundleRoot = null;
                JObject dependencyEvidence;
                if (ext == ".obj" || ext == ".gltf" || ext == ".glb")
                {
                    BundlePlan plan = ModelDependencyBundle.Plan(srcAbs);
                    BundleStageResult staged = ModelDependencyBundle.Stage(plan, p.Get("outputFolder"), baseName);
                    destRel = staged.MainAssetPath;
                    bundleRoot = staged.BundleRoot;
                    dependencyEvidence = staged.Evidence;
                }
                else
                {
                    destRel = StageUnderAssets(srcAbs, baseName, ext, p.Get("outputFolder"));
                    dependencyEvidence = new JObject
                    {
                        ["inspection"] = ext == ".zip" ? "archive_managed" : "not_scanned",
                        ["stage"] = "staged",
                        ["main_asset_path"] = destRel,
                        ["planned_file_count"] = 1,
                        ["staged_file_count"] = 1,
                        ["staged_bytes"] = new FileInfo(AssetGenPaths.ToAbsolute(destRel)).Length,
                    };
                }
                AssetGenJob result = ImportStaged(job, destRel, bundleRoot);

                if (result == null || result.State == AssetGenJobState.Failed)
                    return new ErrorResponse(
                        result?.Error ?? "Import failed.",
                        new
                        {
                            asset_path = result?.AssetPath,
                            asset_guid = result?.AssetGuid,
                            model_import_stage = result?.ModelImportStage,
                            model_import_error_code = result?.ModelImportErrorCode,
                            dependency_bundle = dependencyEvidence,
                            archive_selection = result?.ArchiveSelection,
                        }
                    );

                return new SuccessResponse(
                    $"Imported usable model: {result.AssetPath}",
                    new
                    {
                        asset_path = result.AssetPath,
                        asset_guid = result.AssetGuid,
                        model_import_stage = result.ModelImportStage,
                        dependency_bundle = dependencyEvidence,
                        archive_selection = result.ArchiveSelection,
                    }
                );
            }
            catch (ArchiveSelectionException e)
            {
                return new ErrorResponse(SecretRedactor.Scrub(e.Message), new { model_import_error_code = e.ErrorCode });
            }
            catch (BundleImportException e)
            {
                return new ErrorResponse(SecretRedactor.Scrub(e.Message), new { dependency_bundle = e.Evidence });
            }
            catch (Exception e)
            {
                return new ErrorResponse(SecretRedactor.Scrub(e.Message));
            }
        }

        private static AssetGenJob ImportStaged(AssetGenJob job, string mainPath, string bundleRoot)
        {
            try
            {
                if (bundleRoot != null)
                    AssetDatabase.ImportAsset(
                        bundleRoot,
                        ImportAssetOptions.ImportRecursive | ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport
                    );
                return ModelImportPipeline.ImportInto(job, mainPath);
            }
            catch (Exception e)
            {
                job.AssetPath = mainPath;
                job.AssetGuid = AssetDatabase.AssetPathToGUID(mainPath, AssetPathToGUIDOptions.OnlyExistingAssets);
                job.ModelImportStage = string.IsNullOrEmpty(job.AssetGuid) ? "saved" : "registered";
                job.ModelImportErrorCode = "model_import_failed";
                job.State = AssetGenJobState.Failed;
                job.Error = SecretRedactor.Scrub(e.Message);
                return job;
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

        internal static string StageUnderAssets(string srcAbs, string baseName, string ext, string outputFolder, CancellationToken cancellationToken = default)
        {
            string root = AssetGenPaths.ResolveOutputFolder(outputFolder, "Imported");

            long length = new FileInfo(srcAbs).Length;
            if (length <= 0 || length > AssetDownloadTransport.MaxDownloadBytes)
                throw new IOException("Local model exceeds the 8 GiB artifact limit or is empty.");
            using var folders = new AssetFolderScope();
            using var copying = new LocalAssetCopy(Path.GetDirectoryName(Application.dataPath), length, cancellationToken);
            folders.EnsureFolder(root);

            string safe = SanitizeName(baseName);
            string fileName = safe + ext;
            string abs = AssetGenPaths.ToAbsolute(root.TrimEnd('/') + "/" + fileName);
            int n = 1;
            while (
                File.Exists(abs)
                || Directory.Exists(abs)
                || File.Exists(abs + ".meta")
                || Directory.Exists(abs + ".meta")
                || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(AssetGenPaths.ToProjectRelative(abs), AssetPathToGUIDOptions.OnlyExistingAssets))
            )
            {
                if (n > 1000)
                    throw new IOException("Could not allocate a fresh local asset destination within the collision limit.");
                fileName = safe + "_" + n++ + ext;
                abs = AssetGenPaths.ToAbsolute(root.TrimEnd('/') + "/" + fileName);
            }

            copying.Copy(AssetGenPaths.ToAbsolute(srcAbs), AssetGenPaths.ToAbsolute(abs), length);
            folders.Complete();
            copying.Complete();
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
