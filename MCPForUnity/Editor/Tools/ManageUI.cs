using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Xml;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("manage_ui", AutoRegister = false, Group = "ui")]
    public static class ManageUI
    {
        private static readonly HashSet<string> ValidExtensions = new(StringComparer.OrdinalIgnoreCase) { ".uxml", ".uss" };

        // UTF-8 without BOM — UI Builder in Unity 6 can fail to open UXML files with a BOM.
        private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);

        static ManageUI()
        {
            EditorApplication.quitting += CleanupRenderTextures;
            AssemblyReloadEvents.beforeAssemblyReload += CleanupRenderTextures;
            EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
        }

        private static void CleanupRenderTextures()
        {
            CleanupPendingCapture();
            foreach (var kvp in s_panelRTs)
            {
                if (kvp.Value == null)
                    continue;
                if (s_panelBindings.TryGetValue(kvp.Key, out var binding) && binding.panel != null && binding.panel.targetTexture == kvp.Value)
                    binding.panel.targetTexture = binding.previousTarget;
                string assetPath = AssetDatabase.GetAssetPath(kvp.Value);
                kvp.Value.Release();
                if (!string.IsNullOrEmpty(assetPath))
                    AssetDatabase.DeleteAsset(assetPath);
                else
                    UnityEngine.Object.DestroyImmediate(kvp.Value);
            }
            s_panelRTs.Clear();
            s_panelBindings.Clear();
        }

        private static void OnPlayModeStateChanged(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.EnteredEditMode || state == PlayModeStateChange.ExitingEditMode)
                CleanupPendingCapture();
        }

        public static object HandleCommand(JObject @params)
        {
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            if (string.IsNullOrEmpty(action))
            {
                return new ErrorResponse("Action is required");
            }

            try
            {
                if ((@params["warmup_updates"] != null || @params["warmupUpdates"] != null) && action != "render_ui")
                    return new ErrorResponse("warmup_updates requires render_ui.");
                JToken strictStyleToken = @params["strict_style"];
                if (strictStyleToken != null && strictStyleToken.Type != JTokenType.Boolean)
                    return new ErrorResponse("strict_style must be a Boolean.");
                if (strictStyleToken?.Value<bool>() == true && action != "modify_visual_element")
                    return new ErrorResponse("strict_style requires modify_visual_element.");
                switch (action)
                {
                    case "ping":
                        return new SuccessResponse("pong", new { tool = "manage_ui" });

                    case "create":
                        return CreateFile(@params);

                    case "read":
                        return ReadFile(@params);

                    case "update":
                        return UpdateFile(@params);

                    case "attach_ui_document":
                        return AttachUIDocument(@params);

                    case "create_panel_settings":
                        return CreatePanelSettings(@params);

                    case "update_panel_settings":
                        return UpdatePanelSettings(@params);

                    case "diagnose":
                        return UitoolkitLayoutDiagnostics.Diagnose(@params);

                    case "get_visual_tree":
                        return GetVisualTree(@params);

                    case "render_ui":
                        return RenderUI(@params);

                    case "link_stylesheet":
                        return LinkStylesheet(@params);

                    case "delete":
                        return DeleteFile(@params);

                    case "list":
                        return ListUIAssets(@params);

                    case "detach_ui_document":
                        return DetachUIDocument(@params);

                    case "modify_visual_element":
                        return ModifyVisualElement(@params);

                    default:
                        return new ErrorResponse($"Unknown action: {action}");
                }
            }
            catch (Exception ex)
            {
                return new ErrorResponse(ex.Message, new { stackTrace = ex.StackTrace });
            }
        }

        private static string ValidatePath(string path, out string error)
        {
            error = null;
            if (string.IsNullOrEmpty(path))
            {
                error = "'path' parameter is required.";
                return null;
            }

            path = AssetPathUtility.GetContainedAssetPath(path);
            if (path == null)
            {
                error = "Invalid path: contains traversal sequences.";
                return null;
            }

            string ext = Path.GetExtension(path);
            if (!ValidExtensions.Contains(ext))
            {
                error = $"Invalid file extension '{ext}'. Must be .uxml or .uss.";
                return null;
            }

            return path;
        }

        private static object CreateFile(JObject @params)
        {
            var p = new ToolParams(@params);
            string path = ValidatePath(p.Get("path"), out string pathError);
            if (pathError != null)
                return new ErrorResponse(pathError);

            string contents;
            try
            {
                contents = GetDecodedContents(p);
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse(ex.Message);
            }

            if (contents == null)
            {
                return new ErrorResponse("'contents' parameter is required for create.");
            }

            string fullPath = AssetPathUtility.GetFullAssetPath(path);

            if (File.Exists(fullPath))
            {
                return new ErrorResponse($"File already exists at {path}. Use 'update' action to overwrite.");
            }

            bool isUxml = path.EndsWith(".uxml", StringComparison.OrdinalIgnoreCase);
            var validationWarnings = new List<string>();

            if (isUxml)
            {
                string xmlError = ReadUxmlContent(contents, validationWarnings, out var document);
                if (xmlError != null)
                {
                    return new ErrorResponse($"UXML validation failed — file was NOT written. {xmlError}");
                }
                contents = AddEditorExtensionMode(contents, document);
            }

            using var folders = new AssetFolderScope();
            folders.EnsureParentDirectory(path);
            fullPath = AssetPathUtility.GetFullAssetPath(path);
            File.WriteAllText(fullPath, contents, Utf8NoBom);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);

            if (isUxml)
            {
                ValidateUxmlPostImport(path, validationWarnings);
            }

            string ext = Path.GetExtension(path).TrimStart('.');
            if (validationWarnings.Count > 0)
            {
                var response = new SuccessResponse(
                    $"Created {ext} file at {path} with {validationWarnings.Count} warning(s)",
                    new { path, validationWarnings }
                );
                folders.Complete();
                return response;
            }

            var created = new SuccessResponse($"Created {ext} file at {path}", new { path });
            folders.Complete();
            return created;
        }

        private static object ReadFile(JObject @params)
        {
            var p = new ToolParams(@params);
            string path = ValidatePath(p.Get("path"), out string pathError);
            if (pathError != null)
                return new ErrorResponse(pathError);

            string fullPath = AssetPathUtility.GetFullAssetPath(path);

            if (!File.Exists(fullPath))
            {
                return new ErrorResponse($"File not found: {path}");
            }

            string contents = File.ReadAllText(fullPath, Encoding.UTF8);
            byte[] utf8Bytes = Encoding.UTF8.GetBytes(contents);
            string encoded = Convert.ToBase64String(utf8Bytes);

            return new SuccessResponse(
                $"Read {Path.GetExtension(path).TrimStart('.')} file at {path}",
                new
                {
                    path,
                    contents,
                    encodedContents = encoded,
                    contentsEncoded = true,
                    lengthBytes = utf8Bytes.Length,
                }
            );
        }

        private static object UpdateFile(JObject @params)
        {
            var p = new ToolParams(@params);
            string path = ValidatePath(p.Get("path"), out string pathError);
            if (pathError != null)
                return new ErrorResponse(pathError);

            string contents;
            try
            {
                contents = GetDecodedContents(p);
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse(ex.Message);
            }

            if (contents == null)
            {
                return new ErrorResponse("'contents' parameter is required for update.");
            }

            string fullPath = AssetPathUtility.GetFullAssetPath(path);

            if (!File.Exists(fullPath))
            {
                return new ErrorResponse($"File not found: {path}. Use 'create' action for new files.");
            }

            bool isUxml = path.EndsWith(".uxml", StringComparison.OrdinalIgnoreCase);
            var validationWarnings = new List<string>();

            if (isUxml)
            {
                string xmlError = ReadUxmlContent(contents, validationWarnings, out var document);
                if (xmlError != null)
                {
                    return new ErrorResponse($"UXML validation failed — file was NOT updated. {xmlError}");
                }
                contents = AddEditorExtensionMode(contents, document);
            }

            File.WriteAllText(fullPath, contents, Utf8NoBom);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);

            if (isUxml)
            {
                ValidateUxmlPostImport(path, validationWarnings);
            }

            string ext = Path.GetExtension(path).TrimStart('.');
            if (validationWarnings.Count > 0)
            {
                return new SuccessResponse($"Updated {ext} file at {path} with {validationWarnings.Count} warning(s)", new { path, validationWarnings });
            }

            return new SuccessResponse($"Updated {ext} file at {path}", new { path });
        }

        private static object AttachUIDocument(JObject @params)
        {
            var p = new ToolParams(@params);

            var targetResult = p.GetRequired("target");
            var targetError = targetResult.GetOrError(out string target);
            if (targetError != null)
                return targetError;

            var sourceResult = p.GetRequired("source_asset");
            var sourceError = sourceResult.GetOrError(out string sourceAssetPath);
            if (sourceError != null)
                return sourceError;

            sourceAssetPath = AssetPathUtility.GetContainedAssetPath(sourceAssetPath);
            if (sourceAssetPath == null)
            {
                return new ErrorResponse("Invalid source_asset path.");
            }

            // Find the GameObject
            var goInstruction = new JObject { ["find"] = target };
            GameObject go = ObjectResolver.Resolve(goInstruction, typeof(GameObject)) as GameObject;
            if (go == null)
            {
                return new ErrorResponse($"Could not find target GameObject: {target}");
            }

            Type componentType = UIComponentAdapter.ResolveType(go, p.Get("component_type"), true);

            // Load the VisualTreeAsset
            var vta = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(sourceAssetPath);
            if (vta == null)
            {
                return new ErrorResponse($"Could not load VisualTreeAsset at: {sourceAssetPath}");
            }

            // Load or create PanelSettings
            int sortOrder = p.GetInt("sort_order") ?? 0;
            using var folders = new AssetFolderScope();
            string panelSettingsPath = p.Get("panel_settings") ?? p.Get("panelSettings");
            PanelSettings panelSettings = null;

            if (!string.IsNullOrEmpty(panelSettingsPath))
            {
                panelSettingsPath = AssetPathUtility.GetContainedAssetPath(panelSettingsPath);
                if (panelSettingsPath != null)
                {
                    panelSettings = AssetDatabase.LoadAssetAtPath<PanelSettings>(panelSettingsPath);
                }
                if (panelSettings == null)
                {
                    return new ErrorResponse($"Could not load PanelSettings at: {panelSettingsPath}");
                }
            }
            else
            {
                // Find existing or create default PanelSettings
                string[] guids = AssetDatabase.FindAssets("t:PanelSettings");
                if (guids.Length > 0)
                {
                    string existingPath = AssetPathUtility.GetAssetPathFromGuid(guids[0], allowPackages: true);
                    panelSettings = AssetDatabase.LoadAssetAtPath<PanelSettings>(existingPath);
                }

                if (panelSettings == null)
                {
                    panelSettings = CreateDefaultPanelSettings("Assets/UI/DefaultPanelSettings.asset", folders);
                    if (panelSettings == null)
                    {
                        return new ErrorResponse("Failed to create default PanelSettings.");
                    }
                }
            }

            var ui = UIComponentAdapter.Attach(go, componentType);
            Undo.RecordObject(ui.Component, "Configure UI component");
            ui.Configure(vta, panelSettings, sortOrder);

            EditorUtility.SetDirty(go);

            var response = new SuccessResponse(
                $"Attached {ui.DisplayName} to {go.name}",
                new
                {
                    gameObject = go.name,
                    componentType = ui.Kind,
                    sourceAsset = sourceAssetPath,
                    panelSettings = AssetDatabase.GetAssetPath(panelSettings),
                    sortOrder,
                }
            );
            folders.Complete();
            return response;
        }

        private static object CreatePanelSettings(JObject @params)
        {
            var p = new ToolParams(@params);

            var pathResult = p.GetRequired("path");
            var pathError = pathResult.GetOrError(out string path);
            if (pathError != null)
                return pathError;

            path = AssetPathUtility.GetContainedAssetPath(path);
            if (path == null)
            {
                return new ErrorResponse("Invalid path: contains traversal sequences.");
            }

            if (!path.EndsWith(".asset", StringComparison.OrdinalIgnoreCase))
            {
                path += ".asset";
            }

            if (File.Exists(AssetPathUtility.GetFullAssetPath(path)) || AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path) != null)
            {
                return new ErrorResponse($"Asset already exists at {path}");
            }

            var changes = new List<string>();
            Action<PanelSettings> configure = null;
            JToken settingsToken = p.GetRaw("settings");
            if (settingsToken != null && settingsToken.Type != JTokenType.Null && settingsToken is not JObject)
                return new ErrorResponse("'settings' must be a JSON object.");
            if (settingsToken is JObject settingsObj)
            {
                configure = PreparePanelSettingsProperties(settingsObj, changes);
            }
            else
            {
                // Legacy: support top-level scale_mode / reference_resolution.
                string scaleMode = p.Get("scale_mode");
                if (!string.IsNullOrEmpty(scaleMode) && Enum.TryParse<PanelScaleMode>(scaleMode, true, out var mode))
                {
                    configure += panel => panel.scaleMode = mode;
                    changes.Add("scaleMode");
                }
                if (p.GetRaw("reference_resolution") is JObject refRes)
                {
                    int w = refRes["width"]?.ReadScalar<int?>() ?? 1920;
                    int h = refRes["height"]?.ReadScalar<int?>() ?? 1080;
                    configure += panel => panel.referenceResolution = new Vector2Int(w, h);
                    changes.Add("referenceResolution");
                }
            }
            using var folders = new AssetFolderScope();
            var ps = CreateDefaultPanelSettings(path, folders, configure);
            if (ps == null)
            {
                return new ErrorResponse("Failed to create PanelSettings asset.");
            }

            var response = new SuccessResponse($"Created PanelSettings at {path}", new { path, applied = changes });
            folders.Complete();
            return response;
        }

        private static object UpdatePanelSettings(JObject @params)
        {
            var p = new ToolParams(@params);

            var pathResult = p.GetRequired("path");
            var pathError = pathResult.GetOrError(out string path);
            if (pathError != null)
                return pathError;

            path = AssetPathUtility.GetContainedAssetPath(path);
            if (path == null)
                return new ErrorResponse("Invalid path: contains traversal sequences.");

            if (!path.EndsWith(".asset", StringComparison.OrdinalIgnoreCase))
                path += ".asset";

            var ps = AssetDatabase.LoadAssetAtPath<PanelSettings>(path);
            if (ps == null)
                return new ErrorResponse($"No PanelSettings found at {path}");

            JToken settingsToken = p.GetRaw("settings");
            if (settingsToken is not JObject settingsObj || settingsObj.Count == 0)
                return new ErrorResponse("'settings' dict is required with at least one property to update.");

            var changes = new List<string>();
            ApplyPanelSettingsProperties(ps, settingsObj, changes);

            if (changes.Count == 0)
                return new ErrorResponse("No recognised properties were applied. Check the key names.");

            EditorUtility.SetDirty(ps);
            AssetDatabase.SaveAssets();

            return new SuccessResponse($"Updated PanelSettings at {path}", new { path, applied = changes });
        }

        private static PanelSettings CreateDefaultPanelSettings(string path, AssetFolderScope folders, Action<PanelSettings> configure = null)
        {
            // This helper returns only a newly owned asset, never a borrowed one.
            if (File.Exists(AssetPathUtility.GetFullAssetPath(path)) || AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path) != null)
                return null;

            var ps = ScriptableObject.CreateInstance<PanelSettings>();
            try
            {
                configure?.Invoke(ps);
                folders.EnsureParentDirectory(path);
                AssetPathUtility.GetFullAssetPath(path);
                AssetDatabase.CreateAsset(ps, path);
                if (
                    !AssetDatabase.Contains(ps)
                    || !string.Equals(
                        AssetPathUtility.NormalizeSeparators(AssetDatabase.GetAssetPath(ps)),
                        AssetPathUtility.NormalizeSeparators(path),
                        StringComparison.OrdinalIgnoreCase
                    )
                )
                    return null;

                AssetDatabase.SaveAssets();
                return ps;
            }
            finally
            {
                if (!AssetDatabase.Contains(ps))
                    UnityEngine.Object.DestroyImmediate(ps);
            }
        }

        /// <summary>
        /// Generic, data-driven applicator for PanelSettings properties.
        /// Accepts a flat JObject where each key maps to a PanelSettings property.
        /// Recognised keys (case-insensitive matching via snake_case/camelCase):
        ///   scaleMode, referenceResolution, screenMatchMode, match,
        ///   referenceDpi, fallbackDpi, sortingOrder, targetDisplay,
        ///   clearColor, colorClearValue, clearDepthStencil,
        ///   themeStyleSheet, dynamicAtlasSettings.
        /// </summary>
        private static void ApplyPanelSettingsProperties(PanelSettings ps, JObject settings, List<string> changes)
        {
            PreparePanelSettingsProperties(settings, changes)(ps);
        }

        private static Action<PanelSettings> PreparePanelSettingsProperties(JObject settings, List<string> changes)
        {
            Action<PanelSettings> apply = null;
            foreach (var prop in settings)
            {
                string key = NormalizeKey(prop.Key);
                JToken val = prop.Value;

                switch (key)
                {
                    // ── Enum properties ─────────────────────────────────────
                    case "scalemode":
                        if (TryParseEnum<PanelScaleMode>(val, out var sm))
                        {
                            apply += ps => ps.scaleMode = sm;
                            changes.Add("scaleMode");
                        }
                        break;

                    case "screenmatchmode":
                        if (TryParseEnum<PanelScreenMatchMode>(val, out var smm))
                        {
                            apply += ps => ps.screenMatchMode = smm;
                            changes.Add("screenMatchMode");
                        }
                        break;

                    // ── Numeric properties ──────────────────────────────────
                    case "match":
                        if (TryFloat(val, out float matchVal))
                        {
                            apply += ps => ps.match = Mathf.Clamp01(matchVal);
                            changes.Add("match");
                        }
                        break;

                    case "referencedpi":
                        if (TryFloat(val, out float refDpi))
                        {
                            apply += ps => ps.referenceDpi = refDpi;
                            changes.Add("referenceDpi");
                        }
                        break;

                    case "fallbackdpi":
                        if (TryFloat(val, out float fbDpi))
                        {
                            apply += ps => ps.fallbackDpi = fbDpi;
                            changes.Add("fallbackDpi");
                        }
                        break;

                    case "sortingorder":
                        if (TryInt(val, out int so))
                        {
                            apply += ps => ps.sortingOrder = so;
                            changes.Add("sortingOrder");
                        }
                        break;

                    case "targetdisplay":
                        if (TryInt(val, out int td))
                        {
                            apply += ps => ps.targetDisplay = td;
                            changes.Add("targetDisplay");
                        }
                        break;

                    // ── Bool properties ──────────────────────────────────────
                    case "clearcolor":
                        bool clearColor = ParamCoercion.CoerceBool(val, false);
                        apply += ps => ps.clearColor = clearColor;
                        changes.Add("clearColor");
                        break;

                    case "cleardepthstencil":
                        bool clearDepthStencil = ParamCoercion.CoerceBool(val, false);
                        apply += ps => ps.clearDepthStencil = clearDepthStencil;
                        changes.Add("clearDepthStencil");
                        break;

                    // ── Composite properties ────────────────────────────────
                    case "referenceresolution":
                        if (val is JObject resObj)
                        {
                            int? width = resObj["width"]?.ReadScalar<int?>();
                            int? height = resObj["height"]?.ReadScalar<int?>();
                            apply += ps =>
                            {
                                var resolution = ps.referenceResolution;
                                ps.referenceResolution = new Vector2Int(width ?? resolution.x, height ?? resolution.y);
                            };
                            changes.Add("referenceResolution");
                        }
                        break;

                    case "colorclearvalue":
                        if (TryParseColor(val, out Color clr))
                        {
                            apply += ps => ps.colorClearValue = clr;
                            changes.Add("colorClearValue");
                        }
                        break;

                    case "dynamicatlassettings":
                        if (val is JObject daObj)
                        {
                            int? minSize = TryInt(daObj["minAtlasSize"], out int parsedMin) ? parsedMin : null;
                            int? maxSize = TryInt(daObj["maxAtlasSize"], out int parsedMax) ? parsedMax : null;
                            int? maxSub = TryInt(daObj["maxSubTextureSize"], out int parsedSub) ? parsedSub : null;
                            DynamicAtlasFilters? filters = TryParseEnum<DynamicAtlasFilters>(daObj["activeFilters"], out var parsedFilters)
                                ? parsedFilters
                                : null;
                            apply += ps =>
                            {
                                var atlas = ps.dynamicAtlasSettings;
                                if (minSize.HasValue)
                                    atlas.minAtlasSize = minSize.Value;
                                if (maxSize.HasValue)
                                    atlas.maxAtlasSize = maxSize.Value;
                                if (maxSub.HasValue)
                                    atlas.maxSubTextureSize = maxSub.Value;
                                if (filters.HasValue)
                                    atlas.activeFilters = filters.Value;
                                ps.dynamicAtlasSettings = atlas;
                            };
                            changes.Add("dynamicAtlasSettings");
                        }
                        break;

                    // ── Asset reference properties ──────────────────────────
                    case "themestylesheet":
                    {
                        string tsPath = val?.ToString();
                        if (!string.IsNullOrEmpty(tsPath))
                        {
                            var ts = AssetDatabase.LoadAssetAtPath<ThemeStyleSheet>(AssetPathUtility.GetContainedAssetPath(tsPath));
                            if (ts != null)
                            {
                                apply += ps => ps.themeStyleSheet = ts;
                                changes.Add("themeStyleSheet");
                            }
                        }
                        break;
                    }

                    // unknown keys are silently ignored
                }
            }
            return apply ?? (_ => { });
        }

        // ── Tiny helpers to keep the switch compact ─────────────────────────

        private static string NormalizeKey(string key)
        {
            // Strip underscores and lowercase so "scale_mode", "scaleMode", "ScaleMode"
            // all match the same case label.
            return key.Replace("_", "").ToLowerInvariant();
        }

        private static bool TryParseEnum<T>(JToken token, out T result)
            where T : struct, Enum
        {
            result = default;
            string s = token?.ToString();
            return !string.IsNullOrEmpty(s) && Enum.TryParse(s, true, out result);
        }

        private static bool TryFloat(JToken token, out float result)
        {
            result = 0f;
            if (token == null || token.Type == JTokenType.Null)
                return false;
            try
            {
                result = token.ReadScalar<float>();
                return true;
            }
            catch (ArgumentException)
            {
                return false;
            }
        }

        private static bool TryInt(JToken token, out int result)
        {
            result = 0;
            if (token == null)
                return false;
            if (token.Type == JTokenType.Integer)
            {
                result = token.ReadScalar<int>();
                return true;
            }
            return int.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out result);
        }

        private static bool TryParseColor(JToken token, out Color color)
        {
            color = Color.clear;
            if (token == null)
                return false;

            // Accept "#RRGGBB", "#RRGGBBAA", or {r,g,b,a} object
            if (token.Type == JTokenType.String)
            {
                return ColorUtility.TryParseHtmlString(token.ToString(), out color);
            }

            if (token is JObject cObj)
            {
                color = new Color(
                    cObj["r"]?.ReadScalar<float?>() ?? 0f,
                    cObj["g"]?.ReadScalar<float?>() ?? 0f,
                    cObj["b"]?.ReadScalar<float?>() ?? 0f,
                    cObj["a"]?.ReadScalar<float?>() ?? 1f
                );
                return true;
            }

            return false;
        }

        private static object GetVisualTree(JObject @params)
        {
            var p = new ToolParams(@params);
            // Validate producer budgets before shared scene/component lookup.
            var budget = new VisualTreeBudget(
                ReadTreeBudget(p, "max_depth", 10, 0, 24),
                ReadTreeBudget(p, "max_nodes", 200, 1, 1000),
                ReadTreeBudget(p, "max_evaluations", 5000, 1, 20000),
                ReadTreeBudget(p, "max_characters", 65536, 1, 262144)
            );
            var targetResult = p.GetRequired("target");
            var targetError = targetResult.GetOrError(out string target);
            if (targetError != null)
                return targetError;

            if (target.Length > 4096)
                return new ErrorResponse("get_visual_tree target must be at most 4096 characters.");
            var goInstruction = new JObject { ["find"] = target };
            GameObject go = ObjectResolver.Resolve(goInstruction, typeof(GameObject)) as GameObject;
            if (go == null)
            {
                return new ErrorResponse("Could not find target GameObject for get_visual_tree.");
            }
            UIComponentAdapter ui;
            try
            {
                ui = UIComponentAdapter.Resolve(go, p.Get("component_type"));
            }
            catch (ArgumentException)
            {
                // Adapter diagnostics may contain the entire, arbitrarily long GameObject name.
                return new ErrorResponse(
                    "get_visual_tree could not resolve the requested UI component (UIDocument or supported PanelRenderer; component_type: auto, ui_document, panel_renderer)."
                );
            }
            var root = ui.Root;
            var metadataFields = new List<string>();
            string gameObject = budget.Copy(go.name, "gameObject", metadataFields);
            string sourceAsset = ui.SourceAsset != null ? budget.Copy(AssetDatabase.GetAssetPath(ui.SourceAsset), "sourceAsset", metadataFields) : null;
            var tree = root == null ? null : SerializeVisualTree(root, budget);
            return new SuccessResponse(
                root == null ? "UI component has no visual tree (not yet built)." : "Visual tree read.",
                new
                {
                    gameObject,
                    componentType = ui.Kind,
                    sourceAsset,
                    tree,
                    traversal = new
                    {
                        max_depth = budget.MaxDepth,
                        max_nodes = budget.MaxNodes,
                        max_evaluations = budget.MaxEvaluations,
                        max_characters = budget.MaxCharacters,
                        returned_nodes = budget.Nodes,
                        evaluations = budget.Evaluations,
                        characters = budget.Characters,
                        tree_status = root == null ? "not_initialized" : "ready",
                        complete = root != null && budget.Reasons.Count == 0,
                        truncated = budget.Reasons.Count != 0,
                        reasons = budget.Reasons,
                        metadata_truncated_fields = metadataFields,
                    },
                }
            );
        }

        private static int ReadTreeBudget(ToolParams p, string key, int fallback, int minimum, int maximum)
        {
            int value;
            try
            {
                value = p.GetInt(key) ?? fallback;
            }
            catch (ArgumentException)
            {
                throw new ArgumentException($"{key} must be an integer from {minimum} to {maximum}.");
            }
            if (value < minimum || value > maximum)
                throw new ArgumentException($"{key} must be an integer from {minimum} to {maximum}.");
            return value;
        }

        // Local to get_visual_tree: snapshots and mutation selectors have different domains/bounds.
        private sealed class VisualTreeBudget
        {
            internal readonly int MaxDepth,
                MaxNodes,
                MaxEvaluations,
                MaxCharacters;
            internal int Nodes,
                Evaluations,
                Characters;
            internal readonly List<string> Reasons = new();

            internal VisualTreeBudget(int depth, int nodes, int evaluations, int characters)
            {
                MaxDepth = depth;
                MaxNodes = nodes;
                MaxEvaluations = evaluations;
                MaxCharacters = characters;
            }

            internal void Reason(string reason)
            {
                if (!Reasons.Contains(reason))
                    Reasons.Add(reason);
            }

            internal bool Charge()
            {
                if (Evaluations == MaxEvaluations)
                {
                    Reason("max_evaluations");
                    return false;
                }
                Evaluations++;
                return true;
            }

            internal string Copy(string value, string field, List<string> fields, bool whole = false)
            {
                value ??= "";
                int available = MaxCharacters - Characters;
                int intended = Math.Min(value.Length, 8192);
                if (intended < value.Length && intended > 0 && char.IsHighSurrogate(value[intended - 1]) && char.IsLowSurrogate(value[intended]))
                    intended--;
                int count = Math.Min(intended, available);
                if (count < intended && count > 0 && char.IsHighSurrogate(value[count - 1]) && char.IsLowSurrogate(value[count]))
                    count--;
                if (count < value.Length)
                {
                    if (!fields.Contains(field))
                        fields.Add(field);
                    if (intended < value.Length)
                        Reason("per_string_limit");
                    // A local cap does not exhaust the shared allowance. Compare only
                    // the surrogate-safe prefix this field could otherwise emit.
                    if (count < intended)
                        Reason("max_characters");
                    if (whole)
                        return null;
                }
                Characters += count;
                return count == value.Length ? value : value.Substring(0, count);
            }
        }

        private sealed class VisualTreeFrame
        {
            internal VisualElement Element;
            internal Dictionary<string, object> Output;
            internal List<object> Children;
            internal int ChildCount,
                NextChild,
                Depth;
        }

        private static VisualTreeFrame ReadVisualTreeNode(VisualElement element, int depth, VisualTreeBudget budget)
        {
            // Caller charges admission before fixed scalar reads. Classes require their own probes.
            budget.Nodes++;
            var fields = new List<string>();
            var result = new Dictionary<string, object>
            {
                ["type"] = budget.Copy(element.GetType().Name, "type", fields),
                ["name"] = budget.Copy(element.name, "name", fields),
            };
            var classes = new List<string>();
            result["classes"] = classes;
            using (var names = element.GetClasses().GetEnumerator())
            {
                while (true)
                {
                    if (!budget.Charge())
                    {
                        if (!fields.Contains("classes"))
                            fields.Add("classes");
                        break;
                    }
                    if (!names.MoveNext())
                        break;
                    if (classes.Count == 64)
                    {
                        if (!fields.Contains("classes"))
                            fields.Add("classes");
                        budget.Reason("class_limit");
                        break;
                    }
                    classes.Add(budget.Copy(names.Current, "classes", fields));
                    if (budget.Reasons.Contains("max_characters"))
                        break;
                }
            }

            var style = new Dictionary<string, object>();
            var resolved = element.resolvedStyle;
            if (resolved.width > 0)
                style["width"] = resolved.width;
            if (resolved.height > 0)
                style["height"] = resolved.height;
            if (resolved.color != Color.clear)
            {
                string color = budget.Copy(ColorToHex(resolved.color), "resolvedStyle.color", fields, whole: true);
                if (color != null)
                    style["color"] = color;
            }
            if (resolved.backgroundColor != Color.clear)
            {
                string color = budget.Copy(ColorToHex(resolved.backgroundColor), "resolvedStyle.backgroundColor", fields, whole: true);
                if (color != null)
                    style["backgroundColor"] = color;
            }
            if (resolved.fontSize > 0)
                style["fontSize"] = resolved.fontSize;

            if (style.Count > 0)
                result["resolvedStyle"] = style;

            if (element is TextElement textEl && !string.IsNullOrEmpty(textEl.text))
                result["text"] = budget.Copy(textEl.text, "text", fields);
            if (fields.Count != 0)
                result["truncated_fields"] = fields;
            return new VisualTreeFrame
            {
                Element = element,
                Output = result,
                ChildCount = element.childCount,
                Depth = depth,
            };
        }

        private static object SerializeVisualTree(VisualElement root, VisualTreeBudget budget)
        {
            budget.Charge(); // A root stub is emitted even if envelope strings exhausted the character budget.
            var first = ReadVisualTreeNode(root, 0, budget);
            var stack = new List<VisualTreeFrame> { first };
            while (stack.Count != 0)
            {
                var frame = stack[stack.Count - 1];
                if (frame.NextChild == frame.ChildCount)
                {
                    stack.RemoveAt(stack.Count - 1);
                    continue;
                }
                string cutoff =
                    frame.Depth == budget.MaxDepth ? "max_depth"
                    : budget.Nodes == budget.MaxNodes ? "max_nodes"
                    : budget.Characters == budget.MaxCharacters || budget.Reasons.Contains("max_characters") ? "max_characters"
                    : budget.Evaluations == budget.MaxEvaluations ? "max_evaluations"
                    : null;
                if (cutoff != null)
                {
                    budget.Reason(cutoff);
                    frame.Output["childCount"] = frame.ChildCount;
                    frame.Output["truncated"] = true;
                    stack.RemoveAt(stack.Count - 1);
                    continue;
                }
                budget.Charge(); // Fetch exactly one content-container child; never precollect siblings.
                var child = frame.Element.ElementAt(frame.NextChild);
                if (!budget.Charge())
                {
                    frame.Output["childCount"] = frame.ChildCount;
                    frame.Output["truncated"] = true;
                    stack.RemoveAt(stack.Count - 1);
                    continue;
                }
                var next = ReadVisualTreeNode(child, frame.Depth + 1, budget);
                if (frame.Children == null)
                {
                    frame.Children = new List<object>();
                    frame.Output["children"] = frame.Children;
                }
                frame.Children.Add(next.Output);
                frame.NextChild++;
                stack.Add(next);
            }
            return first.Output;
        }

        // ---- Render UI ----

        // Persistent RenderTextures keyed by PanelSettings instance ID so the panel
        // renders into them automatically every frame.
        private static readonly Dictionary<int, RenderTexture> s_panelRTs = new();
        private static readonly Dictionary<int, (PanelSettings panel, RenderTexture previousTarget)> s_panelBindings = new();
        private const int MaxCachedPanels = 8;
        private const long MaxCachedPanelPixels = 64L * 1024 * 1024;

        private static void PruneUICache()
        {
            List<int> stalePanels = null;
            foreach (var entry in s_panelRTs)
            {
                if (entry.Value == null || (s_panelBindings.TryGetValue(entry.Key, out var binding) && binding.panel == null))
                    (stalePanels ??= new List<int>()).Add(entry.Key);
            }
            if (stalePanels == null)
                return;

            foreach (int panelId in stalePanels)
            {
                if (!s_panelRTs.TryGetValue(panelId, out var ownedTarget))
                    continue;
                bool hasBinding = s_panelBindings.TryGetValue(panelId, out var binding);
                if (ownedTarget != null && (!hasBinding || binding.panel != null))
                    continue;
                if (ReferenceEquals(ownedTarget, null))
                {
                    s_panelRTs.Remove(panelId);
                    s_panelBindings.Remove(panelId);
                }
                else
                    FinishPanelRender(panelId, binding.panel, ownedTarget, false, true);
            }
        }

        private static void ValidateUICacheBudget(int panelId, int width, int height)
        {
            long pixels = (long)width * height;
            int panels = 1;
            foreach (var entry in s_panelRTs)
            {
                if (entry.Key == panelId)
                    continue;
                panels++;
                if (entry.Value == null)
                    continue;
                long retained = (long)entry.Value.width * entry.Value.height;
                if (retained > MaxCachedPanelPixels - pixels)
                    throw new ArgumentException("UI render textures exceed the cached pixel budget.");
                pixels += retained;
            }
            if (panels > MaxCachedPanels)
                throw new ArgumentException($"UI rendering can cache at most {MaxCachedPanels} panels.");
        }

        internal static void FinishPanelRender(int panelId, PanelSettings panel, RenderTexture ownedTarget, bool keepWarmupBinding, bool releaseCache)
        {
            // Destroyed Unity wrappers must remain distinct from null and replacement cache entries.
            if (ReferenceEquals(ownedTarget, null))
                return;
            if (!s_panelRTs.TryGetValue(panelId, out var cachedTarget) || !ReferenceEquals(cachedTarget, ownedTarget))
                return;
            try
            {
                if ((!keepWarmupBinding || releaseCache) && panel != null && s_panelBindings.TryGetValue(panelId, out var binding))
                {
                    var currentTarget = panel.targetTexture;
                    if (ReferenceEquals(currentTarget, ownedTarget) || (ownedTarget != null && currentTarget == ownedTarget))
                    {
                        panel.targetTexture = binding.previousTarget;
                        EditorUtility.SetDirty(panel);
                    }
                }
            }
            finally
            {
                if (releaseCache)
                {
                    s_panelRTs.Remove(panelId);
                    s_panelBindings.Remove(panelId);
                    if (ownedTarget != null)
                    {
                        string assetPath = AssetDatabase.GetAssetPath(ownedTarget);
                        ownedTarget.Release();
                        if (!string.IsNullOrEmpty(assetPath))
                            AssetDatabase.DeleteAsset(assetPath);
                        else
                            UnityEngine.Object.DestroyImmediate(ownedTarget);
                    }
                }
            }
        }

        // One request owns a deferred capture and its completed texture until retrieval, cancellation or expiry.
        private static Texture2D s_pendingCaptureTex;
        private static bool s_pendingCaptureDone;
        private static bool s_pendingCaptureStarted;
        private static int s_pendingCaptureGeneration;
        private static PendingUICapture s_pendingCaptureRequest;
        private static ScreenshotCapturer s_pendingCapturer;
        private static PendingPanelCapture s_pendingPanelCapture;
        private static string s_pendingCaptureFailure;
        private static double s_pendingCaptureExpiresAt;
        private const double PendingCaptureRetentionSeconds = 30;

        // Retain the existing mixed-key precedence for both initial rendering and owner validation.
        private static bool ReadRenderIncludeImage(ToolParams p) => p.GetBool("include_image") || p.GetBool("includeImage");

        private static int ReadRenderMaxResolution(ToolParams p) => p.GetInt("max_resolution") ?? p.GetInt("maxResolution") ?? 640;

        private static string ReadRenderFileName(ToolParams p) => p.Get("file_name") ?? p.Get("fileName");

        private static string ReadRenderOutputFolder(ToolParams p) => p.Get("output_folder") ?? p.Get("outputFolder");

        private static int ReadWarmupUpdates(JObject parameters)
        {
            JToken token = parameters["warmup_updates"] ?? parameters["warmupUpdates"];
            if (token == null)
                return 0;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int value) || value < 0 || value > 4)
                throw new ArgumentException("warmup_updates must be an integer from 0 to 4.");
            return value;
        }

        private sealed class PendingUICapture
        {
            internal readonly string Id = Guid.NewGuid().ToString("N");
            internal readonly string Target;
            internal readonly int TargetId;
            internal readonly string SourceAsset;
            internal readonly string ComponentKind;
            internal readonly int Width;
            internal readonly int Height;
            internal readonly bool IncludeImage;
            internal readonly int MaxResolution;
            internal readonly string FileName;
            internal readonly string Folder;
            internal readonly string FullPath;
            internal readonly string ProjectPath;
            internal readonly bool PlayMode;
            internal readonly int WarmupUpdates;
            internal int WarmupUpdatesObserved;
            internal string CaptureSource => PlayMode ? "game_view" : "panel_render_texture";

            internal PendingUICapture(JObject parameters, UIComponentAdapter ui, Type componentType, string folderSpec)
            {
                var p = new ToolParams(parameters);
                PlayMode = Application.isPlaying;
                WarmupUpdates = ReadWarmupUpdates(parameters);
                Target = p.Get("target");
                TargetId = ui == null ? 0 : ui.Component.gameObject.GetInstanceIDCompat();
                SourceAsset = NormalizeSource(p.Get("path"));
                ComponentKind = ui?.Kind ?? (componentType == typeof(UIDocument) ? "ui_document" : "panel_renderer");
                Width = p.GetInt("width") ?? 1920;
                Height = p.GetInt("height") ?? 1080;
                IncludeImage = ReadRenderIncludeImage(p);
                MaxResolution = ReadRenderMaxResolution(p);
                if (MaxResolution == 0)
                    MaxResolution = 640;
                FileName = NormalizeName(ReadRenderFileName(p));
                var output = ScreenshotUtility.PrepareCaptureResult(
                    FileName ?? $"ui-render-{DateTime.Now:yyyyMMdd-HHmmss}-{Id}.png",
                    1,
                    true,
                    folderSpec,
                    false
                );
                FullPath = output.FullPath;
                ProjectPath = output.ProjectRelativePath;
                Folder = Path.GetDirectoryName(FullPath).Replace('\\', '/');
            }

            internal bool Matches(ToolParams p, JObject parameters)
            {
                if ((parameters["warmup_updates"] != null || parameters["warmupUpdates"] != null) && ReadWarmupUpdates(parameters) != WarmupUpdates)
                    return false;
                if (p.Get("target") is string target)
                {
                    var go = ObjectResolver.Resolve(new JObject { ["find"] = target }, typeof(GameObject)) as GameObject;
                    if (go == null || go.GetInstanceIDCompat() != TargetId)
                        return false;
                }
                if (p.Get("path") is string source && NormalizeSource(source) != SourceAsset)
                    return false;
                if (p.Get("component_type") is string kind && kind != "auto" && kind != ComponentKind)
                    return false;
                if (p.Has("width") && p.GetInt("width") != Width || p.Has("height") && p.GetInt("height") != Height)
                    return false;
                if (p.Has("include_image") && ReadRenderIncludeImage(p) != IncludeImage)
                    return false;
                int maxResolution = ReadRenderMaxResolution(p);
                if (p.Has("max_resolution") && (maxResolution == 0 ? 640 : maxResolution) != MaxResolution)
                    return false;
                if (ReadRenderFileName(p) is string name && NormalizeName(name) != FileName)
                    return false;
                if (ReadRenderOutputFolder(p) is string folder)
                {
                    string normalized = ScreenshotUtility.ResolveFolderAbsolute(ScreenshotPreferences.Resolve(folder)).Replace('\\', '/');
                    var comparison = Application.platform == RuntimePlatform.WindowsEditor ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
                    if (!string.Equals(normalized, Folder, comparison))
                        return false;
                }
                return true;
            }

            private static string NormalizeName(string name) =>
                string.IsNullOrWhiteSpace(name) ? null
                : name.Trim().EndsWith(".png", StringComparison.OrdinalIgnoreCase) ? name.Trim()
                : name.Trim() + ".png";

            private static string NormalizeSource(string path) =>
                path == null ? null : AssetPathUtility.GetContainedAssetPath(path) ?? throw new ArgumentException("Invalid UXML path.");
        }

        // Owns only this capture's panel binding and temporary objects. Editor updates are
        // opportunities to render, not proof that UI Toolkit completed a layout/repaint.
        private sealed class PendingPanelCapture : IDisposable
        {
            internal int Generation;
            internal int PanelId;
            internal UIComponentAdapter UI;
            internal PanelSettings Panel;
            internal RenderTexture Target;
            internal GameObject TemporaryObject;
            internal PanelSettings TemporarySettings;
            internal AssetFolderScope Folders;

            public void Dispose()
            {
                try
                {
                    FinishPanelRender(PanelId, Panel, Target, false, !ReferenceEquals(TemporarySettings, null));
                }
                finally
                {
                    try
                    {
                        if (TemporaryObject != null)
                            UnityEngine.Object.DestroyImmediate(TemporaryObject);
                        if (TemporarySettings != null)
                        {
                            string path = AssetDatabase.GetAssetPath(TemporarySettings);
                            if (!string.IsNullOrEmpty(path))
                                AssetDatabase.DeleteAsset(path);
                            else
                                UnityEngine.Object.DestroyImmediate(TemporarySettings, true);
                        }
                        else
                            Folders.Complete();
                    }
                    finally
                    {
                        Folders.Dispose();
                    }
                }
            }
        }

        private static void ReleasePendingPanelCapture()
        {
            EditorApplication.update -= AdvancePanelCapture;
            var capture = s_pendingPanelCapture;
            s_pendingPanelCapture = null;
            capture?.Dispose();
        }

        private static Texture2D ReadPanelTexture(RenderTexture target)
        {
            ScreenshotUtility.ValidateFrameDimensions(target.width, target.height);
            var previous = RenderTexture.active;
            Texture2D texture = null;
            try
            {
                RenderTexture.active = target;
                texture = new Texture2D(target.width, target.height, TextureFormat.RGBA32, false);
                texture.ReadPixels(new Rect(0, 0, target.width, target.height), 0, 0);
                texture.Apply();
                return texture;
            }
            catch
            {
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
                throw;
            }
            finally
            {
                RenderTexture.active = previous;
            }
        }

        private static void AdvancePanelCapture()
        {
            var capture = s_pendingPanelCapture;
            var request = s_pendingCaptureRequest;
            if (capture == null || request == null || capture.Generation != s_pendingCaptureGeneration)
                return;
            Texture2D texture = null;
            try
            {
                if (
                    Application.isPlaying
                    || capture.UI.Component == null
                    || capture.Panel == null
                    || capture.Target == null
                    || capture.UI.PanelSettings != capture.Panel
                    || capture.Panel.targetTexture != capture.Target
                    || capture.Target.width != request.Width
                    || capture.Target.height != request.Height
                    || !capture.Target.IsCreated()
                    || !s_panelRTs.TryGetValue(capture.PanelId, out var cached)
                    || !ReferenceEquals(cached, capture.Target)
                )
                    throw new InvalidOperationException("The owned panel capture binding changed during warmup.");
                request.WarmupUpdatesObserved++;
                if (request.WarmupUpdatesObserved < request.WarmupUpdates)
                {
                    capture.UI.Root?.MarkDirtyRepaint();
                    EditorApplication.QueuePlayerLoopUpdate();
                    return;
                }
                texture = ReadPanelTexture(capture.Target);
            }
            catch (Exception ex)
            {
                s_pendingCaptureFailure = ex.Message;
            }
            try
            {
                ReleasePendingPanelCapture();
            }
            catch (Exception ex)
            {
                s_pendingCaptureFailure = ex.Message;
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
                texture = null;
            }
            CompletePendingCapture(capture.Generation, texture);
        }

        private static void CleanupPendingCapture()
        {
            // Invalidate callbacks before releasing the result, including callbacks during play-mode teardown.
            unchecked
            {
                s_pendingCaptureGeneration++;
            }
            EditorApplication.update -= ExpirePendingCapture;
            try
            {
                ReleasePendingPanelCapture();
            }
            finally
            {
                var capturer = s_pendingCapturer;
                s_pendingCapturer = null;
                if (capturer != null)
                    UnityEngine.Object.DestroyImmediate(capturer.gameObject);
                if (s_pendingCaptureTex != null)
                    UnityEngine.Object.DestroyImmediate(s_pendingCaptureTex);
                s_pendingCaptureTex = null;
                s_pendingCaptureDone = s_pendingCaptureStarted = false;
                s_pendingCaptureRequest = null;
                s_pendingCaptureExpiresAt = 0;
                s_pendingCaptureFailure = null;
            }
        }

        private static int BeginPendingCapture()
        {
            CleanupPendingCapture();
            s_pendingCaptureStarted = true;
            s_pendingCaptureExpiresAt = EditorApplication.timeSinceStartup + PendingCaptureRetentionSeconds;
            EditorApplication.update += ExpirePendingCapture;
            return s_pendingCaptureGeneration;
        }

        private static void ExpirePendingCapture()
        {
            if ((s_pendingCaptureStarted || s_pendingCaptureDone) && EditorApplication.timeSinceStartup >= s_pendingCaptureExpiresAt)
                CleanupPendingCapture();
        }

        private static void CompletePendingCapture(int generation, Texture2D texture)
        {
            if (generation != s_pendingCaptureGeneration || !s_pendingCaptureStarted)
            {
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
                return;
            }
            s_pendingCaptureTex = texture;
            s_pendingCapturer = null;
            s_pendingCaptureDone = true;
            s_pendingCaptureStarted = false;
            s_pendingCaptureExpiresAt = EditorApplication.timeSinceStartup + PendingCaptureRetentionSeconds;
        }

        private static object PendingCaptureResponse(PendingUICapture request) =>
            new SuccessResponse(
                "UI capture is pending. Resume using the returned capture_id.",
                new Dictionary<string, object>
                {
                    { "pending", true },
                    { "capture_id", request.Id },
                    { "componentType", request.ComponentKind },
                    { "gameObject", request.Target ?? request.SourceAsset },
                    { "capture_source", request.CaptureSource },
                    { "full_game_view", request.PlayMode },
                    { "readiness", request.PlayMode ? "awaiting_end_of_frame" : "awaiting_editor_updates" },
                    { "warmup_updates_requested", request.WarmupUpdates },
                    { "warmup_updates_observed", request.WarmupUpdatesObserved },
                    { "expires_in_seconds", Math.Max(0, s_pendingCaptureExpiresAt - EditorApplication.timeSinceStartup) },
                    {
                        "note",
                        "Poll render_ui with capture_id; cancel with capture_id and cancel_capture=true. Omitting capture_id starts a separate request."
                    },
                }
            );

        private static object ResumePendingCapture(JObject parameters, string captureId)
        {
            var request = s_pendingCaptureRequest;
            if (request == null || request.PlayMode != Application.isPlaying || !string.Equals(request.Id, captureId, StringComparison.Ordinal))
                return new ErrorResponse("No live UI capture matches capture_id. Start a new render_ui request.", new { reason = "capture_not_found" });
            ExpirePendingCapture();
            if (s_pendingCaptureRequest == null)
                return new ErrorResponse("The UI capture expired. Start a new render_ui request.", new { reason = "capture_expired" });
            var p = new ToolParams(parameters);
            if (!request.Matches(p, parameters))
                return new ErrorResponse(
                    "Capture settings differ from the originating request. Resume with capture_id only.",
                    new { reason = "capture_settings_mismatch" }
                );
            if (p.GetBool("cancel_capture"))
            {
                CleanupPendingCapture();
                return new SuccessResponse("Cancelled the owned UI capture.", new { capture_id = request.Id, cancelled = true });
            }
            if (!s_pendingCaptureDone)
                return PendingCaptureResponse(request);
            var texture = s_pendingCaptureTex;
            string failure = s_pendingCaptureFailure;
            s_pendingCaptureTex = null;
            CleanupPendingCapture();
            if (texture == null)
                return new ErrorResponse(
                    failure
                        ?? (
                            request.PlayMode
                                ? "Play-mode screenshot timed out or captured nothing. Keep the Game view visible and the editor unpaused."
                                : "Panel readback did not complete during the bounded warmup."
                        )
                );
            return SavePendingCapture(request, texture);
        }

        private static object SavePendingCapture(PendingUICapture request, Texture2D texture)
        {
            int width = texture.width;
            int height = texture.height;
            byte[] png;
            Dictionary<string, object> evidence;
            try
            {
                ScreenshotUtility.ValidateFrameDimensions(width, height);
                evidence = UIRenderEvidence.Describe(
                    texture,
                    request.CaptureSource,
                    request.PlayMode ? "end_of_frame_capture" : "bounded_editor_updates_elapsed",
                    request.WarmupUpdates,
                    request.WarmupUpdatesObserved
                );
                png = texture.EncodeToPNG();
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
            ScreenshotFileUtility.WriteCaptureBytes(request.FullPath, png);
            if (ScreenshotUtility.IsUnderAssets(request.ProjectPath))
                AssetDatabase.ImportAsset(request.ProjectPath, ImportAssetOptions.ForceSynchronousImport);
            var data = new Dictionary<string, object>
            {
                { "capture_id", request.Id },
                { "capture_source", request.CaptureSource },
                { "path", request.ProjectPath },
                { "fullPath", request.FullPath },
                { "width", width },
                { "height", height },
                { "hasContent", evidence["hasContent"] },
                { "componentType", request.ComponentKind },
            };
            foreach (var item in evidence)
                data[item.Key] = item.Value;
            if (!string.IsNullOrEmpty(request.Target))
                data["gameObject"] = request.Target;
            if (!string.IsNullOrEmpty(request.SourceAsset))
                data["sourceAsset"] = request.SourceAsset;
            if (request.IncludeImage)
            {
                Texture2D full = null;
                Texture2D downscaled = null;
                try
                {
                    if (width > request.MaxResolution || height > request.MaxResolution)
                    {
                        full = new Texture2D(width, height, TextureFormat.RGBA32, false);
                        full.LoadImage(png);
                        downscaled = ScreenshotUtility.DownscaleTexture(full, request.MaxResolution);
                        data["imageBase64"] = Convert.ToBase64String(downscaled.EncodeToPNG());
                        data["imageWidth"] = downscaled.width;
                        data["imageHeight"] = downscaled.height;
                    }
                    else
                    {
                        data["imageBase64"] = Convert.ToBase64String(png);
                        data["imageWidth"] = width;
                        data["imageHeight"] = height;
                    }
                }
                finally
                {
                    if (downscaled != null)
                        UnityEngine.Object.DestroyImmediate(downscaled);
                    if (full != null)
                        UnityEngine.Object.DestroyImmediate(full);
                }
            }
            return new SuccessResponse($"UI render saved to '{request.ProjectPath}'.", data);
        }

        private static object RenderUI(JObject @params)
        {
            var p = new ToolParams(@params);

            int warmupUpdates = ReadWarmupUpdates(@params);
            string captureId = p.Get("capture_id");
            if (captureId != null)
                return ResumePendingCapture(@params, captureId);
            if (p.GetBool("cancel_capture"))
                return new ErrorResponse("cancel_capture requires the owning capture_id.");
            ExpirePendingCapture();
            if (s_pendingCaptureRequest != null)
                return new ErrorResponse(
                    "Another request owns the UI capture. Its caller must resume or cancel using capture_id, or wait for expiry.",
                    new { retry_after_ms = 100, reason = s_pendingCaptureDone ? "capture_result_pending" : "capture_in_progress" }
                );
            if (Application.isPlaying && warmupUpdates != 0)
                return new ErrorResponse("warmup_updates is only supported for Edit-mode panel captures; Play mode already waits for end of frame.");

            string target = p.Get("target");
            string uxmlPath = p.Get("path");
            int width = p.GetInt("width") ?? 1920;
            int height = p.GetInt("height") ?? 1080;
            bool includeImage = ReadRenderIncludeImage(p);
            int maxResolution = ReadRenderMaxResolution(p);
            string fileName = ReadRenderFileName(p);
            string outputFolderOverride = ReadRenderOutputFolder(p);
            try
            {
                ScreenshotUtility.ValidateFrameDimensions(width, height);
                ScreenshotUtility.ValidateMaxResolution(maxResolution);
                if (Application.isPlaying)
                    ScreenshotUtility.ValidateFrameDimensions(Mathf.Max(1, Screen.width), Mathf.Max(1, Screen.height));
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse(ex.Message);
            }
            if (maxResolution == 0)
                maxResolution = 640;

            if (string.IsNullOrEmpty(target) && string.IsNullOrEmpty(uxmlPath))
            {
                return new ErrorResponse("Either 'target' (GameObject with UIDocument or PanelRenderer) or 'path' (UXML asset path) is required.");
            }

            UIComponentAdapter targetUI = null;
            Type renderComponentType;
            if (!string.IsNullOrEmpty(target))
            {
                var goInstruction = new JObject { ["find"] = target };
                var go = ObjectResolver.Resolve(goInstruction, typeof(GameObject)) as GameObject;
                if (go == null)
                    return new ErrorResponse($"Could not find target GameObject: {target}");
                targetUI = UIComponentAdapter.Resolve(go, p.Get("component_type"));
                renderComponentType = targetUI.Component.GetType();
            }
            else
            {
                renderComponentType = UIComponentAdapter.ResolveType(null, p.Get("component_type"), true);
            }

            string resolvedFolderSpec = ScreenshotPreferences.Resolve(outputFolderOverride);
            try
            {
                // Play-mode output is resolved once when its immutable request is created below.
                if (!Application.isPlaying)
                    ScreenshotUtility.PrepareCaptureResult(fileName, 1, true, resolvedFolderSpec, false);
            }
            catch (InvalidOperationException ex)
            {
                return new ErrorResponse(ex.Message);
            }

            // ── Play-mode capture via ScreenCapture coroutine ──────────────────────
            // A newly assigned panel target may not have rendered during a synchronous call.
            // In play mode we dispatch a WaitForEndOfFrame coroutine that uses ScreenCapture, which
            // captures the fully-composited game view (including UI Toolkit overlays).
            // Each start returns an operation ID; only that ID may poll or cancel its result.
            if (Application.isPlaying)
            {
                var request = new PendingUICapture(@params, targetUI, renderComponentType, resolvedFolderSpec);
                int captureGeneration = BeginPendingCapture();
                s_pendingCaptureRequest = request;
                try
                {
                    s_pendingCapturer = ScreenshotCapturer.Begin(1, texture => CompletePendingCapture(captureGeneration, texture));
                }
                catch
                {
                    CleanupPendingCapture();
                    throw;
                }
                return PendingCaptureResponse(request);
            }

            // Resolve the scene component or create a temporary component for the asset.
            UIComponentAdapter ui = targetUI;
            GameObject tempGo = null;
            PanelSettings tempPs = null;
            Texture2D tex = null;
            PanelSettings renderPanel = null;
            int renderPanelId = 0;
            RenderTexture renderTarget = null;
            bool keepWarmupBinding = false;
            var renderAssetFolders = new AssetFolderScope();

            try
            {
                if (ui == null)
                {
                    uxmlPath = AssetPathUtility.GetContainedAssetPath(uxmlPath);
                    if (uxmlPath == null)
                        return new ErrorResponse("Invalid UXML path.");

                    var vta = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(uxmlPath);
                    if (vta == null)
                        return new ErrorResponse($"Could not load VisualTreeAsset at: {uxmlPath}");

                    tempGo = new GameObject("__MCP_UI_Render_Temp__");
                    tempGo.hideFlags = HideFlags.HideAndDontSave;
                    ui = new UIComponentAdapter(tempGo.AddComponent(renderComponentType));

                    string[] guids = AssetDatabase.FindAssets("t:PanelSettings");
                    PanelSettings ps = null;
                    if (guids.Length > 0)
                        ps = AssetDatabase.LoadAssetAtPath<PanelSettings>(AssetPathUtility.GetAssetPathFromGuid(guids[0], allowPackages: true));
                    if (ps == null)
                    {
                        ps = CreateDefaultPanelSettings("Assets/UI/DefaultPanelSettings.asset", renderAssetFolders, created => tempPs = created);
                    }

                    ui.Configure(vta, ps, 0);
                }

                if (ui.PanelSettings == null)
                    return new ErrorResponse($"{ui.DisplayName} has no PanelSettings assigned.");

                var panelSettings = ui.PanelSettings;
                renderPanel = panelSettings;
                int psId = panelSettings.GetInstanceIDCompat();
                renderPanelId = psId;
                PruneUICache();
                ValidateUICacheBudget(psId, width, height);
                bool rememberBinding = !s_panelRTs.TryGetValue(psId, out var ownedTarget) || panelSettings.targetTexture != ownedTarget;
                var previousTarget = rememberBinding ? panelSettings.targetTexture : s_panelBindings[psId].previousTarget;

                // Check if we already have a persistent RT assigned to this PanelSettings.
                // If the RT exists and its size matches, the panel has been rendering into it.
                // If not, create one and assign it — content will be available on the next call.
                // Look up from our cache rather than panelSettings.targetTexture,
                // because we set targetTexture = null after each successful read
                // to restore on-screen rendering.  The RT itself stays alive in s_panelRTs.
                bool rtJustAssigned = false;
                RenderTexture rt = null;

                if (s_panelRTs.TryGetValue(psId, out var cachedRt) && cachedRt != null)
                {
                    if (cachedRt.width == width && cachedRt.height == height)
                    {
                        rt = cachedRt;
                        renderTarget = rt;
                        if (rememberBinding)
                            s_panelBindings[psId] = (panelSettings, previousTarget);
                        // Re-attach if it was detached after the previous read
                        if (panelSettings.targetTexture != rt)
                        {
                            panelSettings.targetTexture = rt;
                            rtJustAssigned = true;

                            ui.Root?.MarkDirtyRepaint();
                            EditorUtility.SetDirty(panelSettings);
                            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                            Canvas.ForceUpdateCanvases();
                        }
                    }
                    else
                    {
                        // Size changed — release the old RT
                        panelSettings.targetTexture = previousTarget;
                        string oldPath = AssetDatabase.GetAssetPath(cachedRt);
                        cachedRt.Release();
                        if (!string.IsNullOrEmpty(oldPath))
                            AssetDatabase.DeleteAsset(oldPath);
                        else
                            UnityEngine.Object.DestroyImmediate(cachedRt);
                        s_panelRTs.Remove(psId);
                    }
                }

                if (rt == null)
                {
                    // Create RT as an asset so PanelSettings can serialize the reference properly
                    bool adopted = false;
                    try
                    {
                        rt = new RenderTexture(width, height, 32, RenderTextureFormat.ARGB32);
                        rt.name = $"MCP_UI_Render_{psId}";
                        rt.Create();

                        string rtFolder = "Assets/UI";
                        renderAssetFolders.EnsureFolder(rtFolder);
                        string rtAssetPath = $"{rtFolder}/RT_MCP_UI_Render_{psId}.renderTexture";
                        AssetDatabase.CreateAsset(rt, rtAssetPath);
                        AssetDatabase.SaveAssets();

                        s_panelRTs[psId] = rt;
                        renderTarget = rt;
                        s_panelBindings[psId] = (panelSettings, previousTarget);
                        adopted = true;
                        panelSettings.targetTexture = rt;
                    }
                    finally
                    {
                        if (!adopted)
                        {
                            s_panelBindings.Remove(psId);
                            if (s_panelRTs.TryGetValue(psId, out var failedCachedRt) && failedCachedRt == null)
                                s_panelRTs.Remove(psId);
                            if (rt != null)
                            {
                                rt.Release();
                                string failedPath = AssetDatabase.GetAssetPath(rt);
                                if (!string.IsNullOrEmpty(failedPath))
                                    AssetDatabase.DeleteAsset(failedPath);
                                else
                                    UnityEngine.Object.DestroyImmediate(rt);
                            }
                        }
                    }
                    rtJustAssigned = true;

                    // Mark dirty and force editor repaint so the panel renders into the RT
                    ui.Root?.MarkDirtyRepaint();
                    EditorUtility.SetDirty(panelSettings);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();

                    // Force a synchronous layout + repaint pass
                    Canvas.ForceUpdateCanvases();
                }

                if (warmupUpdates > 0)
                {
                    var request = new PendingUICapture(@params, ui, renderComponentType, resolvedFolderSpec);
                    int generation = BeginPendingCapture();
                    s_pendingCaptureRequest = request;
                    s_pendingPanelCapture = new PendingPanelCapture
                    {
                        Generation = generation,
                        PanelId = renderPanelId,
                        UI = ui,
                        Panel = renderPanel,
                        Target = renderTarget,
                        TemporaryObject = tempGo,
                        TemporarySettings = tempPs,
                        Folders = renderAssetFolders,
                    };
                    // Transfer ownership before returning; the local finally must not release it.
                    renderTarget = null;
                    tempGo = null;
                    tempPs = null;
                    renderAssetFolders = null;
                    try
                    {
                        EditorApplication.update += AdvancePanelCapture;
                        ui.Root?.MarkDirtyRepaint();
                        EditorApplication.QueuePlayerLoopUpdate();
                    }
                    catch
                    {
                        CleanupPendingCapture();
                        throw;
                    }
                    return PendingCaptureResponse(request);
                }

                tex = ReadPanelTexture(rt);
                var evidence = UIRenderEvidence.Describe(
                    tex,
                    "panel_render_texture",
                    rtJustAssigned ? "target_just_assigned" : "existing_target_readback",
                    0,
                    0
                );
                bool hasContent = (bool)evidence["hasContent"];

                // Save to Screenshots folder
                string resolvedName = string.IsNullOrWhiteSpace(fileName) ? $"ui-render-{DateTime.Now:yyyyMMdd-HHmmss}.png" : fileName.Trim();
                if (!resolvedName.EndsWith(".png", StringComparison.OrdinalIgnoreCase))
                    resolvedName += ".png";

                string fullPath = ScreenshotUtility.PrepareCaptureResult(resolvedName, 1, true, resolvedFolderSpec, false).FullPath;

                byte[] png = tex.EncodeToPNG();
                ScreenshotFileUtility.WriteCaptureBytes(fullPath, png);

                string projectRelPath = ScreenshotUtility.ToProjectRelativePath(fullPath);
                if (ScreenshotUtility.IsUnderAssets(projectRelPath))
                    AssetDatabase.ImportAsset(projectRelPath, ImportAssetOptions.ForceSynchronousImport);

                var data = new Dictionary<string, object>
                {
                    { "path", projectRelPath },
                    { "fullPath", fullPath },
                    { "width", width },
                    { "height", height },
                    { "hasContent", hasContent },
                    { "componentType", ui.Kind },
                };

                foreach (var item in evidence)
                    data[item.Key] = item.Value;
                if (rtJustAssigned)
                    data["note"] =
                        "The panel target was just assigned; a completed UI repaint is not observed. Opt into warmup_updates for a bounded deferred readback.";

                if (!string.IsNullOrEmpty(target))
                    data["gameObject"] = target;
                if (!string.IsNullOrEmpty(uxmlPath))
                    data["sourceAsset"] = uxmlPath;

                if (includeImage)
                {
                    int targetMax = maxResolution > 0 ? maxResolution : 640;
                    Texture2D downscaled = null;
                    try
                    {
                        if (width > targetMax || height > targetMax)
                        {
                            downscaled = ScreenshotUtility.DownscaleTexture(tex, targetMax);
                            data["imageBase64"] = Convert.ToBase64String(downscaled.EncodeToPNG());
                            data["imageWidth"] = downscaled.width;
                            data["imageHeight"] = downscaled.height;
                        }
                        else
                        {
                            data["imageBase64"] = Convert.ToBase64String(png);
                            data["imageWidth"] = width;
                            data["imageHeight"] = height;
                        }
                    }
                    finally
                    {
                        if (downscaled != null)
                            UnityEngine.Object.DestroyImmediate(downscaled);
                    }
                }

                string msg =
                    hasContent ? $"UI rendered to '{projectRelPath}'."
                    : rtJustAssigned ? $"UI render saved to '{projectRelPath}' after assigning the panel target (all image alpha is zero)."
                    : $"UI render saved to '{projectRelPath}' (all image alpha is zero).";

                keepWarmupBinding = rtJustAssigned;
                if (ReferenceEquals(tempPs, null))
                    renderAssetFolders.Complete();
                return new SuccessResponse(msg, data);
            }
            finally
            {
                try
                {
                    try
                    {
                        FinishPanelRender(renderPanelId, renderPanel, renderTarget, keepWarmupBinding, !ReferenceEquals(tempPs, null));
                    }
                    finally
                    {
                        if (tex != null)
                            UnityEngine.Object.DestroyImmediate(tex);
                        if (tempGo != null)
                            UnityEngine.Object.DestroyImmediate(tempGo);
                        if (tempPs != null)
                        {
                            string tempPsPath = AssetDatabase.GetAssetPath(tempPs);
                            if (!string.IsNullOrEmpty(tempPsPath))
                                AssetDatabase.DeleteAsset(tempPsPath);
                            else
                                UnityEngine.Object.DestroyImmediate(tempPs, true);
                        }
                    }
                }
                finally
                {
                    renderAssetFolders?.Dispose();
                }
            }
        }

        // ---- Link Stylesheet ----

        private static object LinkStylesheet(JObject @params)
        {
            var p = new ToolParams(@params);

            string uxmlPathRaw = p.Get("path");
            string uxmlPath = ValidatePath(uxmlPathRaw, out string pathError);
            if (pathError != null)
                return new ErrorResponse(pathError);

            // Validate the UXML path is actually a .uxml
            if (!uxmlPath.EndsWith(".uxml", StringComparison.OrdinalIgnoreCase))
                return new ErrorResponse("'path' must point to a .uxml file.");

            string stylesheetPath = p.Get("stylesheet");
            if (string.IsNullOrEmpty(stylesheetPath))
                return new ErrorResponse("'stylesheet' parameter is required.");

            stylesheetPath = AssetPathUtility.GetContainedAssetPath(stylesheetPath);
            if (stylesheetPath == null)
                return new ErrorResponse("Invalid stylesheet path: contains traversal sequences.");

            if (!stylesheetPath.EndsWith(".uss", StringComparison.OrdinalIgnoreCase))
                return new ErrorResponse("'stylesheet' must point to a .uss file.");

            // Read the UXML file
            string fullPath = AssetPathUtility.GetFullAssetPath(uxmlPath);

            if (!File.Exists(fullPath))
                return new ErrorResponse($"UXML file not found: {uxmlPath}");

            string content = File.ReadAllText(fullPath, Encoding.UTF8);

            string xmlError = ReadUxmlContent(content, new List<string>(), out var document, stylesheetPath);
            if (xmlError != null)
                return new ErrorResponse($"UXML validation failed — file was NOT updated. {xmlError}");

            if (document.StylesheetLinked)
            {
                return new SuccessResponse(
                    $"Stylesheet already linked in '{uxmlPath}'.",
                    new
                    {
                        path = uxmlPath,
                        stylesheet = stylesheetPath,
                        alreadyLinked = true,
                    }
                );
            }

            // Find the insertion point (after the opening <ui:UXML ...> or <UXML ...> tag)
            int insertIdx = FindUxmlBodyStart(content);
            if (insertIdx < 0)
                return new ErrorResponse("Could not find insertion point. Ensure UXML has a root <ui:UXML> or <UXML> element.");

            string styleTag = CreateStylesheetTag(stylesheetPath, document.StyleTagName);
            content = content.Insert(insertIdx, styleTag);

            File.WriteAllText(fullPath, content, Utf8NoBom);
            AssetDatabase.ImportAsset(uxmlPath, ImportAssetOptions.ForceUpdate);

            return new SuccessResponse($"Linked stylesheet '{stylesheetPath}' to '{uxmlPath}'.", new { path = uxmlPath, stylesheet = stylesheetPath });
        }

        // ---- Delete ----

        private static object DeleteFile(JObject @params)
        {
            var p = new ToolParams(@params);
            string path = ValidatePath(p.Get("path"), out string pathError);
            if (pathError != null)
                return new ErrorResponse(pathError);

            string fullPath = AssetPathUtility.GetFullAssetPath(path);

            if (!File.Exists(fullPath))
            {
                return new ErrorResponse($"File not found: {path}");
            }

            try
            {
                bool success = AssetDatabase.DeleteAsset(path);
                if (!success)
                {
                    return new ErrorResponse($"Failed to delete file through AssetDatabase: '{path}'");
                }

                // Fallback: if file still exists after AssetDatabase.DeleteAsset
                if (File.Exists(fullPath))
                {
                    File.Delete(fullPath);
                }

                return new SuccessResponse($"Deleted {Path.GetExtension(path).TrimStart('.')} file at {path}", new { path });
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to delete '{path}': {e.Message}");
            }
        }

        // ---- List UI Assets ----

        private static object ListUIAssets(JObject @params)
        {
            var p = new ToolParams(@params);
            string scope = p.Get("path") ?? "Assets";
            string filterType = p.Get("filter_type") ?? p.Get("filterType");
            if (
                !PaginationBounds.TryRead(
                    @params["page_size"] ?? @params["pageSize"],
                    50,
                    1,
                    PaginationBounds.MaxPageSize,
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

            scope = AssetPathUtility.GetContainedAssetPath(scope);
            if (scope == null)
            {
                return new ErrorResponse("Invalid path: contains traversal sequences.");
            }

            if (!AssetDatabase.IsValidFolder(scope))
                return new ErrorResponse($"UI asset scope is not an existing folder: {scope}");
            string[] folderScope = new[] { scope };

            // Find UXML and USS assets based on filter
            var paged = new List<object>();
            int total = 0;

            void AddPage(string guid, string type)
            {
                string assetPath;
                try
                {
                    assetPath = AssetPathUtility.GetAssetPathFromGuid(guid, allowPackages: true);
                }
                catch (ArgumentException)
                {
                    return;
                }
                catch (InvalidOperationException)
                {
                    return;
                }
                if (string.IsNullOrEmpty(assetPath))
                    return;
                if (total >= startIndex && paged.Count < pageSize)
                    paged.Add(
                        new Dictionary<string, object>
                        {
                            ["path"] = assetPath,
                            ["type"] = type,
                            ["name"] = Path.GetFileName(assetPath),
                        }
                    );
                total++;
            }

            bool includeUxml =
                string.IsNullOrEmpty(filterType)
                || filterType.Equals("uxml", StringComparison.OrdinalIgnoreCase)
                || filterType.Equals("VisualTreeAsset", StringComparison.OrdinalIgnoreCase);
            bool includeUss =
                string.IsNullOrEmpty(filterType)
                || filterType.Equals("uss", StringComparison.OrdinalIgnoreCase)
                || filterType.Equals("StyleSheet", StringComparison.OrdinalIgnoreCase);
            bool includePanelSettings = string.IsNullOrEmpty(filterType) || filterType.Equals("PanelSettings", StringComparison.OrdinalIgnoreCase);

            if (includeUxml)
            {
                string[] guids = AssetDatabase.FindAssets("t:VisualTreeAsset", folderScope);
                foreach (string guid in guids)
                {
                    AddPage(guid, "uxml");
                }
            }

            if (includeUss)
            {
                string[] guids = AssetDatabase.FindAssets("t:StyleSheet", folderScope);
                foreach (string guid in guids)
                {
                    AddPage(guid, "uss");
                }
            }

            if (includePanelSettings)
            {
                string[] guids = AssetDatabase.FindAssets("t:PanelSettings", folderScope);
                foreach (string guid in guids)
                {
                    AddPage(guid, "PanelSettings");
                }
            }

            return new SuccessResponse(
                $"Found {total} UI asset(s). Returning page {pageNumber} ({paged.Count} items).",
                new
                {
                    total,
                    pageSize,
                    pageNumber,
                    maxPageSize = PaginationBounds.MaxPageSize,
                    assets = paged,
                }
            );
        }

        // ---- Detach UIDocument ----

        private static object DetachUIDocument(JObject @params)
        {
            var p = new ToolParams(@params);

            var targetResult = p.GetRequired("target");
            var targetError = targetResult.GetOrError(out string target);
            if (targetError != null)
                return targetError;

            var goInstruction = new JObject { ["find"] = target };
            GameObject go = ObjectResolver.Resolve(goInstruction, typeof(GameObject)) as GameObject;
            if (go == null)
            {
                return new ErrorResponse($"Could not find target GameObject: {target}");
            }

            var ui = UIComponentAdapter.Resolve(go, p.Get("component_type"));
            string sourceAsset = ui.SourceAsset != null ? AssetDatabase.GetAssetPath(ui.SourceAsset) : null;

            string componentKind = ui.Kind;
            string componentName = ui.DisplayName;
            Undo.DestroyObjectImmediate(ui.Component);
            EditorUtility.SetDirty(go);

            return new SuccessResponse(
                $"Removed {componentName} from {go.name}",
                new
                {
                    gameObject = go.name,
                    componentType = componentKind,
                    removedSourceAsset = sourceAsset,
                }
            );
        }

        // ---- Modify Visual Element ----

        private static object ModifyVisualElement(JObject @params)
        {
            var p = new ToolParams(@params);

            var targetResult = p.GetRequired("target");
            var targetError = targetResult.GetOrError(out string target);
            if (targetError != null)
                return targetError;

            string elementName = p.Get("element_name") ?? p.Get("elementName");
            bool explicitSelector = p.Has("element_path") || p.Has("element_id");
            bool strictName = p.GetBool("strict_name");
            if (!explicitSelector && string.IsNullOrEmpty(elementName))
            {
                return new ErrorResponse("'element_name' parameter is required.");
            }

            var goInstruction = new JObject { ["find"] = target };
            GameObject go = ObjectResolver.Resolve(goInstruction, typeof(GameObject)) as GameObject;
            if (go == null)
            {
                return new ErrorResponse($"Could not find target GameObject: {target}");
            }

            var ui = UIComponentAdapter.Resolve(go, p.Get("component_type"));
            if ((explicitSelector || strictName) && (!go.activeInHierarchy || (ui.Component is Behaviour behaviour && !behaviour.isActiveAndEnabled)))
                return new ErrorResponse("Strict or explicit visual element selection requires an active GameObject and enabled UI component.");
            if (p.Has("element_id"))
                RequireCanonicalSnapshotComponent(go, ui);
            var root = ui.Root;
            if (root == null)
            {
                return new ErrorResponse(
                    $"{ui.DisplayName} on {go.name} has no visual tree (not yet built). Enable the component and retry after initialization."
                );
            }

            // Preserve legacy first-match queries unless the caller requests a safer selector.
            var element =
                explicitSelector ? ResolveExplicitVisualElement(p, go, ui, root, elementName)
                : strictName ? ResolveUniqueVisualElement(root, elementName)
                : root.Q(elementName);
            if (element == null)
            {
                return new ErrorResponse($"Visual element with name '{elementName}' not found in the visual tree.");
            }

            elementName = element.name;
            string text = p.Get("text");
            if (text != null && element is not TextElement)
                return new ErrorResponse($"Element '{elementName}' ({element.GetType().Name}) does not support text content.");

            // Parse all style values before changing text, classes or earlier styles.
            JToken styleToken = p.GetRaw("style") ?? p.GetRaw("inline_style") ?? p.GetRaw("inlineStyle");
            bool strictStyle = @params["strict_style"]?.Value<bool>() == true;
            bool includeStyleResults = styleToken != null || strictStyle;
            var inlineStyles = PrepareInlineStyles(styleToken, strictStyle);
            if (inlineStyles.Error != null)
                return new ErrorResponse(inlineStyles.Error, new { style_results = inlineStyles.Report() });
            bool? enabled = p.GetNullableBool("enabled");
            bool? visibility = p.GetNullableBool("visible");
            var modifications = new List<string>();

            // Set text content (Label, Button, etc.)
            if (text != null && element is TextElement textEl)
            {
                textEl.text = text;
                modifications.Add($"text='{text}'");
            }

            // Add CSS classes
            JToken addClassesToken = p.GetRaw("add_classes") ?? p.GetRaw("addClasses");
            if (addClassesToken is JArray addArr)
            {
                foreach (var cls in addArr)
                {
                    string className = cls.ToString();
                    if (!element.ClassListContains(className))
                    {
                        element.AddToClassList(className);
                        modifications.Add($"+class '{className}'");
                    }
                }
            }

            // Remove CSS classes
            JToken removeClassesToken = p.GetRaw("remove_classes") ?? p.GetRaw("removeClasses");
            if (removeClassesToken is JArray removeArr)
            {
                foreach (var cls in removeArr)
                {
                    string className = cls.ToString();
                    if (element.ClassListContains(className))
                    {
                        element.RemoveFromClassList(className);
                        modifications.Add($"-class '{className}'");
                    }
                }
            }

            // Toggle CSS classes
            JToken toggleClassesToken = p.GetRaw("toggle_classes") ?? p.GetRaw("toggleClasses");
            if (toggleClassesToken is JArray toggleArr)
            {
                foreach (var cls in toggleArr)
                {
                    string className = cls.ToString();
                    element.ToggleInClassList(className);
                    modifications.Add($"~class '{className}'");
                }
            }

            // Set inline styles
            foreach (var entry in inlineStyles.Entries)
            {
                entry.Apply?.Invoke(element);
                if (entry.Modification != null)
                    modifications.Add(entry.Modification);
            }

            // Set enabled/disabled
            if (enabled.HasValue)
            {
                element.SetEnabled(enabled.Value);
                modifications.Add($"enabled={enabled.Value}");
            }

            // Set visibility
            if (visibility.HasValue)
            {
                bool isVisible = visibility.Value;
                element.style.display = isVisible ? DisplayStyle.Flex : DisplayStyle.None;
                modifications.Add($"visible={isVisible}");
            }

            // Set tooltip
            string tooltip = p.Get("tooltip");
            if (tooltip != null)
            {
                element.tooltip = tooltip;
                modifications.Add($"tooltip='{tooltip}'");
            }

            // Filter out [skipped] entries so they don't count as real modifications
            var applied = modifications.Where(m => !m.StartsWith("[skipped]")).ToList();
            var skipped = modifications.Where(m => m.StartsWith("[skipped]")).ToList();

            if (applied.Count == 0)
            {
                string msg =
                    skipped.Count > 0
                        ? $"No modifications applied. Skipped unsupported styles: {string.Join(", ", skipped)}"
                        : "No modifications specified. Provide at least one of: text, add_classes, remove_classes, toggle_classes, style, enabled, visible, tooltip.";
                return new ErrorResponse(msg, includeStyleResults ? new { style_results = inlineStyles.Report(element, visibility) } : null);
            }

            var responseData = new Dictionary<string, object>
            {
                { "gameObject", go.name },
                { "componentType", ui.Kind },
                { "elementName", elementName },
                { "elementType", element.GetType().Name },
                { "modifications", applied },
                { "currentClasses", new List<string>(element.GetClasses()) },
            };
            if (skipped.Count > 0)
                responseData["skipped"] = skipped;
            if (includeStyleResults)
                responseData["style_results"] = inlineStyles.Report(element, visibility);

            return new SuccessResponse($"Modified element '{elementName}' on {go.name}: {string.Join(", ", applied)}", responseData);
        }

        private static void RequireCanonicalSnapshotComponent(GameObject go, UIComponentAdapter ui)
        {
            if (ui.Component != UIComponentAdapter.Resolve(go, "auto").Component)
                throw new ArgumentException("element_id requires the snapshot's auto-selected UI component. Use element_path for a different component_type.");
        }

        private static VisualElement ResolveExplicitVisualElement(
            ToolParams parameters,
            GameObject go,
            UIComponentAdapter ui,
            VisualElement root,
            string nameGuard
        )
        {
            bool byPath = parameters.Has("element_path");
            if (byPath && parameters.Has("element_id"))
                throw new ArgumentException("Provide only one of element_path and element_id.");
            if (!byPath)
                RequireCanonicalSnapshotComponent(go, ui);
            string key = byPath ? "element_path" : "element_id";
            JToken token = parameters.GetRaw(key);
            if (token?.Type != JTokenType.String)
                throw new ArgumentException($"'{key}' must be a string.");
            string value = token.Value<string>();
            if (value.Length > 4096)
                throw new ArgumentException("Visual element selector exceeds the 4096 character bound.");
            string suffix = value;
            if (!byPath)
            {
                string rootId = CaptureUISnapshot.Identity(go);
                if (value == rootId)
                    suffix = "";
                else if (value.StartsWith(rootId + "/", StringComparison.Ordinal))
                    suffix = value.Substring(rootId.Length);
                else
                    throw new ArgumentException("element_id does not belong to the selected UI root's snapshot identity.");
            }
            if (suffix.Length != 0 && suffix[0] != '/')
                throw new ArgumentException("element_path must be empty for the root or start with '/'.");
            string[] segments = suffix.Length == 0 ? Array.Empty<string>() : suffix.Substring(1).Split('/');
            if (segments.Length >= 128)
                throw new ArgumentException("Visual element selector exceeds the 128 level bound, including the root.");
            VisualElement element = root;
            int examined = 0;
            foreach (string segment in segments)
            {
                // This is the snapshot's content-container enumeration, not control internals.
                if (element.childCount > 1000)
                    throw new ArgumentException("Visual element selector exceeds the 1000 sibling bound.");
                if (byPath)
                {
                    if (
                        segment.Length == 0
                        || (segment.Length > 1 && segment[0] == '0')
                        || segment.Any(c => c < '0' || c > '9')
                        || !int.TryParse(segment, NumberStyles.None, CultureInfo.InvariantCulture, out int index)
                    )
                        throw new ArgumentException("element_path requires canonical zero-based decimal child indices, for example '/0/1'.");
                    if (index >= element.childCount)
                        throw new ArgumentException("element_path does not exist in the selected UI root.");
                    element = element.ElementAt(index);
                }
                else
                {
                    VisualElement match = null;
                    for (int i = 0; i < element.childCount; i++)
                    {
                        if (++examined > 10000)
                            throw new ArgumentException("Visual element selector exceeds the 10000 node work bound.");
                        VisualElement candidate = element.ElementAt(i);
                        if (UitoolkitSnapshotCapture.Segment(candidate) != segment)
                            continue;
                        if (match != null)
                            throw new ArgumentException("element_id is ambiguous among the selected root's snapshot siblings.");
                        match = candidate;
                    }
                    element = match ?? throw new ArgumentException("element_id is stale or missing in the selected UI root.");
                }
            }
            if (nameGuard != null && element.name != nameGuard)
                throw new ArgumentException("element_name does not match the explicit selector's element.");
            // Bind the resolved managed element to this actual engine root; never search globally.
            VisualElement ancestor = element;
            for (int depth = 0; ancestor != null && depth < 128; depth++, ancestor = ancestor.hierarchy.parent)
                if (ReferenceEquals(ancestor, root))
                    return element;
            throw new ArgumentException("Explicit element is outside the selected root or exceeds the 128 ancestor bound.");
        }

        private static VisualElement ResolveUniqueVisualElement(VisualElement root, string name)
        {
            VisualElement match = null;
            var stack = new Stack<(VisualElement element, int nextChild)>();
            stack.Push((root, 0));
            int examined = 0;
            while (stack.Count > 0)
            {
                var frame = stack.Pop();
                if (frame.nextChild == 0)
                {
                    if (++examined > 10000 || stack.Count >= 128 || frame.element.hierarchy.childCount > 1000)
                        throw new ArgumentException("Strict name selection exceeds the 10000 node, 128 level or 1000 sibling bound.");
                    // UQuery includes the root and physical control internals. A content-container
                    // scan could miss a second match and incorrectly claim the name is unique.
                    if (frame.element.name == name)
                    {
                        if (match != null)
                            throw new ArgumentException("Visual element name is ambiguous; use element_path or element_id.");
                        match = frame.element;
                    }
                }
                if (frame.nextChild < frame.element.hierarchy.childCount)
                {
                    VisualElement child = frame.element.hierarchy[frame.nextChild++];
                    stack.Push(frame);
                    stack.Push((child, 0));
                }
            }
            return match;
        }

        private sealed class PreparedInlineStyle
        {
            public int Index;
            public string Key;
            public string Property;
            public string Reason;
            public string Modification;
            public JToken Assigned;
            public Action<VisualElement> Apply;
            public Func<VisualElement, JToken> Read;

            public JObject Receipt(string reason)
            {
                return new JObject
                {
                    ["index"] = Index,
                    ["key"] = Key,
                    ["property"] = Property,
                    ["reason"] = reason,
                };
            }
        }

        private sealed class PreparedInlineStyles
        {
            public readonly List<PreparedInlineStyle> Entries = new List<PreparedInlineStyle>();
            public string Error;
            public string RequestReason;
            public bool Aborted;

            public JObject Report(VisualElement element = null, bool? visible = null)
            {
                var applied = new JArray();
                var rejected = new JArray();
                foreach (var entry in Entries)
                {
                    if (entry.Apply == null || Aborted)
                    {
                        var refusal = entry.Receipt(entry.Apply == null ? entry.Reason : "request_aborted_before_mutation");
                        if (entry.Apply != null)
                            refusal["prepared_value"] = entry.Assigned.DeepClone();
                        rejected.Add(refusal);
                        continue;
                    }
                    var receipt = entry.Receipt(entry.Reason);
                    receipt["assigned_value"] = entry.Assigned.DeepClone();
                    receipt["final_inline_value"] = entry.Read(element);
                    var last = Entries.Last(candidate => candidate.Property == entry.Property && candidate.Apply != null);
                    if (entry.Property == "display" && visible.HasValue)
                        receipt["superseded_by"] = new JObject { ["parameter"] = "visible" };
                    else if (last != entry)
                        receipt["superseded_by"] = new JObject { ["index"] = last.Index, ["key"] = last.Key };
                    applied.Add(receipt);
                }
                var report = new JObject
                {
                    ["schema_version"] = 1,
                    ["applied"] = applied,
                    ["rejected"] = rejected,
                };
                if (RequestReason != null)
                    report["request_reason"] = RequestReason;
                return report;
            }
        }

        private static string InlineStyleProperty(string key)
        {
            switch (key.ToLowerInvariant())
            {
                case "backgroundcolor":
                case "background-color":
                    return "backgroundColor";
                case "color":
                    return "color";
                case "fontsize":
                case "font-size":
                    return "fontSize";
                case "width":
                    return "width";
                case "height":
                    return "height";
                case "opacity":
                    return "opacity";
                case "display":
                    return "display";
                case "visibility":
                    return "visibility";
                case "flexgrow":
                case "flex-grow":
                    return "flexGrow";
                case "flexshrink":
                case "flex-shrink":
                    return "flexShrink";
                case "marginleft":
                case "margin-left":
                    return "marginLeft";
                case "marginright":
                case "margin-right":
                    return "marginRight";
                case "margintop":
                case "margin-top":
                    return "marginTop";
                case "marginbottom":
                case "margin-bottom":
                    return "marginBottom";
                case "paddingleft":
                case "padding-left":
                    return "paddingLeft";
                case "paddingright":
                case "padding-right":
                    return "paddingRight";
                case "paddingtop":
                case "padding-top":
                    return "paddingTop";
                case "paddingbottom":
                case "padding-bottom":
                    return "paddingBottom";
                case "borderradius":
                case "border-radius":
                    return "borderRadius";
                default:
                    return null;
            }
        }

        private static bool InlineStyleTextTooLong(JToken token)
        {
            if (token is JValue scalar)
                return scalar.Type == JTokenType.String && scalar.Value<string>().Length > 256;
            if (token is JObject typed)
                return new[] { "value", "unit", "keyword" }.Any(key =>
                    typed[key] is JValue value && value.Type == JTokenType.String && value.Value<string>().Length > 256
                );
            return false;
        }

        private static PreparedInlineStyles PrepareInlineStyles(JToken token, bool strict)
        {
            var prepared = new PreparedInlineStyles();
            if (token == null)
                return prepared;
            if (token is not JObject styles)
            {
                prepared.RequestReason = "invalid_style_object";
                if (strict)
                    prepared.Error = "strict_style_rejected";
                prepared.Aborted = strict;
                return prepared;
            }
            if (styles.Count > 64 || styles.Properties().Any(prop => prop.Name.Length > 128 || InlineStyleTextTooLong(prop.Value)))
            {
                prepared.Error = "inline_style_limit_exceeded";
                prepared.RequestReason = "inline_style_limit_exceeded";
                prepared.Aborted = true;
                return prepared;
            }
            foreach (var prop in styles.Properties())
            {
                var entry = new PreparedInlineStyle
                {
                    Index = prepared.Entries.Count,
                    Key = prop.Name,
                    Property = InlineStyleProperty(prop.Name),
                    Reason = "assigned",
                };
                prepared.Entries.Add(entry);
                if (entry.Property == null)
                {
                    entry.Reason = "unsupported_property";
                    entry.Modification = $"[skipped] {prop.Name} (unsupported inline style)";
                    continue;
                }
                try
                {
                    PrepareInlineStyle(entry, prop.Value, strict);
                }
                catch (ArgumentException error)
                {
                    entry.Apply = null;
                    entry.Reason = "malformed_numeric";
                    if (prepared.Error == null)
                        prepared.Error = error.Message;
                }
            }
            if (prepared.Error == null && strict && prepared.Entries.Any(entry => entry.Apply == null))
                prepared.Error = "strict_style_rejected";
            prepared.Aborted = prepared.Error != null;
            return prepared;
        }

        private static bool InlineObjectKeys(JObject value, params string[] keys)
        {
            return value.Count == keys.Length && keys.All(key => value.Property(key) != null);
        }

        private static void PrepareInlineStyle(PreparedInlineStyle entry, JToken value, bool strict)
        {
            var typed = value as JObject;
            bool color = entry.Property == "color" || entry.Property == "backgroundColor";
            bool enumeration = entry.Property == "display" || entry.Property == "visibility";
            if (color || enumeration)
            {
                if (typed != null && !InlineObjectKeys(typed, "value"))
                {
                    entry.Reason = "invalid_typed_value";
                    return;
                }
                var scalar = typed == null ? value : typed["value"];
                if (typed != null && scalar.Type != JTokenType.String)
                {
                    entry.Reason = color ? "invalid_color" : "invalid_enum";
                    return;
                }
                // Legacy scalars keep their original HTML/Enum.TryParse semantics.
                string text = scalar is JValue ? scalar.ToString() : "";
                if (color)
                {
                    if (!ColorUtility.TryParseHtmlString(text, out Color parsed))
                    {
                        entry.Reason = "invalid_color";
                        return;
                    }
                    entry.Assigned = InlineColorValue(parsed);
                    entry.Apply = element =>
                    {
                        if (entry.Property == "color")
                            element.style.color = parsed;
                        else
                            element.style.backgroundColor = parsed;
                    };
                    entry.Read = element => InlineColorValue(entry.Property == "color" ? element.style.color.value : element.style.backgroundColor.value);
                }
                else if (entry.Property == "display")
                {
                    if (
                        !Enum.TryParse<DisplayStyle>(text, true, out var parsed) || ((strict || typed != null) && !Enum.IsDefined(typeof(DisplayStyle), parsed))
                    )
                    {
                        entry.Reason = "invalid_enum";
                        return;
                    }
                    if (!Enum.IsDefined(typeof(DisplayStyle), parsed))
                        entry.Reason = "legacy_undefined_enum";
                    entry.Assigned = new JValue(parsed.ToString());
                    entry.Apply = element => element.style.display = parsed;
                    entry.Read = element => new JValue(element.style.display.value.ToString());
                }
                else
                {
                    if (!Enum.TryParse<Visibility>(text, true, out var parsed) || ((strict || typed != null) && !Enum.IsDefined(typeof(Visibility), parsed)))
                    {
                        entry.Reason = "invalid_enum";
                        return;
                    }
                    if (!Enum.IsDefined(typeof(Visibility), parsed))
                        entry.Reason = "legacy_undefined_enum";
                    entry.Assigned = new JValue(parsed.ToString());
                    entry.Apply = element => element.style.visibility = parsed;
                    entry.Read = element => new JValue(element.style.visibility.value.ToString());
                }
                entry.Modification = $"{entry.Property}={text}";
                return;
            }

            bool unitless = entry.Property == "opacity" || entry.Property == "flexGrow" || entry.Property == "flexShrink";
            bool auto = entry.Property == "width" || entry.Property == "height" || entry.Property.StartsWith("margin", StringComparison.Ordinal);
            if (typed != null && typed.Property("value") != null && (typed["value"].Type != JTokenType.Integer && typed["value"].Type != JTokenType.Float))
                throw new ArgumentException("Invalid typed numeric inline style value.");
            // Validate every supplied numeric value before shape/keyword/unit refusal.
            if (typed != null && typed.Property("value") != null)
                typed["value"].ReadScalar<float>();
            if (typed != null && typed.Property("value") == null && typed.Property("keyword") == null)
                value.ReadScalar<float>(); // Preserve the legacy malformed-object refusal.
            float numeric = 0;
            var length = new StyleLength(0f);
            if (typed != null && typed.Property("keyword") != null)
            {
                if (!InlineObjectKeys(typed, "keyword"))
                    entry.Reason = "invalid_typed_value";
                else if (!auto || typed["keyword"].Type != JTokenType.String || typed.Value<string>("keyword") != "auto")
                    entry.Reason = "unsupported_keyword";
                else
                    length = new StyleLength(StyleKeyword.Auto);
                if (entry.Reason != "assigned")
                    return;
            }
            else
            {
                // Missing/null remains zero only for the legacy scalar form.
                JToken scalar = typed == null ? value : typed["value"];
                if (typed != null && (scalar == null || (scalar.Type != JTokenType.Integer && scalar.Type != JTokenType.Float)))
                    throw new ArgumentException("Invalid typed numeric inline style value.");
                numeric = scalar.ReadScalar<float>();
                if (typed != null)
                {
                    if (!InlineObjectKeys(typed, unitless ? new[] { "value" } : new[] { "value", "unit" }))
                    {
                        entry.Reason = "invalid_typed_value";
                        return;
                    }
                    if (
                        !unitless
                        && (
                            typed["unit"].Type != JTokenType.String
                            || (typed.Value<string>("unit") != "px" && typed.Value<string>("unit") != "%")
                            || (entry.Property == "fontSize" && typed.Value<string>("unit") != "px")
                        )
                    )
                    {
                        entry.Reason = "unsupported_unit";
                        return;
                    }
                }
                length = new StyleLength(new Length(numeric, typed != null && typed.Value<string>("unit") == "%" ? LengthUnit.Percent : LengthUnit.Pixel));
            }
            if (unitless)
            {
                entry.Assigned = new JValue(numeric);
                entry.Apply = element =>
                {
                    switch (entry.Property)
                    {
                        case "opacity":
                            element.style.opacity = numeric;
                            break;
                        case "flexGrow":
                            element.style.flexGrow = numeric;
                            break;
                        case "flexShrink":
                            element.style.flexShrink = numeric;
                            break;
                    }
                };
                entry.Read = element => new JValue(
                    entry.Property == "opacity" ? element.style.opacity.value
                    : entry.Property == "flexGrow" ? element.style.flexGrow.value
                    : element.style.flexShrink.value
                );
            }
            else
            {
                entry.Assigned = InlineLengthValue(length);
                entry.Apply = element => AssignInlineLength(element, entry.Property, length);
                entry.Read = element =>
                    entry.Property == "borderRadius"
                        ? new JObject
                        {
                            ["top_left"] = InlineLengthValue(element.style.borderTopLeftRadius),
                            ["top_right"] = InlineLengthValue(element.style.borderTopRightRadius),
                            ["bottom_left"] = InlineLengthValue(element.style.borderBottomLeftRadius),
                            ["bottom_right"] = InlineLengthValue(element.style.borderBottomRightRadius),
                        }
                        : InlineLengthValue(ReadInlineLength(element, entry.Property));
            }
            entry.Modification = typed == null ? $"{entry.Property}={value}" : $"{entry.Property}={entry.Assigned.ToString(Newtonsoft.Json.Formatting.None)}";
        }

        private static JToken InlineColorValue(Color color)
        {
            return new JValue("#" + ColorUtility.ToHtmlStringRGBA(color));
        }

        private static JToken InlineLengthValue(StyleLength length)
        {
            if (length.keyword != StyleKeyword.Undefined)
                return new JObject { ["keyword"] = length.keyword.ToString().ToLowerInvariant() };
            return new JObject { ["value"] = length.value.value, ["unit"] = length.value.unit == LengthUnit.Percent ? "%" : "px" };
        }

        private static void AssignInlineLength(VisualElement element, string property, StyleLength value)
        {
            switch (property)
            {
                case "width":
                    element.style.width = value;
                    break;
                case "height":
                    element.style.height = value;
                    break;
                case "fontSize":
                    element.style.fontSize = value;
                    break;
                case "marginLeft":
                    element.style.marginLeft = value;
                    break;
                case "marginRight":
                    element.style.marginRight = value;
                    break;
                case "marginTop":
                    element.style.marginTop = value;
                    break;
                case "marginBottom":
                    element.style.marginBottom = value;
                    break;
                case "paddingLeft":
                    element.style.paddingLeft = value;
                    break;
                case "paddingRight":
                    element.style.paddingRight = value;
                    break;
                case "paddingTop":
                    element.style.paddingTop = value;
                    break;
                case "paddingBottom":
                    element.style.paddingBottom = value;
                    break;
                case "borderRadius":
                    element.style.borderTopLeftRadius = value;
                    element.style.borderTopRightRadius = value;
                    element.style.borderBottomLeftRadius = value;
                    element.style.borderBottomRightRadius = value;
                    break;
            }
        }

        private static StyleLength ReadInlineLength(VisualElement element, string property)
        {
            switch (property)
            {
                case "width":
                    return element.style.width;
                case "height":
                    return element.style.height;
                case "fontSize":
                    return element.style.fontSize;
                case "marginLeft":
                    return element.style.marginLeft;
                case "marginRight":
                    return element.style.marginRight;
                case "marginTop":
                    return element.style.marginTop;
                case "marginBottom":
                    return element.style.marginBottom;
                case "paddingLeft":
                    return element.style.paddingLeft;
                case "paddingRight":
                    return element.style.paddingRight;
                case "paddingTop":
                    return element.style.paddingTop;
                case "paddingBottom":
                    return element.style.paddingBottom;
                case "borderRadius":
                    return element.style.borderBottomRightRadius;
                default:
                    throw new InvalidOperationException("Unsupported prepared inline length.");
            }
        }

        private static bool? GetNullableBool(this ToolParams p, string key)
        {
            return p.GetRaw(key).ReadScalar<bool?>();
        }

        /// <summary>
        /// Finds the index right after the closing '>' of the root UXML element opening tag.
        /// Returns -1 if not found or if the root tag is self-closing.
        /// </summary>
        private static int FindUxmlBodyStart(string content)
        {
            int end = FindUxmlRootTagEnd(content);
            return end < 0 || content[end - 1] == '/' ? -1 : end + 1;
        }

        private static int FindUxmlRootTagEnd(string content)
        {
            int searchFrom = 0;
            while (true)
            {
                int start = content.IndexOf('<', searchFrom);
                if (start < 0)
                    return -1;
                if (content.IndexOf("<!--", start, StringComparison.Ordinal) == start)
                {
                    int end = content.IndexOf("-->", start + 4, StringComparison.Ordinal);
                    if (end < 0)
                        return -1;
                    searchFrom = end + 3;
                    continue;
                }
                if (content.IndexOf("<?", start, StringComparison.Ordinal) == start)
                {
                    int end = content.IndexOf("?>", start + 2, StringComparison.Ordinal);
                    if (end < 0)
                        return -1;
                    searchFrom = end + 2;
                    continue;
                }

                int nameEnd = start + 1;
                while (nameEnd < content.Length && !char.IsWhiteSpace(content[nameEnd]) && content[nameEnd] != '/' && content[nameEnd] != '>')
                    nameEnd++;
                string name = content.Substring(start + 1, nameEnd - start - 1);
                if (!name.Equals("ui:UXML", StringComparison.OrdinalIgnoreCase) && !name.Equals("UXML", StringComparison.OrdinalIgnoreCase))
                    return -1;

                char quote = '\0';
                for (int i = nameEnd; i < content.Length; i++)
                {
                    char current = content[i];
                    if (quote != '\0')
                    {
                        if (current == quote)
                            quote = '\0';
                    }
                    else if (current == '\'' || current == '"')
                        quote = current;
                    else if (current == '>')
                        return i;
                }
                return -1;
            }
        }

        private static string CreateStylesheetTag(string stylesheetPath, string tagName)
        {
            var attribute = new XmlDocument().CreateAttribute("src");
            attribute.Value = "project://database/" + stylesheetPath;
            return $"\n    <{tagName} {attribute.OuterXml} />";
        }

        // ---- Helpers ----

        private static string EnsureUniqueFilePath(string path)
        {
            if (!File.Exists(path))
                return path;
            string dir = Path.GetDirectoryName(path) ?? string.Empty;
            string baseName = Path.GetFileNameWithoutExtension(path);
            string ext = Path.GetExtension(path);
            int counter = 1;
            string candidate;
            do
            {
                candidate = Path.Combine(dir, $"{baseName}-{counter}{ext}").Replace('\\', '/');
                counter++;
            } while (File.Exists(candidate));
            return candidate;
        }

        private static string ColorToHex(Color c)
        {
            return $"#{ColorUtility.ToHtmlStringRGBA(c)}";
        }

        private static void ValidateTextParameter(ToolParams p, string key)
        {
            var token = p.GetRaw(key);
            // Transport parsing can expose an original JSON string as Date.
            // Keep legacy string conversion and explicit-null behavior for those values.
            if (token != null && token.Type != JTokenType.String && token.Type != JTokenType.Date && token.Type != JTokenType.Null)
                throw new ArgumentException($"Parameter '{key}' must be a string.");
        }

        private static string GetDecodedContents(ToolParams p)
        {
            bool isEncoded = p.GetBool("contents_encoded") || p.GetBool("contentsEncoded");

            if (isEncoded)
            {
                ValidateTextParameter(p, "encoded_contents");
                string encoded = p.Get("encoded_contents") ?? p.Get("encodedContents");
                if (encoded != null)
                {
                    try
                    {
                        return Encoding.UTF8.GetString(Convert.FromBase64String(encoded));
                    }
                    catch (FormatException ex)
                    {
                        throw new ArgumentException("Parameter 'encodedContents' must be valid base64 when 'contentsEncoded' is true.", ex);
                    }
                }
            }

            ValidateTextParameter(p, "contents");
            return p.Get("contents");
        }

        /// <summary>
        /// Validates UXML content before writing to disk.
        /// Returns null if valid, or an error message if malformed.
        /// Populates warnings list with non-fatal issues.
        /// Uses XmlParserContext to pre-declare common UXML namespace prefixes
        /// (ui, uie, engine, editor) since Unity's parser is more lenient than System.Xml.
        /// </summary>
        /// <summary>
        /// Ensures the root UXML element has editor-extension-mode attribute.
        /// UI Builder requires this to open the file. Injects "False" if missing.
        /// </summary>
        private static string EnsureEditorExtensionMode(string contents)
        {
            return ReadUxmlContent(contents, new List<string>(), out var document) == null ? AddEditorExtensionMode(contents, document) : contents;
        }

        private static string AddEditorExtensionMode(string contents, UxmlContentInfo document)
        {
            if (document.HasEditorExtensionMode || (document.RootName != "ui:UXML" && document.RootName != "UXML"))
                return contents;

            int end = FindUxmlRootTagEnd(contents);
            if (end < 0)
                return contents;
            int insertPos = contents[end - 1] == '/' ? end - 1 : end;
            return contents.Substring(0, insertPos) + " editor-extension-mode=\"False\"" + contents.Substring(insertPos);
        }

        private sealed class UxmlContentInfo
        {
            public string RootName;
            public bool HasEditorExtensionMode;
            public bool StylesheetLinked;
            public string StyleTagName;
        }

        private static string ValidateUxmlContent(string contents, List<string> warnings)
        {
            return ReadUxmlContent(contents, warnings, out _);
        }

        private static string ReadUxmlContent(string contents, List<string> warnings, out UxmlContentInfo document, string stylesheetPath = null)
        {
            document = null;
            if (string.IsNullOrWhiteSpace(contents))
                return "UXML content is empty.";

            var nt = new NameTable();
            var nsMgr = new XmlNamespaceManager(nt);
            nsMgr.AddNamespace("ui", "UnityEngine.UIElements");
            nsMgr.AddNamespace("uie", "UnityEditor.UIElements");
            nsMgr.AddNamespace("engine", "UnityEngine.UIElements");
            nsMgr.AddNamespace("editor", "UnityEditor.UIElements");
            var ctx = new XmlParserContext(nt, nsMgr, null, XmlSpace.Default);

            string rootLocalName = null;
            try
            {
                using (var reader = XmlReader.Create(new StringReader(contents), null, ctx))
                {
                    while (reader.Read())
                    {
                        if (reader.NodeType != XmlNodeType.Element)
                            continue;
                        if (rootLocalName == null)
                        {
                            rootLocalName = reader.LocalName;
                            document = new UxmlContentInfo
                            {
                                RootName = reader.Name,
                                HasEditorExtensionMode = reader.GetAttribute("editor-extension-mode") != null,
                                // XmlParserContext supplies lenient prefixes, but newly inserted
                                // tags must not depend on a binding absent from the actual document.
                                StyleTagName = reader.GetAttribute("xmlns:ui") == "UnityEngine.UIElements" ? "ui:Style" : "Style",
                            };
                        }
                        if (
                            stylesheetPath != null
                            && reader.Depth == 1
                            && reader.LocalName == "Style"
                            && (reader.NamespaceURI.Length == 0 || reader.NamespaceURI == "UnityEngine.UIElements")
                        )
                        {
                            string source = reader.GetAttribute("src");
                            if (source == stylesheetPath || source == "project://database/" + stylesheetPath)
                                document.StylesheetLinked = true;
                        }
                    }
                }
            }
            catch (XmlException ex)
            {
                return $"Malformed XML at line {ex.LineNumber}, position {ex.LinePosition}: {ex.Message}";
            }

            if (rootLocalName == null)
                return "UXML content has no root element.";

            if (rootLocalName != "UXML")
                warnings.Add($"Root element is <{rootLocalName}>, expected <UXML> or <ui:UXML>.");

            if (!contents.Contains("UnityEngine.UIElements"))
            {
                warnings.Add("Missing namespace declaration xmlns:ui=\"UnityEngine.UIElements\". " + "UI Builder may fail to open this file.");
            }

            return null;
        }

        /// <summary>
        /// Validates a UXML asset after import by attempting to load it as a VisualTreeAsset.
        /// </summary>
        private static void ValidateUxmlPostImport(string assetPath, List<string> warnings)
        {
            var asset = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(assetPath);
            if (asset == null)
            {
                warnings.Add(
                    "Unity failed to parse the UXML file. "
                        + "The file was written but UI Builder will not be able to open it. "
                        + "Check the console for details."
                );
            }
        }
    }
}
