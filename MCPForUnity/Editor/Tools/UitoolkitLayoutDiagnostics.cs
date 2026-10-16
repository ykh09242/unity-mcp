using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Bounded current-panel candidates, without text, layout updates or source writes.</summary>
    internal static class UitoolkitLayoutDiagnostics
    {
        internal static bool? ProbeAvailableForTests = null;
        private const BindingFlags Members = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
        private static readonly Type ElementType = typeof(VisualElement);
        private static readonly FieldInfo Computed = ElementType.GetField("m_Style", Members);
        private static readonly MethodInfo ShouldClip = ElementType.GetMethod("ShouldClip", Members, null, Type.EmptyTypes, null);
        private static readonly PropertyInfo Initialized = ElementType.GetProperty("styleInitialized", Members);
        private static readonly PropertyInfo OverflowValue = Computed?.FieldType.GetProperty("overflow", Members);
        private static readonly PropertyInfo ClipBoxValue = Computed?.FieldType.GetProperty("unityOverflowClipBox", Members);
        private static readonly Type PanelBase = ElementType.Assembly.GetType("UnityEngine.UIElements.BaseVisualElementPanel", false);
        private static readonly PropertyInfo Flat = PanelBase?.GetProperty("isFlat", Members);
        private static readonly string[] Limitations =
        {
            "Current layout/style observation only; no layout update, rendering or multi-resolution simulation is performed.",
            "Physical diagnostic paths are current positions, not snapshot IDs, content-container selectors or persistent handles.",
            "Clipping models supported rectangular flat-panel constraints; unsupported APIs, transforms and rounded shapes remain unknown.",
            "Overlap and outside-parent bounds are candidates, not pixel occlusion, bugs or event reachability; cross-branch and cross-document pairs are excluded.",
            "No text, input values, tooltip, classes, bindings or source content is read; arbitrary script-copied secrets in names are not identified.",
            "Producer work/retention is capped; scene lookup and engine getter/cache costs are not hard wall-clock bounds.",
        };

        private sealed class Budget
        {
            internal readonly int Depth,
                Nodes,
                Evaluations,
                Characters,
                Pairs,
                Findings;
            internal int Ancestors,
                Admissions,
                Fetches,
                Probes,
                PairCount,
                Copied;
            internal bool Unknown;
            internal readonly List<string> Reasons = new();
            internal int Used => Ancestors + Admissions + Fetches + Probes + PairCount;

            internal Budget(ToolParams p)
            {
                Depth = Read(p, "max_depth", 10, 0, 24);
                Nodes = Read(p, "max_nodes", 200, 1, 1000);
                Evaluations = Read(p, "max_evaluations", 5000, 1, 20000);
                Characters = Read(p, "max_characters", 65536, 1, 262144);
                Pairs = Read(p, "max_pairs", 2000, 0, 20000);
                Findings = Read(p, "max_findings", 200, 1, 2000);
            }

            internal void Reason(string reason)
            {
                if (!Reasons.Contains(reason))
                    Reasons.Add(reason);
            }

            internal bool Charge(ref int category)
            {
                if (Used >= Evaluations)
                {
                    Reason("max_evaluations");
                    return false;
                }
                category++;
                return true;
            }

            internal string Copy(string value, string field, JArray truncated, bool whole = false)
            {
                value ??= "";
                int intended = whole ? value.Length : Math.Min(256, value.Length);
                if (intended < value.Length && intended > 0 && char.IsHighSurrogate(value[intended - 1]) && char.IsLowSurrogate(value[intended]))
                    intended--;
                int count = Math.Min(intended, Characters - Copied);
                if (count < intended && count > 0 && char.IsHighSurrogate(value[count - 1]) && char.IsLowSurrogate(value[count]))
                    count--;
                if (count < value.Length)
                {
                    truncated?.Add(field);
                    if (intended < value.Length)
                        Reason("per_string_limit");
                    if (count < intended)
                        Reason("max_characters");
                    if (whole)
                        return null;
                }
                Copied += count;
                return count == value.Length ? value : value.Substring(0, count);
            }
        }

        private readonly struct Bounds
        {
            internal readonly double X,
                Y,
                W,
                H;

            internal Bounds(double x, double y, double w, double h)
            {
                X = x;
                Y = y;
                W = w;
                H = h;
            }

            internal bool Valid => Finite(X) && Finite(Y) && Finite(W) && Finite(H) && W >= 0 && H >= 0;
            internal JArray Json => new JArray(X, Y, W, H);

            internal static Bounds From(Rect r) => new Bounds(r.x, r.y, r.width, r.height);

            internal Bounds Intersection(Bounds other)
            {
                double x = Math.Max(X, other.X),
                    y = Math.Max(Y, other.Y);
                return new Bounds(x, y, Math.Max(0, Math.Min(X + W, other.X + other.W) - x), Math.Max(0, Math.Min(Y + H, other.Y + other.H) - y));
            }

            internal bool Outside(Bounds other, double tolerance = .01) =>
                X < other.X - tolerance || Y < other.Y - tolerance || X + W > other.X + other.W + tolerance || Y + H > other.Y + other.H + tolerance;
        }

        private sealed class Context
        {
            internal bool? Display = true;
            internal double? Alpha = 1;
            internal bool Known;
            internal Bounds? Clip;
        }

        private sealed class Node
        {
            internal VisualElement Element;
            internal JObject Output;
            internal Context Children;
            internal Bounds? Box,
                Content,
                Hit;
            internal bool Eligible;
            internal int Index,
                Parent,
                Sibling,
                Depth,
                NextChild,
                Count;
            internal string Path;
        }

        internal static object Diagnose(JObject parameters)
        {
            try
            {
                return Read(parameters);
            }
            catch (DiagnosticException error)
            {
                // Only errors created locally below carry these bounded fixed messages.
                return new ErrorResponse(error.Message);
            }
            catch
            {
                return new ErrorResponse("diagnostics_unavailable");
            }
        }

        private sealed class DiagnosticException : Exception
        {
            internal DiagnosticException(string message)
                : base(message) { }
        }

        private static DiagnosticException Invalid(string message) => new DiagnosticException(message);

        private static int Read(ToolParams p, string key, int fallback, int min, int max)
        {
            var token = p.GetRaw(key);
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (
                token.Type != JTokenType.Integer
                || !int.TryParse(((JValue)token).ToString(CultureInfo.InvariantCulture), out int value)
                || value < min
                || value > max
            )
                throw Invalid(key + " must be an integer from " + min + " to " + max + ".");
            return value;
        }

        private static Component Component(GameObject go, string selection)
        {
            Type type;
            var panel = ElementType.Assembly.GetType("UnityEngine.UIElements.PanelRenderer", false);
            switch (selection)
            {
                case "auto":
                    type = go.GetComponent<UIDocument>() != null ? typeof(UIDocument) : panel;
                    break;
                case "ui_document":
                    type = typeof(UIDocument);
                    break;
                case "panel_renderer":
                    type = panel;
                    break;
                default:
                    throw Invalid("component_type must be auto, ui_document, or panel_renderer.");
            }
            if (type == null || type.Assembly != ElementType.Assembly || !typeof(UnityEngine.Component).IsAssignableFrom(type))
                throw Invalid("unsupported_ui_component");
            var component = go.GetComponent(type);
            if (component == null || component.GetType() != type)
                throw Invalid("unsupported_ui_component");
            if (type == typeof(UIDocument))
            {
                var getter = type.GetProperty("rootVisualElement", Members)?.GetMethod;
                if (getter == null || getter.DeclaringType != type || getter.ReturnType != ElementType || getter.IsVirtual)
                    throw Invalid("unsupported_ui_component");
            }
            else
            {
                foreach (
                    var contract in new[]
                    {
                        ("visualTreeAsset", typeof(VisualTreeAsset)),
                        ("panelSettings", typeof(PanelSettings)),
                        ("sortingOrder", typeof(int)),
                    }
                )
                {
                    var property = type.GetProperty(contract.Item1, Members);
                    if (
                        property == null
                        || property.DeclaringType != type
                        || property.PropertyType != contract.Item2
                        || !property.CanRead
                        || !property.CanWrite
                        || property.GetMethod.IsVirtual
                        || property.SetMethod.IsVirtual
                    )
                        throw Invalid("unsupported_ui_component");
                }
                var registrations = type.GetMethods(BindingFlags.Instance | BindingFlags.Public).Where(m => m.Name == "RegisterUIReloadCallback").ToArray();
                if (registrations.Length != 1)
                    throw Invalid("unsupported_ui_component");
                var register = registrations[0];
                var args = register.GetParameters();
                if (register.DeclaringType != type || register.IsVirtual || register.ReturnType != typeof(void) || args.Length != 1)
                    throw Invalid("unsupported_ui_component");
                var invoke = args[0].ParameterType.GetMethod("Invoke");
                var callbackArgs = invoke?.GetParameters();
                if (
                    !typeof(Delegate).IsAssignableFrom(args[0].ParameterType)
                    || invoke.ReturnType != typeof(void)
                    || callbackArgs.Length != 3
                    || callbackArgs[0].ParameterType != type
                    || callbackArgs[1].ParameterType != ElementType
                    || callbackArgs[2].ParameterType != typeof(int)
                )
                    throw Invalid("unsupported_ui_component");
                var unregisters = type.GetMethods(BindingFlags.Instance | BindingFlags.Public).Where(m => m.Name == "UnregisterUIReloadCallback").ToArray();
                if (
                    unregisters.Length != 1
                    || unregisters[0].DeclaringType != type
                    || unregisters[0].IsVirtual
                    || unregisters[0].ReturnType != typeof(void)
                    || unregisters[0].GetParameters().Length != 1
                    || unregisters[0].GetParameters()[0].ParameterType != args[0].ParameterType
                )
                    throw Invalid("unsupported_ui_component");
            }
            return component;
        }

        private static object Read(JObject parameters)
        {
            var p = new ToolParams(parameters);
            var allowed = new HashSet<string>(StringComparer.Ordinal)
            {
                "action",
                "target",
                "component_type",
                "componentType",
                "max_depth",
                "maxDepth",
                "max_nodes",
                "maxNodes",
                "max_evaluations",
                "maxEvaluations",
                "max_characters",
                "maxCharacters",
                "max_pairs",
                "maxPairs",
                "max_findings",
                "maxFindings",
            };
            if (parameters.Properties().Any(property => !allowed.Contains(property.Name)))
                throw Invalid("unsupported_diagnostics_parameter");
            // Validate every supplied supported spelling, including an otherwise shadowed alias.
            foreach (
                var contract in new[]
                {
                    ("max_depth", "maxDepth", 10, 0, 24),
                    ("max_nodes", "maxNodes", 200, 1, 1000),
                    ("max_evaluations", "maxEvaluations", 5000, 1, 20000),
                    ("max_characters", "maxCharacters", 65536, 1, 262144),
                    ("max_pairs", "maxPairs", 2000, 0, 20000),
                    ("max_findings", "maxFindings", 200, 1, 2000),
                }
            )
            {
                if (parameters.Property(contract.Item1) != null)
                    Read(p, contract.Item1, contract.Item3, contract.Item4, contract.Item5);
                if (parameters.Property(contract.Item2) != null)
                    Read(p, contract.Item2, contract.Item3, contract.Item4, contract.Item5);
            }
            var b = new Budget(p);
            var target = p.GetRaw("target");
            if (target?.Type != JTokenType.String || string.IsNullOrWhiteSpace(target.Value<string>()) || target.Value<string>().Length > 4096)
                throw Invalid("invalid_diagnostics_target");
            GameObject go;
            try
            {
                go = ManageUGUI.Resolve(target, true);
            }
            catch
            {
                throw Invalid("invalid_or_ambiguous_diagnostics_target");
            }
            var component = Component(go, p.Get("component_type") ?? "auto");
            // The adapter receives only the exact prevalidated engine component. No shared discovery or source/settings getter runs.
            var adapter = new UIComponentAdapter(component);
            var root = adapter.Root; // Verified built-in PanelRenderer root receiver is the sole callback exception.
            var nodes = new List<Node>();
            var findings = new JArray();
            var fields = new JArray();
            string name = b.Copy(go.name, "game_object", fields);
            string layout = "current";
            bool? flat = null;
            if (!go.activeInHierarchy || (component is Behaviour behaviour && !behaviour.isActiveAndEnabled))
                layout = "component_inactive";
            else if (root == null)
                layout = "not_initialized";
            else if (root.panel == null)
                layout = "panel_unavailable";
            else
            {
                // Bound the full physical chain before lazy transform/bounds getters may inspect it.
                var ancestors = new List<VisualElement>();
                var ancestor = root.hierarchy.parent;
                while (ancestor != null)
                {
                    if (ancestors.Count == 127)
                    {
                        b.Reason("ancestor_depth");
                        break;
                    }
                    if (!b.Charge(ref b.Ancestors))
                        break;
                    ancestors.Add(ancestor);
                    ancestor = ancestor.hierarchy.parent;
                }
                if (ancestor == null)
                {
                    try
                    {
                        if (
                            Flat != null
                            && Flat.PropertyType == typeof(bool)
                            && Flat.DeclaringType == PanelBase
                            && Flat.GetMethod != null
                            && !Flat.GetMethod.IsVirtual
                            && PanelBase.IsInstanceOfType(root.panel)
                        )
                            flat = (bool)Flat.GetValue(root.panel);
                    }
                    catch { /* Unsupported engine panel capability remains unknown. */ }
                    var viewport = Bounds.From(root.panel.visualTree.worldBound);
                    if (!viewport.Valid || viewport.W <= 0 || viewport.H <= 0)
                        layout = "layout_unavailable";
                    else
                    {
                        var context = new Context { Known = flat == true, Clip = flat == true ? viewport : (Bounds?)null };
                        if (flat != true)
                            b.Unknown = true;
                        for (int i = ancestors.Count - 1; i >= 0; i--)
                        {
                            if (!b.Charge(ref b.Probes))
                                break;
                            context = ObserveContext(ancestors[i], context, b, out _, out _, out _);
                        }
                        if (!b.Reasons.Contains("max_evaluations"))
                            Traverse(root, context, ancestors.Count, nodes, findings, b);
                    }
                }
            }
            bool unavailable = layout != "current";
            string overlap =
                unavailable ? "unavailable"
                : b.Pairs == 0 ? "disabled"
                : "complete";
            if (!unavailable && b.Pairs != 0)
                Pairs(nodes, findings, b, ref overlap);
            bool truncated = b.Reasons.Any(reason => reason != "per_string_limit");
            bool complete = !unavailable && b.Reasons.Count == 0;
            var data = new JObject
            {
                ["kind"] = "uitoolkit_layout_diagnostics",
                ["schema_version"] = 1,
                ["framework"] = "uitoolkit",
                ["mode"] = Application.isPlaying ? "play" : "edit",
                ["component_type"] = adapter.Kind,
                ["target_instance_id"] = go.GetInstanceIDCompat(),
                ["component_instance_id"] = component.GetInstanceIDCompat(),
                ["game_object"] = name,
                ["metadata_truncated_fields"] = fields,
                ["geometry_space"] = "panel_aabb",
                ["evidence"] = new JObject
                {
                    ["path_domain"] = "physical_indices",
                    ["layout_method"] = "current_observation",
                    ["clip_method"] = "rectangular_engine_style_v1",
                    ["overlap_method"] = "sibling_aabb_candidates",
                    ["flat_panel"] = flat.HasValue ? new JValue(flat.Value) : JValue.CreateNull(),
                },
                ["layout_status"] = layout,
                ["status"] =
                    unavailable ? "unavailable"
                    : complete && !b.Unknown && overlap == "complete" ? "complete"
                    : "partial",
                ["nodes"] = new JArray(nodes.Select(n => n.Output)),
                ["findings"] = findings,
                ["traversal"] = new JObject
                {
                    ["max_depth"] = b.Depth,
                    ["max_nodes"] = b.Nodes,
                    ["max_evaluations"] = b.Evaluations,
                    ["max_characters"] = b.Characters,
                    ["max_pairs"] = b.Pairs,
                    ["max_findings"] = b.Findings,
                    ["returned_nodes"] = nodes.Count,
                    ["evaluations"] = b.Used,
                    ["characters"] = b.Copied,
                    ["ancestor_inspections"] = b.Ancestors,
                    ["node_admissions"] = b.Admissions,
                    ["child_fetches"] = b.Fetches,
                    ["style_probes"] = b.Probes,
                    ["pairs_considered"] = b.PairCount,
                    ["findings_retained"] = findings.Count,
                    ["complete"] = complete,
                    ["truncated"] = truncated,
                    ["reasons"] = new JArray(b.Reasons),
                },
                ["stages"] = new JObject
                {
                    ["clipping"] =
                        unavailable ? "unavailable"
                        : b.Unknown || !complete ? "partial"
                        : "complete",
                    ["overlap"] = overlap,
                },
                ["limitations"] = new JArray(Limitations),
            };
            if (System.Text.Encoding.UTF8.GetByteCount(data.ToString()) > 2 * 1024 * 1024)
                throw Invalid("diagnostics_artifact_limit");
            return new SuccessResponse("Observed current-panel layout candidates.", data);
        }

        private static bool Finite(double n) => !double.IsNaN(n) && !double.IsInfinity(n);

        private static bool AxisAligned(Matrix4x4 m)
        {
            for (int i = 0; i < 16; i++)
                if (!Finite(m[i]))
                    return false;
            return m.m00 > 0
                && m.m11 > 0
                && Math.Abs(m.m01) < .000001
                && Math.Abs(m.m10) < .000001
                && Math.Abs(m.m02) < .000001
                && Math.Abs(m.m12) < .000001
                && Math.Abs(m.m20) < .000001
                && Math.Abs(m.m21) < .000001
                && Math.Abs(m.m23) < .000001
                && Math.Abs(m.m30) < .000001
                && Math.Abs(m.m31) < .000001
                && Math.Abs(m.m32) < .000001
                && Math.Abs(m.m22 - 1) < .000001
                && Math.Abs(m.m33 - 1) < .000001;
        }

        private static Bounds? Transform(Rect r, Matrix4x4 m)
        {
            if (!AxisAligned(m))
                return null;
            var result = new Bounds(r.x * (double)m.m00 + m.m03, r.y * (double)m.m11 + m.m13, r.width * (double)m.m00, r.height * (double)m.m11);
            return result.Valid ? result : (Bounds?)null;
        }

        private static Context ObserveContext(VisualElement e, Context inherited, Budget b, out string overflow, out bool? clips, out bool ready)
        {
            var resolved = e.resolvedStyle;
            var next = new Context
            {
                Display = inherited.Display.HasValue ? inherited.Display.Value && resolved.display != DisplayStyle.None : (bool?)null,
                Alpha =
                    inherited.Alpha.HasValue && Finite(resolved.opacity) && resolved.opacity >= 0 && resolved.opacity <= 1
                        ? inherited.Alpha.Value * resolved.opacity
                        : (double?)null,
                Known = inherited.Known,
                Clip = inherited.Clip,
            };
            overflow = "unknown";
            clips = null;
            ready = false;
            try
            {
                if (
                    ProbeAvailableForTests == false
                    || Computed == null
                    || Computed.DeclaringType != ElementType
                    || Computed.FieldType.Assembly != ElementType.Assembly
                    || ShouldClip == null
                    || ShouldClip.ReturnType != typeof(bool)
                    || ShouldClip.IsVirtual
                    || Initialized == null
                    || Initialized.PropertyType != typeof(bool)
                    || Initialized.GetMethod.IsVirtual
                    || OverflowValue == null
                    || ClipBoxValue == null
                    || OverflowValue.DeclaringType != Computed.FieldType
                    || ClipBoxValue.DeclaringType != Computed.FieldType
                )
                    throw new ArgumentException();
                ready = (bool)Initialized.GetValue(e);
                var style = Computed.GetValue(e);
                string value = OverflowValue.GetValue(style).ToString();
                overflow =
                    value == "Visible" ? "visible"
                    : value == "Hidden" ? "hidden"
                    : value == "Scroll" ? "scroll"
                    : "unknown";
                if (overflow == "unknown")
                    throw new ArgumentException();
                clips = (bool)ShouldClip.Invoke(e, null);
                if (clips == true)
                {
                    var matrix = e.worldTransform;
                    string clipBox = ClipBoxValue.GetValue(style).ToString();
                    if (
                        (clipBox != "PaddingBox" && clipBox != "ContentBox")
                        || !AxisAligned(matrix)
                        || resolved.borderTopLeftRadius != 0
                        || resolved.borderTopRightRadius != 0
                        || resolved.borderBottomLeftRadius != 0
                        || resolved.borderBottomRightRadius != 0
                    )
                        throw new ArgumentException();
                    float left = resolved.borderLeftWidth,
                        top = resolved.borderTopWidth,
                        right = resolved.borderRightWidth,
                        bottom = resolved.borderBottomWidth;
                    if (clipBox == "ContentBox")
                    {
                        left += resolved.paddingLeft;
                        top += resolved.paddingTop;
                        right += resolved.paddingRight;
                        bottom += resolved.paddingBottom;
                    }
                    var rect = Transform(new Rect(left, top, Math.Max(0, e.layout.width - left - right), Math.Max(0, e.layout.height - top - bottom)), matrix);
                    if (!rect.HasValue)
                        throw new ArgumentException();
                    next.Clip = inherited.Clip.HasValue ? inherited.Clip.Value.Intersection(rect.Value) : rect;
                }
            }
            catch
            {
                next.Known = false;
                b.Unknown = true;
            }
            if (!ready || !next.Alpha.HasValue)
            {
                next.Known = false;
                b.Unknown = true;
            }
            return next;
        }

        private static Node Admit(
            VisualElement e,
            Context inherited,
            int parent,
            int sibling,
            int depth,
            string path,
            List<Node> nodes,
            JArray findings,
            Budget b
        )
        {
            var fields = new JArray();
            if (path.Length > b.Characters - b.Copied)
            {
                b.Reason("max_characters");
                return null;
            }
            if (!b.Charge(ref b.Admissions))
                return null;
            string copiedPath = b.Copy(path, "diagnostic_path", fields, true);
            var node = new Node
            {
                Element = e,
                Index = nodes.Count,
                Parent = parent,
                Sibling = sibling,
                Depth = depth,
                Path = path,
                Count = e.hierarchy.childCount,
            };
            bool probe = b.Charge(ref b.Probes);
            string overflow = "unknown";
            bool? clips = null;
            bool ready = false;
            var context = probe
                ? ObserveContext(e, inherited, b, out overflow, out clips, out ready)
                : new Context
                {
                    Known = false,
                    Display = null,
                    Alpha = null,
                    Clip = inherited.Clip,
                };
            node.Children = context;
            bool displayed = context.Display == true;
            string visibility = "unknown";
            if (probe)
            {
                var resolved = e.resolvedStyle;
                visibility =
                    resolved.visibility == Visibility.Visible ? "visible"
                    : resolved.visibility == Visibility.Hidden ? "hidden"
                    : "unknown";
                if (displayed)
                {
                    var box = Bounds.From(e.worldBound);
                    if (box.Valid)
                        node.Box = box;
                    node.Content = Transform(e.contentRect, e.worldTransform);
                    if (!node.Content.HasValue)
                    {
                        context.Known = false;
                        b.Unknown = true;
                    }
                }
            }
            string layout =
                context.Display == false ? "not_applicable_display_none"
                : !probe || !ready || !node.Box.HasValue ? "unknown"
                : node.Box.Value.W == 0 || node.Box.Value.H == 0 ? "empty"
                : "current";
            string clipping =
                context.Display == false || visibility == "hidden" || context.Alpha == 0 || layout == "empty" ? "not_applicable"
                : !probe || !ready || !inherited.Known || !node.Box.HasValue || !node.Content.HasValue ? "unknown"
                : "not_clipped";
            if (clipping == "not_clipped" && inherited.Clip.HasValue)
            {
                node.Hit = node.Box.Value.Intersection(inherited.Clip.Value);
                if (node.Hit.Value.W <= 0 || node.Hit.Value.H <= 0)
                    clipping = "full";
                else if (node.Box.Value.Outside(inherited.Clip.Value, 0))
                    clipping = "partial";
            }
            else
                node.Hit = node.Box;
            if (clipping == "unknown" || layout == "unknown" || !probe)
                b.Unknown = true;
            node.Eligible =
                displayed
                && visibility == "visible"
                && context.Alpha > 0
                && node.Box.HasValue
                && node.Box.Value.W > 0
                && node.Box.Value.H > 0
                && clipping != "full";
            node.Output = new JObject
            {
                ["diagnostic_path"] = copiedPath,
                ["parent_index"] = parent < 0 ? JValue.CreateNull() : new JValue(parent),
                ["sibling_index"] = sibling,
                ["name"] = b.Copy(e.name, "name", fields),
                ["type"] = b.Copy(e.GetType().FullName, "type", fields),
                ["truncated_fields"] = fields,
                ["layout_status"] = layout,
                ["bounds"] = node.Box.HasValue ? node.Box.Value.Json : null,
                ["displayed"] = context.Display.HasValue ? new JValue(context.Display.Value) : JValue.CreateNull(),
                ["visibility"] = visibility,
                ["effective_opacity"] = context.Alpha.HasValue ? new JValue(context.Alpha.Value) : JValue.CreateNull(),
                ["suppression"] = new JArray(
                    new[]
                    {
                        context.Display == false ? "display_none" : null,
                        visibility == "hidden" ? "visibility_hidden" : null,
                        context.Alpha == 0 ? "opacity_zero" : null,
                        layout == "empty" ? "empty_geometry" : null,
                    }.Where(v => v != null)
                ),
                ["overflow"] = overflow,
                ["clips_children"] = clips.HasValue ? new JValue(clips.Value) : JValue.CreateNull(),
                ["clipping"] = clipping,
                ["effective_clip"] = inherited.Known && inherited.Clip.HasValue ? inherited.Clip.Value.Json : null,
            };
            nodes.Add(node);
            if (clipping == "full" || clipping == "partial")
                Finding(findings, b, "clipped_" + clipping, node.Index, null, node.Hit, false);
            if (node.Eligible && node.Content.HasValue && parent >= 0 && nodes[parent].Content.HasValue && node.Box.Value.Outside(nodes[parent].Content.Value))
                Finding(findings, b, "outside_parent_content", node.Index, parent, null, clipping == "unknown");
            return node;
        }

        private static void Traverse(VisualElement root, Context context, int ancestors, List<Node> nodes, JArray findings, Budget b)
        {
            var first = Admit(root, context, -1, 0, 0, "", nodes, findings, b);
            if (first == null)
                return;
            var stack = new Stack<Node>();
            stack.Push(first);
            while (stack.Count > 0)
            {
                var frame = stack.Peek();
                if (frame.NextChild == frame.Count)
                {
                    stack.Pop();
                    continue;
                }
                if (frame.Depth >= b.Depth || frame.Depth + ancestors >= 127)
                {
                    b.Reason(frame.Depth >= b.Depth ? "max_depth" : "ancestor_depth");
                    stack.Pop();
                    continue;
                }
                if (nodes.Count == b.Nodes)
                {
                    b.Reason("max_nodes");
                    break;
                }
                if (b.Copied == b.Characters || b.Reasons.Contains("max_characters"))
                {
                    b.Reason("max_characters");
                    break;
                }
                if (findings.Count == b.Findings)
                {
                    b.Reason("max_findings");
                    break;
                }
                if (!b.Charge(ref b.Fetches))
                    break;
                int index = frame.NextChild++;
                var child = frame.Element.hierarchy[index];
                var admitted = Admit(
                    child,
                    frame.Children,
                    frame.Index,
                    index,
                    frame.Depth + 1,
                    frame.Path + "/" + index.ToString(CultureInfo.InvariantCulture),
                    nodes,
                    findings,
                    b
                );
                if (admitted == null)
                    break;
                stack.Push(admitted);
            }
        }

        private static bool Finding(JArray findings, Budget b, string code, int index, int? other, Bounds? intersection, bool unknown)
        {
            if (findings.Count == b.Findings)
            {
                b.Reason("max_findings");
                return false;
            }
            findings.Add(
                new JObject
                {
                    ["code"] = code,
                    ["severity"] = "candidate",
                    ["node_index"] = index,
                    ["other_index"] = other.HasValue ? new JValue(other.Value) : JValue.CreateNull(),
                    ["intersection"] = intersection.HasValue ? intersection.Value.Json : null,
                    ["clipping_unknown"] = unknown,
                }
            );
            return true;
        }

        private static void Pairs(List<Node> nodes, JArray findings, Budget b, ref string status)
        {
            var groups = new Dictionary<int, List<Node>>();
            foreach (var node in nodes)
            {
                if (node.Parent < 0)
                    continue;
                if (!groups.TryGetValue(node.Parent, out var group))
                    groups[node.Parent] = group = new List<Node>();
                group.Add(node);
            }
            foreach (var group in groups.Values)
                for (int i = 0; i < group.Count; i++)
                for (int j = i + 1; j < group.Count; j++)
                {
                    if (b.PairCount == b.Pairs || findings.Count == b.Findings || !b.Charge(ref b.PairCount))
                    {
                        if (b.PairCount == b.Pairs)
                            b.Reason("max_pairs");
                        if (findings.Count == b.Findings)
                            b.Reason("max_findings");
                        status = "truncated";
                        return;
                    }
                    var a = group[i];
                    var c = group[j];
                    if (!a.Eligible || !c.Eligible || !a.Hit.HasValue || !c.Hit.HasValue)
                        continue;
                    var intersection = a.Hit.Value.Intersection(c.Hit.Value);
                    if (intersection.W > .01 && intersection.H > .01)
                        Finding(
                            findings,
                            b,
                            "overlap_candidate",
                            a.Index,
                            c.Index,
                            intersection,
                            a.Output.Value<string>("clipping") == "unknown" || c.Output.Value<string>("clipping") == "unknown"
                        );
                }
            if (b.Reasons.Any(reason => reason != "per_string_limit"))
                status = "truncated";
        }
    }
}
