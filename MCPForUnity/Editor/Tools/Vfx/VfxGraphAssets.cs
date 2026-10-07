using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
#if UNITY_VFX_GRAPH
using UnityEngine.VFX;
#endif

namespace MCPForUnity.Editor.Tools.Vfx
{
    /// <summary>
    /// Asset management operations for VFX Graph.
    /// Handles creating, assigning, and listing VFX assets.
    /// Requires com.unity.visualeffectgraph package and UNITY_VFX_GRAPH symbol.
    /// </summary>
    internal static class VfxGraphAssets
    {
#if !UNITY_VFX_GRAPH
        public static object CreateAsset(JObject @params)
        {
            return new { success = false, message = "VFX Graph package (com.unity.visualeffectgraph) not installed" };
        }

        public static object AssignAsset(JObject @params)
        {
            return new { success = false, message = "VFX Graph package (com.unity.visualeffectgraph) not installed" };
        }

        public static object ListTemplates(JObject @params)
        {
            return new { success = false, message = "VFX Graph package (com.unity.visualeffectgraph) not installed" };
        }

        public static object ListAssets(JObject @params)
        {
            return new { success = false, message = "VFX Graph package (com.unity.visualeffectgraph) not installed" };
        }
#else
        /// <summary>
        /// Creates a new VFX Graph asset file from a template.
        /// </summary>
        public static object CreateAsset(JObject @params)
        {
            string assetName = @params["assetName"]?.ToString();
            string folderPath = @params["folderPath"]?.ToString() ?? "Assets/VFX";
            string template = @params["template"]?.ToString() ?? "empty";

            if (string.IsNullOrWhiteSpace(assetName))
            {
                return new { success = false, message = "assetName is required" };
            }
            if (assetName.Contains("/") || assetName.Contains("\\"))
                return new { success = false, message = "assetName must not contain path separators" };

            bool overwrite;
            try
            {
                overwrite = @params["overwrite"]?.ReadScalar<bool?>() ?? false;
            }
            catch (ArgumentException ex)
            {
                return new { success = false, message = $"Invalid overwrite: {ex.Message}" };
            }

            string assetPath;
            string fullPath;
            try
            {
                folderPath = AssetPathUtility.GetContainedAssetPath(folderPath);
                assetPath = AssetPathUtility.GetContainedAssetPath($"{folderPath}/{assetName}.vfx");
                fullPath = AssetPathUtility.GetFullAssetPath(assetPath);
            }
            catch (Exception ex)
            {
                return new { success = false, message = $"Invalid folderPath or assetName: {ex.Message}" };
            }

            var existing = AssetDatabase.LoadMainAssetAtPath(assetPath);
            if (
                existing != null && existing is not VisualEffectAsset
                || System.IO.Directory.Exists(fullPath)
                || existing == null && (System.IO.File.Exists(fullPath) || System.IO.File.Exists(fullPath + ".meta"))
            )
                return new { success = false, message = $"An unrelated asset already exists at {assetPath}" };
            if (existing != null && !overwrite)
                return new { success = false, message = $"Asset already exists at {assetPath}. Set overwrite=true to replace." };

            string versionError = ValidateVfxGraphVersion();
            if (!string.IsNullOrEmpty(versionError))
            {
                return new { success = false, message = versionError };
            }

            string templatePath = FindTemplate(template);
            string templateAssetPath = TryGetAssetPathFromFileSystem(templatePath);
            if (string.IsNullOrEmpty(templateAssetPath))
            {
                return new { success = false, message = "VFX template not found. Add a .vfx template asset or install VFX Graph templates." };
            }

