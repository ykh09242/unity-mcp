using System;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows
{
    internal sealed class McpSettingsSearch : IDisposable
    {
        internal sealed class Entry
        {
            internal readonly string Title,
                Page,
                Target,
                Terms,
                Choice;

            internal Entry(string title, string page, string target, string terms, string choice = null)
            {
                Title = title;
                Page = page;
                Target = target;
                Terms = terms;
                Choice = choice;
            }

            public override string ToString() => Title;
        }

        private static readonly Entry[] Entries =
        {
            new("Connection transport", "clients", "transport-dropdown", "HTTP stdio session"),
            new("HTTP server URL", "clients", "http-url", "endpoint remote local address connection"),
            new("Remote API key", "clients", "api-key-field", "authentication credentials account"),
            new("Manual server command", "clients", "http-server-command", "launch startup terminal"),
            new("Provider API keys and enablement", "assetgen", "provider-key-fal", "fal shared 2D 3D audio credentials"),
            new("Meshy API key", "assetgen", "provider-key-meshy", "3D credentials"),
            new("Tripo API key", "assetgen", "provider-key-tripo", "3D credentials"),
            new("OpenRouter API key", "assetgen", "provider-key-openrouter", "2D image credentials"),
            new("Sketchfab API key", "assetgen", "provider-key-sketchfab", "3D model credentials"),
            new("Blender host and port", "assetgen", "blender-host", "socket connection addon"),
            new("Generation import and output", "assetgen", "assetgen-output-root", "format normalize models path"),
            new("glTFast dependency", "deps", "com.unity.cloud.gltfast-version", "GLB 3D import compatible versions package"),
            new("Optional package dependencies", "deps", "dependencies-refresh", "ProBuilder Cinemachine VFX compatible version install"),
            new("Reference source access", "graph", "graph-kind", "external repository permission grant", "External sources"),
            new("Reference wrapper rules", "graph", "graph-kind", "Resources Addressables loader analysis", "Wrapper rules"),
            new("Reference collection policy", "graph", "graph-kind", "runtime snapshot coverage", "Collection policy"),
            new("Diagnostic bundle", "advanced", "diagnostics-open", "support redacted export preview connection activity references"),
            new("Debug logging", "advanced", "debug-logs-toggle", "diagnostics console"),
            new("Log record file", "advanced", "log-record-toggle", "diagnostics output"),
            new("Server startup", "advanced", "auto-start-on-load-toggle", "HTTP editor startup"),
            new("LAN HTTP binding", "advanced", "allow-lan-http-bind-toggle", "network local access"),
            new("Insecure remote HTTP", "advanced", "allow-insecure-remote-http-toggle", "network endpoint policy"),
            new("uv executable path", "advanced", "browse-uv-button", "python server runtime override"),
            new("Server package source", "advanced", "git-url-override", "git URL local server override"),
            new("Screenshot output folder", "advanced", "screenshots-folder-override", "image capture path"),
            new("Package deployment source", "advanced", "deploy-source-path", "project copy target development"),
        };

        internal static Entry[] Find(string query)
        {
            if (string.IsNullOrWhiteSpace(query))
                return Array.Empty<Entry>();
            var words = query.Split(new[] { ' ', '\t', '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            return Entries
                .Where(entry =>
                    words.All(word => (entry.Title + " " + entry.Terms + " " + PageName(entry.Page)).IndexOf(word, StringComparison.OrdinalIgnoreCase) >= 0)
                )
                .ToArray();
        }

        private readonly VisualElement root,
            panel;
        private readonly TextField input;
        private readonly ScrollView results;
        private readonly Label message;
        private readonly Button trigger;
        private readonly Action<string> navigate;
        private IVisualElementScheduledItem pending;
        private int generation;
        private bool disposed;

        internal McpSettingsSearch(VisualElement root, Action<string> navigate)
        {
            this.root = root;
            this.navigate = navigate;
            trigger = new Button(Toggle)
            {
                text = "Find settings",
                name = "settings-search-toggle",
                tooltip = "Find by setting name or help text (Ctrl/Cmd+F). Saved values are never indexed.",
            };
            trigger.AddToClassList("settings-search-toggle");
            (root.Q("header-bar") ?? root).Add(trigger);
            panel = new VisualElement { name = "settings-search-panel" };
            panel.AddToClassList("settings-search-panel");
            panel.style.display = DisplayStyle.None;
            var layout = root.Q("root-container") ?? root;
            layout.Insert(Math.Min(1, layout.childCount), panel);
            var row = new VisualElement();
            row.AddToClassList("settings-search-row");
            panel.Add(row);
            input = new TextField("Find settings") { name = "settings-search-input" };
            row.Add(input);
            row.Add(new Button(Hide) { text = "Close", name = "settings-search-close" });
            message = new Label { name = "settings-search-message", enableRichText = false };
            message.AddToClassList("help-text");
            panel.Add(message);
            results = new ScrollView { name = "settings-search-results" };
            results.AddToClassList("settings-search-results");
            panel.Add(results);
            input.RegisterValueChangedCallback(OnQuery);
            root.RegisterCallback<KeyDownEvent>(OnKeyDown, TrickleDown.TrickleDown);
        }

        private void Toggle()
        {
            if (disposed)
                return;
            if (panel.style.display == DisplayStyle.None)
                Show();
            else
                Hide();
        }

        private void Show()
        {
            panel.style.display = DisplayStyle.Flex;
            message.text = "Search setting names and help text. Saved values are excluded.";
            pending?.Pause();
            int ticket = ++generation;
            pending = root.schedule.Execute(() =>
            {
                if (!disposed && generation == ticket)
                    input.Focus();
            });
        }

        private void Hide()
        {
            pending?.Pause();
            generation++;
            panel.style.display = DisplayStyle.None;
            input.SetValueWithoutNotify("");
            results.Clear();
            message.text = "";
        }

        private void OnQuery(ChangeEvent<string> evt)
        {
            results.Clear();
            var entries = Find(evt.newValue);
            message.text =
                entries.Length == 0
                    ? (
                        string.IsNullOrWhiteSpace(evt.newValue)
                            ? "Search setting names and help text."
                            : "No settings match. Try a provider, feature or setting name."
                    )
                    : entries.Length + " settings found. Enter opens the first result; Tab selects another.";
            foreach (var entry in entries)
            {
                var button = new Button(() => OpenEntry(entry)) { text = entry.Title + " · " + PageName(entry.Page), enableRichText = false };
                button.AddToClassList("settings-search-result");
                results.Add(button);
            }
        }

        private static string PageName(string page) =>
            page == "clients" ? "Connect"
            : page == "assetgen" ? "Generation"
            : page == "graph" ? "References"
            : page == "deps" ? "Dependencies"
            : "Advanced";

        private void OnKeyDown(KeyDownEvent evt)
        {
            if (disposed)
                return;
            bool handled = false;
            if ((evt.ctrlKey || evt.commandKey) && !evt.altKey && evt.keyCode == KeyCode.F)
            {
                Show();
                handled = true;
            }
            else if (panel.style.display != DisplayStyle.None && evt.keyCode == KeyCode.Escape)
            {
                Hide();
                trigger.Focus();
                handled = true;
            }
            else if (
                (evt.keyCode == KeyCode.Return || evt.keyCode == KeyCode.KeypadEnter) && (evt.target == input || input.Contains(evt.target as VisualElement))
            )
            {
                var entry = Find(input.value).FirstOrDefault();
                if (entry != null)
                {
                    OpenEntry(entry);
                    handled = true;
                }
            }
            if (!handled)
                return;
            evt.StopPropagation();
#if UNITY_6000_0_OR_NEWER
            root.focusController?.IgnoreEvent(evt);
#else
            evt.PreventDefault();
#endif
        }

        internal bool Open(string target)
        {
            var entry = Entries.FirstOrDefault(value => value.Target == target);
            if (entry == null || disposed)
            {
                if (!disposed)
                    Unavailable();
                return false;
            }
            return OpenEntry(entry);
        }

        private bool OpenEntry(Entry entry)
        {
            if (disposed)
                return false;
            Hide();
            navigate(entry.Page);
            var target = root.Q(entry.Target);
            if (target == null)
            {
                Unavailable();
                return false;
            }
            for (var ancestor = target; ancestor != null; ancestor = ancestor.parent)
                if (ancestor is Foldout foldout)
                    foldout.value = true;
            if (entry.Choice != null && target is DropdownField choices && target.enabledInHierarchy)
                choices.value = entry.Choice;
            int ticket = ++generation;
            pending = root.schedule.Execute(() =>
            {
                if (disposed || ticket != generation || target.panel == null)
                    return;
                for (var ancestor = target; ancestor != null; ancestor = ancestor.parent)
                    if (ancestor.resolvedStyle.display == DisplayStyle.None)
                    {
                        Unavailable();
                        return;
                    }
                if (!target.enabledInHierarchy)
                {
                    Unavailable();
                    return;
                }
                var focus = target.focusable
                    ? target
                    : target.Query<VisualElement>().ToList().FirstOrDefault(value => value.focusable && value.enabledInHierarchy);
                if (focus == null)
                {
                    Unavailable();
                    return;
                }
                focus.Focus();
                for (var ancestor = target.parent; ancestor != null; ancestor = ancestor.parent)
                    if (ancestor is ScrollView scroll)
                    {
                        scroll.ScrollTo(target);
                        break;
                    }
            });
            return true;
        }

        private void Unavailable()
        {
            Show();
            message.text = "This setting is unavailable in the current mode or package state. Review the opened page or choose another setting.";
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            generation++;
            pending?.Pause();
            input.UnregisterValueChangedCallback(OnQuery);
            root.UnregisterCallback<KeyDownEvent>(OnKeyDown, TrickleDown.TrickleDown);
            trigger.clicked -= Toggle;
            trigger.RemoveFromHierarchy();
            panel.RemoveFromHierarchy();
            results.Clear();
        }
    }
}
