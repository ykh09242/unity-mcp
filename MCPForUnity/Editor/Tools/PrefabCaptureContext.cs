using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Read scene context without cloning its behaviours or reparenting its objects.</summary>
    internal static class PrefabCaptureContext
    {
        internal static RectTransform ResolveParent(JToken token, GameObject asset)
        {
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int id) || id == 0)
                throw new ArgumentException("ui_parent must be a nonzero signed 32-bit GameObject instance ID.");
            var parent = UnityObjectIdCompat.InstanceIDToObjectCompat(id) as GameObject;
            if (
                parent == null
                || !(parent.transform is RectTransform rect)
                || EditorUtility.IsPersistent(parent)
                || !parent.scene.IsValid()
                || !parent.scene.isLoaded
                || EditorSceneManager.IsPreviewScene(parent.scene)
                || !parent.activeInHierarchy
            )
                throw new ArgumentException("ui_parent must identify an active RectTransform GameObject in a loaded scene, outside prefab/preview stages.");
            var canvas = parent.GetComponentInParent<Canvas>();
            if (canvas == null || !canvas.isActiveAndEnabled || !canvas.rootCanvas.isActiveAndEnabled || canvas.rootCanvas.renderMode == RenderMode.WorldSpace)
                throw new ArgumentException("ui_parent requires an enabled screen-space root Canvas.");
            if (!(asset.transform is RectTransform))
                throw new ArgumentException("ui_parent is supported only for a prefab with a root RectTransform.");
            int depth = 0;
            for (Transform current = rect; current != canvas.rootCanvas.transform; current = current.parent)
                if (!(current is RectTransform) || ++depth > 64)
                    throw new ArgumentException("ui_parent requires a RectTransform-only ancestry of at most 64 levels beneath its root Canvas.");
            return rect;
        }

        internal static JObject Frame(
            PreviewRenderUtility preview,
            GameObject instance,
            RectTransform rect,
            Camera camera,
            int width,
            int height,
            RectTransform parent,
            List<string> limitations
        )
        {
            // Unity skips Canvas rendering for CameraType.Preview, even with a valid UI mesh.
            camera.cameraType = CameraType.Game;
            camera.transform.SetPositionAndRotation(new Vector3(0, 0, -1000), Quaternion.identity);
            camera.orthographicSize = height / 2f;
            var authoredCanvas = instance.GetComponent<Canvas>();
            var sourceCanvas = parent != null ? parent.GetComponentInParent<Canvas>().rootCanvas : authoredCanvas;
            var scaler = sourceCanvas != null ? sourceCanvas.GetComponents<Behaviour>().FirstOrDefault(IsCanvasScaler) : null;
            if (scaler != null && !scaler.isActiveAndEnabled)
                scaler = null;
            float scale = sourceCanvas != null ? sourceCanvas.scaleFactor : 1f;
            float pixelsPerUnit = sourceCanvas != null ? sourceCanvas.referencePixelsPerUnit : 100f;
            if (scaler != null)
                CanvasScale(scaler, sourceCanvas.renderMode, width, height, out scale, out pixelsPerUnit);
            if (!PositiveFinite(scale) || !PositiveFinite(pixelsPerUnit))
                throw new ArgumentException("Canvas scale and reference pixels per unit must be finite and positive.");

            var metadata = Describe(sourceCanvas, scaler, parent, scale, pixelsPerUnit);
            bool projectedWorldCanvas = sourceCanvas != null && sourceCanvas.renderMode == RenderMode.WorldSpace;
            Canvas canvas;
            if (parent != null)
            {
                canvas = NewRoot(preview);
                canvas.additionalShaderChannels = sourceCanvas.additionalShaderChannels;
                canvas.pixelPerfect = sourceCanvas.pixelPerfect;
                var targetParent = SnapshotParents(preview, sourceCanvas, parent, canvas, metadata, limitations);
                // Disable only the owned prefab's scaler. Disabling a source scaler would reset the live Canvas.
                var ownedScaler = instance.GetComponents<Behaviour>().FirstOrDefault(IsCanvasScaler);
                if (ownedScaler != null)
                    ownedScaler.enabled = false;
                rect.SetParent(targetParent, false);
                if (authoredCanvas != null)
                {
                    // A screen Canvas's root rectangle was display-driven; in this context it fills the chosen parent.
                    rect.anchorMin = Vector2.zero;
                    rect.anchorMax = Vector2.one;
                    rect.pivot = Vector2.one * 0.5f;
                    rect.sizeDelta = Vector2.zero;
                    rect.anchoredPosition3D = Vector3.zero;
                    rect.localRotation = Quaternion.identity;
                    rect.localScale = Vector3.one;
                }
            }
            else if (authoredCanvas != null)
            {
                canvas = authoredCanvas;
                if (scaler != null)
                    scaler.enabled = false;
            }
            else
            {
                canvas = NewRoot(preview);
                rect.SetParent(canvas.transform, false);
                rect.localRotation = Quaternion.identity;
                rect.localScale = Vector3.one;
                limitations.Add("No ui_parent or authored root Canvas was supplied: this is a 1x preview and may differ from the UI's in-game placement.");
            }
            canvas.renderMode = RenderMode.ScreenSpaceCamera;
            canvas.planeDistance = 10f;
            canvas.worldCamera = camera;
            canvas.scaleFactor = scale;
            canvas.referencePixelsPerUnit = pixelsPerUnit;
            foreach (var childCanvas in instance.GetComponentsInChildren<Canvas>(true))
                childCanvas.worldCamera = camera;
            if (projectedWorldCanvas)
                limitations.Add("The authored World Space Canvas is projected into a screen-space preview; scene camera placement is not reproduced.");
            return metadata;
        }

        private static Canvas NewRoot(PreviewRenderUtility preview)
        {
            var root = EditorUtility.CreateGameObjectWithHideFlags("Prefab Preview Canvas", HideFlags.HideAndDontSave, typeof(RectTransform), typeof(Canvas));
            preview.AddSingleGO(root);
            return root.GetComponent<Canvas>();
        }

        private static RectTransform SnapshotParents(
            PreviewRenderUtility preview,
            Canvas source,
            RectTransform parent,
            Canvas target,
            JObject metadata,
            List<string> limitations
        )
        {
            var chain = new Stack<RectTransform>();
            for (var current = parent; current != source.transform; current = (RectTransform)current.parent)
                chain.Push(current);
            var destination = (RectTransform)target.transform;
            var omitted = new SortedSet<string>(StringComparer.Ordinal);
            CopyPresentation(source.gameObject, target.gameObject, omitted);
            foreach (var original in chain)
            {
                var node = EditorUtility.CreateGameObjectWithHideFlags(original.name, HideFlags.HideAndDontSave, typeof(RectTransform));
                preview.AddSingleGO(node);
                var copy = (RectTransform)node.transform;
                copy.SetParent(destination, false);
                copy.anchorMin = original.anchorMin;
                copy.anchorMax = original.anchorMax;
                copy.pivot = original.pivot;
                copy.sizeDelta = original.sizeDelta;
                copy.anchoredPosition3D = original.anchoredPosition3D;
                copy.localRotation = original.localRotation;
                copy.localScale = original.localScale;
                CopyPresentation(original.gameObject, node, omitted);
                destination = copy;
            }
            metadata["parent_layout"] = "rect_transform_snapshot";
            metadata["omitted_parent_components"] = new JArray(omitted);
            limitations.Add(
                "Parent context uses current RectTransform, CanvasGroup and RectMask2D snapshots. Siblings, parent visuals, layout controllers and scripts are not cloned; runtime Safe Area and sibling-dependent layout are not recomputed for the output resolution."
            );
            if (omitted.Count > 0)
                limitations.Add("Omitted parent components: " + string.Join(", ", omitted) + ".");
            return destination;
        }

        private static void CopyPresentation(GameObject source, GameObject destination, SortedSet<string> omitted)
        {
            foreach (var component in source.GetComponents<Component>())
            {
                if (component == null)
                {
                    omitted.Add("Missing script");
                    continue;
                }
                if (component is Transform || component is Canvas || component is Behaviour behaviour && IsCanvasScaler(behaviour))
                    continue;
                if (component is CanvasGroup group)
                {
                    var copy = destination.AddComponent<CanvasGroup>();
                    copy.alpha = group.alpha;
                    copy.interactable = group.interactable;
                    copy.blocksRaycasts = group.blocksRaycasts;
                    copy.ignoreParentGroups = group.ignoreParentGroups;
                    copy.enabled = group.enabled;
                }
                else if (component.GetType().FullName == "UnityEngine.UI.RectMask2D")
                {
                    // Exact built-in type only: never run a user-derived context component.
                    var copy = (Behaviour)destination.AddComponent(component.GetType());
                    foreach (string name in new[] { "padding", "softness" })
                    {
                        var property = component.GetType().GetProperty(name);
                        if (property != null)
                            property.SetValue(copy, property.GetValue(component));
                    }
                    copy.enabled = ((Behaviour)component).enabled;
                }
                else
                    omitted.Add(component.GetType().FullName);
            }
        }

        private static JObject Describe(Canvas canvas, Behaviour scaler, RectTransform parent, float scale, float pixelsPerUnit)
        {
            var result = new JObject
            {
                ["source"] =
                    parent != null ? "scene_parent"
                    : canvas != null ? "prefab_canvas"
                    : "default_canvas",
                ["framing"] = "full_viewport",
                ["scale_mode"] =
                    canvas != null && canvas.renderMode == RenderMode.WorldSpace ? "WorldSpace"
                    : scaler != null ? ScalerValue<object>(scaler, "uiScaleMode").ToString()
                    : "ConstantPixelSize",
                ["scale_factor"] = scale,
                ["reference_pixels_per_unit"] = pixelsPerUnit,
            };
            if (parent != null)
            {
                result["parent_instance_id"] = parent.gameObject.GetInstanceIDCompat();
                result["canvas_instance_id"] = canvas.gameObject.GetInstanceIDCompat();
                result["parent_name"] = parent.name;
            }
            if (scaler != null && (string)result["scale_mode"] == "ScaleWithScreenSize")
            {
                var reference = ScalerValue<Vector2>(scaler, "referenceResolution");
                result["reference_resolution"] = new JObject { ["x"] = reference.x, ["y"] = reference.y };
                result["screen_match_mode"] = ScalerValue<object>(scaler, "screenMatchMode").ToString();
                result["match_width_or_height"] = ScalerValue<float>(scaler, "matchWidthOrHeight");
            }
            if (scaler != null && (string)result["scale_mode"] == "ConstantPhysicalSize")
            {
                result["dpi"] = Screen.dpi == 0 ? ScalerValue<float>(scaler, "fallbackScreenDPI") : Screen.dpi;
                result["dpi_source"] = Screen.dpi == 0 ? "fallback_screen_dpi" : "editor_screen";
                result["physical_unit"] = ScalerValue<object>(scaler, "physicalUnit").ToString();
            }
            return result;
        }

        private static bool PositiveFinite(float value) => value > 0 && !float.IsInfinity(value) && !float.IsNaN(value);

        private static bool IsCanvasScaler(Behaviour behaviour)
        {
            for (Type type = behaviour?.GetType(); type != null; type = type.BaseType)
                if (type.FullName == "UnityEngine.UI.CanvasScaler")
                    return true;
            return false;
        }

        private static T ScalerValue<T>(Behaviour scaler, string property) => (T)scaler.GetType().GetProperty(property).GetValue(scaler);

        private static void CanvasScale(Behaviour scaler, RenderMode mode, int width, int height, out float scale, out float referencePixelsPerUnit)
        {
            // Matches the uGUI CanvasScaler policies, using the owned viewport rather than a display.
            referencePixelsPerUnit = ScalerValue<float>(scaler, "referencePixelsPerUnit");
            if (mode == RenderMode.WorldSpace)
            {
                scale = ScalerValue<float>(scaler, "dynamicPixelsPerUnit");
                return;
            }
            switch (ScalerValue<object>(scaler, "uiScaleMode").ToString())
            {
                case "ScaleWithScreenSize":
                    var reference = ScalerValue<Vector2>(scaler, "referenceResolution");
                    float horizontal = width / reference.x,
                        vertical = height / reference.y;
                    switch (ScalerValue<object>(scaler, "screenMatchMode").ToString())
                    {
                        case "Expand":
                            scale = Mathf.Min(horizontal, vertical);
                            break;
                        case "Shrink":
                            scale = Mathf.Max(horizontal, vertical);
                            break;
                        default:
                            scale = Mathf.Pow(
                                2,
                                Mathf.Lerp(Mathf.Log(horizontal, 2), Mathf.Log(vertical, 2), ScalerValue<float>(scaler, "matchWidthOrHeight"))
                            );
                            break;
                    }
                    break;
                case "ConstantPhysicalSize":
                    float dpi = Screen.dpi == 0 ? ScalerValue<float>(scaler, "fallbackScreenDPI") : Screen.dpi;
                    float unitsPerInch;
                    switch (ScalerValue<object>(scaler, "physicalUnit").ToString())
                    {
                        case "Centimeters":
                            unitsPerInch = 2.54f;
                            break;
                        case "Millimeters":
                            unitsPerInch = 25.4f;
                            break;
                        case "Inches":
                            unitsPerInch = 1f;
                            break;
                        case "Picas":
                            unitsPerInch = 6f;
                            break;
                        default:
                            unitsPerInch = 72f;
                            break;
                    }
                    scale = dpi / unitsPerInch;
                    referencePixelsPerUnit *= unitsPerInch / ScalerValue<float>(scaler, "defaultSpriteDPI");
                    break;
                default:
                    scale = ScalerValue<float>(scaler, "scaleFactor");
                    break;
            }
        }
    }
}