            using var folders = new AssetFolderScope();
            folders.EnsureFolder(folderPath);
            string stagedPath = null;
            string stagedGuid = null;
            Exception operationFailure = null;
            VisualEffectAsset newAsset;
            try
            {
                // Selecting the destination itself as template is already the requested content.
                var pathComparison = System.IO.Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
                if (existing == null || !string.Equals(templateAssetPath, assetPath, pathComparison))
                {
                    string copyPath = assetPath;
                    if (existing != null)
                    {
                        copyPath = AssetPathUtility.GetContainedAssetPath($"{folderPath}/__McpVfxOverwrite_{Guid.NewGuid():N}.vfx");
                        string candidateFullPath = AssetPathUtility.GetFullAssetPath(copyPath);
                        if (
                            System.IO.File.Exists(candidateFullPath)
                            || System.IO.Directory.Exists(candidateFullPath)
                            || System.IO.File.Exists(candidateFullPath + ".meta")
                            || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(copyPath, AssetPathToGUIDOptions.OnlyExistingAssets))
                        )
                            return new { success = false, message = "VFX overwrite staging path is already occupied" };
                        stagedPath = copyPath;
                    }

                    templateAssetPath = AssetPathUtility.GetAssetReferencePath(templateAssetPath, allowPackages: true);
                    AssetPathUtility.GetFullAssetPath(copyPath);
                    bool copied = AssetDatabase.CopyAsset(templateAssetPath, copyPath);
                    if (stagedPath != null)
                        stagedGuid = AssetDatabase.AssetPathToGUID(stagedPath, AssetPathToGUIDOptions.OnlyExistingAssets);
                    if (!copied || AssetDatabase.LoadAssetAtPath<VisualEffectAsset>(copyPath) == null)
                    {
                        operationFailure = new System.IO.IOException($"Failed to copy VFX template from {templateAssetPath}");
                        return new { success = false, message = operationFailure.Message };
                    }
                    if (stagedPath != null)
                    {
                        // Keep destination bytes and GUID until a complete, loadable copy exists.
                        System.IO.File.Replace(AssetPathUtility.GetFullAssetPath(stagedPath), AssetPathUtility.GetFullAssetPath(assetPath), null);
                        AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    }
                }
                newAsset = AssetDatabase.LoadAssetAtPath<VisualEffectAsset>(assetPath);
            }
            catch (Exception ex)
            {
                operationFailure = ex;
                throw;
            }
            finally
            {
                try
                {
                    if (stagedPath != null)
                    {
                        string stagedFullPath = AssetPathUtility.GetFullAssetPath(stagedPath);
                        string currentGuid = AssetDatabase.AssetPathToGUID(stagedPath, AssetPathToGUIDOptions.OnlyExistingAssets);
                        if (
                            System.IO.Directory.Exists(stagedFullPath)
                            || !string.IsNullOrEmpty(stagedGuid)
                                && (
                                    !string.IsNullOrEmpty(currentGuid)
                                        ? !string.Equals(stagedGuid, currentGuid, StringComparison.Ordinal)
                                        : System.IO.File.Exists(stagedFullPath)
                                )
                        )
                            throw new System.IO.IOException("VFX staging asset ownership changed; cleanup refused.");
                        if (!AssetDatabase.DeleteAsset(stagedPath))
                        {
                            // A failed native copy may leave an unregistered partial file at the exact owned path.
                            System.IO.File.Delete(stagedFullPath);
                            System.IO.File.Delete(stagedFullPath + ".meta");
                        }
                    }
                }
                catch (Exception cleanupFailure) when (operationFailure != null)
                {
                    throw new AggregateException("VFX creation failed and its staging asset could not be cleaned up.", operationFailure, cleanupFailure);
                }
            }
            if (newAsset == null)
            {
                return new { success = false, message = "Failed to create VFX asset. Try using a template from list_templates." };
            }

            folders.Complete();
            return new
            {
                success = true,
                message = $"Created VFX asset: {assetPath}",
                data = new
                {
                    assetPath = assetPath,
                    assetName = newAsset.name,
                    template = template,
                },
            };
        }

