using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows
{
    /// <summary>
    /// Editor window for managing Unity EditorPrefs, specifically for MCP for Unity development
    /// </summary>
    public class EditorPrefsWindow : EditorWindow
    {
        // UI Elements
        private ScrollView scrollView;
        private VisualElement prefsContainer;
        private TextField searchField;
        private Label resultCount;
        private Label emptyState;
        private string searchFilter = "";

        // Data
        private List<EditorPrefItem> currentPrefs = new List<EditorPrefItem>();
        private readonly Dictionary<string, VisualElement> prefRows = new Dictionary<string, VisualElement>();
        private HashSet<string> knownMcpKeys = new HashSet<string>();
        private Action<string> showSaveError = message => EditorUtility.DisplayDialog("Error", message, "OK");

        private static readonly Dictionary<string, string> cachedPrefNames = new Dictionary<string, string>
        {
            { EditorPrefKeys.UseHttpTransport, nameof(EditorConfigurationCache.UseHttpTransport) },
            { EditorPrefKeys.DebugLogs, nameof(EditorConfigurationCache.DebugLogs) },
            { EditorPrefKeys.DevModeForceServerRefresh, nameof(EditorConfigurationCache.DevModeForceServerRefresh) },
            { EditorPrefKeys.UvxPathOverride, nameof(EditorConfigurationCache.UvxPathOverride) },
            { EditorPrefKeys.GitUrlOverride, nameof(EditorConfigurationCache.GitUrlOverride) },
            { EditorPrefKeys.HttpBaseUrl, nameof(EditorConfigurationCache.HttpBaseUrl) },
            { EditorPrefKeys.HttpRemoteBaseUrl, nameof(EditorConfigurationCache.HttpRemoteBaseUrl) },
            { EditorPrefKeys.ClaudeCliPathOverride, nameof(EditorConfigurationCache.ClaudeCliPathOverride) },
            { EditorPrefKeys.HttpTransportScope, nameof(EditorConfigurationCache.HttpTransportScope) },
            { EditorPrefKeys.UnitySocketPort, nameof(EditorConfigurationCache.UnitySocketPort) },
        };

        // Type mapping for known EditorPrefs
        private readonly Dictionary<string, EditorPrefType> knownPrefTypes = new Dictionary<string, EditorPrefType>
        {
            // Boolean prefs
            { EditorPrefKeys.DebugLogs, EditorPrefType.Bool },
            { EditorPrefKeys.UseHttpTransport, EditorPrefType.Bool },
            { EditorPrefKeys.ResumeStdioAfterReload, EditorPrefType.Bool },
            { EditorPrefKeys.UseEmbeddedServer, EditorPrefType.Bool },
            { EditorPrefKeys.LockCursorConfig, EditorPrefType.Bool },
            { EditorPrefKeys.AutoRegisterEnabled, EditorPrefType.Bool },
            { EditorPrefKeys.SetupCompleted, EditorPrefType.Bool },
            { EditorPrefKeys.SetupDismissed, EditorPrefType.Bool },
            { EditorPrefKeys.CustomToolRegistrationEnabled, EditorPrefType.Bool },
            { EditorPrefKeys.TelemetryDisabled, EditorPrefType.Bool },
            { EditorPrefKeys.DevModeForceServerRefresh, EditorPrefType.Bool },
            { EditorPrefKeys.ProjectScopedToolsLocalHttp, EditorPrefType.Bool },
            { EditorPrefKeys.AllowLanHttpBind, EditorPrefType.Bool },
            { EditorPrefKeys.AllowInsecureRemoteHttp, EditorPrefType.Bool },
            { EditorPrefKeys.ClientDetailsFoldoutOpen, EditorPrefType.Bool },
            { EditorPrefKeys.AutoStartOnLoad, EditorPrefType.Bool },
            { EditorPrefKeys.HttpServerLaunchConfirmed, EditorPrefType.Bool },
            { EditorPrefKeys.LogRecordEnabled, EditorPrefType.Bool },
            { EditorPrefKeys.AssetGenAutoNormalize, EditorPrefType.Bool },
            // Integer prefs
            { EditorPrefKeys.UnitySocketPort, EditorPrefType.Int },
            { EditorPrefKeys.ValidationLevel, EditorPrefType.Int },
            { EditorPrefKeys.LastUpdateCheck, EditorPrefType.String },
            { EditorPrefKeys.LastStdIoUpgradeVersion, EditorPrefType.Int },
            { EditorPrefKeys.LastLocalHttpServerPid, EditorPrefType.Int },
            { EditorPrefKeys.LastLocalHttpServerPort, EditorPrefType.Int },
            { EditorPrefKeys.BatchExecuteMaxCommands, EditorPrefType.Int },
            { EditorPrefKeys.BlenderPort, EditorPrefType.Int },
            // String prefs
            { EditorPrefKeys.EditorWindowActivePanel, EditorPrefType.String },
            { EditorPrefKeys.ClaudeCliPathOverride, EditorPrefType.String },
            { EditorPrefKeys.UvxPathOverride, EditorPrefType.String },
            { EditorPrefKeys.HttpBaseUrl, EditorPrefType.String },
            { EditorPrefKeys.HttpRemoteBaseUrl, EditorPrefType.String },
            { EditorPrefKeys.HttpTransportScope, EditorPrefType.String },
            { EditorPrefKeys.SessionId, EditorPrefType.String },
            { EditorPrefKeys.WebSocketUrlOverride, EditorPrefType.String },
            { EditorPrefKeys.GitUrlOverride, EditorPrefType.String },
            { EditorPrefKeys.PackageDeploySourcePath, EditorPrefType.String },
            { EditorPrefKeys.PackageDeployLastBackupPath, EditorPrefType.String },
            { EditorPrefKeys.PackageDeployLastTargetPath, EditorPrefType.String },
            { EditorPrefKeys.PackageDeployLastSourcePath, EditorPrefType.String },
            { EditorPrefKeys.ServerSrc, EditorPrefType.String },
            { EditorPrefKeys.LastSelectedClientId, EditorPrefType.String },
            { EditorPrefKeys.LatestKnownVersion, EditorPrefType.String },
            { EditorPrefKeys.LastAssetStoreUpdateCheck, EditorPrefType.String },
            { EditorPrefKeys.LatestKnownAssetStoreVersion, EditorPrefType.String },
            { EditorPrefKeys.LastLocalHttpServerStartedUtc, EditorPrefType.String },
            { EditorPrefKeys.LastLocalHttpServerPidArgsHash, EditorPrefType.String },
            { EditorPrefKeys.LastLocalHttpServerPidFilePath, EditorPrefType.String },
            { EditorPrefKeys.LastLocalHttpServerInstanceToken, EditorPrefType.String },
        };

        // Templates
        private VisualTreeAsset itemTemplate;

        /// <summary>
        /// Show the EditorPrefs window
        /// </summary>
        public static void ShowWindow()
        {
            var window = GetWindow<EditorPrefsWindow>("EditorPrefs");
            window.minSize = new Vector2(600, 400);
            window.Show();
        }

        public void CreateGUI()
        {
            // Clear search filter on GUI recreation to avoid stale filtered results
            searchFilter = "";

            string basePath = AssetPathUtility.GetMcpPackageRootPath();

            // Load UXML
            var visualTree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>($"{basePath}/Editor/Windows/EditorPrefs/EditorPrefsWindow.uxml");

            if (visualTree == null)
            {
                McpLog.Error("Failed to load EditorPrefsWindow.uxml template");
                return;
            }

            // Load item template
            itemTemplate = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>($"{basePath}/Editor/Windows/EditorPrefs/EditorPrefItem.uxml");

            if (itemTemplate == null)
            {
                McpLog.Error("Failed to load EditorPrefItem.uxml template");
                return;
            }

            rootVisualElement.Clear();
            rootVisualElement.AddToClassList("mcp-editor");
            rootVisualElement.EnableInClassList("unity-theme-light", !EditorGUIUtility.isProSkin);
            rootVisualElement.EnableInClassList("unity-theme-dark", EditorGUIUtility.isProSkin);
            visualTree.CloneTree(rootVisualElement);

            searchField = rootVisualElement.Q<TextField>("search-field");
            searchField.SetValueWithoutNotify(searchFilter);
            searchField.RegisterValueChangedCallback(evt =>
            {
                searchFilter = evt.newValue ?? "";
                ApplyFilter();
            });
            rootVisualElement.Q<Button>("refresh-button").clicked += RefreshPrefs;

            // Get references
            scrollView = rootVisualElement.Q<ScrollView>("scroll-view");
            prefsContainer = rootVisualElement.Q<VisualElement>("prefs-container");
            resultCount = rootVisualElement.Q<Label>("result-count");
            emptyState = rootVisualElement.Q<Label>("empty-state");

            // Load known MCP keys
            LoadKnownMcpKeys();

            // Load initial data
            RefreshPrefs();
        }

        private void LoadKnownMcpKeys()
        {
            knownMcpKeys.Clear();
            var fields = typeof(EditorPrefKeys).GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);

            foreach (var field in fields)
            {
                if (field.IsLiteral && !field.IsInitOnly)
                {
                    knownMcpKeys.Add(field.GetValue(null).ToString());
                }
            }
        }

        internal static bool IsSensitiveKey(string key) =>
            key == EditorPrefKeys.ApiKey
            || key == EditorPrefKeys.CustomerUuid
            || key == EditorPrefKeys.SessionId
            || key == EditorPrefKeys.LastLocalHttpServerInstanceToken;

        private static string InitialValue(EditorPrefItem item) =>
            !item.IsUnset ? item.Value
            : item.Type == EditorPrefType.Bool ? "false"
            : item.Type == EditorPrefType.Int || item.Type == EditorPrefType.Float ? "0"
            : string.Empty;

        private void RefreshPrefs()
        {
            var drafts = new Dictionary<string, (string value, EditorPrefType type)>();
            foreach (var item in currentPrefs)
            {
                if (!prefRows.TryGetValue(item.Key, out var row) || IsSensitiveKey(item.Key))
                    continue;
                var value = row.Q<TextField>("value-field").value;
                var type = (EditorPrefType)row.Q<DropdownField>("type-dropdown").index;
                if (value != InitialValue(item) || type != item.Type)
                    drafts[item.Key] = (value, type);
            }
            currentPrefs.Clear();
            prefRows.Clear();
            prefsContainer.Clear();

            // Get all EditorPrefs keys
            var allKeys = new List<string>();

            // Always show all MCP keys
            allKeys.AddRange(knownMcpKeys);

            // Try to find additional MCP keys
            var mcpKeys = GetAllMcpKeys();
            foreach (var key in mcpKeys)
            {
                if (!allKeys.Contains(key))
                {
                    allKeys.Add(key);
                }
            }

            // Sort keys
            allKeys.Sort();

            // Create items for existing prefs
            foreach (var key in allKeys)
            {
                // Exclude secrets before reading EditorPrefs or creating controls.
                if (!IsSensitiveKey(key))
                {
                    var item = CreateEditorPrefItem(key);
                    if (item != null)
                    {
                        currentPrefs.Add(item);
                        var row = CreateItemUI(item);
                        if (drafts.TryGetValue(key, out var draft))
                        {
                            row.Q<TextField>("value-field").SetValueWithoutNotify(draft.value);
                            if (!knownPrefTypes.ContainsKey(key))
                                row.Q<DropdownField>("type-dropdown").index = (int)draft.type;
                            UpdateDraftState(item, row);
                        }
                        prefRows.Add(item.Key, row);
                        prefsContainer.Add(row);
                    }
                }
            }
            ApplyFilter();
        }

        private void ApplyFilter()
        {
            var filter = searchFilter.Trim();
            int visibleCount = 0;
            // Keep the same fields alive so filtering preserves pending value and type edits.
            foreach (var item in currentPrefs)
            {
                bool visible = filter.Length == 0 || item.Key.IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0;
                prefRows[item.Key].EnableInClassList("pref-hidden", !visible);
                if (visible)
                {
                    visibleCount++;
                }
            }
            resultCount.text = $"{visibleCount} / {currentPrefs.Count}";
            emptyState.EnableInClassList("pref-hidden", visibleCount != 0);
            emptyState.text = currentPrefs.Count == 0 ? "No preferences available." : "No matching preferences.";
        }

        private List<string> GetAllMcpKeys()
        {
            // This is a simplified approach - in reality, getting all EditorPrefs is platform-specific
            // For now, we'll return known MCP keys that might exist
            var keys = new List<string>();

            // Add some common MCP keys that might not be in EditorPrefKeys
            keys.Add("MCPForUnity.TestKey");

            // Filter to only those that actually exist
            return keys.Where(EditorPrefs.HasKey).ToList();
        }

        private EditorPrefItem CreateEditorPrefItem(string key)
        {
            if (IsSensitiveKey(key))
                return null;
            var item = new EditorPrefItem { Key = key, IsKnown = knownMcpKeys.Contains(key) };

            // Check if we know the type of this pref
            if (knownPrefTypes.TryGetValue(key, out var knownType))
            {
                // Check if the key actually exists
                item.IsUnset = !EditorPrefs.HasKey(key);

                // Use the known type
                switch (knownType)
                {
                    case EditorPrefType.Bool:
                        item.Type = EditorPrefType.Bool;
                        item.Value = item.IsUnset ? "Unset" : EditorPrefs.GetBool(key, false).ToString();
                        break;
                    case EditorPrefType.Int:
                        item.Type = EditorPrefType.Int;
                        item.Value = item.IsUnset ? "Unset" : EditorPrefs.GetInt(key, 0).ToString();
                        break;
                    case EditorPrefType.Float:
                        item.Type = EditorPrefType.Float;
                        item.Value = item.IsUnset ? "Unset" : EditorPrefs.GetFloat(key, 0f).ToString();
                        break;
                    case EditorPrefType.String:
                        item.Type = EditorPrefType.String;
                        item.Value = item.IsUnset ? "Unset" : EditorPrefs.GetString(key, "");
                        break;
                }
            }
            else
            {
                // Unknown keys have no schema; preserve their string content without coercion.
                if (!EditorPrefs.HasKey(key))
                {
                    // Key doesn't exist and we don't know its type, skip it
                    return null;
                }

                item.Type = EditorPrefType.String;
                item.Value = EditorPrefs.GetString(key, "");
            }

            return item;
        }

        private VisualElement CreateItemUI(EditorPrefItem item)
        {
            if (IsSensitiveKey(item.Key))
                return new Label("Manage credentials in Connect or Generation.");
            if (itemTemplate == null)
            {
                McpLog.Error("Item template not loaded");
                return new VisualElement();
            }

            var itemElement = itemTemplate.CloneTree();

            // Set values
            var keyLabel = itemElement.Q<Label>("key-label");
            keyLabel.text = item.Key;
            keyLabel.tooltip = item.Key;
            var valueField = itemElement.Q<TextField>("value-field");
            valueField.SetValueWithoutNotify(InitialValue(item));

            var typeDropdown = itemElement.Q<DropdownField>("type-dropdown");
            typeDropdown.index = (int)item.Type;
            typeDropdown.SetEnabled(!knownPrefTypes.ContainsKey(item.Key));

            // Buttons
            var saveButton = itemElement.Q<Button>("save-button");

            valueField.RegisterValueChangedCallback(_ => UpdateDraftState(item, itemElement));
            typeDropdown.RegisterValueChangedCallback(_ => UpdateDraftState(item, itemElement));
            saveButton.clicked += () => SavePref(item, valueField.value, (EditorPrefType)typeDropdown.index);
            var reset = itemElement.Q<Button>("reset-button");
            reset.SetEnabled(!item.IsUnset);
            reset.clicked += () => ResetPref(item);
            UpdateDraftState(item, itemElement);

            return itemElement;
        }

        private static void UpdateDraftState(EditorPrefItem item, VisualElement row)
        {
            var value = row.Q<TextField>("value-field");
            var type = (EditorPrefType)row.Q<DropdownField>("type-dropdown").index;
            bool dirty = !string.Equals(value.value, InitialValue(item), StringComparison.Ordinal) || type != item.Type;
            row.EnableInClassList("pref-dirty", dirty);
            value.label = item.IsUnset ? "New override" : string.Empty;
            value.tooltip = item.IsUnset ? "Draft for a new stored override. The current application default is not shown." : "Stored preference value";
            row.Q<Label>("pref-state").text = dirty
                ? item.IsUnset
                    ? "Unsaved override"
                    : "Unsaved draft"
                : item.IsUnset
                    ? "No stored override"
                    : "Stored value";
            var save = row.Q<Button>("save-button");
            save.text = item.IsUnset ? "Initialize" : "Save";
            save.tooltip =
                item.IsUnset ? "Create this stored override"
                : dirty ? "Save changes"
                : "No changes to save";
            save.SetEnabled(item.IsUnset || dirty);
        }

        private void SavePref(EditorPrefItem item, string newValue, EditorPrefType newType)
        {
            if (IsSensitiveKey(item.Key))
                return;
            if (knownPrefTypes.TryGetValue(item.Key, out var requiredType) && requiredType != newType)
            {
                showSaveError("This preference has a fixed type. Refresh its row before saving.");
                return;
            }
            if (!SaveValue(item.Key, newValue, newType))
            {
                return;
            }

            item.Value = newValue;
            item.Type = newType;
            item.IsUnset = false;
            var row = prefRows[item.Key];
            row.Q<TextField>("value-field").SetValueWithoutNotify(newValue);
            row.Q<DropdownField>("type-dropdown").index = (int)newType;
            row.Q<Button>("reset-button").SetEnabled(true);
            UpdateDraftState(item, row);

            if (cachedPrefNames.TryGetValue(item.Key, out var cacheName))
            {
                EditorConfigurationCache.Instance.InvalidateKey(cacheName);
            }
        }

        private void ResetPref(EditorPrefItem item)
        {
            if (IsSensitiveKey(item.Key))
                return;
            EditorPrefs.DeleteKey(item.Key);
            item.IsUnset = true;
            item.Value = "Unset";
            var row = prefRows[item.Key];
            row.Q<TextField>("value-field").SetValueWithoutNotify(InitialValue(item));
            row.Q<DropdownField>("type-dropdown").SetValueWithoutNotify(item.Type.ToString());
            row.Q<Button>("reset-button").SetEnabled(false);
            UpdateDraftState(item, row);
            if (cachedPrefNames.TryGetValue(item.Key, out var cacheName))
                EditorConfigurationCache.Instance.InvalidateKey(cacheName);
        }

        private bool SaveValue(string key, string value, EditorPrefType type)
        {
            if (IsSensitiveKey(key))
                return false;
            switch (type)
            {
                case EditorPrefType.String:
                    EditorPrefs.SetString(key, value);
                    break;
                case EditorPrefType.Int:
                    if (int.TryParse(value, out var intValue))
                    {
                        EditorPrefs.SetInt(key, intValue);
                    }
                    else
                    {
                        showSaveError($"Cannot convert '{value}' to int");
                        return false;
                    }
                    break;
                case EditorPrefType.Float:
                    if (float.TryParse(value, out var floatValue))
                    {
                        EditorPrefs.SetFloat(key, floatValue);
                    }
                    else
                    {
                        showSaveError($"Cannot convert '{value}' to float");
                        return false;
                    }
                    break;
                case EditorPrefType.Bool:
                    if (bool.TryParse(value, out var boolValue))
                    {
                        EditorPrefs.SetBool(key, boolValue);
                    }
                    else
                    {
                        showSaveError($"Cannot convert '{value}' to bool (use 'True' or 'False')");
                        return false;
                    }
                    break;
                default:
                    showSaveError("Unsupported preference type");
                    return false;
            }
            return true;
        }
    }

    /// <summary>
    /// Represents an EditorPrefs item
    /// </summary>
    public class EditorPrefItem
    {
        public string Key { get; set; }
        public string Value { get; set; }
        public EditorPrefType Type { get; set; }
        public bool IsKnown { get; set; }
        public bool IsUnset { get; set; }
    }

    /// <summary>
    /// EditorPrefs value types
    /// </summary>
    public enum EditorPrefType
    {
        String,
        Int,
        Float,
        Bool,
    }
}
