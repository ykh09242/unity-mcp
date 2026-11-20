using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Windows.Components.ReferenceGraph
{
    internal sealed class ReviewReportWindow : EditorWindow
    {
        private const int PageSize = 20;
        private readonly List<(string label, JObject locator)> entries = new();
        private int page;
        private Vector2 scroll;
        private string feedback,
            selectedProperty;
        private Object reference;

        [MenuItem("Window/MCP for Unity/Review Dependency Report")]
        internal static void Open() => GetWindow<ReviewReportWindow>("Dependency Report");

        internal void LoadReport(string filename)
        {
            entries.Clear();
            feedback = selectedProperty = null;
            reference = null;
            page = 0;
            using var file = new FileStream(filename, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (file.Length > 16 * 1024 * 1024)
                throw new InvalidDataException("Select a report of up to 16 MiB.");
            using var reader = new StreamReader(file);
            var header = JObject.Parse(reader.ReadLine() ?? "{}");
            if ((string)header["kind"] != "verification_dependency_diff" || (int?)header["schema_version"] != 1)
                throw new InvalidDataException("Select a verification-candidate dependency-diff JSON Lines report.");
            var parsed = new List<(string, JObject)>();
            string line;
            while ((line = reader.ReadLine()) != null)
            {
                var row = JObject.Parse(line);
                if (row["editor_locators"] is not JArray locators)
                    continue;
                foreach (JObject locator in locators)
                {
                    string side = (string)locator["side"];
                    if ((side != "candidate" && side != "baseline") || (string)locator["commit"] != (string)header[side]?["commit"])
                        throw new InvalidDataException("The item's commit differs from its report side.");
                    string field = (string)locator["property_path"] ?? ((string)locator["field_status"] == "owner_only" ? "Owner only" : "");
                    parsed.Add(($"{(string)row["kind"]} / {side}   {(string)locator["path"]}   {field}", locator));
                }
            }
            entries.AddRange(parsed);
            feedback = entries.Count == 0 ? "No selectable locators. Export a dependency diff with a current CLI." : Path.GetFileName(filename);
        }

        private void OnGUI()
        {
            if (GUILayout.Button("Open dependency-diff JSONL"))
            {
                string filename = EditorUtility.OpenFilePanel("Select dependency report", "", "jsonl");
                if (!string.IsNullOrEmpty(filename))
                {
                    try
                    {
                        LoadReport(filename);
                    }
                    catch (Exception error)
                    {
                        feedback = error.Message;
                    }
                }
            }
            if (!string.IsNullOrEmpty(feedback))
                EditorGUILayout.HelpBox(feedback, MessageType.Info);
            if (!string.IsNullOrEmpty(selectedProperty))
            {
                EditorGUILayout.SelectableLabel(selectedProperty, GUILayout.Height(EditorGUIUtility.singleLineHeight));
                using (new EditorGUI.DisabledScope(true))
                    EditorGUILayout.ObjectField("Field value at selection", reference, typeof(Object), true);
            }
            if (entries.Count == 0)
                return;
            using (new EditorGUILayout.HorizontalScope())
            {
                using (new EditorGUI.DisabledScope(page == 0))
                    if (GUILayout.Button("Previous"))
                        page--;
                GUILayout.Label($"{page + 1} / {(entries.Count + PageSize - 1) / PageSize}");
                using (new EditorGUI.DisabledScope((page + 1) * PageSize >= entries.Count))
                    if (GUILayout.Button("Next"))
                        page++;
            }
            scroll = EditorGUILayout.BeginScrollView(scroll);
            foreach (var entry in entries.Skip(page * PageSize).Take(PageSize))
            {
                if (!GUILayout.Button(entry.label, EditorStyles.miniButton))
                    continue;
                selectedProperty = null;
                reference = null;
                try
                {
                    string head = ReferenceGraphCommitInputs.GitHead(Path.GetDirectoryName(Application.dataPath));
                    var selected = Select(entry.locator, head, out selectedProperty, out reference);
                    feedback = $"Selected {selected.name} ({selected.GetType().Name}).";
                }
                catch (Exception error)
                {
                    feedback = error.Message;
                }
            }
            EditorGUILayout.EndScrollView();
        }

        internal static Object Select(JObject locator, string checkout, out string selectedProperty, out Object reference)
        {
            selectedProperty = null;
            reference = null;
            string expected = (string)locator["commit"];
            if (expected == null || !Regex.IsMatch(expected, @"\A(?:[a-f0-9]{40}|[a-f0-9]{64})\z"))
                throw new InvalidDataException("The locator needs a full evidence commit SHA.");
            if (expected != checkout)
                throw new InvalidOperationException($"Open the Unity project at evidence commit {expected}. Current checkout: {checkout ?? "unavailable"}.");
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                throw new InvalidOperationException("Open the locator in stable Edit Mode.");
            string guid = (string)locator["guid"];
            if (guid == null || !Regex.IsMatch(guid, @"\A[a-f0-9]{32}\z"))
                throw new InvalidDataException("The locator needs an asset GUID.");
            string path = AssetDatabase.GUIDToAssetPath(guid);
            if (!ReferenceGraphCommitInputs.Eligible(path))
                throw new InvalidDataException($"Missing or unsupported asset GUID {guid} in {expected}.");
            Object selected;
            string field = (string)locator["property_path"];
            if (locator["local_file_id"] == null)
            {
                if (field != null)
                    throw new InvalidDataException("The field locator needs its owner's local file ID.");
                selected = AssetDatabase.LoadMainAssetAtPath(path);
            }
            else
            {
                if (!long.TryParse((string)locator["local_file_id"], NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out long id) || id == 0)
                    throw new InvalidDataException("Invalid local file ID.");
                if (field != null)
                {
                    if (field.Length == 0 || field.Length > 4096 || !MatchesSource(AssetPathUtility.GetFullAssetPath(path), (string)locator["source_sha256"]))
                        throw new InvalidDataException($"The field source no longer matches evidence at {expected}. Open that revision in a clean checkout.");
                }
                selected = ResolveOwner(path, guid, id);
            }
            if (!selected)
                throw new InvalidDataException($"The reported object no longer resolves at {path} in {expected}.");
            if (field != null)
            {
                var go = selected is Component component ? component.gameObject : selected as GameObject;
                if (field.Contains(".Array.data[") && (EditorUtility.IsDirty(selected) || (go != null && go.scene.IsValid() && go.scene.isDirty)))
                    throw new InvalidOperationException("An unsaved edit makes this collection index uncertain. Inspect the evidence in a clean checkout.");
                using var serialized = new SerializedObject(selected);
                using var property = serialized.FindProperty(field);
                if (property == null || property.propertyType != SerializedPropertyType.ObjectReference)
                    throw new InvalidDataException($"The reported reference field '{field}' is unavailable on {selected.GetType().Name}.");
                selectedProperty = property.propertyPath;
                reference = property.objectReferenceValue;
            }
            Selection.activeObject = selected;
            EditorGUIUtility.PingObject(selected);
            return selected;
        }

        internal static bool MatchesSource(string file, string expected)
        {
            long budget = 1024 * 1024; // Allow CRLF expansion of committed Unity YAML up to 512 KiB.
            byte[] bytes = ReferenceGraphCommitInputs.ReadBytes(file, ref budget);
            using var sha = SHA256.Create();
            bool Matches(int length) => BitConverter.ToString(sha.ComputeHash(bytes, 0, length)).Replace("-", "").ToLowerInvariant() == expected;
            if (Matches(bytes.Length))
                return true;
            // Git commonly checks LF Unity YAML out as CRLF on Windows. Normalize only
            // these byte pairs; preserve encoding, standalone CRs and all field values.
            if (bytes.Length < 6 || Encoding.ASCII.GetString(bytes, 0, 6) != "%YAML " || Array.IndexOf(bytes, (byte)0) != -1)
                return false;
            int written = 0;
            for (int i = 0; i < bytes.Length; i++)
            {
                if (bytes[i] == 13 && i + 1 < bytes.Length && bytes[i + 1] == 10)
                    continue;
                bytes[written++] = bytes[i];
            }
            return written <= 512 * 1024 && written != bytes.Length && Matches(written);
        }

        private static Object ResolveOwner(string path, string guid, long fileId)
        {
            bool sceneAsset = path.EndsWith(".unity", StringComparison.OrdinalIgnoreCase);
            if (sceneAsset && !SceneManager.GetSceneByPath(path).isLoaded)
            {
                var active = SceneManager.GetActiveScene();
                EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
                if (active.IsValid() && active.isLoaded && SceneManager.GetActiveScene() != active)
                    SceneManager.SetActiveScene(active);
            }
            foreach (int kind in sceneAsset ? new[] { 2 } : new[] { 1, 3 })
            {
                if (!GlobalObjectId.TryParse($"GlobalObjectId_V1-{kind}-{guid}-{unchecked((ulong)fileId)}-0", out var id))
                    continue;
                var candidate = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(id);
                if (candidate && GlobalObjectId.GetGlobalObjectIdSlow(candidate).Equals(id))
                    return candidate;
            }
            return null;
        }
    }
}
