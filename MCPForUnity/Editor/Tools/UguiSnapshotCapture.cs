using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    internal static class UguiSnapshotCapture
    {
        internal static Func<Type, UnityEngine.Object[]> InventoryForTests;

        private sealed class InputPrivacy
        {
            internal readonly HashSet<int> Owners = new();
            internal readonly HashSet<int> Renderers = new();

            internal bool Suppress(GameObject go, Component text)
            {
                if (text != null && Renderers.Contains(text.GetInstanceIDCompat()))
                    return true;
                int depth = 0;
                for (var current = go.transform; current != null; current = current.parent)
                {
                    if (depth++ == 128)
                        throw new ArgumentException("Input privacy ancestry is incomplete; snapshot refused.");
                    if (Owners.Contains(current.gameObject.GetInstanceIDCompat()))
                        return true;
                }
                return false;
            }
        }

        private static InputPrivacy CaptureInputPrivacy()
        {
            // Native arrays may allocate before these managed processing/retention caps.
            // An incomplete inventory must never be treated as an empty successful lookup.
            try
            {
                var result = new InputPrivacy();
                int entries = 0;
                foreach (string name in new[] { "UnityEngine.UI.InputField", "TMPro.TMP_InputField" })
                {
                    if (!UnityTypeResolver.TryResolve(name, out var type, out var error, typeof(Component)))
                    {
                        if (error.StartsWith("Type '" + name + "' not found", StringComparison.Ordinal))
                            continue;
                        throw new ArgumentException();
                    }
                    var inventory = InventoryForTests == null ? UnityEngine.Resources.FindObjectsOfTypeAll(type) : InventoryForTests(type);
                    if (inventory == null || inventory.Length > 4096 - entries)
                        throw new ArgumentException();
                    entries += inventory.Length;
                    foreach (var item in inventory)
                    {
                        if (item == null)
                            continue;
                        if (!(item is Component owner) || !type.IsInstanceOfType(owner))
                            throw new ArgumentException();
                        result.Owners.Add(owner.gameObject.GetInstanceIDCompat());
                        foreach (string property in new[] { "textComponent", "placeholder" })
                        {
                            var member = type.GetProperty(property);
                            if (member == null)
                                throw new ArgumentException();
                            var renderer = member.GetValue(owner) as Component;
                            if (renderer != null)
                                result.Renderers.Add(renderer.GetInstanceIDCompat());
                        }
                        if (result.Owners.Count > 4096 || result.Renderers.Count > 8192)
                            throw new ArgumentException();
                    }
                }
                return result;
            }
            catch
            {
                throw new ArgumentException("Input privacy preflight is incomplete; snapshot refused.");
            }
        }

        private static JObject State(Component[] components)
        {
            var state = new JObject();
            foreach (var component in components)
            {
                string name = component.GetType().FullName;
                if (
                    name != "UnityEngine.UI.Toggle"
                    && name != "UnityEngine.UI.Slider"
                    && name != "UnityEngine.UI.Dropdown"
                    && name != "TMPro.TMP_Dropdown"
                    && name != "UnityEngine.UI.InputField"
                    && name != "TMPro.TMP_InputField"
                    && name != "UnityEngine.UI.ScrollRect"
                )
                    continue;
                if (!UnityTypeResolver.TryResolve(name, out var knownType, out _, typeof(Component)) || knownType != component.GetType())
                    throw new ArgumentException("UI control type is ambiguous or unavailable; snapshot refused.");
                string category;
                JObject value;
                object Read(string property) => component.GetType().GetProperty(property).GetValue(component);
                switch (component.GetType().FullName)
                {
                    case "UnityEngine.UI.Toggle":
                        category = "toggle";
                        value = new JObject { ["value"] = (bool)Read("isOn") };
                        break;
                    case "UnityEngine.UI.Slider":
                        category = "slider";
                        value = new JObject
                        {
                            ["kind"] = "float",
                            ["value"] = Number((float)Read("value")),
                            ["low"] = Number((float)Read("minValue")),
                            ["high"] = Number((float)Read("maxValue")),
                            ["whole_numbers"] = (bool)Read("wholeNumbers"),
                        };
                        break;
                    case "UnityEngine.UI.Dropdown":
                    case "TMPro.TMP_Dropdown":
                        category = "selection";
                        value = new JObject { ["kind"] = "index", ["index"] = (int)Read("value") };
                        break;
                    case "UnityEngine.UI.InputField":
                    case "TMPro.TMP_InputField":
                        category = "input";
                        value = new JObject
                        {
                            ["redacted"] = true,
                            ["read_only"] = (bool)Read("readOnly"),
                            ["password"] = Read("inputType").ToString() == "Password",
                        };
                        break;
                    case "UnityEngine.UI.ScrollRect":
                        var position = (Vector2)Read("normalizedPosition");
                        category = "scroll";
                        value = new JObject { ["kind"] = "normalized", ["position"] = new JArray(Number(position.x), Number(position.y)) };
                        break;
                    default:
                        continue;
                }
                if (state[category] != null)
                    throw new ArgumentException("Ambiguous duplicate UI control state; snapshot refused.");
                state[category] = value;
            }
            return state;
        }

        private static float Number(float value)
        {
            if (float.IsNaN(value) || float.IsInfinity(value))
                throw new ArgumentException("UI control state is unavailable or non-finite.");
            return value;
        }

        internal static JArray Capture(GameObject root, int limit, UISnapshotIdentityMap identities = null)
        {
            if (!(root.transform is RectTransform) || root.GetComponentInParent<Canvas>(true) == null)
                throw new ArgumentException("uGUI snapshot requires a RectTransform beneath a Canvas.");
            var privacy = CaptureInputPrivacy();
            var nodes = new JArray();
            var mappedNodes = identities == null ? null : new Dictionary<Transform, (string Path, string Identity, int Depth)>();
            var seen = identities == null ? null : new HashSet<string>(StringComparer.Ordinal);
            Type graphicType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Graphic");
            Type selectableType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Selectable");
            Type maskType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Mask");
            foreach (var go in ManageUGUI.Descendants(root, true))
            {
                if (nodes.Count == limit)
                    throw new ArgumentException("Snapshot overflow: hierarchy exceeds max_nodes; incomplete evidence cannot be saved.");
                string identity,
                    parentIdentity = null,
                    path = null;
                if (identities == null)
                {
                    identity = CaptureUISnapshot.Identity(go);
                    if (go != root)
                        parentIdentity = CaptureUISnapshot.Identity(go.transform.parent.gameObject);
                }
                else if (go == root)
                {
                    identity = CaptureUISnapshot.Identity(root);
                    path = "";
                    mappedNodes.Add(go.transform, (path, identity, 1));
                    seen.Add(identity);
                }
                else
                {
                    var parent = mappedNodes[go.transform.parent];
                    if (parent.Depth >= 128 || go.transform.parent.childCount > 1000)
                        throw new ArgumentException("Snapshot overflow: hierarchy exceeds the identity depth or sibling bound.");
                    path = parent.Path + "/" + go.transform.GetSiblingIndex().ToString(System.Globalization.CultureInfo.InvariantCulture);
                    string segment = EffectiveSegment(go.transform, path, identities);
                    int matches = 0;
                    for (int i = 0; i < go.transform.parent.childCount; i++)
                        if (
                            EffectiveSegment(
                                go.transform.parent.GetChild(i),
                                parent.Path + "/" + i.ToString(System.Globalization.CultureInfo.InvariantCulture),
                                identities
                            ) == segment
                        )
                            matches++;
                    if (matches != 1)
                        throw new ArgumentException("Hierarchy contains ambiguous effective sibling identities.");
                    identity = parent.Identity + "/" + segment;
                    if (identity.Length > 4096)
                        throw new ArgumentException("Hierarchy identity exceeds 4096 characters.");
                    if (!seen.Add(identity))
                        throw new ArgumentException("identity_map produced ambiguous snapshot identities.");
                    parentIdentity = parent.Identity;
                    mappedNodes.Add(go.transform, (path, identity, parent.Depth + 1));
                }
                var components = go.GetComponents<Component>().Where(item => item != null).ToArray();
                if (components.Length > 64)
                    throw new ArgumentException("UI node exceeds the component bound.");
                var textComponent = components.FirstOrDefault(item => item.GetType().FullName == "UnityEngine.UI.Text" || IsTmp(item.GetType()));
                var state = State(components);
                string text = privacy.Suppress(go, textComponent) ? null : textComponent?.GetType().GetProperty("text")?.GetValue(textComponent)?.ToString();
                if (text != null && text.Length > 8192)
                    throw new ArgumentException("UI text exceeds the snapshot bound.");
                var graphic = graphicType == null ? null : go.GetComponent(graphicType);
                var selectable = selectableType == null ? null : go.GetComponent(selectableType);
                var renderer = go.GetComponent<CanvasRenderer>();
                var mask = graphic == null || maskType == null ? null : go.GetComponent(maskType);
                // Inherited alpha contains CanvasGroups, separately from the renderer's current alpha and Graphic vertex alpha.
                float alpha = renderer == null ? GroupAlpha(go.transform) : renderer.GetAlpha() * renderer.GetInheritedAlpha();
                if (graphic != null)
                {
                    var color = graphic.GetType().GetProperty("color")?.GetValue(graphic);
                    if (color is Color value)
                        alpha *= value.a;
                }
                if (float.IsNaN(alpha) || float.IsInfinity(alpha) || alpha < 0 || alpha > 1)
                    throw new ArgumentException("UI alpha is unavailable or outside 0..1.");
                var rect = go.transform as RectTransform;
                Rect bounds = new Rect(go.transform.position.x, go.transform.position.y, 0, 0);
                var geometry = new JObject();
                if (rect != null)
                {
                    var corners = new Vector3[4];
                    rect.GetWorldCorners(corners);
                    float minX = corners.Min(item => item.x),
                        minY = corners.Min(item => item.y);
                    bounds = Rect.MinMaxRect(minX, minY, corners.Max(item => item.x), corners.Max(item => item.y));
                    geometry["local_rect"] = CaptureUISnapshot.Rect(rect.rect);
                    geometry["anchor_min"] = new JArray(rect.anchorMin.x, rect.anchorMin.y);
                    geometry["anchor_max"] = new JArray(rect.anchorMax.x, rect.anchorMax.y);
                    geometry["pivot"] = new JArray(rect.pivot.x, rect.pivot.y);
                    geometry["anchored_position"] = new JArray(rect.anchoredPosition.x, rect.anchoredPosition.y);
                }
                geometry["bounds"] = CaptureUISnapshot.Rect(bounds);
                geometry["local_scale"] = new JArray(go.transform.localScale.x, go.transform.localScale.y, go.transform.localScale.z);
                geometry["local_position"] = new JArray(go.transform.localPosition.x, go.transform.localPosition.y, go.transform.localPosition.z);
                geometry["local_rotation"] = new JArray(go.transform.localEulerAngles.x, go.transform.localEulerAngles.y, go.transform.localEulerAngles.z);
                // Graphics know their effective Canvas, including fallback past a disabled nested Canvas.
                // Containers describe eligibility beneath the nearest usable Canvas; they have no rendered pixels of their own.
                var canvas =
                    graphic == null
                        ? go.GetComponentsInParent<Canvas>(true).FirstOrDefault(item => item.isActiveAndEnabled)
                        : graphicType.GetProperty("canvas")?.GetValue(graphic) as Canvas;
                bool canvasEnabled = canvas != null && canvas.isActiveAndEnabled;
                bool visible =
                    go.activeInHierarchy
                    && canvasEnabled
                    && alpha > 0
                    && (!(graphic is Behaviour behaviour) || behaviour.enabled)
                    && (renderer == null || !renderer.cull)
                    && (mask == null || !Enabled(mask) || (bool)(mask.GetType().GetProperty("showMaskGraphic")?.GetValue(mask) ?? true));
                nodes.Add(
                    new JObject
                    {
                        ["id"] = identity,
                        ["parent"] = parentIdentity,
                        ["sibling_index"] = go == root ? 0 : go.transform.GetSiblingIndex(),
                        ["type"] = go.transform.GetType().FullName,
                        ["components"] = new JArray(components.Select(item => item.GetType().FullName).OrderBy(item => item, StringComparer.Ordinal)),
                        ["classes"] = new JArray(),
                        ["text"] = text == null ? JValue.CreateNull() : new JValue(text),
                        ["state"] = state,
                        ["active"] = go.activeInHierarchy,
                        ["visible"] = visible,
                        ["interactable"] =
                            selectable == null
                                ? JValue.CreateNull()
                                : new JValue(Enabled(selectable) && (bool)selectable.GetType().GetMethod("IsInteractable").Invoke(selectable, null)),
                        ["renderer_culled"] = renderer == null ? JValue.CreateNull() : new JValue(renderer.cull),
                        ["alpha"] = alpha,
                        ["geometry"] = geometry,
                    }
                );
                identities?.MarkVisited(path);
            }
            return nodes;
        }

        private static string EffectiveSegment(Transform element, string path, UISnapshotIdentityMap identities)
        {
            if (identities.TryGetSegment(path, out string segment))
                return segment;
            string name = element.name;
            if (name.Length > 1024)
                throw new ArgumentException("Hierarchy identity exceeds the name bound.");
            return Uri.EscapeDataString(name);
        }

        private static float GroupAlpha(Transform target)
        {
            float alpha = 1;
            for (Transform current = target; current != null; current = current.parent)
            {
                bool ignoreParents = false;
                foreach (var group in current.GetComponents<CanvasGroup>())
                {
                    if (!group.enabled)
                        continue;
                    alpha *= group.alpha;
                    ignoreParents |= group.ignoreParentGroups;
                }
                if (ignoreParents)
                    break;
            }
            return alpha;
        }

        private static bool Enabled(Component component) => !(component is Behaviour behaviour) || behaviour.isActiveAndEnabled;

        private static bool IsTmp(Type type)
        {
            for (; type != null; type = type.BaseType)
                if (type.FullName == "TMPro.TMP_Text")
                    return true;
            return false;
        }
    }
}