        /// <summary>
        /// Finds VFX template path by name.
        /// </summary>
        private static string FindTemplate(string templateName)
        {
            if (string.IsNullOrWhiteSpace(templateName) || templateName.Contains("/") || templateName.Contains("\\"))
                return null;
            // Get the actual filesystem path for the VFX Graph package using PackageManager API
            var packageInfo = UnityEditor.PackageManager.PackageInfo.FindForAssetPath("Packages/com.unity.visualeffectgraph");

            var searchPaths = new List<string>();

            if (packageInfo != null)
            {
                // Use the resolved path from PackageManager (handles Library/PackageCache paths)
                searchPaths.Add(System.IO.Path.Combine(packageInfo.resolvedPath, "Editor/Templates"));
                searchPaths.Add(System.IO.Path.Combine(packageInfo.resolvedPath, "Samples"));
            }

            // Also search project-local paths
            searchPaths.Add("Assets/VFX/Templates");

            string[] templatePatterns = new[] { $"{templateName}.vfx", $"VFX{templateName}.vfx", $"Simple{templateName}.vfx", $"{templateName}VFX.vfx" };

            foreach (string basePath in searchPaths)
            {
                string searchRoot = basePath;
                if (basePath.StartsWith("Assets/"))
                {
                    searchRoot = System.IO.Path.Combine(UnityEngine.Application.dataPath, basePath.Substring("Assets/".Length));
                }

                if (!System.IO.Directory.Exists(searchRoot))
                {
                    continue;
                }

                foreach (string pattern in templatePatterns)
                {
                    string[] files = FindContainedTemplateFiles(searchRoot, pattern);
                    if (files.Length > 0)
                    {
                        return files[0];
                    }
                }

                // Also search by partial match
                try
                {
                    string[] allVfxFiles = FindContainedTemplateFiles(searchRoot, "*.vfx");
                    foreach (string file in allVfxFiles)
                    {
                        if (System.IO.Path.GetFileNameWithoutExtension(file).ToLower().Contains(templateName.ToLower()))
                        {
                            return file;
                        }
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"Failed to search VFX templates under '{searchRoot}': {ex.Message}");
                }
            }

            // Search in project assets
            string[] guids = AssetDatabase.FindAssets("t:VisualEffectAsset " + templateName);
            if (guids.Length > 0)
            {
                string assetPath = AssetPathUtility.GetAssetPathFromGuid(guids[0], allowPackages: true);
                // Convert asset path (e.g., "Assets/...") to absolute filesystem path
                if (!string.IsNullOrEmpty(assetPath) && assetPath.StartsWith("Assets/"))
                {
                    return System.IO.Path.Combine(UnityEngine.Application.dataPath, assetPath.Substring("Assets/".Length));
                }
                if (!string.IsNullOrEmpty(assetPath) && assetPath.StartsWith("Packages/"))
                {
                    var info = UnityEditor.PackageManager.PackageInfo.FindForAssetPath(assetPath);
                    if (info != null)
                    {
                        string relPath = assetPath.Substring(("Packages/" + info.name + "/").Length);
                        return System.IO.Path.Combine(info.resolvedPath, relPath);
                    }
                }
                return null;
            }

            return null;
        }

        private static string[] FindContainedTemplateFiles(string root, string pattern)
        {
            var files = new List<string>();
            var pending = new Stack<string>();
            pending.Push(root);
            while (pending.Count > 0)
            {
                string directory = pending.Pop();
                if (string.IsNullOrEmpty(TryGetAssetPathFromFileSystem(directory)))
                    continue;
                foreach (string file in System.IO.Directory.GetFiles(directory, pattern, System.IO.SearchOption.TopDirectoryOnly))
                {
                    if ((System.IO.File.GetAttributes(file) & System.IO.FileAttributes.ReparsePoint) != 0)
                        continue;
                    if (!string.IsNullOrEmpty(TryGetAssetPathFromFileSystem(file)))
                        files.Add(file);
                }
                foreach (string child in System.IO.Directory.GetDirectories(directory))
                {
                    if ((System.IO.File.GetAttributes(child) & System.IO.FileAttributes.ReparsePoint) == 0)
                        pending.Push(child);
                }
            }
            return files.ToArray();
        }

        /// <summary>
        /// Assigns a VFX asset to a VisualEffect component.
        /// </summary>
        public static object AssignAsset(JObject @params)
        {
            VisualEffect vfx = VfxGraphCommon.FindVisualEffect(@params);
            if (vfx == null)
            {
                return new { success = false, message = "VisualEffect component not found" };
            }

            string assetPath = @params["assetPath"]?.ToString();
            if (string.IsNullOrEmpty(assetPath))
            {
                return new { success = false, message = "assetPath is required" };
            }

            // Validate and normalize path
            // Reject absolute paths, parent directory traversal, and backslashes
            if (assetPath.Contains("\\") || assetPath.Contains("..") || System.IO.Path.IsPathRooted(assetPath))
            {
                return new { success = false, message = "Invalid assetPath: traversal and absolute paths are not allowed" };
            }

            if (assetPath.StartsWith("Packages/"))
            {
                return new { success = false, message = "Invalid assetPath: VFX assets must live under Assets/." };
            }

            if (!assetPath.StartsWith("Assets/"))
            {
                assetPath = "Assets/" + assetPath;
            }
            if (!assetPath.EndsWith(".vfx"))
            {
                assetPath += ".vfx";
            }

            // Verify the normalized path doesn't escape the project
            string fullPath = System.IO.Path.Combine(UnityEngine.Application.dataPath, assetPath.Substring("Assets/".Length));
            string canonicalProjectRoot = System.IO.Path.GetFullPath(UnityEngine.Application.dataPath);
            string canonicalAssetPath = System.IO.Path.GetFullPath(fullPath);
            if (!canonicalAssetPath.StartsWith(canonicalProjectRoot + System.IO.Path.DirectorySeparatorChar) && canonicalAssetPath != canonicalProjectRoot)
            {
                return new { success = false, message = "Invalid assetPath: would escape project directory" };
            }

