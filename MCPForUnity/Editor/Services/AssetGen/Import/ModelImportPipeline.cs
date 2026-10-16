using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
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

        public static AssetGenJob ImportInto(AssetGenJob job, string localFilePath)
        {
            if (job == null)
                return null;
            job.AssetPath = null;
            job.AssetGuid = null;
            job.ModelImportStage = null;
            job.ModelImportErrorCode = null;
            try
            {
                if (string.IsNullOrEmpty(localFilePath))
                    return Fail(job, "No file to import.");

                if (!AssetGenPaths.TryGetAssetsRelativePath(localFilePath, out string rel))
                    return Fail(job, "Generated file is not under the Assets folder.");

                if (!File.Exists(AssetGenPaths.ToAbsolute(rel)))
                    return Fail(job, "Model file was not found on disk.", "model_file_missing");
                RecordSavedFile(job, rel);

                string ext = Path.GetExtension(rel).ToLowerInvariant();
                if (ext == ".zip")
                    return ImportArchive(job, rel);

                return ImportModelFile(job, rel, ext);
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

        private static AssetGenJob ImportModelFile(AssetGenJob job, string rel, string ext, bool importAsset = true)
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

        /// <summary>
        /// Unpack a downloaded archive (Sketchfab ships .zip) into a sibling folder named
        /// after the archive, import it, then locate the first model file inside and import that.
        /// FBX/OBJ are preferred over glTF; glTF still needs an active compatible importer.
        /// </summary>
        private static AssetGenJob ImportArchive(AssetGenJob job, string zipRel)
        {
            string zipAbs = AssetGenPaths.ToAbsolute(zipRel);
            if (!File.Exists(zipAbs))
                return Fail(job, "Downloaded archive was not found on disk.");

            string folderRel = zipRel.Substring(0, zipRel.Length - ".zip".Length);
            string folderBaseRel = folderRel;
            string folderAbs = AssetGenPaths.ToAbsolute(folderRel);
            int suffix = 1;
            while (Directory.Exists(folderAbs) || File.Exists(folderAbs))
            {
                folderRel = folderBaseRel + "_" + suffix++;
                folderAbs = AssetGenPaths.ToAbsolute(folderRel);
            }

            using var folders = new AssetFolderScope();
            folders.EnsureFolder(folderRel);
            // Provider archives are untrusted: only inert model/texture files are written under
            // Assets/ — scripts/assemblies are skipped so they can't be compiled on import.
            SafeZipExtractor.ExtractTo(
                zipAbs,
                folderAbs,
                ArchiveAllowedExtensions,
                ensureDirectory: directory => folders.EnsureFolder(AssetGenPaths.ToProjectRelative(directory))
            );

            string modelRel = FindFirstModel(folderAbs);
            if (string.IsNullOrEmpty(modelRel))
                return Fail(job, "Archive extracted but no model file (.fbx/.obj/.glb/.gltf) was found inside.");

            // The same usability gate applies to extracted models. Failed outputs remain available
            // for repair; AssetFolderScope only removes this operation's empty directories.
            RecordSavedFile(job, modelRel);
            AssetDatabase.ImportAsset(
                folderRel,
                ImportAssetOptions.ImportRecursive | ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport
            );
            ImportModelFile(job, modelRel, Path.GetExtension(modelRel).ToLowerInvariant(), importAsset: false);
            folders.Complete();
            return job;
        }

        /// <summary>
        /// Walk the extracted directory for a model file, preferring FBX/OBJ (built-in importer)
        /// over glTF (needs a compatible importer). Returns a project-relative path or null when none found.
        /// </summary>
        private static string FindFirstModel(string folderAbs)
        {
            string[] all;
            try
            {
                all = Directory.GetFiles(folderAbs, "*", SearchOption.AllDirectories);
            }
            catch
            {
                return null;
            }

            string firstGltf = null;
            foreach (string abs in all)
            {
                string e = Path.GetExtension(abs).ToLowerInvariant();
                if (e == ".fbx" || e == ".obj")
                    return AssetGenPaths.ToProjectRelative(abs);
                if (firstGltf == null && (e == ".glb" || e == ".gltf"))
                    firstGltf = abs;
            }
            return firstGltf == null ? null : AssetGenPaths.ToProjectRelative(firstGltf);
        }

        private static void ApplyModelImporterSettings(string rel, AssetGenJob job)
        {
            if (!(AssetImporter.GetAtPath(rel) is ModelImporter importer))
                return;
            importer.useFileScale = true;
            importer.materialImportMode = ModelImporterMaterialImportMode.ImportStandard;
            importer.animationType = ParseAnimationType(job?.AnimationType);

            if (AssetGenPrefs.AutoNormalize && job.TargetSize > 0f)
            {
                float maxDim = ComputeMaxDimension(rel);
                if (maxDim > 0.0001f)
                {
                    float scale = job.TargetSize / maxDim;
                    if (scale > 0f && Math.Abs(scale - 1f) > 0.01f)
                    {
                        importer.useFileScale = false;
                        importer.globalScale = Mathf.Clamp(importer.globalScale * scale, 0.0001f, 1_000_000f);
                    }
                }
            }
            importer.SaveAndReimport();
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

        private static float ComputeMaxDimension(string rel)
        {
            try
            {
                var go = AssetDatabase.LoadAssetAtPath<GameObject>(rel);
                if (go == null)
                    return 0f;

                bool any = false;
                Bounds acc = new Bounds(Vector3.zero, Vector3.zero);
                foreach (var mf in go.GetComponentsInChildren<MeshFilter>(true))
                {
                    if (mf.sharedMesh == null)
                        continue;
                    if (!any)
                    {
                        acc = mf.sharedMesh.bounds;
                        any = true;
                    }
                    else
                        acc.Encapsulate(mf.sharedMesh.bounds);
                }
                foreach (var smr in go.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                {
                    if (smr.sharedMesh == null)
                        continue;
                    if (!any)
                    {
                        acc = smr.sharedMesh.bounds;
                        any = true;
                    }
                    else
                        acc.Encapsulate(smr.sharedMesh.bounds);
                }
                if (!any)
                    return 0f;
                Vector3 s = acc.size;
                return Mathf.Max(s.x, Mathf.Max(s.y, s.z));
            }
            catch
            {
                return 0f;
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
