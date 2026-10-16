using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Tools
{
    internal static class UitoolkitSnapshotCapture
    {
        internal static Action<VisualElement> InputPrivacyForTests;

        private static bool InputOwned(VisualElement element)
        {
            try
            {
                InputPrivacyForTests?.Invoke(element);
                int depth = 0;
                for (var current = element; current != null; current = current.parent)
                {
                    if (depth++ == 128)
                        throw new ArgumentException();
                    int types = 0;
                    for (var type = current.GetType(); type != null; type = type.BaseType)
                    {
                        if (types++ == 32)
                            throw new ArgumentException();
                        if ((type.FullName ?? "").StartsWith("UnityEngine.UIElements.TextInputBaseField`1", StringComparison.Ordinal))
                            return true;
                    }
                }
                // Newer native input internals use an owned TextElement rather than a
                // specialized TextInput subtype. Read its role metadata, never its text.
                if (element is TextElement)
                {
                    const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
                    var member = typeof(TextElement).GetField("isInputField", flags);
                    if (member != null && member.FieldType == typeof(bool))
                        return (bool)member.GetValue(element);
                    var property = typeof(TextElement).GetProperty("isInputField", flags);
                    if (property != null && property.PropertyType == typeof(bool))
                        return (bool)property.GetValue(element);
                    // Older engines represent the input itself as a TextElement subtype,
                    // already recognized by the type walk above. Establish that layout.
                    var input = typeof(TextField).BaseType.GetNestedType("TextInputBase", BindingFlags.Public | BindingFlags.NonPublic);
                    if (input != null && typeof(TextElement).IsAssignableFrom(input))
                        return false;
                    throw new ArgumentException();
                }
                return false;
            }
            catch
            {
                throw new ArgumentException("Input privacy evidence is unavailable; snapshot refused.");
            }
        }

        private static float Number(float value)
        {
            if (float.IsNaN(value) || float.IsInfinity(value))
                throw new ArgumentException("UI control state is unavailable or non-finite.");
            return value;
        }

        private static JObject State(VisualElement element)
        {
            var state = new JObject();
            var type = element.GetType();
            if (type == typeof(Toggle))
                state["toggle"] = new JObject { ["value"] = ((Toggle)element).value };
            else if (type == typeof(Slider))
            {
                var slider = (Slider)element;
                state["slider"] = new JObject
                {
                    ["kind"] = "float",
                    ["value"] = Number(slider.value),
                    ["low"] = Number(slider.lowValue),
                    ["high"] = Number(slider.highValue),
                    ["whole_numbers"] = false,
                };
            }
            else if (type == typeof(SliderInt))
            {
                var slider = (SliderInt)element;
                state["slider"] = new JObject
                {
                    ["kind"] = "integer",
                    ["value"] = slider.value,
                    ["low"] = slider.lowValue,
                    ["high"] = slider.highValue,
                };
            }
            else if (type == typeof(DropdownField))
                state["selection"] = new JObject { ["kind"] = "index", ["index"] = ((DropdownField)element).index };
            else if (type == typeof(ListView))
            {
                var list = (ListView)element;
                var indices = new List<int>();
                using (var values = list.selectedIndices.GetEnumerator())
                    while (values.MoveNext())
                    {
                        if (indices.Count == 64 || values.Current < 0)
                            throw new ArgumentException("UI selection exceeds the index bound or is invalid.");
                        indices.Add(values.Current);
                    }
                indices.Sort();
                if (indices.Distinct().Count() != indices.Count)
                    throw new ArgumentException("UI selection contains duplicate indices.");
                string selection =
                    list.selectionType == SelectionType.None ? "none"
                    : list.selectionType == SelectionType.Single ? "single"
                    : list.selectionType == SelectionType.Multiple ? "multiple"
                    : throw new ArgumentException("UI selection type is unavailable.");
                state["selection"] = new JObject
                {
                    ["kind"] = "indices",
                    ["selection_type"] = selection,
                    ["indices"] = new JArray(indices),
                };
            }
            else if (type == typeof(TextField))
            {
                var field = (TextField)element;
                state["input"] = new JObject
                {
                    ["redacted"] = true,
                    ["read_only"] = field.isReadOnly,
                    ["password"] = field.isPasswordField,
                };
            }
            else if (type == typeof(ScrollView))
            {
                var position = ((ScrollView)element).scrollOffset;
                state["scroll"] = new JObject { ["kind"] = "panel_points", ["position"] = new JArray(Number(position.x), Number(position.y)) };
            }
            return state;
        }

        private sealed class Frame
        {
            public VisualElement Element;
            public string Identity;
            public int NextChild;
            public float Alpha;
            public bool Displayed;
        }

        internal static JArray Capture(GameObject go, int limit)
        {
            // Resolve the same version-aware live root used by manage_ui.get_visual_tree.
            var ui = UIComponentAdapter.Resolve(go, "auto");
            var root = ui.Root;
            if (root == null || root.panel == null || !(root.worldBound.width > 0) || !(root.worldBound.height > 0))
                throw new ArgumentException("UI Toolkit live panel/layout is unavailable; build the visual tree before capture.");
            float inheritedAlpha = 1;
            bool inheritedDisplay = true;
            int ancestorDepth = 0;
            for (var ancestor = root.parent; ancestor != null; ancestor = ancestor.parent)
            {
                if (ancestorDepth++ == 128)
                    throw new ArgumentException("Snapshot overflow: document ancestor chain exceeds the depth bound.");
                inheritedAlpha *= ancestor.resolvedStyle.opacity;
                inheritedDisplay &= ancestor.resolvedStyle.display != DisplayStyle.None;
            }
            string identity = CaptureUISnapshot.Identity(go);
            var nodes = new JArray();
            var stack = new Stack<Frame>();
            Add(root, identity, null, go.activeInHierarchy && Enabled(ui.Component), inheritedAlpha, nodes, inheritedDisplay);
            stack.Push(
                new Frame
                {
                    Element = root,
                    Identity = identity,
                    Alpha = inheritedAlpha * root.resolvedStyle.opacity,
                    Displayed = inheritedDisplay && root.resolvedStyle.display != DisplayStyle.None,
                }
            );
            while (stack.Count > 0)
            {
                var parent = stack.Peek();
                if (parent.NextChild >= parent.Element.childCount)
                {
                    stack.Pop();
                    continue;
                }
                if (nodes.Count == limit || stack.Count >= 128 || parent.Element.childCount > 1000)
                    throw new ArgumentException("Snapshot overflow: visual tree exceeds max_nodes, depth or sibling bound.");
                var child = parent.Element.ElementAt(parent.NextChild++);
                string segment = Segment(child);
                int matches = 0;
                for (int i = 0; i < parent.Element.childCount; i++)
                    if (Segment(parent.Element.ElementAt(i)) == segment)
                        matches++;
                if (matches != 1)
                    throw new ArgumentException("UI Toolkit semantic identity is ambiguous; give same-type unnamed siblings unique names.");
                string childIdentity = parent.Identity + "/" + segment;
                if (childIdentity.Length > 4096)
                    throw new ArgumentException("Visual element identity exceeds 4096 characters.");
                Add(child, childIdentity, parent.Identity, go.activeInHierarchy && Enabled(ui.Component), parent.Alpha, nodes, parent.Displayed);
                stack.Push(
                    new Frame
                    {
                        Element = child,
                        Identity = childIdentity,
                        Alpha = parent.Alpha * child.resolvedStyle.opacity,
                        Displayed = parent.Displayed && child.resolvedStyle.display != DisplayStyle.None,
                    }
                );
            }
            return nodes;
        }

        internal static string Segment(VisualElement element)
        {
            string segment = string.IsNullOrEmpty(element.name) ? "type:" + element.GetType().FullName : "name:" + element.name;
            if (segment.Length > 1024)
                throw new ArgumentException("Visual element name exceeds the identity bound.");
            return Uri.EscapeDataString(segment);
        }

        private static bool Eligible(VisualElement element) =>
            element.resolvedStyle.display != DisplayStyle.None && element.resolvedStyle.visibility != Visibility.Hidden;

        private static bool Enabled(Component component) => !(component is Behaviour behaviour) || behaviour.isActiveAndEnabled;

        private static void Add(VisualElement element, string identity, string parent, bool active, float inheritedAlpha, JArray nodes, bool inheritedDisplay)
        {
            bool inputOwned = InputOwned(element);
            var state = State(element);
            string text = !inputOwned && element is TextElement textElement ? textElement.text : null;
            var classes = element.GetClasses().OrderBy(value => value, StringComparer.Ordinal).ToArray();
            if ((text != null && text.Length > 8192) || classes.Length > 64 || classes.Any(value => value.Length == 0 || value.Length > 4096))
                throw new ArgumentException("Visual element text/classes exceed the snapshot bound.");
            float alpha = inheritedAlpha * element.resolvedStyle.opacity;
            if (float.IsNaN(alpha) || float.IsInfinity(alpha) || alpha < 0 || alpha > 1)
                throw new ArgumentException("UI Toolkit alpha evidence is unavailable or outside 0..1.");
            nodes.Add(
                new JObject
                {
                    ["id"] = identity,
                    ["parent"] = parent,
                    ["sibling_index"] = element.parent == null || parent == null ? 0 : element.parent.IndexOf(element),
                    ["type"] = element.GetType().FullName,
                    ["components"] = new JArray(),
                    ["classes"] = new JArray(classes),
                    ["text"] = text == null ? JValue.CreateNull() : new JValue(text),
                    ["state"] = state,
                    ["active"] = active,
                    ["visible"] = active && inheritedDisplay && Eligible(element) && alpha > 0 && element.worldBound.width > 0 && element.worldBound.height > 0,
                    ["interactable"] = active && element.enabledInHierarchy,
                    ["renderer_culled"] = null,
                    ["alpha"] = alpha,
                    ["geometry"] = new JObject { ["bounds"] = CaptureUISnapshot.Rect(element.worldBound) },
                }
            );
        }
    }
}