            assetPath = AssetPathUtility.GetAssetReferencePath(assetPath, allowPackages: true);
            var asset = AssetDatabase.LoadAssetAtPath<VisualEffectAsset>(assetPath);
            if (asset == null)
            {
                // Try searching by name
                string searchName = System.IO.Path.GetFileNameWithoutExtension(assetPath);
                string[] guids = AssetDatabase.FindAssets($"t:VisualEffectAsset {searchName}");
                if (guids.Length > 0)
                {
                    assetPath = AssetPathUtility.GetAssetPathFromGuid(guids[0], allowPackages: true);
                    asset = AssetDatabase.LoadAssetAtPath<VisualEffectAsset>(assetPath);
                }
            }

            if (asset == null)
            {
                return new { success = false, message = $"VFX asset not found: {assetPath}" };
            }

            Undo.RecordObject(vfx, "Assign VFX Asset");
            vfx.visualEffectAsset = asset;
            EditorUtility.SetDirty(vfx);

            return new
            {
                success = true,
                message = $"Assigned VFX asset '{asset.name}' to {vfx.gameObject.name}",
                data = new
                {
                    gameObject = vfx.gameObject.name,
                    assetName = asset.name,
                    assetPath = assetPath,
                },
            };
        }

        /// <summary>
        /// Lists available VFX templates.
        /// </summary>
        public static object ListTemplates(JObject @params)
        {
            var templates = new List<object>();
            var seenPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            // Get the actual filesystem path for the VFX Graph package using PackageManager API
            var packageInfo = UnityEditor.PackageManager.PackageInfo.FindForAssetPath("Packages/com.unity.visualeffectgraph");

            var searchPaths = new List<string>();

            if (packageInfo != null)
            {
                // Use the resolved path from PackageManager (handles Library/PackageCache paths)
                searchPaths.Add(System.IO.Path.Combine(packageInfo.resolvedPath, "Editor/Templates"));
                searchPaths.Add(System.IO.Path.Combine(packageInfo.resolvedPath, "Samples"));
            }

            // Also search project-local paths
            searchPaths.Add("Assets/VFX/Templates");
            searchPaths.Add("Assets/VFX");

            // Precompute normalized package path for comparison
            string normalizedPackagePath = null;
            if (packageInfo != null)
            {
                normalizedPackagePath = packageInfo.resolvedPath.Replace("\\", "/");
            }

            // Precompute the Assets base path for converting absolute paths to project-relative
            string assetsBasePath = Application.dataPath.Replace("\\", "/");

            foreach (string basePath in searchPaths)
            {
                if (!System.IO.Directory.Exists(basePath))
                {
                    continue;
                }

                try
                {
                    string[] vfxFiles = System.IO.Directory.GetFiles(basePath, "*.vfx", System.IO.SearchOption.AllDirectories);
                    foreach (string file in vfxFiles)
                    {
                        string absolutePath = file.Replace("\\", "/");
                        string name = System.IO.Path.GetFileNameWithoutExtension(file);
                        bool isPackage = normalizedPackagePath != null && absolutePath.StartsWith(normalizedPackagePath);

                        // Convert absolute path to project-relative path
                        string projectRelativePath;
                        if (isPackage)
                        {
                            // For package paths, convert to Packages/... format
                            projectRelativePath = "Packages/" + packageInfo.name + absolutePath.Substring(normalizedPackagePath.Length);
                        }
                        else if (absolutePath.StartsWith(assetsBasePath))
                        {
                            // For project assets, convert to Assets/... format
                            projectRelativePath = "Assets" + absolutePath.Substring(assetsBasePath.Length);
                        }
                        else
                        {
                            // Fallback: use the absolute path if we can't determine the relative path
                            projectRelativePath = absolutePath;
                        }

                        string normalizedPath = projectRelativePath.Replace("\\", "/");
                        if (seenPaths.Add(normalizedPath))
                        {
                            templates.Add(
                                new
                                {
                                    name = name,
                                    path = projectRelativePath,
                                    source = isPackage ? "package" : "project",
                                }
                            );
                        }
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"Failed to list VFX templates under '{basePath}': {ex.Message}");
                }
            }

