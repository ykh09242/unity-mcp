using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    internal static class UguiSnapshotCapture
    {
        internal static JArray Capture(GameObject root, int limit)
        {
            if (!(root.transform is RectTransform) || root.GetComponentInParent<Canvas>(true) == null)
                throw new ArgumentException("uGUI snapshot requires a RectTransform beneath a Canvas.");
            var nodes = new JArray();
            Type graphicType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Graphic");
            Type selectableType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Selectable");
            Type maskType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Mask");
            foreach (var go in ManageUGUI.Descendants(root, true))
            {
                if (nodes.Count == limit)
                    throw new ArgumentException("Snapshot overflow: hierarchy exceeds max_nodes; incomplete evidence cannot be saved.");
                var components = go.GetComponents<Component>().Where(item => item != null).ToArray();
                if (components.Length > 64)
                    throw new ArgumentException("UI node exceeds the component bound.");
                var textComponent = components.FirstOrDefault(item => item.GetType().FullName == "UnityEngine.UI.Text" || IsTmp(item.GetType()));
                string text = textComponent?.GetType().GetProperty("text")?.GetValue(textComponent)?.ToString();
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
                        ["id"] = CaptureUISnapshot.Identity(go),
                        ["parent"] = go == root ? null : CaptureUISnapshot.Identity(go.transform.parent.gameObject),
                        ["sibling_index"] = go == root ? 0 : go.transform.GetSiblingIndex(),
                        ["type"] = go.transform.GetType().FullName,
                        ["components"] = new JArray(components.Select(item => item.GetType().FullName).OrderBy(item => item, StringComparer.Ordinal)),
                        ["classes"] = new JArray(),
                        ["text"] = text,
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
            }
            return nodes;
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
