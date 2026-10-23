using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows
{
    internal sealed class McpDiagnosticWindow : EditorWindow
    {
        private Func<int, JObject> capture;
        private string previewText;
        private readonly Dictionary<int, Toggle> categories = new();
        internal Action<string> CopyText = null;
        internal Func<string> ChooseSavePath = null;
        internal Action<string, string> SaveText = null;
        private TextField preview;
        private Label status;
        private Button copy,
            save;

        internal static void Open(Func<int, JObject> capture)
        {
            var window = CreateInstance<McpDiagnosticWindow>();
            window.Configure(capture);
            window.titleContent = new GUIContent("Diagnostic bundle");
            window.minSize = new Vector2(360, 320);
            window.Show();
        }

        internal void Configure(Func<int, JObject> source) => capture = source;

        internal void CreateGUI()
        {
            var root = rootVisualElement;
            root.Clear();
            categories.Clear();
            previewText = null;
            root.AddToClassList("mcp-editor");
            root.EnableInClassList("unity-theme-light", !EditorGUIUtility.isProSkin);
            root.EnableInClassList("unity-theme-dark", EditorGUIUtility.isProSkin);
            string common = AssetPathUtility.GetMcpPackageRootPath() + "/Editor/Windows/Components/Common.uss";
            var sheet = AssetDatabase.LoadAssetAtPath<StyleSheet>(common);
            if (sheet != null && !root.styleSheets.Contains(sheet))
                root.styleSheets.Add(sheet);
            root.style.paddingLeft = root.style.paddingRight = root.style.paddingTop = root.style.paddingBottom = 12;
            var body = new ScrollView { name = "diagnostics-body" };
            body.style.flexGrow = 1;
            body.style.minHeight = 0;
            root.Add(body);
            var content = body.contentContainer;
            Note(content, "Choose categories, generate a preview, then copy or save it. Nothing is uploaded.");
            Note(content, "Excluded: API keys, endpoints, paths, configuration values, raw results and free-form error text.");
            var options = new VisualElement();
            options.style.flexDirection = FlexDirection.Row;
            options.style.flexWrap = Wrap.Wrap;
            content.Add(options);
            foreach (
                var category in new[]
                {
                    (McpDiagnosticBundle.Versions, "Versions"),
                    (McpDiagnosticBundle.Connection, "Connection"),
                    (McpDiagnosticBundle.Capabilities, "Capabilities"),
                    (McpDiagnosticBundle.Graph, "Reference freshness"),
                    (McpDiagnosticBundle.Operation, "Selected operation"),
                }
            )
            {
                var toggle = new Toggle(category.Item2) { name = "diagnostics-category-" + category.Item1, value = true };
                toggle.style.minWidth = 150;
                toggle.style.marginRight = 8;
                toggle.style.marginBottom = 6;
                toggle.RegisterValueChangedCallback(_ => InvalidatePreview());
                categories.Add(category.Item1, toggle);
                options.Add(toggle);
            }
            var actions = new VisualElement();
            actions.style.flexDirection = FlexDirection.Row;
            actions.style.flexWrap = Wrap.Wrap;
            content.Add(actions);
            var generate = Action(actions, "Generate preview", "diagnostics-generate", Generate);
            generate.SetEnabled(capture != null);
            copy = Action(actions, "Copy preview", "diagnostics-copy", Copy);
            save = Action(actions, "Save preview", "diagnostics-save", Save);
            status = Note(
                content,
                capture != null
                    ? "Preview has not been generated. Missing observations will be marked unavailable."
                    : "Reopen diagnostics from the main window after script reload or when its owner is available."
            );
            status.name = "diagnostics-status";
            preview = new TextField
            {
                name = "diagnostics-preview",
                multiline = true,
                isReadOnly = true,
            };
            preview.style.minWidth = 0;
            preview.style.minHeight = 180;
            preview.style.whiteSpace = WhiteSpace.Normal;
            content.Add(preview);
            copy.SetEnabled(false);
            save.SetEnabled(false);
        }

        private static Label Note(VisualElement root, string text)
        {
            var label = new Label(text) { enableRichText = false };
            label.style.whiteSpace = WhiteSpace.Normal;
            label.style.marginBottom = 8;
            root.Add(label);
            return label;
        }

        private static Button Action(VisualElement root, string text, string name, System.Action clicked)
        {
            var button = new Button(clicked) { text = text, name = name };
            button.style.minHeight = 28;
            button.style.marginRight = 8;
            button.style.marginBottom = 6;
            root.Add(button);
            return button;
        }

        private void InvalidatePreview()
        {
            previewText = null;
            preview?.SetValueWithoutNotify("");
            copy?.SetEnabled(false);
            save?.SetEnabled(false);
            if (status != null)
                status.text = "Categories changed. Generate a new preview before exporting.";
        }

        private void Generate()
        {
            InvalidatePreview();
            if (capture == null)
                return;
            int selected = 0;
            foreach (var pair in categories)
                if (pair.Value.value)
                    selected |= pair.Key;
            try
            {
                var input = capture(selected);
                if (input == null)
                {
                    status.text = "The owner is unavailable. Reopen diagnostics from the main window.";
                    return;
                }
                previewText = McpDiagnosticBundle.Build(input, selected, DateTimeOffset.UtcNow).ToString(Formatting.Indented);
                preview.SetValueWithoutNotify(previewText);
                copy.SetEnabled(true);
                save.SetEnabled(true);
                status.text = "Preview generated from available observations. Copy and Save export exactly this preview.";
            }
            catch (Exception)
            {
                status.text = "Could not collect diagnostics. Review the main window and generate the preview again.";
            }
        }

        private void Copy()
        {
            if (previewText == null)
                return;
            try
            {
                (CopyText ?? (text => EditorGUIUtility.systemCopyBuffer = text))(previewText);
                status.text = "Diagnostic preview copied.";
            }
            catch (Exception)
            {
                status.text = "Could not copy the diagnostic preview. Try Save preview instead.";
            }
        }

        private void Save()
        {
            if (previewText == null)
                return;
            try
            {
                string path = (ChooseSavePath ?? (() => EditorUtility.SaveFilePanel("Save diagnostic bundle", "", "unity-mcp-diagnostics.json", "json")))();
                if (string.IsNullOrEmpty(path))
                {
                    status.text = "Save cancelled; the preview is still available.";
                    return;
                }
                (SaveText ?? ((file, text) => File.WriteAllText(file, text, new UTF8Encoding(false))))(path, previewText);
                status.text = "Diagnostic preview saved.";
            }
            catch (Exception)
            {
                status.text = "Could not save the diagnostic preview. Choose another location and retry.";
            }
        }

        private void OnDisable()
        {
            capture = null;
            previewText = null;
            CopyText = null;
            ChooseSavePath = null;
            SaveText = null;
            categories.Clear();
            rootVisualElement.Clear();
            preview = null;
            status = null;
            copy = save = null;
        }
    }
}