            // Also search project assets
            string[] guids = AssetDatabase.FindAssets("t:VisualEffectAsset");
            foreach (string guid in guids)
            {
                string path = AssetPathUtility.GetAssetPathFromGuid(guid, allowPackages: true);
                string normalizedPath = path.Replace("\\", "/");
                if (seenPaths.Add(normalizedPath))
                {
                    string name = System.IO.Path.GetFileNameWithoutExtension(path);
                    templates.Add(
                        new
                        {
                            name = name,
                            path = path,
                            source = "project",
                        }
                    );
                }
            }

            return new { success = true, data = new { count = templates.Count, templates = templates } };
        }

        /// <summary>
        /// Lists all VFX assets in the project.
        /// </summary>
        public static object ListAssets(JObject @params)
        {
            string searchFolder = @params["folder"]?.ToString();
            string searchPattern = @params["search"]?.ToString();

            string filter = "t:VisualEffectAsset";
            if (!string.IsNullOrEmpty(searchPattern))
            {
                filter += " " + searchPattern;
            }

            string[] guids;
            if (!string.IsNullOrEmpty(searchFolder))
            {
                if (searchFolder.Contains("\\") || searchFolder.Contains("..") || System.IO.Path.IsPathRooted(searchFolder))
                {
                    return new { success = false, message = "Invalid folder: traversal and absolute paths are not allowed" };
                }

                if (searchFolder.StartsWith("Packages/"))
                {
                    return new { success = false, message = "Invalid folder: VFX assets must live under Assets/." };
                }

                if (!searchFolder.StartsWith("Assets/"))
                {
                    searchFolder = "Assets/" + searchFolder;
                }

                string fullPath = System.IO.Path.Combine(UnityEngine.Application.dataPath, searchFolder.Substring("Assets/".Length));
                string canonicalProjectRoot = System.IO.Path.GetFullPath(UnityEngine.Application.dataPath);
                string canonicalSearchFolder = System.IO.Path.GetFullPath(fullPath);
                if (
                    !canonicalSearchFolder.StartsWith(canonicalProjectRoot + System.IO.Path.DirectorySeparatorChar)
                    && canonicalSearchFolder != canonicalProjectRoot
                )
                {
                    return new { success = false, message = "Invalid folder: would escape project directory" };
                }

                guids = AssetDatabase.FindAssets(filter, new[] { searchFolder });
            }
            else
            {
                guids = AssetDatabase.FindAssets(filter);
            }

            var assets = new List<object>();
            foreach (string guid in guids)
            {
                string path = AssetPathUtility.GetAssetPathFromGuid(guid, allowPackages: true);
                var asset = AssetDatabase.LoadAssetAtPath<VisualEffectAsset>(path);
                if (asset != null)
                {
                    assets.Add(
                        new
                        {
                            name = asset.name,
                            path = path,
                            guid = guid,
                        }
                    );
                }
            }

            return new { success = true, data = new { count = assets.Count, assets = assets } };
        }

        private static string ValidateVfxGraphVersion()
        {
            // UNITY_VFX_GRAPH is set by the asmdef versionDefines whenever the package is
            // installed, so reaching this branch already implies presence. Keep a runtime
            // double-check for the rare window during install/uninstall where the compile
            // gate and the package state can briefly disagree.
            var info = UnityEditor.PackageManager.PackageInfo.FindForAssetPath("Packages/com.unity.visualeffectgraph");
            if (info == null)
            {
                return "VFX Graph package (com.unity.visualeffectgraph) not installed";
            }

            return null;
        }

        private static string TryGetAssetPathFromFileSystem(string templatePath)
        {
            if (string.IsNullOrEmpty(templatePath))
            {
                return null;
            }

            string normalized = templatePath.Replace("\\", "/");
            string assetsRoot = Application.dataPath.Replace("\\", "/");
            var comparison = System.IO.Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;

            if (normalized.StartsWith(assetsRoot + "/", comparison))
            {
                return AssetPathUtility.GetAssetReferencePath("Assets/" + normalized.Substring(assetsRoot.Length + 1));
            }

            var packageInfo = UnityEditor.PackageManager.PackageInfo.FindForAssetPath("Packages/com.unity.visualeffectgraph");
            if (packageInfo != null)
            {
                string packageRoot = packageInfo.resolvedPath.Replace("\\", "/");
                if (normalized.StartsWith(packageRoot + "/", comparison))
                {
                    return AssetPathUtility.GetAssetReferencePath(
                        "Packages/" + packageInfo.name + "/" + normalized.Substring(packageRoot.Length + 1),
                        allowPackages: true
                    );
                }
            }

            return null;
        }
#endif
    }
}
