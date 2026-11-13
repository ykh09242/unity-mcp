using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers; // For Response class
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
#if MCP_FOR_UNITY_PHYSICS
#if UNITY_6000_0_OR_NEWER
using PhysicsMaterialType = UnityEngine.PhysicsMaterial;
using PhysicsMaterialCombine = UnityEngine.PhysicsMaterialCombine;
#else
using PhysicsMaterialType = UnityEngine.PhysicMaterial;
using PhysicsMaterialCombine = UnityEngine.PhysicMaterialCombine;
#endif
#endif

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Handles asset management operations within the Unity project.
    /// </summary>
    [McpForUnityTool("manage_asset", AutoRegister = false, Group = "legacy")]
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
            "search_metadata_v1",
            "get_info",
            "create_folder",
            "get_components",
            "list_asset_bundles",
            "get_bundle_assets",
            "get_bundle_dependencies",
            "get_dependencies",
            "get_reverse_references",
            "preview_move",
            "preview_delete",
            "get_labels",
            "set_labels",
            "add_labels",
            "remove_labels",
        };

        public static Task<object> HandleCommand(JObject parameters, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            string action = parameters?["action"]?.ToString()?.ToLowerInvariant();
            if (!AssetReferenceDiagnostics.IsAction(action))
                return Task.FromResult(HandleCommand(parameters));
            var optionError = AssetSearchSnapshots.ValidateOptions(parameters, false);
            return optionError != null
                ? Task.FromResult<object>(optionError)
                : AssetReferenceDiagnostics.HandleAsync((JObject)parameters.DeepClone(), cancellationToken);
        }

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
                return new ErrorResponse($"Unknown action: '{action}'. Valid actions are: {validActionsList}");
            }

            var searchOptionError = AssetSearchSnapshots.ValidateOptions(@params, action == "search_metadata_v1");
            if (searchOptionError != null)
                return searchOptionError;

            if (AssetReferenceDiagnostics.IsAction(action))
                return AssetReferenceDiagnostics.Handle(@params);
            bool guardedReferenceMutation = (action == "move" || action == "delete") && @params["referenceRevision"] != null;
            if (AssetReferenceDiagnostics.HasOptions(@params) && !guardedReferenceMutation)
                return new ErrorResponse(
                    "invalid_reference_diagnostics",
                    new { reason = "Reference options require a reference action or guarded move/delete." }
                );

            if (AssetLabels.IsAction(action))
                return AssetLabels.Handle(@params, RequireScriptConsent);

            // Common parameters
            string path = @params["path"]?.ToString();

            // Coerce string JSON to JObject for 'properties' if provided as a JSON string
            var propertiesToken = @params["properties"];
            if (
                action == "create"
                && propertiesToken != null
                && propertiesToken.Type != JTokenType.Null
                && propertiesToken.Type != JTokenType.Object
                && propertiesToken.Type != JTokenType.String
            )
            {
                return new ErrorResponse("'properties' must be a JSON object or null for create.");
            }
            if (action != "import" && propertiesToken != null && propertiesToken.Type == JTokenType.String)
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
                if (
                    action == "create"
                    || action == "import"
                    || action == "modify"
                    || action == "delete"
                    || action == "duplicate"
                    || action == "move"
                    || action == "rename"
                )
                {
                    var consentError = RequireScriptConsent(path, @params["destination"]?.ToString());
                    if (consentError != null)
                        return consentError;
                }
                // Bundle inspection does not use path; validate only parameters used by this action.
                bool usesPath = action != "list_asset_bundles" && action != "get_bundle_assets" && action != "get_bundle_dependencies";
                var pathToken = @params["path"];
                if (usesPath && pathToken != null && pathToken.Type != JTokenType.Null && pathToken.Type != JTokenType.String)
                    return new ErrorResponse("'path' must be a string or null.");
                var destinationToken = @params["destination"];
                if (
                    (action == "duplicate" || action == "move" || action == "rename")
                    && destinationToken != null
                    && destinationToken.Type != JTokenType.Null
                    && destinationToken.Type != JTokenType.String
                )
                    return new ErrorResponse("'destination' must be a string or null.");
                if (guardedReferenceMutation)
                {
                    var referenceError = AssetReferenceDiagnostics.GuardMutation(@params);
                    if (referenceError != null)
                        return referenceError;
                }
                if (
                    action == "import"
                    || action == "create"
                    || action == "modify"
                    || action == "delete"
                    || action == "duplicate"
                    || action == "move"
                    || action == "rename"
                    || action == "create_folder"
                )
                    AssetSearchSnapshots.Invalidate();
                switch (action)
                {
                    case "import":
                        // Note: Unity typically auto-imports. This might re-import or configure import settings.
                        return ReimportAsset(path, @params["properties"]);
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
                    case "search_metadata_v1":
                        return SearchAssets(@params, metadataOnly: true);
                    case "get_info":
                        return GetAssetInfo(path, @params["generatePreview"]?.ReadScalar<bool?>() ?? false);
                    case "create_folder": // Added specific action for clarity
                        return CreateFolder(path);
                    case "get_components":
                        return GetComponentsFromAsset(path);
                    case "list_asset_bundles":
                    case "get_bundle_assets":
                    case "get_bundle_dependencies":
                        return AssetBundleInspection.Inspect(@params);

                    default:
                        // This error message is less likely to be hit now, but kept here as a fallback or for potential future modifications.
                        string validActionsListDefault = string.Join(", ", ValidActions);
                        return new ErrorResponse($"Unknown action: '{action}'. Valid actions are: {validActionsListDefault}");
                }
            }
            catch (Exception e)
            {
                McpLog.Error($"[ManageAsset] Action '{action}' failed for path '{path}': {e}");
                return new ErrorResponse($"Internal error processing action '{action}' on '{path}': {e.Message}");
            }
        }

        // --- Action Implementations ---

        internal static ErrorResponse RequireScriptConsent(params string[] paths)
        {
            var consentError = ManageScript.RequireExplicitConsent();
            if (consentError == null)
                return null;
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
            ".cs",
            ".dll",
            ".asmdef",
            ".asmref",
            ".rsp",
            ".a",
            ".aar",
            ".bc",
            ".c",
            ".cc",
            ".cpp",
            ".def",
            ".dylib",
            ".h",
            ".jar",
            ".jslib",
            ".jspre",
            ".m",
            ".mm",
            ".prx",
            ".rpl",
            ".so",
            ".sprx",
            ".suprx",
            ".swift",
            ".winmd",
            ".xcframework",
            ".xex",
            ".xib",
            ".androidlib",
            ".bundle",
            ".framework",
            ".plugin",
        };

        private static bool AffectsCompilation(string path, ref int remaining, int depth, System.Diagnostics.Stopwatch inspection)
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
            if (!Directory.Exists(full))
                return false;
            try
            {
                foreach (string entry in Directory.EnumerateFileSystemEntries(full))
                {
                    string child = contained + "/" + Path.GetFileName(entry);
                    // Validate each descendant before traversing a possible directory link.
                    AssetPathUtility.GetFullAssetPath(child);
                    if (AffectsCompilation(child, ref remaining, depth + 1, inspection))
                        return true;
                }
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                return true;
            }
            return false;
        }

        private static object ReimportAsset(string path, JToken properties)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for reimport.");
            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Asset not found at path: {fullPath}");

            try
            {
                if (properties?.Type == JTokenType.String)
                {
                    try
                    {
                        properties = JObject.Parse(properties.Value<string>());
                    }
                    catch (Newtonsoft.Json.JsonReaderException)
                    {
                        return new ErrorResponse("'properties' must be a JSON object or null for import.");
                    }
                }
                // Import is a plain reimport. Settings were previously ignored after a warning,
                // yet the asset was still reimported and the caller received a success response.
                if (properties != null && properties.Type != JTokenType.Null && (!(properties is JObject importProperties) || importProperties.HasValues))
                    return new ErrorResponse("Importer 'properties' are not supported by import. Omit 'properties' to reimport the asset.");

                AssetPathUtility.GetFullAssetPath(fullPath);
                var consentError = RequireScriptConsent(fullPath);
                if (consentError != null)
                    return consentError;
                using (MCPForUnity.Editor.Services.Transport.CommandPhaseTiming.MeasureImport())
                {
                    AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);
                }
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
            string assetType = @params["assetType"]?.ToString() ?? @params["asset_type"]?.ToString(); // tolerate snake_case payloads from batched commands
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

