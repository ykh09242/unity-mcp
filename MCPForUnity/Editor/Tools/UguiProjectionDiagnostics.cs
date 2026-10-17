using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Current mono camera geometry only; never renders, rebuilds layout or reads control values.</summary>
    internal static class UguiProjectionDiagnostics
    {
        private static readonly string[] PlaneNames = { "left", "right", "bottom", "top", "near", "far" };
        private static readonly int[] PlaneOrder = { 4, 5, 0, 1, 2, 3 };

        private sealed class Options
        {
            internal JToken Target,
                Camera;
            internal bool IncludeInactive;
            internal int Nodes = 200,
                Depth = 10,
                Evaluations = 20000,
                Characters = 65536;

            internal static Options Parse(JObject raw)
            {
                var allowed = new HashSet<string>(StringComparer.Ordinal)
                {
                    "action",
                    "target",
                    "camera",
                    "include_inactive",
                    "includeInactive",
                    "max_nodes",
                    "maxNodes",
                    "max_depth",
                    "maxDepth",
                    "max_evaluations",
                    "maxEvaluations",
                    "max_characters",
                    "maxCharacters",
                };
                if (raw == null || raw.Properties().Any(p => !allowed.Contains(p.Name)))
                    throw new ArgumentException();
                var value = new Options { Target = raw["target"], Camera = raw["camera"] };
                CheckReference(value.Target, false);
                CheckReference(value.Camera, true);
                value.Nodes = Integer(One(raw, "max_nodes", "maxNodes"), 200, 1, 1000);
                value.Depth = Integer(One(raw, "max_depth", "maxDepth"), 10, 0, 24);
                value.Evaluations = Integer(One(raw, "max_evaluations", "maxEvaluations"), 20000, 1, 200000);
                value.Characters = Integer(One(raw, "max_characters", "maxCharacters"), 65536, 1, 262144);
                var inactive = One(raw, "include_inactive", "includeInactive");
                if (inactive != null && inactive.Type != JTokenType.Boolean)
                    throw new ArgumentException();
                value.IncludeInactive = inactive?.Value<bool>() ?? false;
                return value;
            }

            private static JToken One(JObject raw, string snake, string camel)
            {
                if (raw.Property(snake) != null && raw.Property(camel) != null)
                    throw new ArgumentException();
                return raw.Property(snake)?.Value ?? raw.Property(camel)?.Value;
            }

            private static int Integer(JToken token, int fallback, int min, int max)
            {
                if (token == null || token.Type == JTokenType.Null)
                    return fallback;
                if (
                    token.Type != JTokenType.Integer
                    || !int.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int n)
                    || n < min
                    || n > max
                )
                    throw new ArgumentException();
                return n;
            }

            private static void CheckReference(JToken token, bool optional)
            {
                if (token == null || token.Type == JTokenType.Null)
                {
                    if (optional)
                        return;
                    throw new ArgumentException();
                }
                if (token.Type == JTokenType.Integer)
                {
                    if (!int.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out _))
                        throw new ArgumentException();
                    return;
                }
                if (token.Type != JTokenType.String)
                    throw new ArgumentException();
                string s = token.Value<string>();
                if (string.IsNullOrWhiteSpace(s) || s.Length > 4096)
                    throw new ArgumentException();
                for (int i = 0; i < s.Length; i++)
                {
                    if (char.IsHighSurrogate(s[i]))
                    {
                        if (++i == s.Length || !char.IsLowSurrogate(s[i]))
                            throw new ArgumentException();
                    }
                    else if (char.IsLowSurrogate(s[i]))
                        throw new ArgumentException();
                }
            }
        }

        private sealed class Budget
        {
            internal readonly Options Options;
            internal readonly Dictionary<string, int> Counts = new Dictionary<string, int>(StringComparer.Ordinal)
            {
                ["camera_observations"] = 0,
                ["ancestor_steps"] = 0,
                ["node_admissions"] = 0,
                ["child_fetches"] = 0,
                ["world_corners"] = 0,
                ["clip_distances"] = 0,
                ["intersections"] = 0,
                ["projected_vertices"] = 0,
            };
            internal readonly HashSet<string> Reasons = new HashSet<string>(StringComparer.Ordinal);
            internal int Evaluations,
                Copied,
                Visited;
            internal bool Unknown,
                LocalTruncated;

            internal Budget(Options options) => Options = options;

            internal bool Charge(string kind, int amount = 1)
            {
                if (amount > Options.Evaluations - Evaluations)
                {
                    Reasons.Add("max_evaluations");
                    return false;
                }
                Evaluations += amount;
                Counts[kind] += amount;
                return true;
            }

            internal string Copy(string value, string field, JArray fields, bool whole = false)
            {
                value = value ?? "";
                int intended = whole ? value.Length : Math.Min(256, value.Length);
                if (intended > 0 && intended < value.Length && char.IsHighSurrogate(value[intended - 1]))
                    intended--;
                int count = Math.Min(intended, Options.Characters - Copied);
                if (count > 0 && count < value.Length && char.IsHighSurrogate(value[count - 1]))
                    count--;
                if (whole && count < intended)
                    return null;
                if (count < intended)
                    Reasons.Add("max_characters");
                if (count < value.Length)
                {
                    fields.Add(field);
                    LocalTruncated = true;
                }
                Copied += count;
                return value.Substring(0, count);
            }

            internal bool Truncated => Reasons.Count != 0 || LocalTruncated;
        }

        private struct Point
        {
            internal double X,
                Y,
                Z;

            internal Point(double x, double y, double z)
            {
                X = x;
                Y = y;
                Z = z;
            }

            internal bool Valid => Finite(X) && Finite(Y) && Finite(Z);
            internal JArray Json => new JArray(X, Y, Z);
        }

        private sealed class CameraContext
        {
            internal Matrix4x4 View,
                Projection;
            internal readonly Plane[] Planes = new Plane[6];
            internal Camera Camera;
            internal Canvas Canvas;
            internal JObject Json;
            internal Rect Pixels;
            internal bool PixelReady;
            internal int CullingMask;
        }

        private sealed class Frame
        {
            internal Transform Transform;
            internal int Depth,
                NextChild,
                Parent;
            internal string Path;
            internal Canvas Canvas;
        }

        internal static object Diagnose(JObject raw)
        {
            Options options;
            try
            {
                options = Options.Parse(raw);
            }
            catch
            {
                return new ErrorResponse("invalid_projection_request");
            }
            var b = new Budget(options);
            GameObject target;
            try
            {
                target = ManageUGUI.Resolve(options.Target, options.IncludeInactive);
            }
            catch
            {
                return new ErrorResponse("invalid_or_ambiguous_projection_target");
            }
            if (!(target.transform is RectTransform))
                return new ErrorResponse("invalid_projection_request");
            try
            {
                Canvas canvas = null;
                int ancestors = 0;
                for (Transform t = target.transform; t != null; t = t.parent)
                {
                    if (ancestors++ == 128)
                        return new ErrorResponse("projection_unavailable");
                    if (!b.Charge("ancestor_steps"))
                        return Finish(null, new JArray(), b, "partial");
                    var candidate = t.GetComponent<Canvas>();
                    if (canvas == null && candidate != null && candidate.isActiveAndEnabled)
                        canvas = candidate;
                }
                if (canvas == null)
                    return Finish(null, new JArray(), b, "unavailable", "canvas_unavailable");
                canvas = canvas.rootCanvas;
                if (canvas.renderMode == RenderMode.ScreenSpaceOverlay)
                    return new ErrorResponse("unsupported_projection_canvas_mode");
                bool supplied = options.Camera != null && options.Camera.Type != JTokenType.Null;
                if (canvas.renderMode == RenderMode.WorldSpace && !supplied)
                    return new ErrorResponse("camera_required");
                if (!b.Charge("camera_observations"))
                    return Finish(null, new JArray(), b, "partial");
                Camera selected = canvas.worldCamera;
                if (supplied)
                {
                    try
                    {
                        selected = ManageUGUI.Resolve(options.Camera, true).GetComponent<Camera>();
                    }
                    catch
                    {
                        return new ErrorResponse("invalid_or_ambiguous_projection_camera");
                    }
                    if (selected == null)
                        return new ErrorResponse("invalid_or_ambiguous_projection_camera");
                    if (canvas.renderMode == RenderMode.ScreenSpaceCamera && selected != canvas.worldCamera)
                        return new ErrorResponse("camera_canvas_mismatch");
                }
                if (selected == null)
                    return Finish(null, new JArray(), b, "unavailable", "camera_unassigned");
                var context = Observe(target, canvas, selected, b);
                if (context == null)
                    return Finish(null, new JArray(), b, "unavailable", "unsupported_camera_projection");
                return Traverse(target.transform, context, b);
            }
            catch
            {
                return new ErrorResponse("projection_unavailable");
            }
        }

        private static CameraContext Observe(GameObject target, Canvas canvas, Camera camera, Budget b)
        {
            var c = new CameraContext
            {
                View = camera.worldToCameraMatrix,
                Projection = camera.projectionMatrix,
                Camera = camera,
                Canvas = canvas,
                CullingMask = camera.cullingMask,
            };
            if (
                !MatrixValid(c.View)
                || !MatrixValid(c.Projection)
                || !Row(c.View, 0, 0, 0, 1)
                || !Row(c.Projection, 0, 0, camera.orthographic ? 0 : -1, camera.orthographic ? 1 : 0)
                || camera.stereoEnabled
            )
                return null;
            GeometryUtility.CalculateFrustumPlanes(c.Projection * c.View, c.Planes);
            if (
                c.Planes.Any(p =>
                    !Finite(p.distance) || !Finite(p.normal.x) || !Finite(p.normal.y) || !Finite(p.normal.z) || p.normal.sqrMagnitude < 0.000000000001f
                )
            )
                return null;
            if (!Finite(camera.nearClipPlane) || !Finite(camera.farClipPlane) || !Finite(canvas.scaleFactor) || !Finite(canvas.referencePixelsPerUnit))
                return null;
            var fields = new JArray();
            var output = new JObject
            {
                ["kind"] = camera.targetTexture == null ? "display" : "render_texture",
                ["route_basis"] = "camera_target_properties",
                ["target_display"] = camera.targetDisplay,
                ["width"] = null,
                ["height"] = null,
                ["viewport_pixels"] = null,
                ["camera_rect_ignored"] = camera.targetTexture == null ? (JToken)new JValue(false) : JValue.CreateNull(),
                ["texture_id"] = null,
                ["active"] = false,
                ["dimension_source"] = "unavailable",
                ["pixel_status"] = "unavailable",
                ["texture_dimension"] = null,
                ["volume_depth"] = null,
                ["dynamic_scale"] = null,
            };
            Rect pixels = camera.pixelRect;
            if (camera.targetTexture != null)
            {
                var rt = camera.targetTexture;
                output["texture_id"] = rt.GetInstanceIDCompat();
                output["texture_dimension"] = rt.dimension.ToString();
                output["volume_depth"] = rt.volumeDepth;
                output["dynamic_scale"] = rt.useDynamicScale;
                output["width"] = rt.width;
                output["height"] = rt.height;
                output["active"] = rt.IsCreated();
                output["dimension_source"] = "attached_render_texture";
                // A non-full RT viewport has no established read-only normalized-to-pixel mapping.
                // Keep its normalized geometry while withholding pixel coordinates and the ignore claim.
                Rect viewport = camera.rect;
                bool fullViewport = viewport.x == 0f && viewport.y == 0f && viewport.width == 1f && viewport.height == 1f;
                output["camera_rect_ignored"] = fullViewport ? (JToken)new JValue(true) : JValue.CreateNull();
                pixels = new Rect(0, 0, rt.width, rt.height);
                c.PixelReady =
                    fullViewport
                    && rt.IsCreated()
                    && rt.dimension == TextureDimension.Tex2D
                    && rt.volumeDepth == 1
                    && !rt.useDynamicScale
                    && Dimension(rt.width)
                    && Dimension(rt.height);
            }
            else if (camera.targetDisplay >= 0 && camera.targetDisplay < Display.displays.Length && camera.targetDisplay < 8)
            {
                var display = Display.displays[camera.targetDisplay];
                int width = camera.targetDisplay == 0 ? Screen.width : display.renderingWidth;
                int height = camera.targetDisplay == 0 ? Screen.height : display.renderingHeight;
                bool active = camera.targetDisplay == 0 || display.active;
                output["width"] = width;
                output["height"] = height;
                output["active"] = active;
                output["dimension_source"] = camera.targetDisplay == 0 ? "current_screen" : "active_display_rendering";
                c.PixelReady =
                    active
                    && Dimension(width)
                    && Dimension(height)
                    && RectValid(pixels)
                    && pixels.xMin >= 0
                    && pixels.yMin >= 0
                    && pixels.xMax <= width
                    && pixels.yMax <= height;
            }
            bool jitterMismatch = !MatricesEqual(camera.nonJitteredProjectionMatrix, c.Projection);
            if (
                camera.allowDynamicResolution
                || camera.scaledPixelWidth != camera.pixelWidth
                || camera.scaledPixelHeight != camera.pixelHeight
                || jitterMismatch
            )
                c.PixelReady = false;
            c.Pixels = pixels;
            if (c.PixelReady)
            {
                output["viewport_pixels"] = RectJson(pixels);
                output["pixel_status"] = "available";
            }
            else
                b.Unknown = true;
            c.Json = new JObject
            {
                ["target_id"] = target.GetInstanceIDCompat(),
                ["canvas_id"] = canvas.GetInstanceIDCompat(),
                ["root_canvas_id"] = canvas.rootCanvas.GetInstanceIDCompat(),
                ["render_mode"] = canvas.renderMode == RenderMode.WorldSpace ? "world_space" : "screen_space_camera",
                ["camera_id"] = camera.GetInstanceIDCompat(),
                ["assigned_camera_id"] = canvas.worldCamera == null ? (JToken)JValue.CreateNull() : new JValue(canvas.worldCamera.GetInstanceIDCompat()),
                ["camera_selection"] = canvas.renderMode == RenderMode.WorldSpace ? "explicit_world_view" : "assigned_screen_camera",
                ["camera_enabled"] = camera.enabled,
                ["camera_active"] = camera.gameObject.activeInHierarchy,
                ["canvas_enabled"] = canvas.isActiveAndEnabled,
                ["allow_dynamic_resolution"] = camera.allowDynamicResolution,
                ["non_jittered_projection_mismatch"] = jitterMismatch,
                ["culling_mask"] = camera.cullingMask,
                ["orthographic"] = camera.orthographic,
                ["stereo_enabled"] = camera.stereoEnabled,
                ["near_clip"] = camera.nearClipPlane,
                ["far_clip"] = camera.farClipPlane,
                ["scale_factor"] = canvas.scaleFactor,
                ["reference_pixels_per_unit"] = canvas.referencePixelsPerUnit,
                ["view_matrix"] = MatrixJson(c.View),
                ["projection_matrix"] = MatrixJson(c.Projection),
                ["camera_rect"] = RectValid(camera.rect) ? RectJson(camera.rect) : null,
                ["camera_pixel_rect"] = RectValid(camera.pixelRect) ? RectJson(camera.pixelRect) : null,
                ["pixel_width"] = camera.pixelWidth,
                ["pixel_height"] = camera.pixelHeight,
                ["scaled_pixel_width"] = camera.scaledPixelWidth,
                ["scaled_pixel_height"] = camera.scaledPixelHeight,
                ["target_name"] = b.Copy(target.name, "target_name", fields),
                ["canvas_name"] = b.Copy(canvas.name, "canvas_name", fields),
                ["camera_name"] = b.Copy(camera.name, "camera_name", fields),
                ["metadata_truncated_fields"] = fields,
                ["output"] = output,
            };
            return c;
        }

        private static object Traverse(Transform root, CameraContext c, Budget b)
        {
            var nodes = new JArray();
            var stack = new Stack<Frame>();
            stack.Push(
                new Frame
                {
                    Transform = root,
                    Depth = 0,
                    NextChild = -1,
                    Parent = -1,
                    Path = "",
                    Canvas = c.Canvas,
                }
            );
            while (stack.Count != 0)
            {
                var f = stack.Peek();
                if (f.NextChild == -1)
                {
                    if (b.Visited == b.Options.Nodes)
                    {
                        b.Reasons.Add("max_nodes");
                        break;
                    }
                    if (!b.Charge("node_admissions"))
                        break;
                    b.Visited++;
                    f.NextChild = 0;
                    var localCanvas = f.Transform.GetComponent<Canvas>();
                    if (localCanvas != null && localCanvas.isActiveAndEnabled)
                        f.Canvas = localCanvas.rootCanvas;
                    if (f.Transform is RectTransform rect)
                    {
                        var fields = new JArray();
                        string path = b.Copy(f.Path, "diagnostic_path", fields, true);
                        if (path == null)
                        {
                            b.Reasons.Add("max_characters");
                            break;
                        }
                        var node = Stub(rect, f, path, b.Copy(rect.name, "name", fields), fields, c);
                        nodes.Add(node);
                        if (f.Canvas != c.Canvas)
                        {
                            ((JArray)node["reasons"]).Add("canvas_context_changed");
                            b.Unknown = true;
                        }
                        else
                            Project(rect, node, c, b);
                        f.Parent = nodes.Count - 1;
                    }
                }
                if (f.NextChild >= f.Transform.childCount)
                {
                    stack.Pop();
                    continue;
                }
                if (f.Depth == b.Options.Depth)
                {
                    b.Reasons.Add("max_depth");
                    stack.Pop();
                    continue;
                }
                if (!b.Charge("child_fetches"))
                    break;
                int index = f.NextChild++;
                var child = f.Transform.GetChild(index);
                if (!b.Options.IncludeInactive && !child.gameObject.activeInHierarchy)
                    continue;
                stack.Push(
                    new Frame
                    {
                        Transform = child,
                        Depth = f.Depth + 1,
                        NextChild = -1,
                        Parent = f.Parent,
                        Path = f.Path + "/" + index.ToString(CultureInfo.InvariantCulture),
                        Canvas = f.Canvas,
                    }
                );
            }
            return Finish(c.Json, nodes, b, b.Truncated || b.Unknown ? "partial" : "complete");
        }

        private static JObject Stub(RectTransform rect, Frame f, string path, string name, JArray fields, CameraContext c) =>
            new JObject
            {
                ["diagnostic_path"] = path,
                ["parent_index"] = f.Parent < 0 ? (JToken)JValue.CreateNull() : new JValue(f.Parent),
                ["sibling_index"] = f.Depth == 0 ? 0 : rect.GetSiblingIndex(),
                ["instance_id"] = rect.gameObject.GetInstanceIDCompat(),
                ["name"] = name,
                ["truncated_fields"] = fields,
                ["active"] = rect.gameObject.activeInHierarchy,
                ["layer_in_culling_mask"] = (c.CullingMask & (1 << rect.gameObject.layer)) != 0,
                ["world_corners"] = null,
                ["depth_range"] = null,
                ["camera_plane_relation"] = "unknown",
                ["frustum_relation"] = "unknown",
                ["cut_planes"] = new JArray(),
                ["rejected_by"] = null,
                ["clipped_viewport_polygon"] = new JArray(),
                ["unclipped_viewport_bounds"] = null,
                ["clipped_target_pixel_bounds"] = null,
                ["pixel_status"] = "unavailable",
                ["boundary_clamped"] = false,
                ["reasons"] = new JArray(),
            };

        private static void Project(RectTransform rect, JObject node, CameraContext c, Budget b)
        {
            if (!b.Charge("world_corners", 4))
            {
                Fail(node, b, "budget_incomplete");
                return;
            }
            var corners = new Vector3[4];
            rect.GetWorldCorners(corners);
            var a = new Point[10];
            var z = new double[4];
            var world = new JArray();
            for (int i = 0; i < 4; i++)
            {
                a[i] = new Point(corners[i].x, corners[i].y, corners[i].z);
                if (!a[i].Valid)
                {
                    Fail(node, b, "nonfinite_geometry");
                    return;
                }
                var view = Multiply(c.View, a[i].X, a[i].Y, a[i].Z, 1);
                if (!Finite(view[2]) || !Finite(view[3]) || Math.Abs(view[3] - 1) > 0.000001)
                {
                    Fail(node, b, "unsupported_view_matrix");
                    return;
                }
                z[i] = -view[2];
                world.Add(a[i].Json);
            }
            node["world_corners"] = world;
            node["depth_range"] = new JArray(z.Min(), z.Max());
            node["camera_plane_relation"] =
                z.Max() <= 0 ? "behind"
                : z.Min() > 0 ? "in_front"
                : "crossing";
            if (z.Min() > 0)
            {
                var original = new List<double[]>();
                foreach (var point in a.Take(4))
                {
                    if (!b.Charge("projected_vertices"))
                    {
                        Fail(node, b, "budget_incomplete");
                        return;
                    }
                    if (!Viewport(point, c, out double x, out double y))
                    {
                        original.Clear();
                        break;
                    }
                    original.Add(new[] { x, y });
                }
                if (original.Count == 4)
                    node["unclipped_viewport_bounds"] = BoundsJson(original);
            }
            var scratch = new Point[10];
            int count = 4;
            bool cut = false;
            foreach (int planeIndex in PlaneOrder)
            {
                int emitted = 0;
                bool planeCut = false;
                var plane = c.Planes[planeIndex];
                var previous = a[count - 1];
                if (!Distance(previous, plane, b, out double previousDistance))
                {
                    Fail(node, b, "budget_incomplete");
                    return;
                }
                for (int i = 0; i < count; i++)
                {
                    var current = a[i];
                    if (!Distance(current, plane, b, out double distance))
                    {
                        Fail(node, b, "budget_incomplete");
                        return;
                    }
                    bool beforeInside = previousDistance >= 0,
                        inside = distance >= 0;
                    if (!inside)
                        planeCut = true;
                    if (beforeInside != inside)
                    {
                        if (!b.Charge("intersections"))
                        {
                            Fail(node, b, "budget_incomplete");
                            return;
                        }
                        double divisor = previousDistance - distance;
                        if (Math.Abs(divisor) <= 0.000000000001 * Math.Max(1, Math.Max(Math.Abs(previousDistance), Math.Abs(distance))))
                        {
                            Fail(node, b, "indeterminate_intersection");
                            return;
                        }
                        double t = previousDistance / divisor;
                        var hit = new Point(
                            previous.X + (current.X - previous.X) * t,
                            previous.Y + (current.Y - previous.Y) * t,
                            previous.Z + (current.Z - previous.Z) * t
                        );
                        if (!hit.Valid || !Emit(scratch, ref emitted, hit))
                        {
                            Fail(node, b, "unsupported_clip_geometry");
                            return;
                        }
                    }
                    if (inside && !Emit(scratch, ref emitted, current))
                    {
                        Fail(node, b, "unsupported_clip_geometry");
                        return;
                    }
                    previous = current;
                    previousDistance = distance;
                }
                if (emitted > 1 && Same(scratch[0], scratch[emitted - 1]))
                    emitted--;
                if (planeCut)
                {
                    cut = true;
                    ((JArray)node["cut_planes"]).Add(PlaneNames[planeIndex]);
                }
                if (emitted == 0)
                {
                    node["frustum_relation"] = "outside";
                    node["rejected_by"] = PlaneNames[planeIndex];
                    node["pixel_status"] = c.PixelReady ? "not_applicable" : "unavailable";
                    return;
                }
                var temp = a;
                a = scratch;
                scratch = temp;
                count = emitted;
            }
            var projected = new List<double[]>();
            bool clamped = false;
            for (int i = 0; i < count; i++)
            {
                if (!b.Charge("projected_vertices"))
                {
                    Fail(node, b, "budget_incomplete");
                    return;
                }
                if (!Viewport(a[i], c, out double x, out double y) || x < -0.000001 || y < -0.000001 || x > 1.000001 || y > 1.000001)
                {
                    Fail(node, b, "unsafe_projection");
                    return;
                }
                clamped |= x < 0 || x > 1 || y < 0 || y > 1;
                projected.Add(new[] { Math.Max(0, Math.Min(1, x)), Math.Max(0, Math.Min(1, y)) });
            }
            double area = 0;
            for (int i = 0; i < projected.Count; i++)
            {
                var next = projected[(i + 1) % projected.Count];
                area += projected[i][0] * next[1] - next[0] * projected[i][1];
            }
            if (projected.Count < 3 || Math.Abs(area) * 0.5 <= 0.000000000001)
            {
                node["frustum_relation"] = "degenerate";
                node["pixel_status"] = c.PixelReady ? "not_applicable" : "unavailable";
                return;
            }
            node["frustum_relation"] = cut ? "partial" : "inside";
            node["boundary_clamped"] = clamped;
            node["clipped_viewport_polygon"] = new JArray(projected.Select(p => new JArray(p[0], p[1])));
            if (c.PixelReady)
            {
                var pixels = projected.Select(p => new[] { c.Pixels.x + p[0] * c.Pixels.width, c.Pixels.y + p[1] * c.Pixels.height }).ToList();
                node["clipped_target_pixel_bounds"] = BoundsJson(pixels);
                node["pixel_status"] = "available";
            }
        }

        private static bool Distance(Point p, Plane plane, Budget b, out double result)
        {
            result = 0;
            if (!b.Charge("clip_distances"))
                return false;
            result = p.X * plane.normal.x + p.Y * plane.normal.y + p.Z * plane.normal.z + plane.distance;
            return true;
        }

        private static bool Emit(Point[] values, ref int count, Point p)
        {
            if (count > 0 && Same(values[count - 1], p))
                return true;
            if (count == values.Length)
                return Same(values[0], p);
            values[count++] = p;
            return true;
        }

        private static bool Same(Point a, Point b) => a.X == b.X && a.Y == b.Y && a.Z == b.Z;

        private static void Fail(JObject node, Budget b, string reason)
        {
            b.Unknown = true;
            ((JArray)node["reasons"]).Add(reason);
        }

        private static double[] Multiply(Matrix4x4 m, double x, double y, double z, double w) =>
            new[]
            {
                m.m00 * x + m.m01 * y + m.m02 * z + m.m03 * w,
                m.m10 * x + m.m11 * y + m.m12 * z + m.m13 * w,
                m.m20 * x + m.m21 * y + m.m22 * z + m.m23 * w,
                m.m30 * x + m.m31 * y + m.m32 * z + m.m33 * w,
            };

        private static bool Viewport(Point p, CameraContext c, out double x, out double y)
        {
            x = y = 0;
            var v = Multiply(c.View, p.X, p.Y, p.Z, 1);
            var clip = Multiply(c.Projection, v[0], v[1], v[2], v[3]);
            if (
                clip.Any(n => !Finite(n))
                || clip[3] <= 0.000000000001 * Math.Max(1, Math.Max(Math.Abs(clip[0]), Math.Max(Math.Abs(clip[1]), Math.Abs(clip[2]))))
            )
                return false;
            x = (clip[0] / clip[3] + 1) * 0.5;
            y = (clip[1] / clip[3] + 1) * 0.5;
            return Finite(x) && Finite(y);
        }

        private static object Finish(JObject context, JArray nodes, Budget b, string status, string unavailableReason = null)
        {
            var data = new JObject
            {
                ["kind"] = "ugui_projection_diagnostics",
                ["schema_version"] = 1,
                ["framework"] = "ugui",
                ["mode"] = Application.isPlaying ? "play" : "edit",
                ["frame"] = Time.frameCount,
                ["status"] = status,
                ["unavailable_reason"] = status == "unavailable" ? unavailableReason : null,
                ["evidence"] = new JObject
                {
                    ["observation"] = "current_camera_properties",
                    ["geometry_space"] = "camera_viewport_normalized",
                    ["pixel_origin"] = "bottom_left",
                    ["quad_source"] = "current_world_corners",
                    ["layout_method"] = "current_unforced",
                    ["matrix_order"] = "column_major",
                    ["rasterization"] = "not_observed",
                    ["mask_clipping"] = "not_evaluated",
                    ["occlusion"] = "not_evaluated",
                    ["interaction"] = "not_evaluated",
                    ["profile"] = null,
                },
                ["context"] = context,
                ["nodes"] = nodes,
                ["traversal"] = new JObject
                {
                    ["max_nodes"] = b.Options.Nodes,
                    ["max_depth"] = b.Options.Depth,
                    ["max_evaluations"] = b.Options.Evaluations,
                    ["max_characters"] = b.Options.Characters,
                    ["nodes_visited"] = b.Visited,
                    ["nodes_emitted"] = nodes.Count,
                    ["evaluations"] = b.Evaluations,
                    ["evaluation_counts"] = JObject.FromObject(b.Counts),
                    ["characters"] = b.Copied,
                    ["truncated"] = b.Truncated,
                    ["reasons"] = new JArray(b.Reasons.OrderBy(v => v, StringComparer.Ordinal)),
                },
                ["limitations"] = new JArray
                {
                    "Current unforced world-quad geometry is not rendered visibility, occlusion, mask clipping, event delivery or actual compositor submission.",
                    "Camera target properties do not establish MRT, SRP camera-stack, custom compositor or late pipeline matrix behavior.",
                    "Projection has no device profile, safe-area or DPI simulation; current Canvas scale is already present in world corners.",
                    "Diagnostic paths and instance IDs are current observations, not semantic snapshot identities or persistent mutation handles.",
                    "Textual scene lookup and native getter internals are outside managed traversal/work and wall-time bounds.",
                },
            };
            if (System.Text.Encoding.UTF8.GetByteCount(data.ToString(Formatting.None)) > 2 * 1024 * 1024)
                return new ErrorResponse("projection_artifact_limit");
            return new SuccessResponse("Current uGUI projection observed.", data);
        }

        private static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);

        private static bool Dimension(int n) => n >= 1 && n <= 16384;

        private static bool RectValid(Rect r) => Finite(r.x) && Finite(r.y) && Finite(r.width) && Finite(r.height) && r.width > 0 && r.height > 0;

        private static JArray RectJson(Rect r) => new JArray(r.x, r.y, r.width, r.height);

        private static JArray MatrixJson(Matrix4x4 m) => new JArray(Enumerable.Range(0, 16).Select(i => (double)m[i]));

        private static bool MatrixValid(Matrix4x4 m) =>
            Enumerable.Range(0, 16).All(i => Finite(m[i])) && Finite(m.determinant) && Math.Abs(m.determinant) > 0.000000000001;

        private static bool Row(Matrix4x4 m, double x, double y, double z, double w) =>
            Math.Abs(m.m30 - x) <= 0.000001 && Math.Abs(m.m31 - y) <= 0.000001 && Math.Abs(m.m32 - z) <= 0.000001 && Math.Abs(m.m33 - w) <= 0.000001;

        private static bool MatricesEqual(Matrix4x4 a, Matrix4x4 b) => Enumerable.Range(0, 16).All(i => Finite(a[i]) && Math.Abs(a[i] - b[i]) <= 0.000001);

        private static JArray BoundsJson(List<double[]> points)
        {
            double x = points.Min(p => p[0]),
                y = points.Min(p => p[1]);
            return new JArray(x, y, points.Max(p => p[0]) - x, points.Max(p => p[1]) - y);
        }
    }
}
