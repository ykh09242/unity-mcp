using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Editor.Helpers; // For Response class
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;

#if UNITY_6000_0_OR_NEWER
using PhysicsMaterialType = UnityEngine.PhysicsMaterial;
using PhysicsMaterialCombine = UnityEngine.PhysicsMaterialCombine;
#else
using PhysicsMaterialType = UnityEngine.PhysicMaterial;
using PhysicsMaterialCombine = UnityEngine.PhysicMaterialCombine;
#endif

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Handles asset management operations within the Unity project.
    /// </summary>
    [McpForUnityTool("manage_asset", AutoRegister = false)]
    public static class ManageAsset
    {
        // --- Main Handler ---

        // Define the list of valid actions
        private static readonly List<string> ValidActions = new List<string>
        {
            "import",
            "create",
            "modify",
            "delete",
            "duplicate",
            "move",
            "rename",
            "search",
            "get_info",
            "create_folder",
            "get_components",
        };

        public static object HandleCommand(JObject @params)
        {
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            if (string.IsNullOrEmpty(action))
            {
                return new ErrorResponse("Action parameter is required.");
            }

            // Check if the action is valid before switching
            if (!ValidActions.Contains(action))
            {
                string validActionsList = string.Join(", ", ValidActions);
                return new ErrorResponse(
                    $"Unknown action: '{action}'. Valid actions are: {validActionsList}"
                );
            }

            // Common parameters
            string path = @params["path"]?.ToString();

            // Coerce string JSON to JObject for 'properties' if provided as a JSON string
            var propertiesToken = @params["properties"];
            if (action == "create" && propertiesToken != null
                && propertiesToken.Type != JTokenType.Null
                && propertiesToken.Type != JTokenType.Object
                && propertiesToken.Type != JTokenType.String)
            {
                return new ErrorResponse("'properties' must be a JSON object or null for create.");
            }
            if (propertiesToken != null && propertiesToken.Type == JTokenType.String)
            {
                try
                {
                    var parsed = JObject.Parse(propertiesToken.ToString());
                    @params["properties"] = parsed;
                }
                catch (Exception e)
                {
                    if (action == "create")
                        return new ErrorResponse("'properties' must be a valid JSON object for create.");
                    McpLog.Warn($"[ManageAsset] Could not parse 'properties' JSON string: {e.Message}");
                }
            }

            try
            {
                if (action == "create" || action == "import" || action == "modify" || action == "delete"
                    || action == "duplicate" || action == "move" || action == "rename")
                {
                    var consentError = RequireScriptConsent(path, @params["destination"]?.ToString());
                    if (consentError != null) return consentError;
                }
                switch (action)
                {
                    case "import":
                        // Note: Unity typically auto-imports. This might re-import or configure import settings.
                        return ReimportAsset(path, @params["properties"] as JObject);
                    case "create":
                        return CreateAsset(@params);
                    case "modify":
                        var properties = @params["properties"] as JObject;
                        return ModifyAsset(path, properties);
                    case "delete":
                        return DeleteAsset(path);
                    case "duplicate":
                        return DuplicateAsset(path, @params["destination"]?.ToString());
                    case "move": // Often same as rename if within Assets/
                    case "rename":
                        return MoveOrRenameAsset(path, @params["destination"]?.ToString());
                    case "search":
                        return SearchAssets(@params);
                    case "get_info":
                        return GetAssetInfo(
                            path,
                            @params["generatePreview"]?.ToObject<bool>() ?? false
                        );
                    case "create_folder": // Added specific action for clarity
                        return CreateFolder(path);
                    case "get_components":
                        return GetComponentsFromAsset(path);

                    default:
                        // This error message is less likely to be hit now, but kept here as a fallback or for potential future modifications.
                        string validActionsListDefault = string.Join(", ", ValidActions);
                        return new ErrorResponse(
                            $"Unknown action: '{action}'. Valid actions are: {validActionsListDefault}"
                        );
                }
            }
            catch (Exception e)
            {
                McpLog.Error($"[ManageAsset] Action '{action}' failed for path '{path}': {e}");
                return new ErrorResponse(
                    $"Internal error processing action '{action}' on '{path}': {e.Message}"
                );
            }
        }

        // --- Action Implementations ---

        private static ErrorResponse RequireScriptConsent(params string[] paths)
        {
            var consentError = ManageScript.RequireExplicitConsent();
            if (consentError == null) return null;
            int remaining = 65536;
            var inspection = System.Diagnostics.Stopwatch.StartNew();
            foreach (string path in paths)
            {
                if (!string.IsNullOrEmpty(path) && AffectsCompilation(path, ref remaining, 0, inspection))
                    return consentError;
            }
            return null;
        }

        // Unity's supported plug-in file/bundle types, plus C# assembly/compiler inputs.
        private static readonly HashSet<string> CompilationAssetExtensions = new(StringComparer.OrdinalIgnoreCase)
        {
            ".cs", ".dll", ".asmdef", ".asmref", ".rsp",
            ".a", ".aar", ".bc", ".c", ".cc", ".cpp", ".def", ".dylib", ".h", ".jar",
            ".jslib", ".jspre", ".m", ".mm", ".prx", ".rpl", ".so", ".sprx", ".suprx",
            ".swift", ".winmd", ".xcframework", ".xex", ".xib",
            ".androidlib", ".bundle", ".framework", ".plugin"
        };

        private static bool AffectsCompilation(string path, ref int remaining, int depth,
            System.Diagnostics.Stopwatch inspection)
        {
            // An incomplete inspection cannot establish that a folder is inert.
            if (--remaining < 0 || depth > 128 || inspection.ElapsedMilliseconds >= 1000)
                return true;
            string extension = Path.GetExtension(path);
            if (extension.Equals(".meta", StringComparison.OrdinalIgnoreCase))
                extension = Path.GetExtension(path.Substring(0, path.Length - extension.Length));
            if (CompilationAssetExtensions.Contains(extension))
                return true;

            string contained = AssetPathUtility.GetContainedAssetPath(path);
            string full = AssetPathUtility.GetFullAssetPath(contained);
            if (!Directory.Exists(full)) return false;
            try
            {
                foreach (string entry in Directory.EnumerateFileSystemEntries(full))
                {
                    string child = contained + "/" + Path.GetFileName(entry);
                    // Validate each descendant before traversing a possible directory link.
                    AssetPathUtility.GetFullAssetPath(child);
                    if (AffectsCompilation(child, ref remaining, depth + 1, inspection)) return true;
                }
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            { return true; }
            return false;
        }

        private static object ReimportAsset(string path, JObject properties)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for reimport.");
            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Asset not found at path: {fullPath}");

            try
            {
                // TODO: Apply importer properties before reimporting?
                // This is complex as it requires getting the AssetImporter, casting it,
                // applying properties via reflection or specific methods, saving, then reimporting.
                if (properties != null && properties.HasValues)
                {
                    McpLog.Warn(
                        "[ManageAsset.Reimport] Modifying importer properties before reimport is not fully implemented yet."
                    );
                    // AssetImporter importer = AssetImporter.GetAtPath(fullPath);
                    // if (importer != null) { /* Apply properties */ AssetDatabase.WriteImportSettingsIfDirty(fullPath); }
                }

                AssetPathUtility.GetFullAssetPath(fullPath);
                var consentError = RequireScriptConsent(fullPath);
                if (consentError != null) return consentError;
                AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);
                // AssetDatabase.Refresh(); // Usually ImportAsset handles refresh
                return new SuccessResponse($"Asset '{fullPath}' reimported.", GetAssetData(fullPath));
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to reimport asset '{fullPath}': {e.Message}");
            }
        }

        private static object CreateAsset(JObject @params)
        {
            string path = @params["path"]?.ToString();
            string assetType =
                @params["assetType"]?.ToString()
                ?? @params["asset_type"]?.ToString(); // tolerate snake_case payloads from batched commands
            JObject properties = @params["properties"] as JObject;

            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for create.");
            if (string.IsNullOrEmpty(assetType))
                return new ErrorResponse("'assetType' is required for create.");

            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            if (AssetExists(fullPath))
                return new ErrorResponse($"Asset already exists at path: {fullPath}");

            string lowerAssetType = assetType.ToLowerInvariant();
            if (lowerAssetType == "folder")
                return CreateFolder(path);
            if (lowerAssetType == "prefab")
            {
                return new ErrorResponse(
                    "Creating prefabs programmatically usually requires a source GameObject. Use manage_gameobject to create/configure, then save as prefab via a separate mechanism or future enhancement."
                );
            }
            if (lowerAssetType != "material" && lowerAssetType != "physicsmaterial")
            {
                return new ErrorResponse(
                    $"Creation for asset type '{assetType}' is not explicitly supported yet. Supported: Folder, Material, PhysicsMaterial."
                );
            }

            Shader shader = null;
            if (lowerAssetType == "material")
            {
                var requested = properties?["shader"]?.ToString();
                shader = RenderPipelineUtility.ResolveShader(requested);
                if (shader == null)
                    return new ErrorResponse($"Could not find a project-compatible shader (requested: '{requested ?? "none"}'). Consider installing URP/HDRP or provide an explicit shader path.");
            }

            UnityEngine.Object newAsset = null;
            try
            {
                EnsureDirectoryExists(Path.GetDirectoryName(fullPath));

                // Handle common asset types
                if (lowerAssetType == "material")
                {
                    var mat = new Material(shader);
                    newAsset = mat;
                    if (properties != null)
                    {
                        JObject propertiesForApply = properties;
                        if (propertiesForApply["shader"] != null)
                        {
                            propertiesForApply = (JObject)properties.DeepClone();
                            propertiesForApply.Remove("shader");
                        }

                        if (propertiesForApply.HasValues)
                        {
                            MaterialOps.ApplyProperties(mat, propertiesForApply, UnityJsonSerializer.Instance);
                        }
                    }
                    AssetPathUtility.GetFullAssetPath(fullPath);
                    AssetDatabase.CreateAsset(mat, fullPath);
                }
                else if (lowerAssetType == "physicsmaterial")
                {
                    PhysicsMaterialType pmat = new PhysicsMaterialType();
                    newAsset = pmat;
                    if (properties != null)
                        ApplyPhysicsMaterialProperties(pmat, properties);
                    AssetPathUtility.GetFullAssetPath(fullPath);
                    AssetDatabase.CreateAsset(pmat, fullPath);
                }

                if (newAsset == null || !EditorUtility.IsPersistent(newAsset)
                    || !string.Equals(
                        AssetPathUtility.NormalizeSeparators(AssetDatabase.GetAssetPath(newAsset)),
                        AssetPathUtility.NormalizeSeparators(fullPath),
                        StringComparison.OrdinalIgnoreCase))
                {
                    return new ErrorResponse(
                        $"Failed to create asset '{assetType}' at '{fullPath}'. See logs for details."
                    );
                }

                AssetDatabase.SaveAssets();
                // AssetDatabase.Refresh(); // CreateAsset often handles refresh
                return new SuccessResponse(
                    $"Asset '{fullPath}' created successfully.",
                    GetAssetData(fullPath)
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to create asset at '{fullPath}': {e.Message}");
            }
            finally
            {
                if (newAsset != null && !EditorUtility.IsPersistent(newAsset))
                    UnityEngine.Object.DestroyImmediate(newAsset);
            }
        }

        private static object CreateFolder(string path)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for create_folder.");
            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            string parentDir = AssetPathUtility.NormalizeSeparators(Path.GetDirectoryName(fullPath));
            string folderName = Path.GetFileName(fullPath);

            if (AssetExists(fullPath))
            {
                // Check if it's actually a folder already
                if (AssetDatabase.IsValidFolder(fullPath))
                {
                    return new SuccessResponse(
                        $"Folder already exists at path: {fullPath}",
                        GetAssetData(fullPath)
                    );
                }
                else
                {
                    return new ErrorResponse(
                        $"An asset (not a folder) already exists at path: {fullPath}"
                    );
                }
            }

            try
            {
                // Ensure parent exists
                if (!string.IsNullOrEmpty(parentDir) && !AssetDatabase.IsValidFolder(parentDir))
                {
                    var parentResult = CreateFolder(parentDir);
                    if (parentResult is ErrorResponse)
                        return parentResult;
                }

                AssetPathUtility.GetFullAssetPath(fullPath);
                string guid = AssetDatabase.CreateFolder(parentDir, folderName);
                if (string.IsNullOrEmpty(guid))
                {
                    return new ErrorResponse(
                        $"Failed to create folder '{fullPath}'. Check logs and permissions."
                    );
                }

                // AssetDatabase.Refresh(); // CreateFolder usually handles refresh
                return new SuccessResponse(
                    $"Folder '{fullPath}' created successfully.",
                    GetAssetData(fullPath)
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to create folder '{fullPath}': {e.Message}");
            }
        }

        private static object ModifyAsset(string path, JObject properties)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for modify.");
            if (properties == null || !properties.HasValues)
                return new ErrorResponse("'properties' are required for modify.");

            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Asset not found at path: {fullPath}");

            try
            {
                UnityEngine.Object asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(
                    fullPath
                );
                if (asset == null)
                    return new ErrorResponse($"Failed to load asset at path: {fullPath}");

                AssetPathUtility.GetFullAssetPath(fullPath);
                bool modified = false; // Flag to track if any changes were made

                // --- NEW: Handle GameObject / Prefab Component Modification ---
                if (asset is GameObject gameObject)
                {
                    // Iterate through the properties JSON: keys are component names, values are properties objects for that component
                    foreach (var prop in properties.Properties())
                    {
                        string componentName = prop.Name; // e.g., "Collectible"
                        // Check if the value associated with the component name is actually an object containing properties
                        if (
                            prop.Value is JObject componentProperties
                            && componentProperties.HasValues
                        ) // e.g., {"bobSpeed": 2.0}
                        {
                            // Resolve component type via ComponentResolver, then fetch by Type
                            Component targetComponent = null;
                            bool resolved = ComponentResolver.TryResolve(componentName, out var compType, out var compError);
                            if (resolved)
                            {
                                targetComponent = gameObject.GetComponent(compType);
                            }

                            // Only warn about resolution failure if component also not found
                            if (targetComponent == null && !resolved)
                            {
                                McpLog.Warn(
                                    $"[ManageAsset.ModifyAsset] Failed to resolve component '{componentName}' on '{gameObject.name}': {compError}"
                                );
                            }

                            if (targetComponent != null)
                            {
                                // Apply the nested properties (e.g., bobSpeed) to the found component instance
                                // Use |= to ensure 'modified' becomes true if any component is successfully modified
                                modified |= ApplyObjectProperties(
                                    targetComponent,
                                    componentProperties
                                );
                            }
                            else
                            {
                                // Log a warning if a specified component couldn't be found
                                McpLog.Warn(
                                    $"[ManageAsset.ModifyAsset] Component '{componentName}' not found on GameObject '{gameObject.name}' in asset '{fullPath}'. Skipping modification for this component."
                                );
                            }
                        }
                        else
                        {
                            // Log a warning if the structure isn't {"ComponentName": {"prop": value}}
                            // We could potentially try to apply this property directly to the GameObject here if needed,
                            // but the primary goal is component modification.
                            McpLog.Warn(
                                $"[ManageAsset.ModifyAsset] Property '{prop.Name}' for GameObject modification should have a JSON object value containing component properties. Value was: {prop.Value.Type}. Skipping."
                            );
                        }
                    }
                    // Note: 'modified' is now true if ANY component property was successfully changed.
                }
                // --- End NEW ---

                // --- Existing logic for other asset types (now as else-if) ---
                // Example: Modifying a Material
                else if (asset is Material material)
                {
                    // Apply properties directly to the material. If this modifies, it sets modified=true.
                    // Use |= in case the asset was already marked modified by previous logic (though unlikely here)
                    modified |= MaterialOps.ApplyProperties(material, properties, UnityJsonSerializer.Instance);
                }
                // Example: Modifying a ScriptableObject (Use manage_scriptable_object instead!)
                else if (asset is ScriptableObject so)
                {
                    // Deprecated: Prefer manage_scriptable_object for robust patching.
                    // Kept for simple property setting fallback on existing assets if manage_scriptable_object isn't used.
                    modified |= ApplyObjectProperties(so, properties);
                }
                // Example: Modifying TextureImporter settings
                else if (asset is Texture)
                {
                    AssetImporter importer = AssetImporter.GetAtPath(fullPath);
                    if (importer is TextureImporter textureImporter)
                    {
                        bool importerModified = ApplyObjectProperties(textureImporter, properties);
                        if (importerModified)
                        {
                            // Importer settings need saving and reimporting
                            AssetPathUtility.GetFullAssetPath(fullPath);
                            AssetDatabase.WriteImportSettingsIfDirty(fullPath);
                            AssetPathUtility.GetFullAssetPath(fullPath);
                            AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate); // Reimport to apply changes
                            modified = true; // Mark overall operation as modified
                        }
                    }
                    else
                    {
                        McpLog.Warn($"Could not get TextureImporter for {fullPath}.");
                    }
                }
                // TODO: Add modification logic for other common asset types (Models, AudioClips importers, etc.)
                else // Fallback for other asset types OR direct properties on non-GameObject assets
                {
                    // This block handles non-GameObject/Material/ScriptableObject/Texture assets.
                    // Attempts to apply properties directly to the asset itself.
                    McpLog.Warn(
                        $"[ManageAsset.ModifyAsset] Asset type '{asset.GetType().Name}' at '{fullPath}' is not explicitly handled for component modification. Attempting generic property setting on the asset itself."
                    );
                    modified |= ApplyObjectProperties(asset, properties);
                }
                // --- End Existing Logic ---

                // Check if any modification happened (either component or direct asset modification)
                if (modified)
                {
                    AssetPathUtility.GetFullAssetPath(fullPath);
                    // Mark the asset as dirty (important for prefabs/SOs) so Unity knows to save it.
                    EditorUtility.SetDirty(asset);
                    // Save all modified assets to disk.
                    AssetDatabase.SaveAssets();
                    // Refresh might be needed in some edge cases, but SaveAssets usually covers it.
                    // AssetDatabase.Refresh();
                    return new SuccessResponse(
                        $"Asset '{fullPath}' modified successfully.",
                        GetAssetData(fullPath)
                    );
                }
                else
                {
                    // If no changes were made (e.g., component not found, property names incorrect, value unchanged), return a success message indicating nothing changed.
                    return new SuccessResponse(
                        $"No applicable or modifiable properties found for asset '{fullPath}'. Check component names, property names, and values.",
                        GetAssetData(fullPath)
                    );
                    // Previous message: return new SuccessResponse($"No applicable properties found to modify for asset '{fullPath}'.", GetAssetData(fullPath));
                }
            }
            catch (Exception e)
            {
                // Log the detailed error internally
                McpLog.Error($"[ManageAsset] Action 'modify' failed for path '{path}': {e}");
                // Return a user-friendly error message
                return new ErrorResponse($"Failed to modify asset '{fullPath}': {e.Message}");
            }
        }

        private static object DeleteAsset(string path)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for delete.");
            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Asset not found at path: {fullPath}");

            try
            {
                AssetPathUtility.GetFullAssetPath(fullPath);
                bool success = AssetDatabase.DeleteAsset(fullPath);
                if (success)
                {
                    // AssetDatabase.Refresh(); // DeleteAsset usually handles refresh
                    return new SuccessResponse($"Asset '{fullPath}' deleted successfully.");
                }
                else
                {
                    // This might happen if the file couldn't be deleted (e.g., locked)
                    return new ErrorResponse(
                        $"Failed to delete asset '{fullPath}'. Check logs or if the file is locked."
                    );
                }
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error deleting asset '{fullPath}': {e.Message}");
            }
        }

        private static object DuplicateAsset(string path, string destinationPath)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for duplicate.");

            string sourcePath = AssetPathUtility.GetContainedAssetPath(path);
            if (!AssetExists(sourcePath))
                return new ErrorResponse($"Source asset not found at path: {sourcePath}");

            string destPath;
            if (string.IsNullOrEmpty(destinationPath))
            {
                // Generate a unique path if destination is not provided
                destPath = AssetPathUtility.GetContainedAssetPath(
                    AssetDatabase.GenerateUniqueAssetPath(sourcePath));
            }
            else
            {
                destPath = AssetPathUtility.GetContainedAssetPath(destinationPath);
                if (AssetExists(destPath))
                    return new ErrorResponse($"Asset already exists at destination path: {destPath}");
                // Ensure destination directory exists
                EnsureDirectoryExists(Path.GetDirectoryName(destPath));
            }

            try
            {
                AssetPathUtility.GetFullAssetPath(sourcePath);
                AssetPathUtility.GetFullAssetPath(destPath);
                var consentError = RequireScriptConsent(sourcePath, destPath);
                if (consentError != null) return consentError;
                bool success = AssetDatabase.CopyAsset(sourcePath, destPath);
                if (success)
                {
                    // AssetDatabase.Refresh();
                    return new SuccessResponse(
                        $"Asset '{sourcePath}' duplicated to '{destPath}'.",
                        GetAssetData(destPath)
                    );
                }
                else
                {
                    return new ErrorResponse(
                        $"Failed to duplicate asset from '{sourcePath}' to '{destPath}'."
                    );
                }
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error duplicating asset '{sourcePath}': {e.Message}");
            }
        }

        private static object MoveOrRenameAsset(string path, string destinationPath)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for move/rename.");
            if (string.IsNullOrEmpty(destinationPath))
                return new ErrorResponse("'destination' path is required for move/rename.");

            string sourcePath = AssetPathUtility.GetContainedAssetPath(path);
            string destPath = AssetPathUtility.GetContainedAssetPath(destinationPath);

            if (!AssetExists(sourcePath))
                return new ErrorResponse($"Source asset not found at path: {sourcePath}");
            if (AssetExists(destPath))
                return new ErrorResponse(
                    $"An asset already exists at the destination path: {destPath}"
                );

            // Ensure destination directory exists
            EnsureDirectoryExists(Path.GetDirectoryName(destPath));

            try
            {
                // Validate will return an error string if failed, null if successful
                AssetPathUtility.GetFullAssetPath(sourcePath);
                AssetPathUtility.GetFullAssetPath(destPath);
                string error = AssetDatabase.ValidateMoveAsset(sourcePath, destPath);
                if (!string.IsNullOrEmpty(error))
                {
                    return new ErrorResponse(
                        $"Failed to move/rename asset from '{sourcePath}' to '{destPath}': {error}"
                    );
                }

                AssetPathUtility.GetFullAssetPath(sourcePath);
                AssetPathUtility.GetFullAssetPath(destPath);
                var consentError = RequireScriptConsent(sourcePath, destPath);
                if (consentError != null) return consentError;
                string moveError = AssetDatabase.MoveAsset(sourcePath, destPath);
                if (string.IsNullOrEmpty(moveError))
                {
                    // AssetDatabase.Refresh(); // MoveAsset usually handles refresh
                    return new SuccessResponse(
                        $"Asset moved/renamed from '{sourcePath}' to '{destPath}'.",
                        GetAssetData(destPath)
                    );
                }
                else
                {
                    return new ErrorResponse(
                        $"Failed to move/rename asset from '{sourcePath}' to '{destPath}': {moveError}"
                    );
                }
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error moving/renaming asset '{sourcePath}': {e.Message}");
            }
        }

        private static object SearchAssets(JObject @params)
        {
            string searchPattern = @params["searchPattern"]?.ToString();
            string filterType = @params["filterType"]?.ToString();
            string pathScope = @params["path"]?.ToString(); // Use path as folder scope
            string filterDateAfterStr = @params["filterDateAfter"]?.ToString();
            bool generatePreview = @params["generatePreview"]?.ToObject<bool>() ?? false;
            int maximum = generatePreview ? PaginationBounds.MaxPreviewPageSize : PaginationBounds.MaxPageSize;
            if (!PaginationBounds.TryRead(@params["page_size"] ?? @params["pageSize"], Math.Min(50, maximum),
                    1, maximum, "pageSize", out int pageSize, out string pageError)
                || !PaginationBounds.TryRead(@params["page_number"] ?? @params["pageNumber"], 1,
                    1, int.MaxValue, "pageNumber", out int pageNumber, out pageError))
                return new ErrorResponse(pageError);

            long startIndex = PaginationBounds.StartIndex(pageNumber, pageSize);
            var previewBudget = new PaginationBounds.PreviewBudget();

            List<string> searchFilters = new List<string>();
            if (!string.IsNullOrEmpty(searchPattern))
                searchFilters.Add(searchPattern);
            if (!string.IsNullOrEmpty(filterType))
                searchFilters.Add($"t:{filterType}");

            string[] folderScope = null;
            if (!string.IsNullOrEmpty(pathScope))
            {
                folderScope = new string[] { AssetPathUtility.SanitizeAssetPath(pathScope) };
                if (!AssetDatabase.IsValidFolder(folderScope[0]))
                {
                    return new ErrorResponse(
                        $"Search path '{pathScope}' is not a valid folder."
                    );
                }
            }

            DateTime? filterDateAfter = null;
            if (!string.IsNullOrEmpty(filterDateAfterStr))
            {
                if (
                    DateTime.TryParse(
                        filterDateAfterStr,
                        CultureInfo.InvariantCulture,
                        DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                        out DateTime parsedDate
                    )
                )
                {
                    filterDateAfter = parsedDate;
                }
                else
                {
                    return new ErrorResponse(
                        $"Could not parse filterDateAfter: '{filterDateAfterStr}'. Expected ISO 8601 format."
                    );
                }
            }

            try
            {
                string[] guids = AssetDatabase.FindAssets(
                    string.Join(" ", searchFilters),
                    folderScope
                );
                List<object> results = new List<object>();
                int totalFound = 0;

                foreach (string guid in guids)
                {
                    string assetPath;
                    try { assetPath = AssetPathUtility.GetAssetPathFromGuid(guid, allowPackages: true); }
                    catch (ArgumentException) { continue; }
                    catch (InvalidOperationException) { continue; }
                    if (string.IsNullOrEmpty(assetPath))
                        continue;

                    // Apply date filter if present
                    if (filterDateAfter.HasValue)
                    {
                        DateTime lastWriteTime = File.GetLastWriteTimeUtc(
                            Path.Combine(Directory.GetCurrentDirectory(), assetPath)
                        );
                        if (lastWriteTime <= filterDateAfter.Value)
                        {
                            continue; // Skip assets older than or equal to the filter date
                        }
                    }

                    // Only load assets and generate previews for the requested page.
                    if (totalFound >= startIndex && results.Count < pageSize)
                        results.Add(GetAssetData(assetPath, generatePreview, previewBudget));
                    totalFound++;
                }

                return new SuccessResponse(
                    $"Found {totalFound} asset(s). Returning page {pageNumber} ({results.Count} assets).",
                    new
                    {
                        totalAssets = totalFound,
                        pageSize = pageSize,
                        pageNumber = pageNumber,
                        maxPageSize = maximum,
                        previewBase64Bytes = previewBudget.Base64Bytes,
                        assets = results,
                    }
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error searching assets: {e.Message}");
            }
        }

        private static object GetAssetInfo(string path, bool generatePreview)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for get_info.");
            string fullPath = AssetPathUtility.SanitizeAssetPath(path);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Asset not found at path: {fullPath}");

            try
            {
                return new SuccessResponse(
                    "Asset info retrieved.",
                    GetAssetData(fullPath, generatePreview)
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error getting info for asset '{fullPath}': {e.Message}");
            }
        }

        /// <summary>
        /// Retrieves components attached to a GameObject asset (like a Prefab).
        /// </summary>
        /// <param name="path">The asset path of the GameObject or Prefab.</param>
        /// <returns>A response object containing a list of component type names or an error.</returns>
        private static object GetComponentsFromAsset(string path)
        {
            // 1. Validate input path
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for get_components.");

            // 2. Sanitize and check existence
            string fullPath = AssetPathUtility.SanitizeAssetPath(path);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Asset not found at path: {fullPath}");

            try
            {
                // 3. Load the asset
                UnityEngine.Object asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(
                    fullPath
                );
                if (asset == null)
                    return new ErrorResponse($"Failed to load asset at path: {fullPath}");

                // 4. Check if it's a GameObject (Prefabs load as GameObjects)
                GameObject gameObject = asset as GameObject;
                if (gameObject == null)
                {
                    // Also check if it's *directly* a Component type (less common for primary assets)
                    Component componentAsset = asset as Component;
                    if (componentAsset != null)
                    {
                        // If the asset itself *is* a component, maybe return just its info?
                        // This is an edge case. Let's stick to GameObjects for now.
                        return new ErrorResponse(
                            $"Asset at '{fullPath}' is a Component ({asset.GetType().FullName}), not a GameObject. Components are typically retrieved *from* a GameObject."
                        );
                    }
                    return new ErrorResponse(
                        $"Asset at '{fullPath}' is not a GameObject (Type: {asset.GetType().FullName}). Cannot get components from this asset type."
                    );
                }

                // 5. Get components
                Component[] components = gameObject.GetComponents<Component>();

                // 6. Format component data
                List<object> componentList = components
                    .Select(comp => new
                    {
                        typeName = comp.GetType().FullName,
                        instanceID = comp.GetInstanceIDCompat(),
                        // TODO: Add more component-specific details here if needed in the future?
                        //       Requires reflection or specific handling per component type.
                    })
                    .ToList<object>(); // Explicit cast for clarity if needed

                // 7. Return success response
                return new SuccessResponse(
                    $"Found {componentList.Count} component(s) on asset '{fullPath}'.",
                    componentList
                );
            }
            catch (Exception e)
            {
                McpLog.Error(
                    $"[ManageAsset.GetComponentsFromAsset] Error getting components for '{fullPath}': {e}"
                );
                return new ErrorResponse(
                    $"Error getting components for asset '{fullPath}': {e.Message}"
                );
            }
        }

        // --- Internal Helpers ---

        /// <summary>
        /// Ensures the asset path starts with "Assets/".
        /// </summary>
        /// <summary>
        /// Checks if an asset exists at the given path (file or folder).
        /// </summary>
        private static bool AssetExists(string sanitizedPath)
        {
            // AssetDatabase APIs are generally preferred over raw File/Directory checks for assets.
            // Check if it's a known asset GUID.
            if (!string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(sanitizedPath, AssetPathToGUIDOptions.OnlyExistingAssets)))
            {
                return true;
            }
            // AssetPathToGUID might not work for newly created folders not yet refreshed.
            // Check directory explicitly for folders.
            if (Directory.Exists(Path.Combine(Directory.GetCurrentDirectory(), sanitizedPath)))
            {
                // Check if it's considered a *valid* folder by Unity
                return AssetDatabase.IsValidFolder(sanitizedPath);
            }
            // Check file existence for non-folder assets.
            if (File.Exists(Path.Combine(Directory.GetCurrentDirectory(), sanitizedPath)))
            {
                return true; // Assume if file exists, it's an asset or will be imported
            }

            return false;
            // Alternative: return !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(sanitizedPath));
        }

        /// <summary>
        /// Ensures the directory for a given asset path exists, creating it if necessary.
        /// </summary>
        private static void EnsureDirectoryExists(string directoryPath)
        {
            if (string.IsNullOrEmpty(directoryPath))
                return;
            string fullDirPath = AssetPathUtility.GetFullAssetPath(directoryPath);
            if (!Directory.Exists(fullDirPath))
            {
                Directory.CreateDirectory(AssetPathUtility.GetFullAssetPath(directoryPath));
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport); // Let Unity know about the new folder
            }
        }



        /// <summary>
        ///  Applies properties from JObject to a PhysicsMaterial.
        /// </summary>
        private static bool ApplyPhysicsMaterialProperties(PhysicsMaterialType pmat, JObject properties)
        {
            if (pmat == null || properties == null)
                return false;
            bool modified = false;

            // Example: Set dynamic friction
            if (properties["dynamicFriction"]?.Type == JTokenType.Float)
            {
                float dynamicFriction = properties["dynamicFriction"].ToObject<float>();
                pmat.dynamicFriction = dynamicFriction;
                modified = true;
            }

            // Example: Set static friction
            if (properties["staticFriction"]?.Type == JTokenType.Float)
            {
                float staticFriction = properties["staticFriction"].ToObject<float>();
                pmat.staticFriction = staticFriction;
                modified = true;
            }

            // Example: Set bounciness
            if (properties["bounciness"]?.Type == JTokenType.Float)
            {
                float bounciness = properties["bounciness"].ToObject<float>();
                pmat.bounciness = bounciness;
                modified = true;
            }

            List<String> averageList = new List<String> { "ave", "Ave", "average", "Average" };
            List<String> multiplyList = new List<String> { "mul", "Mul", "mult", "Mult", "multiply", "Multiply" };
            List<String> minimumList = new List<String> { "min", "Min", "minimum", "Minimum" };
            List<String> maximumList = new List<String> { "max", "Max", "maximum", "Maximum" };

            // Example: Set friction combine
            if (properties["frictionCombine"]?.Type == JTokenType.String)
            {
                string frictionCombine = properties["frictionCombine"].ToString();
                if (averageList.Contains(frictionCombine))
                    pmat.frictionCombine = PhysicsMaterialCombine.Average;
                else if (multiplyList.Contains(frictionCombine))
                    pmat.frictionCombine = PhysicsMaterialCombine.Multiply;
                else if (minimumList.Contains(frictionCombine))
                    pmat.frictionCombine = PhysicsMaterialCombine.Minimum;
                else if (maximumList.Contains(frictionCombine))
                    pmat.frictionCombine = PhysicsMaterialCombine.Maximum;
                modified = true;
            }

            // Example: Set bounce combine
            if (properties["bounceCombine"]?.Type == JTokenType.String)
            {
                string bounceCombine = properties["bounceCombine"].ToString();
                if (averageList.Contains(bounceCombine))
                    pmat.bounceCombine = PhysicsMaterialCombine.Average;
                else if (multiplyList.Contains(bounceCombine))
                    pmat.bounceCombine = PhysicsMaterialCombine.Multiply;
                else if (minimumList.Contains(bounceCombine))
                    pmat.bounceCombine = PhysicsMaterialCombine.Minimum;
                else if (maximumList.Contains(bounceCombine))
                    pmat.bounceCombine = PhysicsMaterialCombine.Maximum;
                modified = true;
            }

            return modified;
        }

        /// <summary>
        /// Generic helper to set properties on any UnityEngine.Object using reflection.
        /// </summary>
        private static bool ApplyObjectProperties(UnityEngine.Object target, JObject properties)
        {
            if (target == null || properties == null)
                return false;
            bool modified = false;
            Type type = target.GetType();

            foreach (var prop in properties.Properties())
            {
                string propName = prop.Name;
                JToken propValue = prop.Value;
                if (SetPropertyOrField(target, propName, propValue, type))
                {
                    modified = true;
                }
            }
            return modified;
        }

        /// <summary>
        /// Helper to set a property or field via reflection, handling basic types and Unity objects.
        /// </summary>
        private static bool SetPropertyOrField(
            object target,
            string memberName,
            JToken value,
            Type type = null
        )
        {
            type = type ?? target.GetType();
            System.Reflection.BindingFlags flags =
                System.Reflection.BindingFlags.Public
                | System.Reflection.BindingFlags.Instance
                | System.Reflection.BindingFlags.IgnoreCase;

            try
            {
                System.Reflection.PropertyInfo propInfo = type.GetProperty(memberName, flags);
                if (propInfo != null && propInfo.CanWrite)
                {
                    object convertedValue = Helpers.PropertyConversion.TryConvertToType(value, propInfo.PropertyType);
                    if (
                        convertedValue != null
                        && !object.Equals(propInfo.GetValue(target), convertedValue)
                    )
                    {
                        propInfo.SetValue(target, convertedValue);
                        return true;
                    }
                }
                else
                {
                    System.Reflection.FieldInfo fieldInfo = type.GetField(memberName, flags);
                    if (fieldInfo != null)
                    {
                        object convertedValue = Helpers.PropertyConversion.TryConvertToType(value, fieldInfo.FieldType);
                        if (
                            convertedValue != null
                            && !object.Equals(fieldInfo.GetValue(target), convertedValue)
                        )
                        {
                            fieldInfo.SetValue(target, convertedValue);
                            return true;
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn(
                    $"[SetPropertyOrField] Failed to set '{memberName}' on {type.Name}: {ex.Message}"
                );
            }
            return false;
        }

        // --- Data Serialization ---

        /// <summary>
        /// Creates a serializable representation of an asset.
        /// </summary>
        private static object GetAssetData(string path, bool generatePreview = false,
            PaginationBounds.PreviewBudget previewBudget = null)
        {
            path = AssetPathUtility.GetAssetReferencePath(path, allowPackages: true);
            if (string.IsNullOrEmpty(path) || !AssetExists(path))
                return null;

            string guid = AssetDatabase.AssetPathToGUID(path);
            Type assetType = AssetDatabase.GetMainAssetTypeAtPath(path);
            UnityEngine.Object asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
            string previewBase64 = null;
            int previewWidth = 0;
            int previewHeight = 0;

            previewBudget ??= new PaginationBounds.PreviewBudget();
            if (generatePreview && asset != null && previewBudget.CanGenerate)
            {
                Texture2D preview = AssetPreview.GetAssetPreview(asset);

                if (preview != null && preview.width > 0 && preview.height > 0)
                {
                    try
                    {
                        // Ensure texture is readable for EncodeToPNG
                        // Creating a temporary readable copy is safer
                        RenderTexture rt = null;
                        Texture2D readablePreview = null;
                        RenderTexture previous = RenderTexture.active;
                        try
                        {
                            double scale = Math.Min(1.0, (double)PaginationBounds.MaxPreviewEdge
                                / Math.Max(preview.width, preview.height));
                            int width = Math.Max(1, (int)(preview.width * scale));
                            int height = Math.Max(1, (int)(preview.height * scale));
                            rt = RenderTexture.GetTemporary(width, height);
                            UnityEngine.Graphics.Blit(preview, rt);
                            RenderTexture.active = rt;
                            readablePreview = new Texture2D(width, height, TextureFormat.RGB24, false);
                            readablePreview.ReadPixels(new Rect(0, 0, rt.width, rt.height), 0, 0);
                            readablePreview.Apply();

                            var pngData = readablePreview.EncodeToPNG();
                            if (pngData != null && previewBudget.TryReserve(pngData.Length))
                            {
                                previewBase64 = Convert.ToBase64String(pngData);
                                previewWidth = readablePreview.width;
                                previewHeight = readablePreview.height;
                            }
                        }
                        finally
                        {
                            RenderTexture.active = previous;
                            if (rt != null) RenderTexture.ReleaseTemporary(rt);
                            if (readablePreview != null) UnityEngine.Object.DestroyImmediate(readablePreview);
                        }
                    }
                    catch (Exception ex)
                    {
                        McpLog.Warn(
                            $"Failed to generate readable preview for '{path}': {ex.Message}. Preview might not be readable."
                        );
                        // Fallback: Try getting static preview if available?
                        // Texture2D staticPreview = AssetPreview.GetMiniThumbnail(asset);
                    }
                }
                else
                {
                    McpLog.Warn(
                        $"Could not get asset preview for {path} (Type: {assetType?.Name}). Is it supported?"
                    );
                }
            }

            return new
            {
                path = path,
                guid = guid,
                assetType = assetType?.FullName ?? "Unknown",
                name = Path.GetFileNameWithoutExtension(path),
                fileName = Path.GetFileName(path),
                isFolder = AssetDatabase.IsValidFolder(path),
                instanceID = asset?.GetInstanceIDCompat() ?? 0,
                lastWriteTimeUtc = File.GetLastWriteTimeUtc(
                        Path.Combine(Directory.GetCurrentDirectory(), path)
                    )
                    .ToString("o"), // ISO 8601
                // --- Preview Data ---
                previewBase64 = previewBase64, // PNG data as Base64 string
                previewWidth = previewWidth,
                previewHeight = previewHeight,
                // TODO: Add more metadata? Importer settings? Dependencies?
            };
        }
    }
}
