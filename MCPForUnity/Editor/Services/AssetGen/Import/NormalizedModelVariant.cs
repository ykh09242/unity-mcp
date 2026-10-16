using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    internal sealed class ModelNormalizationException : Exception
    {
        internal string ErrorCode { get; }
        internal string SavedAssetPath { get; }

        internal ModelNormalizationException(string code, string message, string savedAssetPath = null)
            : base(message)
        {
            ErrorCode = code;
            SavedAssetPath = savedAssetPath;
        }
    }

    /// <summary>Persists one scale override while keeping the imported model and its subasset references intact.</summary>
    internal static class NormalizedModelVariant
    {
        internal static string ResolveSourceAssetPath(string assetPath)
        {
            var root = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (root == null || PrefabUtility.GetPrefabAssetType(root) != PrefabAssetType.Variant)
                return assetPath;
            GameObject original = PrefabUtility.GetCorrespondingObjectFromOriginalSource(root);
            string source = original != null ? AssetDatabase.GetAssetPath(original) : null;
            return !string.IsNullOrEmpty(source) && AssetDatabase.LoadAssetAtPath<GameObject>(source) != null ? source : assetPath;
        }

        internal static string Create(string sourceAssetPath, float factor, int maxAttempts = 1000, Action<string> afterSave = null)
        {
            sourceAssetPath = AssetPathUtility.GetContainedAssetPath(sourceAssetPath);
            AssetPathUtility.GetFullAssetPath(sourceAssetPath);
            var source = AssetDatabase.LoadAssetAtPath<GameObject>(sourceAssetPath);
            if (source == null || PrefabUtility.GetPrefabAssetType(source) == PrefabAssetType.NotAPrefab)
                throw new ModelNormalizationException(
                    "normalization_variant_unsupported",
                    "The imported hierarchy cannot be preserved as a prefab variant. Export FBX/OBJ or keep the imported scale."
                );
            Vector3 scale = source.transform.localScale * factor;
            if (!ModelHierarchyBounds.IsFinite(factor) || factor <= 0f || !ModelHierarchyBounds.IsFinite(scale))
                throw new ModelNormalizationException("target_size_out_of_range", "The requested target size cannot be represented by a finite model scale.");

            string stem = Path.GetDirectoryName(sourceAssetPath).Replace('\\', '/') + "/" + Path.GetFileNameWithoutExtension(sourceAssetPath) + "_Normalized";
            string folder = null;
            for (int attempt = 0; attempt < maxAttempts; attempt++)
            {
                string candidate = stem + (attempt == 0 ? string.Empty : "_" + attempt);
                if (!Occupied(candidate))
                {
                    folder = candidate;
                    break;
                }
            }
            if (folder == null)
                throw new ModelNormalizationException(
                    "normalization_output_conflict",
                    "No free normalization output folder was found. Choose a different output location."
                );

            string output = folder + "/model.prefab";
            using var folders = new AssetFolderScope();
            folders.EnsureFolder(folder);
            if (Occupied(output))
                throw new ModelNormalizationException("normalization_output_conflict", "The normalization output is already occupied.");
            var preview = EditorSceneManager.NewPreviewScene();
            try
            {
                var instance = PrefabUtility.InstantiatePrefab(source, preview) as GameObject;
                if (instance == null)
                    throw new ModelNormalizationException(
                        "normalization_variant_unsupported",
                        "Unity could not instantiate the imported model as a prefab source."
                    );
                instance.transform.localScale = scale;
                PrefabUtility.RecordPrefabInstancePropertyModifications(instance.transform);
                PrefabUtility.SaveAsPrefabAsset(instance, output, out bool saved);
                if (!saved)
                    throw new IOException("Unity did not save the normalized prefab variant.");
                afterSave?.Invoke(output);
                AssetDatabase.ImportAsset(output, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                var result = AssetDatabase.LoadAssetAtPath<GameObject>(output);
                if (
                    result == null
                    || PrefabUtility.GetPrefabAssetType(result) != PrefabAssetType.Variant
                    || ResolveSourceAssetPath(output) != ResolveSourceAssetPath(sourceAssetPath)
                )
                    throw new IOException("Unity did not retain the original model reference in the normalized prefab variant.");
                folders.Complete();
                return output;
            }
            catch (Exception error)
            {
                string retained = File.Exists(AssetPathUtility.GetFullAssetPath(output)) ? output : null;
                string code = error is ModelNormalizationException normalization ? normalization.ErrorCode : "normalization_variant_failed";
                throw new ModelNormalizationException(code, error.Message, retained);
            }
            finally
            {
                EditorSceneManager.ClosePreviewScene(preview);
            }
        }

        private static bool Occupied(string path)
        {
            string full = AssetPathUtility.GetFullAssetPath(path);
            // Resolve metadata independently: Exists alone hides a dangling metadata symlink.
            string meta = AssetPathUtility.GetFullAssetPath(path + ".meta");
            return File.Exists(full)
                || Directory.Exists(full)
                || File.Exists(meta)
                || Directory.Exists(meta)
                || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
        }
    }
}