#if !MCP_FOR_UNITY_PHYSICS
            if (lowerAssetType == "physicsmaterial")
                return new ErrorResponse("physics_module_unavailable: PhysicsMaterial creation requires com.unity.modules.physics.");
#endif
            Shader shader = null;
            if (lowerAssetType == "material")
            {
                var requested = properties?["shader"]?.ToString();
                shader = RenderPipelineUtility.ResolveShader(requested);
                if (shader == null)
                    return new ErrorResponse(
                        $"Could not find a project-compatible shader (requested: '{requested ?? "none"}'). Consider installing URP/HDRP or provide an explicit shader path."
                    );
            }

            UnityEngine.Object newAsset = null;
            try
            {
                if (lowerAssetType == "material")
                    MaterialOps.ValidateReferences(properties, UnityJsonSerializer.Instance);
#if MCP_FOR_UNITY_PHYSICS
                else
                    ValidatePhysicsMaterialProperties(properties);
#endif
                using var folders = new AssetFolderScope();
                folders.EnsureParentDirectory(fullPath);

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
#if MCP_FOR_UNITY_PHYSICS
                else if (lowerAssetType == "physicsmaterial")
                {
                    PhysicsMaterialType pmat = new PhysicsMaterialType();
                    newAsset = pmat;
                    if (properties != null)
                        ApplyPhysicsMaterialProperties(pmat, properties);
                    AssetPathUtility.GetFullAssetPath(fullPath);
                    AssetDatabase.CreateAsset(pmat, fullPath);
                }
#endif

                if (
                    newAsset == null
                    || !EditorUtility.IsPersistent(newAsset)
                    || !string.Equals(
                        AssetPathUtility.NormalizeSeparators(AssetDatabase.GetAssetPath(newAsset)),
                        AssetPathUtility.NormalizeSeparators(fullPath),
                        StringComparison.OrdinalIgnoreCase
                    )
                )
                {
                    return new ErrorResponse($"Failed to create asset '{assetType}' at '{fullPath}'. See logs for details.");
                }

                AssetDatabase.SaveAssetIfDirty(newAsset);
                // AssetDatabase.Refresh(); // CreateAsset often handles refresh
                var response = SavedAssetResponse(fullPath, $"Asset '{fullPath}' created successfully.");
                folders.Complete();
                return response;
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

        private static SuccessResponse SavedAssetResponse(string path, string message)
        {
            JObject data;
            try
            {
                data = JObject.FromObject(GetAssetData(path));
            }
            catch (Exception)
            {
                data = new JObject
                {
                    ["path"] = path,
                    ["saved"] = true,
                    ["metadata_status"] = "unavailable",
                };
            }
            data["verification"] = AssetReferenceDiagnostics.ObserveTarget(path);
            return new SuccessResponse(message, data);
        }

        private static object CreateFolder(string path, bool includeData = true)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for create_folder.");
            string fullPath = AssetPathUtility.GetContainedAssetPath(path);

            if (AssetExists(fullPath))
            {
                // Check if it's actually a folder already
                if (AssetDatabase.IsValidFolder(fullPath))
                {
                    return new SuccessResponse($"Folder already exists at path: {fullPath}", includeData ? GetAssetData(fullPath) : null);
                }
                else
                {
                    return new ErrorResponse($"An asset (not a folder) already exists at path: {fullPath}");
                }
            }

            try
            {
                using var folders = new AssetFolderScope();
                folders.EnsureFolder(fullPath);
                var response = new SuccessResponse($"Folder '{fullPath}' created successfully.", includeData ? GetAssetData(fullPath) : null);
                folders.Complete();
                return response;
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
                UnityEngine.Object asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(fullPath);
                if (asset == null)
                    return new ErrorResponse($"Failed to load asset at path: {fullPath}");

                AssetPathUtility.GetFullAssetPath(fullPath);
                bool modified = false; // Flag to track if any changes were made

                if (asset is GameObject gameObject)
                {
                    if (ManagePrefabs.IsDirtyStageForAsset(fullPath))
                        return new ErrorResponse(
                            "The same prefab asset is open in a dirty Prefab Stage. Save or discard its pending edits before modifying the asset."
                        );
                    var assignments = new List<(string name, Func<object> get, Action<object> set, object previous, object next)>();
                    foreach (var entry in properties.Properties())
                    {
                        if (!(entry.Value is JObject componentProperties) || !componentProperties.HasValues)
                            throw new ArgumentException($"Component '{entry.Name}' must contain a non-empty properties object.");
                        if (!ComponentResolver.TryResolve(entry.Name, out var componentType, out var error))
                            throw new ArgumentException($"Could not resolve component '{entry.Name}': {error}");
                        var component = gameObject.GetComponent(componentType);
                        if (component == null)
                            throw new ArgumentException($"Component '{entry.Name}' not found on '{gameObject.name}'.");
                        PrepareObjectProperties(component, componentProperties, assignments);
                    }
                    modified = ApplyPreparedAssignments(assignments);
                }
                else if (asset is Material material)
                {
                    try
                    {
                        modified = MaterialOps.ApplyModification(material, properties, UnityJsonSerializer.Instance);
                    }
                    catch (ArgumentException e)
                    {
                        return new ErrorResponse($"Failed to modify asset '{fullPath}': {e.Message}");
                    }
                }
                // Example: Modifying a ScriptableObject (Use manage_scriptable_object instead!)
                else if (asset is ScriptableObject so)
                {
                    // Deprecated: Prefer manage_scriptable_object for robust patching.
                    // Kept for simple property setting fallback on existing assets if manage_scriptable_object isn't used.
                    modified |= ApplyObjectPropertiesAtomically(so, properties);
                }
                // Example: Modifying TextureImporter settings
                else if (asset is Texture)
                {
                    AssetImporter importer = AssetImporter.GetAtPath(fullPath);
                    if (importer is TextureImporter textureImporter)
                    {
                        bool importerModified = ApplyObjectPropertiesAtomically(textureImporter, properties);
                        if (importerModified)
                        {
                            // Importer settings need saving and reimporting
                            AssetPathUtility.GetFullAssetPath(fullPath);
                            AssetDatabase.WriteImportSettingsIfDirty(fullPath);
                            AssetPathUtility.GetFullAssetPath(fullPath);
                            using (MCPForUnity.Editor.Services.Transport.CommandPhaseTiming.MeasureImport())
                            {
                                AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);
                            } // Reimport to apply changes
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
                    modified |= ApplyObjectPropertiesAtomically(asset, properties);
                }
                // --- End Existing Logic ---

                // Check if any modification happened (either component or direct asset modification)
                if (modified)
                {
                    AssetPathUtility.GetFullAssetPath(fullPath);
                    // Mark the asset as dirty (important for prefabs/SOs) so Unity knows to save it.
                    EditorUtility.SetDirty(asset);
                    // Save only the requested asset file, preserving unrelated dirty assets.
                    AssetDatabase.SaveAssetIfDirty(asset);
                    return SavedAssetResponse(fullPath, $"Asset '{fullPath}' modified successfully.");
                }
                else
                {
                    if (asset is Material)
                        return new SuccessResponse($"Material '{fullPath}' already has the requested values; no changes were made.", GetAssetData(fullPath));

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
                    return new SuccessResponse(
                        $"Asset '{fullPath}' deleted successfully.",
                        new { verification = AssetReferenceDiagnostics.ObserveTarget(fullPath, false) }
                    );
                }
                else
                {
                    // This might happen if the file couldn't be deleted (e.g., locked)
                    return new ErrorResponse($"Failed to delete asset '{fullPath}'. Check logs or if the file is locked.");
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
                destPath = AssetPathUtility.GetContainedAssetPath(AssetDatabase.GenerateUniqueAssetPath(sourcePath));
            }
            else
            {
                destPath = AssetPathUtility.GetContainedAssetPath(destinationPath);
                if (AssetExists(destPath))
                    return new ErrorResponse($"Asset already exists at destination path: {destPath}");
            }

            try
            {
                AssetPathUtility.GetFullAssetPath(sourcePath);
                AssetPathUtility.GetFullAssetPath(destPath);
                var consentError = RequireScriptConsent(sourcePath, destPath);
                if (consentError != null)
                    return consentError;
                using var folders = new AssetFolderScope();
                folders.EnsureParentDirectory(destPath);
                AssetPathUtility.GetFullAssetPath(sourcePath);
                AssetPathUtility.GetFullAssetPath(destPath);
                bool success = AssetDatabase.CopyAsset(sourcePath, destPath);
                if (success)
                {
                    // AssetDatabase.Refresh();
                    var response = SavedAssetResponse(destPath, $"Asset '{sourcePath}' duplicated to '{destPath}'.");
                    folders.Complete();
                    return response;
                }
                else
                {
                    return new ErrorResponse($"Failed to duplicate asset from '{sourcePath}' to '{destPath}'.");
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
                return new ErrorResponse($"An asset already exists at the destination path: {destPath}");

            try
            {
                var consentError = RequireScriptConsent(sourcePath, destPath);
                if (consentError != null)
                    return consentError;
                using var folders = new AssetFolderScope();
                folders.EnsureParentDirectory(destPath);
                // Validate will return an error string if failed, null if successful
                AssetPathUtility.GetFullAssetPath(sourcePath);
                AssetPathUtility.GetFullAssetPath(destPath);
                string error = AssetDatabase.ValidateMoveAsset(sourcePath, destPath);
                if (!string.IsNullOrEmpty(error))
                {
                    return new ErrorResponse($"Failed to move/rename asset from '{sourcePath}' to '{destPath}': {error}");
                }

                AssetPathUtility.GetFullAssetPath(sourcePath);
                AssetPathUtility.GetFullAssetPath(destPath);
                string moveError = AssetDatabase.MoveAsset(sourcePath, destPath);
                if (string.IsNullOrEmpty(moveError))
                {
                    // AssetDatabase.Refresh(); // MoveAsset usually handles refresh
                    var response = SavedAssetResponse(destPath, $"Asset moved/renamed from '{sourcePath}' to '{destPath}'.");
                    folders.Complete();
                    return response;
                }
                else
                {
                    return new ErrorResponse($"Failed to move/rename asset from '{sourcePath}' to '{destPath}': {moveError}");
                }
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error moving/renaming asset '{sourcePath}': {e.Message}");
            }
        }

        private static object SearchAssets(JObject @params, bool metadataOnly = false)
        {
            string searchPattern = @params["searchPattern"]?.ToString();
            string filterType = @params["filterType"]?.ToString();
            string pathScope = @params["path"]?.ToString(); // Use path as folder scope
            string filterDateAfterStr = @params["filterDateAfter"]?.ToString();
            bool generatePreview = @params["generatePreview"]?.ReadScalar<bool?>() ?? false;
            int maximum = generatePreview ? PaginationBounds.MaxPreviewPageSize : PaginationBounds.MaxPageSize;
            if (
                !PaginationBounds.TryRead(
                    @params["page_size"] ?? @params["pageSize"],
                    Math.Min(50, maximum),
                    1,
                    maximum,
                    "pageSize",
                    out int pageSize,
                    out string pageError
                )
                || !PaginationBounds.TryRead(
                    @params["page_number"] ?? @params["pageNumber"],
                    1,
                    1,
                    int.MaxValue,
                    "pageNumber",
                    out int pageNumber,
                    out pageError
                )
            )
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
                folderScope = new string[] { AssetPathUtility.GetAssetReferencePath(pathScope, allowPackages: true) };
                // A continuation reads the captured scope; changed/deleted folders are handled by snapshot invalidation.
                if ((!metadataOnly || @params["searchSnapshot"] == null) && !AssetDatabase.IsValidFolder(folderScope[0]))
                {
                    return new ErrorResponse($"Search path '{pathScope}' is not a valid folder.");
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
                    return new ErrorResponse($"Could not parse filterDateAfter: '{filterDateAfterStr}'. Expected ISO 8601 format.");
                }
            }

            try
            {
                if (metadataOnly)
                    return AssetSearchSnapshots.Search(
                        string.Join(" ", searchFilters),
                        folderScope,
                        filterDateAfter,
                        pageSize,
                        pageNumber,
                        @params["searchSnapshot"]?.Value<string>()
                    );
                AssetSearchSnapshots.Observe("find_assets");
                string[] guids = AssetDatabase.FindAssets(string.Join(" ", searchFilters), folderScope);
                List<object> results = new List<object>();
                int totalFound = 0;

                foreach (string guid in guids)
                {
                    string assetPath;
                    try
                    {
                        assetPath = AssetPathUtility.GetAssetPathFromGuid(guid, allowPackages: true);
                    }
                    catch (ArgumentException)
                    {
                        continue;
                    }
                    catch (InvalidOperationException)
                    {
                        continue;
                    }
                    if (string.IsNullOrEmpty(assetPath))
                        continue;

                    // Apply date filter if present
                    if (filterDateAfter.HasValue)
                    {
                        DateTime? lastWriteTime = AssetPathUtility.GetLastWriteTimeUtc(assetPath);
                        if (!lastWriteTime.HasValue || lastWriteTime.Value <= filterDateAfter.Value)
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
            string fullPath = AssetPathUtility.GetAssetReferencePath(path, allowPackages: true);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Asset not found at path: {fullPath}");

            try
            {
                return new SuccessResponse("Asset info retrieved.", GetAssetData(fullPath, generatePreview));
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
                UnityEngine.Object asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(fullPath);
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
                int missingScriptCount = 0;
                var componentList = new List<object>(components.Length);
                for (int slotIndex = 0; slotIndex < components.Length; slotIndex++)
                {
                    Component component = components[slotIndex];
                    bool missingScript = component == null;
                    if (missingScript)
                        missingScriptCount++;
                    componentList.Add(
                        new
                        {
                            typeName = missingScript ? null : component.GetType().FullName,
                            instanceID = missingScript ? (int?)null : component.GetInstanceIDCompat(),
                            slotIndex,
                            missingScript,
                        }
                    );
                }

                // 7. Return success response
                return new SuccessResponse(
                    $"Found {components.Length - missingScriptCount} component(s) and {missingScriptCount} missing script slot(s) on asset '{fullPath}'.",
                    componentList
                );
            }
            catch (Exception e)
            {
                McpLog.Error($"[ManageAsset.GetComponentsFromAsset] Error getting components for '{fullPath}': {e}");
                return new ErrorResponse($"Error getting components for asset '{fullPath}': {e.Message}");
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
        ///  Applies properties from JObject to a PhysicsMaterial.
        /// </summary>
#if MCP_FOR_UNITY_PHYSICS
        private static bool ApplyPhysicsMaterialProperties(PhysicsMaterialType pmat, JObject properties)
        {
            if (pmat == null || properties == null)
                return false;
            ValidatePhysicsMaterialProperties(properties);
            bool modified = false;

            // Example: Set dynamic friction
            if (properties["dynamicFriction"]?.Type == JTokenType.Float || properties["dynamicFriction"]?.Type == JTokenType.Integer)
            {
                float dynamicFriction = properties["dynamicFriction"].ReadScalar<float>();
                pmat.dynamicFriction = dynamicFriction;
                modified = true;
            }

            // Example: Set static friction
            if (properties["staticFriction"]?.Type == JTokenType.Float || properties["staticFriction"]?.Type == JTokenType.Integer)
            {
                float staticFriction = properties["staticFriction"].ReadScalar<float>();
                pmat.staticFriction = staticFriction;
                modified = true;
            }

            // Example: Set bounciness
            if (properties["bounciness"]?.Type == JTokenType.Float || properties["bounciness"]?.Type == JTokenType.Integer)
            {
                float bounciness = properties["bounciness"].ReadScalar<float>();
                pmat.bounciness = bounciness;
                modified = true;
            }

            var frictionCombine = ReadPhysicsMaterialCombine(properties, "frictionCombine");
            if (frictionCombine.HasValue)
            {
                pmat.frictionCombine = frictionCombine.Value;
                modified = true;
            }

            var bounceCombine = ReadPhysicsMaterialCombine(properties, "bounceCombine");
            if (bounceCombine.HasValue)
            {
                pmat.bounceCombine = bounceCombine.Value;
                modified = true;
            }

            return modified;
        }

        private static void ValidatePhysicsMaterialProperties(JObject properties)
        {
            if (properties == null)
                return;
            foreach (string field in new[] { "dynamicFriction", "staticFriction", "bounciness" })
            {
                if (!ParamCoercion.ValidateNumericField(properties, field, out string error))
                    throw new ArgumentException($"Physics material property '{field}' {error}.", field);
            }
            _ = ReadPhysicsMaterialCombine(properties, "frictionCombine");
            _ = ReadPhysicsMaterialCombine(properties, "bounceCombine");
        }

        private static PhysicsMaterialCombine? ReadPhysicsMaterialCombine(JObject properties, string field)
        {
            var value = properties[field];
            if (value == null || value.Type == JTokenType.Null)
                return null;
            if (value.Type != JTokenType.String)
                throw new ArgumentException($"Physics material property '{field}' must be a combine mode string.", field);
            return value.Value<string>() switch
            {
                "ave" or "Ave" or "average" or "Average" => PhysicsMaterialCombine.Average,
                "mul" or "Mul" or "mult" or "Mult" or "multiply" or "Multiply" => PhysicsMaterialCombine.Multiply,
                "min" or "Min" or "minimum" or "Minimum" => PhysicsMaterialCombine.Minimum,
                "max" or "Max" or "maximum" or "Maximum" => PhysicsMaterialCombine.Maximum,
                _ => throw new ArgumentException($"Unknown physics material combine mode for '{field}': '{value.Value<string>()}'.", field),
            };
        }
#endif

        // Validate and capture every member before making any direct asset assignment.
        private static bool ApplyObjectPropertiesAtomically(UnityEngine.Object target, JObject properties)
        {
            var assignments = new List<(string name, Func<object> get, Action<object> set, object previous, object next)>();
            PrepareObjectProperties(target, properties, assignments);
            return ApplyPreparedAssignments(assignments);
        }

        private static void PrepareObjectProperties(
            UnityEngine.Object target,
            JObject properties,
            List<(string name, Func<object> get, Action<object> set, object previous, object next)> assignments
        )
        {
            var flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.IgnoreCase;
            Type type = target.GetType();
            foreach (var entry in properties.Properties())
            {
                var property = type.GetProperty(entry.Name, flags);
                var field = property == null ? type.GetField(entry.Name, flags) : null;
                Type memberType;
                object previous;
                Func<object> get;
                Action<object> set;
                if (property != null && property.CanRead && property.CanWrite && property.GetIndexParameters().Length == 0)
                {
                    memberType = property.PropertyType;
                    get = () => property.GetValue(target);
                    set = value => property.SetValue(target, value);
                }
                else if (field != null && !field.IsInitOnly && !field.IsLiteral)
                {
                    memberType = field.FieldType;
                    get = () => field.GetValue(target);
                    set = value => field.SetValue(target, value);
                }
                else
                    throw new ArgumentException($"Member '{entry.Name}' is not readable and writable on {type.Name}.");

                previous = get();
                if (!TryConvertMemberValue(entry.Value, memberType, out object next))
                    throw new ArgumentException($"Could not convert value for member '{entry.Name}' on {type.Name}.");
                assignments.Add((entry.Name, get, set, previous, next));
            }
        }

        private static bool ApplyPreparedAssignments(List<(string name, Func<object> get, Action<object> set, object previous, object next)> assignments)
        {
            var attempted = new List<int>();
            for (int i = 0; i < assignments.Count; i++)
            {
                try
                {
                    // Earlier setters may have changed this member, including through an alias.
                    if (object.Equals(assignments[i].get(), assignments[i].next))
                        continue;
                    attempted.Add(i);
                    assignments[i].set(assignments[i].next);
                }
                catch (Exception ex)
                {
                    var restorationErrors = new List<string>();
                    // A setter can mutate its backing state before throwing.
                    for (int j = attempted.Count - 1; j >= 0; j--)
                    {
                        int index = attempted[j];
                        try
                        {
                            assignments[index].set(assignments[index].previous);
                        }
                        catch (Exception restore)
                        {
                            restorationErrors.Add($"{assignments[index].name}: {restore.GetBaseException().Message}");
                        }
                    }
                    // Setters can change other requested members. Repair observed differences
                    // once in request order, then verify all captured values without retrying.
                    foreach (var assignment in assignments)
                    {
                        try
                        {
                            if (!object.Equals(assignment.get(), assignment.previous))
                                assignment.set(assignment.previous);
                        }
                        catch (Exception restore)
                        {
                            restorationErrors.Add($"{assignment.name}: {restore.GetBaseException().Message}");
                        }
                    }
                    foreach (var assignment in assignments)
                    {
                        try
                        {
                            if (!object.Equals(assignment.get(), assignment.previous))
                                restorationErrors.Add($"{assignment.name}: value differs from its original value");
                        }
                        catch (Exception inspect)
                        {
                            restorationErrors.Add($"{assignment.name}: could not verify restoration: {inspect.GetBaseException().Message}");
                        }
                    }
                    string restoration =
                        restorationErrors.Count == 0
                            ? "All attempted assignments were restored."
                            : $"Restoration incomplete: {string.Join(", ", restorationErrors)}.";
                    throw new InvalidOperationException($"Failed to assign '{assignments[i].name}': {ex.GetBaseException().Message}. {restoration}", ex);
                }
            }
            return attempted.Count != 0;
        }

        private static bool TryConvertMemberValue(JToken value, Type type, out object convertedValue)
        {
            // Explicit null is an assignment for reference/nullable members. A failed
            // conversion of non-null input also returns null, but must preserve the member.
            if (value?.Type == JTokenType.Null)
            {
                convertedValue = null;
                return !type.IsValueType || Nullable.GetUnderlyingType(type) != null;
            }
            convertedValue = Helpers.PropertyConversion.TryConvertToType(value, type);
            return convertedValue != null;
        }

        // --- Data Serialization ---

        /// <summary>
        /// Creates a serializable representation of an asset.
        /// </summary>
        private static object GetAssetData(string path, bool generatePreview = false, PaginationBounds.PreviewBudget previewBudget = null)
        {
            path = AssetPathUtility.GetAssetReferencePath(path, allowPackages: true);
            if (string.IsNullOrEmpty(path) || !AssetExists(path))
                return null;

            string guid = AssetDatabase.AssetPathToGUID(path);
            Type assetType = AssetDatabase.GetMainAssetTypeAtPath(path);
            AssetSearchSnapshots.Observe("load_asset");
            UnityEngine.Object asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
            string previewBase64 = null;
            int previewWidth = 0;
            int previewHeight = 0;

            previewBudget ??= new PaginationBounds.PreviewBudget();
            if (generatePreview && asset != null && previewBudget.CanGenerate)
            {
                AssetSearchSnapshots.Observe("asset_preview");
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
                            double scale = Math.Min(1.0, (double)PaginationBounds.MaxPreviewEdge / Math.Max(preview.width, preview.height));
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
                            if (rt != null)
                                RenderTexture.ReleaseTemporary(rt);
                            if (readablePreview != null)
                                UnityEngine.Object.DestroyImmediate(readablePreview);
                        }
                    }
                    catch (Exception ex)
                    {
                        McpLog.Warn($"Failed to generate readable preview for '{path}': {ex.Message}. Preview might not be readable.");
                        // Fallback: Try getting static preview if available?
                        // Texture2D staticPreview = AssetPreview.GetMiniThumbnail(asset);
                    }
                }
                else
                {
                    McpLog.Warn($"Could not get asset preview for {path} (Type: {assetType?.Name}). Is it supported?");
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
                lastWriteTimeUtc = AssetPathUtility.GetLastWriteTimeUtc(path)?.ToString("o", CultureInfo.InvariantCulture),
                // --- Preview Data ---
                previewBase64 = previewBase64, // PNG data as Base64 string
                previewWidth = previewWidth,
                previewHeight = previewHeight,
                // TODO: Add more metadata? Importer settings? Dependencies?
            };
        }
    }
}
