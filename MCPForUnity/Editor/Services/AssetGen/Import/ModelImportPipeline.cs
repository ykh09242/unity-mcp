using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Runtime.Helpers;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    /// <summary>
    /// Imports a downloaded model file (already under Assets/) into the project and applies
    /// import settings. GLB/glTF need an active compatible importer (for example optional glTFast);
    /// FBX/OBJ use Unity's built-in ModelImporter. Success requires a hierarchy with indexed mesh
    /// geometry. Saved/registered failures retain bytes for repair. Optionally normalizes scale.
    /// </summary>
    public static class ModelImportPipeline
    {
        // Bounds managed validation work, not Unity's native importer. Stop at the first usable mesh.
        internal const int MaxInspectionSteps = 10000;

        // Inert asset types permitted out of an UNTRUSTED provider archive (Sketchfab et al.).
        // Anything else — scripts, assemblies, asmdefs — is skipped on extraction so it can never
        // compile or load inside the Editor. See SafeZipExtractor for the enforcement.
        private static readonly HashSet<string> ArchiveAllowedExtensions = new(StringComparer.OrdinalIgnoreCase)
        {
            ".gltf",
            ".glb",
            ".bin",
            ".fbx",
            ".obj",
            ".mtl",
            ".png",
            ".jpg",
            ".jpeg",
            ".tga",
            ".bmp",
            ".tif",
            ".tiff",
            ".webp",
            ".exr",
            ".hdr",
            ".ktx",
            ".ktx2",
            ".basis",
            ".dds",
        };

        public static AssetGenJob ImportInto(AssetGenJob job, string localFilePath) => ImportInto(job, localFilePath, AssetGenPrefs.AutoNormalize);

        // Capture policy once per import. The explicit overload lets native fixtures avoid global preference writes.
        internal static AssetGenJob ImportInto(AssetGenJob job, string localFilePath, bool autoNormalize)
        {
            if (job == null)
                return null;
            job.AssetPath = null;
            job.AssetGuid = null;
            job.ModelImportStage = null;
            job.ModelImportErrorCode = null;
            job.ArchiveSelection = null;
            try
            {
                if (string.IsNullOrEmpty(localFilePath))
                    return Fail(job, "No file to import.");

                if (!AssetGenPaths.TryGetAssetsRelativePath(localFilePath, out string rel))
                    return Fail(job, "Generated file is not under the Assets folder.");

                if (!File.Exists(AssetGenPaths.ToAbsolute(rel)))
                    return Fail(job, "Model file was not found on disk.", "model_file_missing");
                RecordSavedFile(job, rel);
                if (!ModelHierarchyBounds.IsFinite(job.TargetSize))
                    return Fail(job, "target_size must be finite. Zero or a negative value preserves the imported scale.", "invalid_target_size");

                string ext = Path.GetExtension(rel).ToLowerInvariant();
                job.ArchiveModelPath = ModelArchiveSelection.NormalizeSelector(job.ArchiveModelPath);
                if (job.ArchiveModelPath != null && ext != ".zip")
                    return Fail(job, "archive_model_path applies only to ZIP archives.", "archive_selector_requires_zip");
                if (ext == ".zip")
                    return ImportArchive(job, rel, autoNormalize);

                return ImportModelFile(job, rel, ext, autoNormalize);
            }
            catch (ArchiveSelectionException e)
            {
                return Fail(job, SecretRedactor.Scrub(e.Message), e.ErrorCode);
            }
            catch (Exception e)
            {
                // Import/settings exceptions may occur after Unity registered the retained file.
                if (job.AssetPath != null)
                {
                    try
                    {
                        RecordSavedFile(job, job.AssetPath);
                    }
                    catch { /* preserve the original import failure */ }
                }
                return Fail(job, SecretRedactor.Scrub(e.Message), "model_import_failed");
            }
        }

        private static void RecordSavedFile(AssetGenJob job, string rel)
        {
            job.AssetPath = rel;
            job.AssetGuid = AssetDatabase.AssetPathToGUID(rel, AssetPathToGUIDOptions.OnlyExistingAssets);
            job.ModelImportStage = string.IsNullOrEmpty(job.AssetGuid) ? "saved" : "registered";
        }

        private static AssetGenJob ImportModelFile(AssetGenJob job, string rel, string ext, bool autoNormalize, bool importAsset = true)
        {
            RecordSavedFile(job, rel);
            if (importAsset)
                AssetDatabase.ImportAsset(rel, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            RecordSavedFile(job, rel);
            if (!VerifyImportedModel(job, rel, ext))
                return job;

            if (AssetImporter.GetAtPath(rel) is ModelImporter)
            {
                ApplyModelImporterSettings(rel, job);
                RecordSavedFile(job, rel);
                // Reimport may change the imported hierarchy; certify the final output.
                if (!VerifyImportedModel(job, rel, ext))
                    return job;
            }

            if (autoNormalize && job.TargetSize > 0f && !Normalize(job, rel, ext))
                return job;

            job.ModelImportStage = "usable";
            job.ModelImportErrorCode = null;
            if (job.State != AssetGenJobState.Failed)
                job.State = AssetGenJobState.Done;
            return job;
        }

        private static bool VerifyImportedModel(AssetGenJob job, string rel, string ext)
        {
            if (string.IsNullOrEmpty(job.AssetGuid))
            {
                Fail(
                    job,
                    "The model file was saved, but Unity did not register it. Inspect the Console and reimport the retained file.",
                    "model_not_registered"
                );
                return false;
            }

            GameObject model = AssetDatabase.LoadAssetAtPath<GameObject>(rel);
            AssetImporter importer = AssetImporter.GetAtPath(rel);
            // Unity exposes its native default importer as the base AssetImporter type,
            // not a public DefaultImporter class. Specific model/scripted importers are subclasses.
            if (model == null && (importer == null || importer.GetType() == typeof(AssetImporter)))
            {
                bool gltf = ext == ".glb" || ext == ".gltf";
                Fail(
                    job,
                    gltf
                        ? "Unity has no active GLB/glTF model importer for the saved file. Install glTFast from the MCP for Unity Dependencies tab or export FBX/OBJ, then reimport. A valid container alone does not make a usable model."
                        : $"Unity has no model importer for '{ext}'. Install a compatible importer or export FBX/OBJ, then reimport the retained file.",
                    gltf ? "missing_gltf_importer" : "unsupported_model_importer"
                );
                return false;
            }

            string error = InspectModel(model);
            if (error == null)
                return true;
            Fail(
                job,
                error == "model_validation_limit"
                    ? "Model usability is unverified: the hierarchy/mesh inspection limit was reached. Simplify or split the retained model and reimport."
                    : "Unity registered the file, but no usable model hierarchy with an indexed mesh was imported. Inspect importer errors in the Console, repair or re-export the model, then reimport the retained file.",
                error
            );
            return false;
        }

        /// <summary>
        /// Conservatively require an attached mesh with vertices and an indexed primitive.
        /// This does not verify material, rig, topology quality or rendering. No vertex/index arrays are read.
        /// </summary>
        internal static string InspectModel(GameObject model, int maxSteps = MaxInspectionSteps)
        {
            if (model == null)
                return "unusable_model";
            int remaining = maxSteps;
            var pending = new Stack<(Transform node, int nextChild)>();
            pending.Push((model.transform, -1));
            while (pending.Count > 0)
            {
                if (remaining-- <= 0)
                    return "model_validation_limit";
                var frame = pending.Pop();
                if (frame.nextChild < 0)
                {
                    // Unity's missing-component objects can be managed non-null wrappers.
                    // Use Unity's equality operator before touching component properties.
                    var filter = frame.node.GetComponent<MeshFilter>();
                    if (filter != null && HasIndexedPrimitive(filter.sharedMesh, ref remaining))
                        return null;
                    var skinned = frame.node.GetComponent<SkinnedMeshRenderer>();
                    if (skinned != null && HasIndexedPrimitive(skinned.sharedMesh, ref remaining))
                        return null;
                    if (remaining < 0)
                        return "model_validation_limit";
                    frame.nextChild = 0;
                }
                if (frame.nextChild < frame.node.childCount)
                {
                    pending.Push((frame.node, frame.nextChild + 1));
                    pending.Push((frame.node.GetChild(frame.nextChild), -1));
                }
            }
            return "unusable_model";
        }

        private static bool HasIndexedPrimitive(Mesh mesh, ref int remaining)
        {
            if (mesh == null || mesh.vertexCount <= 0)
                return false;
            for (int submesh = 0; submesh < mesh.subMeshCount; submesh++)
            {
                if (remaining-- <= 0)
                    return false;
                uint indices = mesh.GetIndexCount(submesh);
                switch (mesh.GetTopology(submesh))
                {
                    case MeshTopology.Triangles:
                        if (indices >= 3)
                            return true;
                        break;
                    case MeshTopology.Quads:
                        if (indices >= 4)
                            return true;
                        break;
                    case MeshTopology.Lines:
                    case MeshTopology.LineStrip:
                        if (indices >= 2)
                            return true;
                        break;
                    case MeshTopology.Points:
                        if (indices >= 1)
                            return true;
                        break;
                }
            }
            return false;
        }

        /// <summary>Inspect and select from the bounded archive before writing any extracted files.</summary>
        private static AssetGenJob ImportArchive(AssetGenJob job, string zipRel, bool autoNormalize)
        {
            job.ArchiveSelection = ModelArchiveSelection.Evidence(zipRel, job.ArchiveModelPath);
            var evidence = job.ArchiveSelection;
            using var folders = new AssetFolderScope();
            string folderRel;
            string modelRel;
            bool selectionComplete = false;
            try
            {
                string zipAbs = AssetGenPaths.ToAbsolute(zipRel);
                folderRel = FindArchiveOutput(zipRel);
                string folderAbs = AssetGenPaths.ToAbsolute(folderRel);
                folders.EnsureFolder(folderRel);
                string selected = null;
                // Only inert model/texture files are written. Selection inspects the same open,
                // size-limited archive; the extractor retains streaming and owned-file rollback.
                SafeZipExtractor.ExtractToWithInspection(
                    zipAbs,
                    folderAbs,
                    ArchiveAllowedExtensions,
                    archive =>
                    {
                        selected = ModelArchiveSelection.Select(archive, ArchiveAllowedExtensions, job.ArchiveModelPath, evidence);
                        selectionComplete = true;
                    },
                    directory => folders.EnsureFolder(AssetGenPaths.ToProjectRelative(directory))
                );
                string selectedAbs = SafePathUtility.ResolveWithinRoot(folderAbs, selected);
                if (!File.Exists(selectedAbs))
                    throw new IOException("The selected archive model was not extracted.");
                modelRel = AssetGenPaths.ToProjectRelative(selectedAbs);
                evidence["selected_asset_path"] = modelRel;
                evidence["extraction_root"] = folderRel;
                evidence["extraction"] = "complete";
            }
            catch (ArchiveSelectionException e)
            {
                evidence["selection_error_code"] = e.ErrorCode;
                return Fail(job, SecretRedactor.Scrub(e.Message), e.ErrorCode);
            }
            catch (Exception e)
            {
                evidence["selection_error_code"] = "archive_extraction_failed";
                evidence["extraction"] = selectionComplete ? "failed" : "not_started";
                return Fail(job, SecretRedactor.Scrub(e.Message), "archive_extraction_failed");
            }

            // Failed model imports retain the completed extraction for repair. Selection never
            // retries another candidate; GEN-03 usability and GEN-04 normalization apply as usual.
            RecordSavedFile(job, modelRel);
            AssetDatabase.ImportAsset(
                folderRel,
                ImportAssetOptions.ImportRecursive | ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport
            );
            ImportModelFile(job, modelRel, Path.GetExtension(modelRel).ToLowerInvariant(), autoNormalize, importAsset: false);
            folders.Complete();
            return job;
        }

        internal static string FindArchiveOutput(string zipRel, int maxAttempts = 1000)
        {
            string stem = zipRel.Substring(0, zipRel.Length - ".zip".Length);
            try
            {
                for (int attempt = 0; attempt < maxAttempts; attempt++)
                {
                    string candidate = stem + (attempt == 0 ? "" : "_" + attempt);
                    string full = AssetGenPaths.ToAbsolute(candidate);
                    string metadata = AssetGenPaths.ToAbsolute(candidate + ".meta");
                    if (
                        !File.Exists(full)
                        && !Directory.Exists(full)
                        && !File.Exists(metadata)
                        && !Directory.Exists(metadata)
                        && string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(candidate, AssetPathToGUIDOptions.OnlyExistingAssets))
                    )
                        return candidate;
                }
            }
            catch (Exception e)
            {
                throw new ArchiveSelectionException(
                    "archive_output_conflict",
                    "The archive output path is unsafe or inaccessible: " + SecretRedactor.Scrub(e.Message)
                );
            }
            throw new ArchiveSelectionException("archive_output_conflict", "No free archive output folder was found within the 1000-attempt limit.");
        }

        private static void ApplyModelImporterSettings(string rel, AssetGenJob job)
        {
            if (!(AssetImporter.GetAtPath(rel) is ModelImporter importer))
                return;
            var animation = ParseAnimationType(job?.AnimationType);
            if (importer.materialImportMode == ModelImporterMaterialImportMode.ImportStandard && importer.animationType == animation)
                return;
            // Preserve the existing unit conversion. Measuring in one unit policy and then disabling
            // fileScale invalidates the target/measured ratio, even if globalScale is adjusted correctly.
            importer.materialImportMode = ModelImporterMaterialImportMode.ImportStandard;
            importer.animationType = animation;
            importer.SaveAndReimport();
        }

        private static bool Normalize(AssetGenJob job, string rel, string ext)
        {
            if (!TryDimension(job, rel, out float before))
                return false;
            if (WithinTarget(before, job.TargetSize))
                return true;
            float factor = job.TargetSize / before;
            if (!ModelHierarchyBounds.IsFinite(factor) || factor <= 0f)
            {
                Fail(job, "The requested target size cannot be represented by a finite model scale.", "target_size_out_of_range");
                return false;
            }
            try
            {
                if (AssetImporter.GetAtPath(rel) is ModelImporter importer)
                {
                    float adjusted = importer.globalScale * factor;
                    if (!ModelHierarchyBounds.IsFinite(adjusted) || adjusted < .0001f || adjusted > 1000000f)
                    {
                        Fail(
                            job,
                            "The requested target size exceeds the supported importer scale range; the saved model was retained.",
                            "target_size_out_of_range"
                        );
                        return false;
                    }
                    importer.globalScale = adjusted;
                    importer.SaveAndReimport();
                }
                else
                {
                    rel = NormalizedModelVariant.Create(rel, factor);
                    ext = ".prefab";
                }
                RecordSavedFile(job, rel);
                if (!VerifyImportedModel(job, rel, ext) || !TryDimension(job, rel, out float after))
                    return false;
                if (!WithinTarget(after, job.TargetSize))
                {
                    Fail(
                        job,
                        "The retained model did not reach target_size after normalization. Inspect its importer and transforms.",
                        "target_size_not_achieved"
                    );
                    return false;
                }
                return true;
            }
            catch (ModelNormalizationException error)
            {
                if (error.SavedAssetPath != null)
                    RecordSavedFile(job, error.SavedAssetPath);
                Fail(job, SecretRedactor.Scrub(error.Message), error.ErrorCode);
                return false;
            }
        }

        private static bool WithinTarget(float measured, float target) => Math.Abs(measured - target) <= Mathf.Max(.00001f, target * .001f);

        private static bool TryDimension(AssetGenJob job, string rel, out float dimension)
        {
            dimension = 0f;
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(rel);
            string error = "normalization_invalid_bounds";
            if (model == null || !ModelHierarchyBounds.TryMeasure(model, ModelHierarchyBounds.RootMatrix(model), out Bounds bounds, out error))
            {
                Fail(
                    job,
                    "The complete model bounds could not be measured safely. Simplify or repair the retained hierarchy, or keep its imported scale.",
                    error
                );
                return false;
            }
            dimension = Mathf.Max(bounds.size.x, Mathf.Max(bounds.size.y, bounds.size.z));
            if (!ModelHierarchyBounds.IsFinite(dimension) || dimension <= .000001f)
            {
                Fail(job, "The model has no finite nonzero size to normalize; the saved asset was retained.", "normalization_invalid_bounds");
                return false;
            }
            return true;
        }

        /// <summary>
        /// Map the caller's rig mode to a <see cref="ModelImporterAnimationType"/>. Defaults to
        /// <c>None</c> (no rig) when unset — pass "generic"/"humanoid" to surface a rigged FBX/OBJ's
        /// AnimationClips, which the None default otherwise hides. "legacy" selects Unity's
        /// legacy Animation system — rarely needed.
        /// </summary>
        internal static ModelImporterAnimationType ParseAnimationType(string value)
        {
            switch ((value ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "generic":
                    return ModelImporterAnimationType.Generic;
                case "humanoid":
                case "human":
                    return ModelImporterAnimationType.Human;
                case "legacy":
                    return ModelImporterAnimationType.Legacy;
                default:
                    return ModelImporterAnimationType.None;
            }
        }

        private static bool? _gltfastAvailable;

        /// <summary>
        /// True when the glTFast package is present. Cached after the first probe — the result only
        /// changes on a package install/uninstall, which triggers a domain reload that resets this
        /// static. Shared with the Asset Gen settings tab so the reflection scan runs at most once.
        /// </summary>
        internal static bool IsGltfastAvailable()
        {
            if (_gltfastAvailable.HasValue)
                return _gltfastAvailable.Value;
            bool found = Type.GetType("GLTFast.GltfImport, glTFast") != null;
            if (!found)
            {
                foreach (var asm in MCPForUnity.Runtime.Helpers.UnityAssembliesCompat.GetLoadedAssemblies())
                {
                    try
                    {
                        if (asm.GetType("GLTFast.GltfImport") != null)
                        {
                            found = true;
                            break;
                        }
                    }
                    catch { /* dynamic/!resolvable assembly */ }
                }
            }
            _gltfastAvailable = found;
            return found;
        }

        private static AssetGenJob Fail(AssetGenJob job, string message, string code = "model_import_failed")
        {
            job.State = AssetGenJobState.Failed;
            job.Error = message;
            job.ModelImportErrorCode = code;
            return job;
        }
    }
}
