using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Read-only uGUI inspection. Only exact, built-in layout types execute in a preview scene.</summary>
    internal static class UguiDiagnostics
    {
        private const int PreviewLimit = 1000;
        private const int FindingLimit = 2000;
        private const int PairLimit = 20000;
        private static readonly HashSet<string> PreviewTypes = new HashSet<string>(StringComparer.Ordinal)
        {
            "UnityEngine.UI.HorizontalLayoutGroup",
            "UnityEngine.UI.VerticalLayoutGroup",
            "UnityEngine.UI.GridLayoutGroup",
            "UnityEngine.UI.LayoutElement",
            "UnityEngine.UI.ContentSizeFitter",
            "UnityEngine.UI.AspectRatioFitter",
            "UnityEngine.UI.Text",
            "UnityEngine.UI.Image",
            "UnityEngine.UI.RawImage"
        };
        private sealed class Node
        {
            public RectTransform Source;
            public RectTransform Preview;
            public string Path;
            public int Order;
            public bool Visible;
            public bool PointerActive;
            public bool Interactive;
            public Transform Receiver;
            public bool Raycast;
            public Component Text;
            public Component PreviewText;
            public Rect Bounds;
            public Rect HitBounds;
            public Rect RaycastBounds;
            public Vector4 RaycastPadding;
            public bool UsesRectMaskCulling;
            public bool RectMask;
            public bool StencilMask;
            public bool PointerStencilMask;
            public Vector4 MaskPadding;
            public MaskChain VisualMasks;
            public MaskChain PointerMasks;
            public bool NeedsPointerMaskBounds;
            public Rect UnpaddedMaskBounds;
            public Rect RectangularClipBounds;
            public Rect VisualMaskBounds;
            public Rect PointerMaskBounds;
        }

        private sealed class MaskChain
        {
            public Node Mask;
            public MaskChain Parent;
        }

        private sealed class PreviewBranch
        {
            public Transform Source;
            public Transform Preview;
            public int NextChild;
        }

        internal static object Diagnose(GameObject root, JArray resolutions, bool includeInactive, int maxNodes = 200)
        {
            if (root == null || !(root.transform is RectTransform))
                return new ErrorResponse("invalid_target", new { message = "Diagnose requires a uGUI RectTransform target." });
            if (maxNodes < 1 || maxNodes > 1000)
                return new ErrorResponse("invalid_max_nodes", new { message = "max_nodes must be between 1 and 1000 (default 200)." });
            var canvas = root.GetComponentInParent<Canvas>(true);
            if (canvas == null)
                return new ErrorResponse("missing_canvas", new { message = "The target must belong to a Canvas." });
            canvas = canvas.rootCanvas;
            if (canvas.renderMode != RenderMode.ScreenSpaceOverlay)
                return new ErrorResponse("unsupported_canvas_mode", new { message = "Resolution diagnostics currently support Screen Space Overlay. Camera and World Space require camera projection and are not evaluated as screen pixels." });
            var sizes = new List<Vector2>();
            if (resolutions != null)
            {
                if (resolutions.Count < 1 || resolutions.Count > 8)
                    return new ErrorResponse("invalid_resolutions", new { message = "resolutions must contain 1 to 8 {width,height} objects." });
                foreach (var token in resolutions)
                {
                    if (!(token is JObject obj) || !TryDimension(obj["width"], out int width) || !TryDimension(obj["height"], out int height))
                        return new ErrorResponse("invalid_resolutions", new { message = "Resolution width and height must be integer pixels between 64 and 8192." });
                    sizes.Add(new Vector2(width, height));
                }
            }

            var canvasRect = canvas.transform as RectTransform;
            if (canvasRect == null)
                return new ErrorResponse("invalid_canvas", new { message = "Canvas has no RectTransform." });
            var componentTypes = new Dictionary<string, Type>(StringComparer.Ordinal);
            var anyTypes = new Dictionary<string, Type>(StringComparer.Ordinal);
            var scaler = ComponentNamed(canvas.gameObject, "UnityEngine.UI.CanvasScaler", componentTypes);
            if (Enabled(scaler) && EnumValue(scaler, "uiScaleMode") == 2)
                return new ErrorResponse("unsupported_physical_scaling", new { message = "Constant Physical Size depends on actual device DPI, which a resolution alone does not supply." });
            bool current = resolutions == null;
            if (current)
            {
                Vector2 size = canvasRect.rect.size * Mathf.Max(0.0001f, canvas.scaleFactor);
                if (size.x <= 0 || size.y <= 0)
                    size = Read(scaler, "referenceResolution", new Vector2(800, 600));
                sizes.Add(size);
            }

            var limitations = new HashSet<string>(StringComparer.Ordinal)
            {
                "Rectangles are axis-aligned canvas-space bounds; rotation, sprite alpha, stencil shapes and custom raycast filters are not pixel-tested.",
                "Overlap and raycast-blocker reports are candidates for review; intended overlays and event routing can make overlaps valid.",
                "Interaction order uses hierarchy order on the root Canvas; nested Canvas sorting overrides, raycaster blocking objects and input-module configuration require runtime verification.",
                "Animations, custom scripts/layout controllers, camera/world-space projection and runtime event handlers are not simulated.",
                "TextMeshPro uses a conservative character/font-size estimate without executing TMP callbacks or changing its font atlas; rich-text shaping, wrapping, autosizing and TMP layout contribution are not simulated."
            };
            if (current)
                limitations.Add("No explicit resolutions supplied: evaluated the current Canvas size (reference resolution fallback if unavailable), not a future Game View resize.");
            var findings = new JArray();
            var geometry = new JArray();
            var summaries = new JArray();
            var scene = default(Scene);
            bool truncated = false;
            try
            {
                scene = EditorSceneManager.NewPreviewScene();
                var previewRoot = new GameObject("__McpUguiDiagnostics", typeof(RectTransform));
                previewRoot.hideFlags = HideFlags.HideAndDontSave;
                previewRoot.SetActive(false);
                SceneManager.MoveGameObjectToScene(previewRoot, scene);
                var nodes = new List<Node>();
                var bySource = new Dictionary<RectTransform, Node>();
                var maskChains = new Dictionary<Transform, MaskChain>();
                var maskNodes = new List<Node>();
                // Keep missing optional types local too: the shared resolver caches successful lookups only.
                var graphicType = ResolveType("UnityEngine.UI.Graphic", componentTypes, true);
                var maskableGraphicType = ResolveType("UnityEngine.UI.MaskableGraphic", componentTypes, true);
                var rectMaskType = ResolveType("UnityEngine.UI.RectMask2D", componentTypes, true);
                var maskType = ResolveType("UnityEngine.UI.Mask", componentTypes, true);
                var branches = new Stack<PreviewBranch>();
                Transform nextSource = canvasRect;
                Transform previewParent = null;
                int scanned = 0;
                while (nextSource != null && scanned < PreviewLimit)
                {
                    scanned++;
                    Transform source = nextSource;
                    MaskChain inheritedMasks = null;
                    if (source.parent != null)
                        maskChains.TryGetValue(source.parent, out inheritedMasks);
                    var sourceCanvasBoundary = source.GetComponent<Canvas>();
                    if (sourceCanvasBoundary != null && sourceCanvasBoundary.overrideSorting)
                        inheritedMasks = null;
                    MaskChain pointerMasks = inheritedMasks;
                    GameObject clone;
                    if (source == canvasRect)
                        clone = previewRoot;
                    else
                    {
                        clone = new GameObject(source.name, source is RectTransform ? typeof(RectTransform) : typeof(Transform));
                        clone.hideFlags = HideFlags.HideAndDontSave;
                        clone.SetActive(false);
                        clone.transform.SetParent(previewParent, false);
                        clone.transform.localPosition = source.localPosition;
                        clone.transform.localRotation = source.localRotation;
                        clone.transform.localScale = source.localScale;
                    }

                    if (source is RectTransform rect)
                    {
                        var preview = (RectTransform)clone.transform;
                        if (source != canvasRect)
                            CopyRect(rect, preview);
                        var node = new Node
                        {
                            Source = rect,
                            Preview = preview,
                            Path = Path(source),
                            Order = scanned
                        };
                        bySource.Add(rect, node);
                        var graphic = graphicType == null ? null : source.GetComponent(graphicType);
                        node.RaycastPadding = Read(graphic, "raycastPadding", Vector4.zero);
                        var maskableGraphic = maskableGraphicType == null ? null : source.GetComponent(maskableGraphicType);
                        bool maskable = Read(maskableGraphic, "maskable", true);
                        node.UsesRectMaskCulling = maskableGraphic != null && maskable;
                        var rectMask = rectMaskType == null ? null : source.GetComponent(rectMaskType);
                        node.RectMask = source.gameObject.activeInHierarchy && Enabled(rectMask);
                        node.PointerStencilMask = source.gameObject.activeInHierarchy && maskType != null && Enabled(source.GetComponent(maskType));
                        // An enabled Mask filters pointers even without the active Graphic needed for stencil rendering.
                        node.StencilMask = node.PointerStencilMask && Enabled(graphic);
                        node.MaskPadding = Read(rectMask, "padding", Vector4.zero);
                        node.VisualMasks = maskable ? inheritedMasks : null;
                        if (node.RectMask || node.PointerStencilMask)
                        {
                            pointerMasks = new MaskChain
                            {
                                Mask = node,
                                Parent = inheritedMasks
                            };
                            maskNodes.Add(node);
                        }

                        node.PointerMasks = maskable ? pointerMasks : null;
                        foreach (var component in source.GetComponents<Component>())
                        {
                            if (component == null)
                                continue;
                            string name = component.GetType().FullName;
                            if (PreviewTypes.Contains(name))
                            {
                                // A root screen-space Canvas makes AspectRatioFitter inert. The preview
                                // deliberately uses WorldSpace, so preserve the source's eligibility.
                                if (name == "UnityEngine.UI.AspectRatioFitter" && !AspectEligible(component, componentTypes))
                                    continue;
                                var copy = clone.AddComponent(component.GetType());
                                EditorUtility.CopySerialized(component, copy);
                                if (name == "UnityEngine.UI.Text")
                                {
                                    node.Text = component;
                                    node.PreviewText = copy;
                                }
                            }
                            else if (component is Canvas sourceCanvas)
                            {
                                // WorldSpace supplies public Graphic density context without letting a
                                // screen-space Canvas resize the diagnostic-controlled root rectangle.
                                var copy = clone.AddComponent<Canvas>();
                                copy.renderMode = RenderMode.WorldSpace;
                                copy.referencePixelsPerUnit = sourceCanvas.referencePixelsPerUnit;
                                copy.scaleFactor = sourceCanvas.scaleFactor;
                                copy.overrideSorting = sourceCanvas.overrideSorting;
                                copy.enabled = sourceCanvas.enabled;
                            }
                            else if (name == "TMPro.TextMeshProUGUI")
                            {
                                node.Text = component;
                                limitations.Add("TMP text components were omitted from preview layout providers; containers relying on TMP preferred sizes require Game View verification.");
                            }
                            else if (component is MonoBehaviour && HasLayoutInterface(component.GetType()))
                                limitations.Add("Custom layout controllers/elements were omitted from the preview; affected geometry may differ at runtime.");
                        }

                        if ((source == root.transform || source.IsChildOf(root.transform)) && (includeInactive || source.gameObject.activeInHierarchy))
                        {
                            if (nodes.Count < maxNodes)
                                nodes.Add(node);
                            else
                                truncated = true;
                        }
                    }

                    // Plain Transform containers share the inherited chain without adding a filter.
                    maskChains.Add(source, pointerMasks);
                    // Keep inactive objects inactive so the normal layout exclusion rules still apply.
                    if (source != canvasRect)
                        clone.SetActive(source.gameObject.activeSelf);
                    // Advance depth-first on demand so the preview limit also bounds sibling lookups.
                    if (source.childCount > 0)
                    {
                        branches.Push(new PreviewBranch
                        {
                            Source = source,
                            Preview = clone.transform,
                            NextChild = 1
                        });
                        nextSource = source.GetChild(0);
                        previewParent = clone.transform;
                    }
                    else
                    {
                        nextSource = null;
                        while (branches.Count > 0)
                        {
                            var branch = branches.Peek();
                            if (branch.NextChild >= branch.Source.childCount)
                            {
                                branches.Pop();
                                continue;
                            }

                            nextSource = branch.Source.GetChild(branch.NextChild++);
                            previewParent = branch.Preview;
                            break;
                        }
                    }
                }

                if (nextSource != null)
                {
                    truncated = true;
                    limitations.Add("Preview context exceeded 1000 transforms; omitted siblings/children can change layout. Rerun on a smaller Canvas.");
                }

                if (!bySource.ContainsKey((RectTransform)root.transform))
                    return new ErrorResponse("preview_limit_exceeded", new { message = "Target was outside the bounded Canvas preview. Diagnose a smaller Canvas hierarchy." });
                var previewCanvas = (RectTransform)previewRoot.transform;
                previewCanvas.pivot = new Vector2(0.5f, 0.5f);
                previewCanvas.localScale = Vector3.one;
                previewCanvas.localRotation = Quaternion.identity;
                previewCanvas.localPosition = Vector3.zero;
                previewRoot.SetActive(canvas.gameObject.activeInHierarchy);
                var previewRects = bySource.Values.OrderBy(n => n.Order).Select(n => n.Preview).ToArray();
                bool hasInteractions = false;
                foreach (var node in nodes)
                {
                    node.Visible = IsVisible(node.Source, componentTypes);
                    node.PointerActive = IsActiveForCanvas(node.Source, componentTypes);
                    node.Raycast = IsRaycastGraphic(node.Source.gameObject, componentTypes) && GroupsAllow(node.Source, false);
                    node.Receiver = FindInteractiveReceiver(node.Source, componentTypes);
                    node.Interactive = node.Receiver != null && GroupsAllow(node.Receiver, true);
                    hasInteractions |= node.PointerActive && node.Interactive && node.Raycast;
                    if (node.PointerActive && node.Raycast)
                        for (var filter = node.PointerMasks; filter != null; filter = filter.Parent)
                            filter.Mask.NeedsPointerMaskBounds = true;
                    LayoutFindings(node, findings, componentTypes, anyTypes, ref truncated);
                }

                if (hasInteractions)
                {
                    Type eventType = ResolveType("UnityEngine.EventSystems.EventSystem", componentTypes, true);
                    bool eventSystem = eventType != null && UnityEngine.Resources.FindObjectsOfTypeAll(eventType)
                        .OfType<Component>()
                        .Any(c => c.gameObject.scene.IsValid()
                            && !EditorSceneManager.IsPreviewScene(c.gameObject.scene)
                            && c.gameObject.activeInHierarchy
                            && Enabled(c));
                    if (!eventSystem)
                        Add(
                            findings,
                            "missing_event_system",
                            "warning",
                            bySource[canvasRect],
                            "No enabled EventSystem was found in loaded scenes. Add an EventSystem and the appropriate input module; prefab assets may receive these from the host scene.",
                            null,
                            null,
                            ref truncated
                        );
                    var reportedCanvases = new HashSet<Canvas>();
                    foreach (var node in nodes.Where(n => n.PointerActive && n.Interactive && n.Raycast))
                    {
                        var ownCanvas = FindActiveCanvas(node.Source);
                        if (ownCanvas != null && reportedCanvases.Add(ownCanvas) && !Enabled(ComponentNamed(ownCanvas.gameObject, "UnityEngine.UI.GraphicRaycaster", componentTypes)))
                            Add(
                                findings,
                                "missing_graphic_raycaster",
                                "warning",
                                node,
                                "The nearest Canvas has no enabled GraphicRaycaster. Add one to that Canvas so its interactive Graphics receive pointer events.",
                                bySource.TryGetValue(ownCanvas.transform as RectTransform, out Node relatedCanvas) ? relatedCanvas : null,
                                null,
                                ref truncated
                            );
                    }
                }

                var previewRootCanvas = previewRoot.GetComponent<Canvas>();
                foreach (Vector2 size in sizes)
                {
                    float scale = current ? Mathf.Max(0.0001f, canvas.scaleFactor) : Scale(scaler, canvas.scaleFactor, size);
                    previewRootCanvas.scaleFactor = scale;
                    scale = previewRootCanvas.scaleFactor;
                    previewCanvas.sizeDelta = size / scale;
                    Rebuild(previewRects, componentTypes, anyTypes);
                    var resolution = new JObject
                    {
                        ["width"] = size.x,
                        ["height"] = size.y,
                        ["scaleFactor"] = scale,
                        ["mode"] = current ? "current" : "preview"
                    };
                    Rect canvasBounds = new Rect(Vector2.zero, size);
                    int before = findings.Count;
                    foreach (var mask in maskNodes)
                    {
                        Rect rawBounds = Bounds(mask.Preview, previewCanvas, scale, size);
                        Rect clipBounds = mask.RectMask ? RectMaskBounds(mask.Preview, previewCanvas, scale, size) : rawBounds;
                        mask.UnpaddedMaskBounds = clipBounds;
                        Vector4 padding = mask.MaskPadding * scale;
                        mask.RectangularClipBounds = mask.RectMask
                            ? new Rect(
                                clipBounds.xMin + padding.x,
                                clipBounds.yMin + padding.y,
                                Mathf.Max(0, clipBounds.width - padding.x - padding.z),
                                Mathf.Max(0, clipBounds.height - padding.y - padding.w)
                            )
                            : rawBounds;
                        mask.VisualMaskBounds = mask.RectangularClipBounds;
                        if (mask.RectMask && mask.StencilMask)
                            mask.VisualMaskBounds = Intersect(mask.VisualMaskBounds, rawBounds);
                        if (mask.NeedsPointerMaskBounds)
                        {
                            mask.PointerMaskBounds = mask.RectMask && mask.MaskPadding != Vector4.zero
                                ? PaddedBounds(mask.Preview, previewCanvas, scale, size, mask.MaskPadding)
                                : rawBounds;
                            if (mask.PointerStencilMask)
                                mask.PointerMaskBounds = Intersect(mask.PointerMaskBounds, rawBounds);
                        }
                    }

                    foreach (var node in nodes)
                    {
                        node.Bounds = Bounds(node.Preview, previewCanvas, scale, size);
                        node.HitBounds = Intersect(node.Bounds, canvasBounds);
                        Rect rectangularClip = default;
                        Rect rectangularMaskBounds = default;
                        bool hasRectangularClip = false;
                        for (var filter = node.VisualMasks; filter != null; filter = filter.Parent)
                        {
                            Node mask = filter.Mask;
                            if (!mask.RectMask && !mask.StencilMask)
                                continue;
                            node.HitBounds = Intersect(node.HitBounds, mask.VisualMaskBounds);
                            if (mask.RectMask)
                            {
                                if (!hasRectangularClip)
                                    rectangularMaskBounds = mask.UnpaddedMaskBounds;
                                rectangularClip = hasRectangularClip
                                    ? Intersect(rectangularClip, mask.RectangularClipBounds)
                                    : mask.RectangularClipBounds;
                                hasRectangularClip = true;
                            }
                            if (node.Visible && !Contains(mask.VisualMaskBounds, node.Bounds))
                                Add(
                                    findings,
                                    "clipped_by_mask",
                                    "candidate",
                                    node,
                                    "Bounds extend beyond an enabled ancestor Mask/RectMask2D. Review scrolling and intentional clipping; stencil masks are approximated by their rectangle.",
                                    mask,
                                    resolution,
                                    ref truncated
                                );
                        }

                        // Graphic.Raycast checks filters on the Graphic itself as well as its ancestors.
                        // Pointer padding is local to each filter; rendering padding remains canvas-space.
                        if (node.PointerActive && node.Raycast)
                        {
                            // Native RectMask2D culling rejects the whole renderer before pointer filters.
                            // Same-object masks and stencil-only approximations do not apply this gate.
                            // Native renderer overlap can retain zero-area rects with expanded pointer padding.
                            // The nearest mask compares the clip to its own rect in root Canvas coordinates.
                            bool rectMaskCulled = node.UsesRectMaskCulling && hasRectangularClip
                                && (rectangularClip.width <= 0 || rectangularClip.height <= 0
                                    || !rectangularClip.Overlaps(rectangularMaskBounds, true)
                                    || !rectangularClip.Overlaps(node.Bounds, true));
                            if (rectMaskCulled)
                                node.RaycastBounds = new Rect();
                            else
                            {
                                node.RaycastBounds = node.RaycastPadding == Vector4.zero
                                    ? Intersect(node.Bounds, canvasBounds)
                                    : Intersect(PaddedBounds(node.Preview, previewCanvas, scale, size, node.RaycastPadding), canvasBounds);
                                for (var filter = node.PointerMasks; filter != null; filter = filter.Parent)
                                    node.RaycastBounds = Intersect(node.RaycastBounds, filter.Mask.PointerMaskBounds);
                            }
                        }

                        geometry.Add(new JObject
                        {
                            ["path"] = node.Path,
                            ["instanceID"] = node.Source.gameObject.GetInstanceIDCompat(),
                            ["resolution"] = resolution.DeepClone(),
                            ["rect"] = RectJson(node.Bounds),
                            ["visibleRect"] = RectJson(node.HitBounds),
                            ["raycastRect"] = node.PointerActive && node.Raycast ? RectJson(node.RaycastBounds) : null,
                            ["active"] = node.Source.gameObject.activeInHierarchy,
                            ["visible"] = node.Visible
                        });
                        if (!node.Visible)
                            continue;
                        if (node.Preview.rect.width <= 0.01f || node.Preview.rect.height <= 0.01f)
                            Add(
                                findings,
                                "zero_size",
                                "warning",
                                node,
                                "The evaluated rectangle has zero width or height. Check sizeDelta, anchors and layout size providers.",
                                null,
                                resolution,
                                ref truncated
                            );
                        else if (!Contains(canvasBounds, node.Bounds))
                            Add(
                                findings,
                                "off_canvas",
                                "candidate",
                                node,
                                "Bounds extend outside the Canvas. Review anchors, offsets and intentionally off-screen content.",
                                null,
                                resolution,
                                ref truncated
                            );
                        TextFindings(node, resolution, findings, ref truncated);
                    }

                    int pairs = 0;
                    for (int i = 0; i < nodes.Count && pairs < PairLimit; i++)
                    {
                        Node first = nodes[i];
                        if (!first.PointerActive || !first.Interactive || !first.Raycast)
                            continue;
                        for (int j = 0; j < nodes.Count && pairs < PairLimit; j++)
                        {
                            Node second = nodes[j];
                            if (i == j || !second.PointerActive || !second.Raycast)
                                continue;
                            pairs++;
                            if ((first.Receiver != null && first.Receiver == second.Receiver)
                                || !Overlaps(first.RaycastBounds, second.RaycastBounds)
                                || first.Source.IsChildOf(second.Source)
                                || second.Source.IsChildOf(first.Source))
                                continue;
                            if (second.Interactive && j > i)
                                Add(
                                    findings,
                                    "interactive_overlap",
                                    "candidate",
                                    first,
                                    "Interactive rectangles overlap. Check intentional stacking and pointer navigation at this resolution.",
                                    second,
                                    resolution,
                                    ref truncated
                                );
                            else if (!second.Interactive && second.Order > first.Order)
                                Add(
                                    findings,
                                    "raycast_blocker",
                                    "candidate",
                                    first,
                                    "A later non-interactive Graphic with raycastTarget enabled overlaps this interactive rectangle. Review draw order, custom filters and whether raycastTarget can be disabled.",
                                    second,
                                    resolution,
                                    ref truncated
                                );
                        }
                    }

                    if (pairs >= PairLimit)
                    {
                        truncated = true;
                        limitations.Add("Interaction comparisons are capped at 20000 pairs per resolution; candidates may be omitted.");
                    }

                    summaries.Add(new JObject
                    {
                        ["resolution"] = resolution,
                        ["nodesEvaluated"] = nodes.Count,
                        ["findingsReturned"] = findings.Count - before,
                        ["interactionPairsChecked"] = pairs
                    });
                }

                return new SuccessResponse("uGUI diagnostics complete.", new
                {
                    findings,
                    rects = geometry,
                    resolutions = summaries,
                    counts = new
                    {
                        nodes = nodes.Count,
                        findings = findings.Count,
                        warnings = findings.Count(f => (string)f["severity"] == "warning"),
                        candidates = findings.Count(f => (string)f["severity"] == "candidate")
                    },
                    truncated,
                    limitations = limitations.OrderBy(x => x).ToArray(),
                    evaluation = "sanitized_layout_preview"
                });
            }
            catch (Exception ex)
            {
                return new ErrorResponse("diagnostics_failed", new { message = ex.GetBaseException().Message });
            }
            finally
            {
                if (scene.IsValid())
                    EditorSceneManager.ClosePreviewScene(scene);
            }
        }

        private static bool TryDimension(JToken value, out int result)
        {
            result = 0;
            return value != null && value.Type == JTokenType.Integer && int.TryParse(value.ToString(), out result) && result >= 64 && result <= 8192;
        }

        private static void CopyRect(RectTransform source, RectTransform target)
        {
            target.anchorMin = source.anchorMin;
            target.anchorMax = source.anchorMax;
            target.pivot = source.pivot;
            target.sizeDelta = source.sizeDelta;
            target.anchoredPosition3D = source.anchoredPosition3D;
        }

        private static Type ResolveType(string name, Dictionary<string, Type> types, bool component)
        {
            if (!types.TryGetValue(name, out Type type))
            {
                type = component ? UnityTypeResolver.ResolveComponent(name) : UnityTypeResolver.ResolveAny(name);
                types.Add(name, type);
            }

            return type;
        }

        private static Component ComponentNamed(GameObject go, string name, Dictionary<string, Type> componentTypes)
        {
            Type type = ResolveType(name, componentTypes, true);
            return type == null ? null : go.GetComponent(type);
        }

        private static T Read<T>(object target, string name, T fallback)
        {
            if (target == null)
                return fallback;
            var property = target.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public);
            if (property == null || !property.CanRead)
                return fallback;
            object value = property.GetValue(target);
            return value is T typed ? typed : fallback;
        }

        private static bool Enabled(Component component) => component != null && (!(component is Behaviour behaviour) || behaviour.enabled);
        private static bool HasLayoutInterface(Type type) => type.GetInterfaces().Any(t => t.FullName == "UnityEngine.UI.ILayoutController" || t.FullName == "UnityEngine.UI.ILayoutElement");
        private static float Scale(Component scaler, float fallback, Vector2 size)
        {
            if (!Enabled(scaler))
                return Mathf.Max(0.0001f, fallback);
            object modeValue = scaler.GetType().GetProperty("uiScaleMode")?.GetValue(scaler);
            int mode = modeValue == null ? 0 : Convert.ToInt32(modeValue);
            if (mode == 0)
                return Mathf.Max(0.0001f, Read(scaler, "scaleFactor", 1f));
            Vector2 reference = Read(scaler, "referenceResolution", new Vector2(800, 600));
            float x = size.x / reference.x, y = size.y / reference.y;
            object matchValue = scaler.GetType().GetProperty("screenMatchMode")?.GetValue(scaler);
            int match = matchValue == null ? 0 : Convert.ToInt32(matchValue);
            if (match == 1)
                return Mathf.Min(x, y);
            if (match == 2)
                return Mathf.Max(x, y);
            return Mathf.Pow(2, Mathf.Lerp(Mathf.Log(x, 2), Mathf.Log(y, 2), Read(scaler, "matchWidthOrHeight", 0f)));
        }

        private static void Rebuild(RectTransform[] rects, Dictionary<string, Type> componentTypes, Dictionary<string, Type> anyTypes)
        {
            var type = ResolveType("UnityEngine.UI.LayoutRebuilder", anyTypes, false);
            var method = type?.GetMethod("ForceRebuildLayoutImmediate", BindingFlags.Public | BindingFlags.Static);
            var controllerType = ResolveType("UnityEngine.UI.ILayoutController", anyTypes, false);
            var groupType = ResolveType("UnityEngine.UI.LayoutGroup", componentTypes, true);
            if (method == null || controllerType == null || groupType == null)
                return;
            var roots = new List<RectTransform>();
            foreach (var rect in rects)
            {
                if (!rect.gameObject.activeInHierarchy)
                    continue;
                if (rect.parent != null && Enabled(rect.parent.GetComponent(groupType)))
                    continue;
                if (rect.GetComponents(controllerType).Any(Enabled))
                    roots.Add(rect);
            }

            // An enabled parent LayoutGroup already rebuilds its child controllers.
            // Keep separate roots behind plain/disabled parents, resolving preferred sizes
            // bottom-up before the final top-down placement without repeating every subtree.
            for (int i = roots.Count - 1; i >= 0; i--)
                method.Invoke(null, new object[] { roots[i] });
            for (int i = 0; i < roots.Count; i++)
                method.Invoke(null, new object[] { roots[i] });
        }

        private static Rect Bounds(RectTransform rect, RectTransform canvas, float scale, Vector2 size)
        {
            var corners = new Vector3[4];
            rect.GetWorldCorners(corners);
            Vector2 min = new Vector2(float.PositiveInfinity, float.PositiveInfinity), max = new Vector2(float.NegativeInfinity, float.NegativeInfinity);
            foreach (var corner in corners)
            {
                Vector2 point = (Vector2)canvas.InverseTransformPoint(corner) * scale + size * 0.5f;
                min = Vector2.Min(min, point);
                max = Vector2.Max(max, point);
            }

            return Rect.MinMaxRect(min.x, min.y, max.x, max.y);
        }

        private static Rect RectMaskBounds(RectTransform rect, RectTransform canvas, float scale, Vector2 size)
        {
            // RectangularVertexClipper retains signed corner0-to-corner2 extents for rendering.
            var corners = new Vector3[4];
            rect.GetWorldCorners(corners);
            Vector2 first = (Vector2)canvas.InverseTransformPoint(corners[0]) * scale + size * 0.5f;
            Vector2 last = (Vector2)canvas.InverseTransformPoint(corners[2]) * scale + size * 0.5f;
            return new Rect(first, last - first);
        }

        private static Rect PaddedBounds(RectTransform rect, RectTransform canvas, float scale, Vector2 size, Vector4 padding)
        {
            if (padding == Vector4.zero)
                return Bounds(rect, canvas, scale, size);
            Rect local = rect.rect;
            float left = local.xMin + padding.x;
            float bottom = local.yMin + padding.y;
            float right = local.xMax - padding.z;
            float top = local.yMax - padding.w;
            // Native hit testing accepts reversed padded edges; transformed corners normalize their bounds.
            var corners = new[]
            {
                new Vector3(left, bottom, 0),
                new Vector3(left, top, 0),
                new Vector3(right, top, 0),
                new Vector3(right, bottom, 0)
            };
            Vector2 min = new Vector2(float.PositiveInfinity, float.PositiveInfinity);
            Vector2 max = new Vector2(float.NegativeInfinity, float.NegativeInfinity);
            foreach (var corner in corners)
            {
                Vector2 point = (Vector2)canvas.InverseTransformPoint(rect.TransformPoint(corner)) * scale + size * 0.5f;
                min = Vector2.Min(min, point);
                max = Vector2.Max(max, point);
            }

            return Rect.MinMaxRect(min.x, min.y, max.x, max.y);
        }

        private static bool IsActiveForCanvas(RectTransform rect, Dictionary<string, Type> componentTypes)
        {
            if (!rect.gameObject.activeInHierarchy || FindActiveCanvas(rect) == null)
                return false;
            var graphic = ComponentNamed(rect.gameObject, "UnityEngine.UI.Graphic", componentTypes);
            return graphic == null || Enabled(graphic);
        }

        private static Canvas FindActiveCanvas(Transform source)
        {
            // Graphic.CacheCanvas skips disabled nested Canvases and falls back to an enabled ancestor.
            for (Transform t = source; t != null; t = t.parent)
            {
                var canvas = t.GetComponent<Canvas>();
                if (canvas != null && canvas.isActiveAndEnabled)
                    return canvas;
            }

            return null;
        }

        private static bool IsVisible(RectTransform rect, Dictionary<string, Type> componentTypes)
        {
            if (!IsActiveForCanvas(rect, componentTypes))
                return false;
            for (Transform t = rect; t != null; t = t.parent)
            {
                bool ignoreParents = false;
                foreach (var group in t.GetComponents<CanvasGroup>())
                {
                    if (!group.enabled)
                        continue;
                    if (group.alpha <= 0.001f)
                        return false;
                    ignoreParents |= group.ignoreParentGroups;
                }

                if (ignoreParents)
                    break;
            }

            var graphic = ComponentNamed(rect.gameObject, "UnityEngine.UI.Graphic", componentTypes);
            if (graphic == null)
                return true;
            if (!Enabled(graphic) || !(Read(graphic, "color", Color.white).a > 0.001f))
                return false;
            var renderer = rect.GetComponent<CanvasRenderer>();
            if (renderer != null && !(renderer.GetAlpha() > 0.001f))
                return false;
            var mask = ComponentNamed(rect.gameObject, "UnityEngine.UI.Mask", componentTypes);
            return !Enabled(mask) || Read(mask, "showMaskGraphic", true);
        }

        private static bool GroupsAllow(Transform rect, bool interactive)
        {
            for (Transform t = rect; t != null; t = t.parent)
            {
                foreach (var group in t.GetComponents<CanvasGroup>())
                {
                    if (interactive)
                    {
                        if (group.enabled && !group.interactable)
                            return false;
                        // Selectable checks this boundary even on a disabled CanvasGroup.
                        if (group.ignoreParentGroups)
                            return true;
                    }
                    else
                    {
                        if (!group.enabled)
                            continue;
                        if (!group.blocksRaycasts)
                            return false;
                        if (group.ignoreParentGroups)
                            return true;
                    }
                }

                if (!interactive)
                {
                    var canvas = t.GetComponent<Canvas>();
                    if (canvas != null && canvas.overrideSorting)
                        break;
                }
            }

            return true;
        }

        private static Transform FindInteractiveReceiver(Transform source, Dictionary<string, Type> componentTypes)
        {
            // targetGraphic selects transition visuals; raycast Graphics can be on this object or a child.
            for (Transform t = source; t != null; t = t.parent)
            {
                var selectable = ComponentNamed(t.gameObject, "UnityEngine.UI.Selectable", componentTypes);
                if (!Enabled(selectable) || !t.gameObject.activeInHierarchy)
                    continue;
                return Read(selectable, "interactable", true) ? t : null;
            }

            return null;
        }

        private static bool IsRaycastGraphic(GameObject go, Dictionary<string, Type> componentTypes)
        {
            var graphic = ComponentNamed(go, "UnityEngine.UI.Graphic", componentTypes);
            return Enabled(graphic) && Read(graphic, "raycastTarget", false);
        }

        private static void LayoutFindings(Node node, JArray findings, Dictionary<string, Type> componentTypes, Dictionary<string, Type> anyTypes, ref bool truncated)
        {
            if (!node.Source.gameObject.activeInHierarchy)
                return;
            var fitterType = ResolveType("UnityEngine.UI.ContentSizeFitter", componentTypes, true);
            var fitters = fitterType == null ? Array.Empty<Component>() : node.Source.GetComponents(fitterType);
            var aspect = ComponentNamed(node.Source.gameObject, "UnityEngine.UI.AspectRatioFitter", componentTypes);
            bool fitsX = fitters.Any(fitter => Fit(fitter, "horizontalFit"));
            bool fitsY = fitters.Any(fitter => Fit(fitter, "verticalFit"));
            int aspectMode = Enabled(aspect) && AspectEligible(aspect, componentTypes) ? EnumValue(aspect, "aspectMode") : 0;
            bool aspectX = aspectMode == 2 || aspectMode == 3 || aspectMode == 4;
            bool aspectY = aspectMode == 1 || aspectMode == 3 || aspectMode == 4;
            if ((fitsX && aspectX) || (fitsY && aspectY))
                Add(
                    findings,
                    "layout_driver_conflict",
                    "warning",
                    node,
                    "ContentSizeFitter and AspectRatioFitter both drive this rectangle. Assign one size controller per axis.",
                    null,
                    null,
                    ref truncated
                );
            if (node.Source.parent == null)
                return;
            if (IgnoredByParentLayout(node.Source, anyTypes))
                return;
            var parent = node.Source.parent.gameObject;
            var grid = ComponentNamed(parent, "UnityEngine.UI.GridLayoutGroup", componentTypes);
            var group = ComponentNamed(parent, "UnityEngine.UI.HorizontalOrVerticalLayoutGroup", componentTypes);
            bool parentX = Enabled(grid) || (Enabled(group) && Read(group, "childControlWidth", false));
            bool parentY = Enabled(grid) || (Enabled(group) && Read(group, "childControlHeight", false));
            if (((fitsX || aspectX) && parentX) || ((fitsY || aspectY) && parentY))
                Add(
                    findings,
                    "layout_driver_conflict",
                    "warning",
                    node,
                    "A parent LayoutGroup and this child's ContentSizeFitter/AspectRatioFitter drive the same size axis. Disable parent childControlWidth/Height or remove the conflicting fitter axis.",
                    null,
                    null,
                    ref truncated
                );
        }

        private static bool IgnoredByParentLayout(RectTransform rect, Dictionary<string, Type> anyTypes)
        {
            var type = ResolveType("UnityEngine.UI.ILayoutIgnorer", anyTypes, false);
            var property = type?.GetProperty("ignoreLayout");
            if (property == null)
                return false;
            var ignorers = rect.GetComponents(type);
            if (ignorers.Length == 0)
                return false;
            // LayoutGroup does not filter disabled ignorers and includes a child if any one opts in.
            return ignorers.All(component => (bool)property.GetValue(component));
        }

        private static int EnumValue(Component component, string property)
        {
            object value = component?.GetType().GetProperty(property)?.GetValue(component);
            return value == null ? 0 : Convert.ToInt32(value);
        }

        private static bool Fit(Component component, string property) => Enabled(component) && EnumValue(component, property) != 0;
        private static bool AspectEligible(Component component, Dictionary<string, Type> componentTypes)
        {
            if (component == null)
                return false;
            // Invoke only the exact built-in type, never a user subclass's hidden method.
            var type = ResolveType("UnityEngine.UI.AspectRatioFitter", componentTypes, true);
            var method = type?.GetMethod("IsComponentValidOnObject", BindingFlags.Public | BindingFlags.Instance);
            if (component.GetType() == type && method != null && method.ReturnType == typeof(bool))
                return (bool)method.Invoke(component, null);
            var canvas = component.GetComponent<Canvas>();
            return canvas == null || !canvas.isRootCanvas || canvas.renderMode == RenderMode.WorldSpace;
        }

        private static void TextFindings(Node node, JObject resolution, JArray findings, ref bool truncated)
        {
            if (!Enabled(node.Text) || string.IsNullOrEmpty(Read(node.Text, "text", "")))
                return;
            bool tmp = node.Text.GetType().FullName == "TMPro.TextMeshProUGUI";
            // Query only the legacy Text clone. TMP getters can rebuild the original text or font atlas.
            float preferredWidth, preferredHeight;
            if (tmp)
            {
                string text = Read(node.Text, "text", "");
                float fontSize = Read(node.Text, "fontSize", 36f);
                string[] lines = text.Split('\n');
                preferredWidth = lines.Max(line => line.Length) * fontSize * 0.6f;
                preferredHeight = lines.Length * fontSize * 1.2f;
            }
            else
            {
                preferredWidth = Read(node.PreviewText, "preferredWidth", 0f);
                preferredHeight = Read(node.PreviewText, "preferredHeight", 0f);
            }

            Vector2 available = node.Preview.rect.size;
            bool wraps = tmp ? Read(node.Text, "enableWordWrapping", true) : EnumValue(node.Text, "horizontalOverflow") == 0;
            bool autoSize = tmp ? Read(node.Text, "enableAutoSizing", false) : Read(node.Text, "resizeTextForBestFit", false);
            if (autoSize)
                return;
            bool overflow = preferredHeight > available.y + 0.5f || (!wraps && preferredWidth > available.x + 0.5f);
            if (tmp && wraps && preferredWidth > available.x && available.x > 0)
                overflow |= preferredHeight * Mathf.Ceil(preferredWidth / available.x) > available.y + 0.5f;
            if (overflow)
                Add(
                    findings,
                    "text_overflow",
                    tmp ? "candidate" : "warning",
                    node,
                    tmp
                        ? "Estimated TMP text content exceeds the rectangle. Verify wrapping/autosizing with the installed font in Game View; this estimate uses character count and font size without invoking TMP rebuilds."
                        : "Text preferred size exceeds its evaluated rectangle. Increase available size or adjust wrapping, font size and layout constraints.",
                    null,
                    resolution,
                    ref truncated
                );
        }

        private static string Path(Transform transform)
        {
            var parts = new List<string>();
            for (var t = transform; t != null; t = t.parent)
                parts.Add(t.name);
            parts.Reverse();
            return string.Join("/", parts);
        }

        private static JObject RectJson(Rect rect) => new JObject
        {
            ["x"] = rect.x,
            ["y"] = rect.y,
            ["width"] = rect.width,
            ["height"] = rect.height
        };
        private static bool Contains(Rect outer, Rect inner) => inner.xMin >= outer.xMin - 0.5f
            && inner.xMax <= outer.xMax + 0.5f
            && inner.yMin >= outer.yMin - 0.5f
            && inner.yMax <= outer.yMax + 0.5f;
        private static bool Overlaps(Rect a, Rect b) => a.width > 0 && a.height > 0 && b.width > 0 && b.height > 0 && a.Overlaps(b);
        private static Rect Intersect(Rect a, Rect b) => new Rect(
            Mathf.Max(a.xMin, b.xMin),
            Mathf.Max(a.yMin, b.yMin),
            Mathf.Max(0, Mathf.Min(a.xMax, b.xMax) - Mathf.Max(a.xMin, b.xMin)),
            Mathf.Max(0, Mathf.Min(a.yMax, b.yMax) - Mathf.Max(a.yMin, b.yMin))
        );
        private static void Add(JArray findings, string code, string severity, Node node, string description, Node related, JObject resolution, ref bool truncated)
        {
            if (findings.Count >= FindingLimit)
            {
                truncated = true;
                return;
            }

            findings.Add(new JObject
            {
                ["code"] = code,
                ["severity"] = severity,
                ["status"] = severity == "candidate" ? "candidate" : "observed",
                ["path"] = node.Path,
                ["instanceID"] = node.Source.gameObject.GetInstanceIDCompat(),
                ["description"] = description,
                ["relatedTarget"] = related == null ? null : new JObject
                {
                    ["path"] = related.Path,
                    ["instanceID"] = related.Source.gameObject.GetInstanceIDCompat()
                },
                ["resolution"] = resolution?.DeepClone()
            });
        }
    }
}
