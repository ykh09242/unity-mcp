using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
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
        internal static Action AfterCommitForTests;

        public static object HandleCommand(JObject @params) => Handle(@params, CancellationToken.None, false).GetAwaiter().GetResult();

        public static Task<object> HandleCommand(JObject @params, CancellationToken cancellationToken) => Handle(@params, cancellationToken, true);

        private static async Task<object> Handle(JObject @params, CancellationToken cancellationToken, bool asynchronous)
        {
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");
            var p = new ToolParams(@params);
            BundleStageResult committed = null;
            AssetGenJob job = null;
            try
            {
                cancellationToken.ThrowIfCancellationRequested();
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

                job = new AssetGenJob
                {
                    TargetSize = p.GetFloat("targetSize", 1f) ?? 1f,
                    AnimationType = p.Get("animationType"),
                    ArchiveModelPath = archiveModelPath,
                };
                string assetsRoot = Application.dataPath;
                string outputFolder = AssetGenPaths.ResolveOutputFolder(p.Get("outputFolder"), "Imported");
                // Await the actual worker, never a cancellation race: it owns files and disk
                // credit until all reads/writes have stopped. Unity's captured context returns
                // this continuation to the Editor thread for publication and native import.
                using LocalModelPreparation prepared = asynchronous
                    ? await LocalModelPreparation.PrepareAsync(srcAbs, assetsRoot, cancellationToken)
                    : LocalModelPreparation.Prepare(srcAbs, assetsRoot, cancellationToken);
                cancellationToken.ThrowIfCancellationRequested();
                BundleStageResult staged = prepared.Publish(outputFolder, baseName);
                JObject dependencyEvidence = staged.Evidence;
                prepared.Commit();
                committed = staged;
                job.AssetPath = staged.MainAssetPath;
                job.ModelImportStage = "saved";
                AfterCommitForTests?.Invoke();
                AssetGenJob result = ImportStaged(job, staged.MainAssetPath, staged.BundleRoot, cancellationToken);
                cancellationToken.ThrowIfCancellationRequested();

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
                        verification = ImportObservation.Read(result.AssetPath, result.AssetGuid, "model"),
                        model_import_stage = result.ModelImportStage,
                        dependency_bundle = dependencyEvidence,
                        archive_selection = result.ArchiveSelection,
                    }
                );
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                if (committed != null)
                    return CancellationReceipt(job, committed);
                throw;
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

        internal static object CancellationReceipt(AssetGenJob job, BundleStageResult committed) =>
            new ErrorResponse(
                "model_import_canceled_after_publication",
                new
                {
                    source_saved = true,
                    execution_status = "canceled",
                    asset_path = job.AssetPath ?? committed.MainAssetPath,
                    asset_guid = job.AssetGuid,
                    published_asset_path = committed.MainAssetPath,
                    bundle_root = committed.BundleRoot,
                    model_import_stage = job.ModelImportStage,
                    dependency_bundle = committed.Evidence,
                    archive_selection = CancellationArchiveEvidence(job.ArchiveSelection),
                    retry_safe = false,
                    recovery_action = "inspect_then_reimport_retained",
                    retry_guidance = "Published files were retained. Inspect the reported paths and import stage; reimport retained output if needed instead of repeating source staging.",
                }
            );

        private static JObject CancellationArchiveEvidence(JObject evidence)
        {
            if (evidence == null)
                return null;
            // Optional candidate inventories can exceed the bounded terminal journal. Retain
            // the actual extraction/selection facts needed to inspect output without restaging.
            var retained = new JObject { ["candidates_omitted"] = true };
            foreach (string key in new[] { "archive_path", "selected_path", "selected_asset_path", "extraction_root", "extraction", "candidate_count" })
                if (evidence.TryGetValue(key, out JToken value))
                    retained[key] = value.DeepClone();
            return retained;
        }

        private static AssetGenJob ImportStaged(AssetGenJob job, string mainPath, string bundleRoot, CancellationToken cancellationToken)
        {
            try
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (bundleRoot != null)
                    AssetDatabase.ImportAsset(
                        bundleRoot,
                        ImportAssetOptions.ImportRecursive | ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport
                    );
                return ModelImportPipeline.ImportInto(job, mainPath, cancellationToken);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
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
