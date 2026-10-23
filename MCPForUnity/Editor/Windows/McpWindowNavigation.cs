using System;
using System.Collections.Generic;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows
{
    internal sealed class McpWindowNavigation : IDisposable
    {
        private static readonly (string Id, string Title, string Description)[] Pages =
        {
            ("clients", "Connect", "Connect this Editor to your AI client and review its configuration."),
            ("tools", "Tools", "Choose the actions your AI can use in this project."),
            ("resources", "Resources", "Choose the project information your AI can read."),
            ("assetgen", "Generation", "Set up providers, model choices and import preferences."),
            ("graph", "References", "Inspect reference coverage, source health and graph operations."),
            ("activity", "Activity", "Follow long-running work and inspect retained results."),
            ("deps", "Dependencies", "Add optional capabilities with explicitly selected package versions."),
            ("advanced", "Advanced", "Manage diagnostics, paths and advanced connection settings."),
        };

        private readonly VisualElement root;
        private readonly VisualElement layout;
        private readonly VisualElement sidebar;
        private readonly DropdownField selector;
        private readonly Label title;
        private readonly Label description;
        private readonly Action<string> navigate;
        private readonly McpSettingsSearch settingsSearch;
        private int selected;
        private bool? compact;
        private bool disposed;
        private IVisualElementScheduledItem pendingFocus;

        internal McpWindowNavigation(VisualElement root, Action<string> navigate)
        {
            this.root = root;
            this.navigate = navigate;
            layout = root.Q("root-container");
            sidebar = root.Q("tab-toolbar");
            selector = root.Q<DropdownField>("page-selector");
            title = root.Q<Label>("page-title");
            description = root.Q<Label>("page-description");
            if (selector != null)
            {
                var choices = new List<string>(Pages.Length);
                foreach (var page in Pages)
                    choices.Add(page.Title);
                selector.choices = choices;
                selector.RegisterValueChangedCallback(OnSelection);
            }
            foreach (var page in Pages)
            {
                var tab = root.Q<ToolbarToggle>(page.Id + "-tab");
                if (tab != null)
                    tab.focusable = true;
            }
            sidebar?.RegisterCallback<KeyDownEvent>(OnKeyDown);
            layout?.RegisterCallback<GeometryChangedEvent>(OnGeometryChanged);
            Select("clients");
            UpdateHeight(layout?.resolvedStyle.height ?? 0);
            UpdateLayout(layout?.resolvedStyle.width ?? 0);
            settingsSearch = new McpSettingsSearch(root, navigate);
        }

        internal bool OpenSetting(string target) => settingsSearch.Open(target);

        internal void Select(string id)
        {
            if (disposed)
                return;
            for (int i = 0; i < Pages.Length; i++)
            {
                if (Pages[i].Id != id)
                    continue;
                selected = i;
                selector?.SetValueWithoutNotify(Pages[i].Title);
                if (title != null)
                    title.text = Pages[i].Title;
                if (description != null)
                    description.text = Pages[i].Description;
                return;
            }
        }

        private void OnSelection(ChangeEvent<string> evt)
        {
            if (disposed)
                return;
            for (int i = 0; i < Pages.Length; i++)
                if (Pages[i].Title == evt.newValue)
                {
                    navigate(Pages[i].Id);
                    return;
                }
        }

        private void OnKeyDown(KeyDownEvent evt)
        {
            if (disposed || evt.altKey || evt.ctrlKey || evt.commandKey)
                return;
            var target = evt.target as VisualElement;
            var toggle = target as ToolbarToggle ?? target?.GetFirstAncestorOfType<ToolbarToggle>();
            if (toggle == null)
                return;
            int index = Array.FindIndex(Pages, page => toggle.name == page.Id + "-tab");
            if (index < 0)
                return;
            switch (evt.keyCode)
            {
                case KeyCode.UpArrow:
                    index = (index + Pages.Length - 1) % Pages.Length;
                    break;
                case KeyCode.DownArrow:
                    index = (index + 1) % Pages.Length;
                    break;
                case KeyCode.Home:
                    index = 0;
                    break;
                case KeyCode.End:
                    index = Pages.Length - 1;
                    break;
                default:
                    return;
            }
            navigate(Pages[index].Id);
            var next = root.Q<ToolbarToggle>(Pages[index].Id + "-tab");
            next?.Focus();
            if (next != null)
                root.Q<ScrollView>("navigation-scroll")?.ScrollTo(next);
            evt.StopPropagation();
#if UNITY_6000_0_OR_NEWER
            root.focusController?.IgnoreEvent(evt);
#else
            evt.PreventDefault();
#endif
        }

        private void OnGeometryChanged(GeometryChangedEvent evt)
        {
            UpdateHeight(evt.newRect.height);
            UpdateLayout(evt.newRect.width);
        }

        private void UpdateHeight(float height)
        {
            if (!disposed)
                layout?.EnableInClassList("compact-height", !float.IsNaN(height) && height > 0 && height < 480);
        }

        private void UpdateLayout(float width)
        {
            bool next = float.IsNaN(width) || width < 760;
            if (compact == next || disposed)
                return;
            compact = next;
            var focused = root.focusController?.focusedElement as VisualElement;
            bool fromSidebar = focused != null && sidebar != null && (focused == sidebar || sidebar.Contains(focused));
            bool fromSelector = focused != null && selector != null && (focused == selector || selector.Contains(focused));
            layout?.EnableInClassList("compact-navigation", next);
            pendingFocus?.Pause();
            if (next && fromSidebar || !next && fromSelector)
                pendingFocus = root.schedule.Execute(() =>
                {
                    pendingFocus = null;
                    if (disposed || compact != next)
                        return;
                    var current = root.focusController?.focusedElement;
                    if (current != null && current != focused)
                        return;
                    if (next)
                        selector?.Focus();
                    else
                    {
                        var tab = root.Q<ToolbarToggle>(Pages[selected].Id + "-tab");
                        tab?.Focus();
                        if (tab != null)
                            root.Q<ScrollView>("navigation-scroll")?.ScrollTo(tab);
                    }
                });
        }

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            settingsSearch.Dispose();
            pendingFocus?.Pause();
            pendingFocus = null;
            selector?.UnregisterValueChangedCallback(OnSelection);
            sidebar?.UnregisterCallback<KeyDownEvent>(OnKeyDown);
            layout?.UnregisterCallback<GeometryChangedEvent>(OnGeometryChanged);
        }
    }
}
