using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class LineWrite
    {
        public static object SetPositions(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            if (!TryParsePositions(@params["positions"], out var positions, out var error))
                return new { success = false, message = error };

            RendererHelpers.EnsureMaterial(lr);

            Undo.RecordObject(lr, "Set Line Positions");
            lr.positionCount = positions.Length;
            lr.SetPositions(positions);
            EditorUtility.SetDirty(lr);

            return new { success = true, message = $"Set {positions.Length} positions" };
        }

        public static object AddPosition(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            if (!TryParsePosition(@params["position"], allowDefault: true, out var pos))
                return new { success = false, message = "Invalid position: expected [x, y, z] or {x, y, z}" };

            RendererHelpers.EnsureMaterial(lr);

            Undo.RecordObject(lr, "Add Line Position");
            int idx = lr.positionCount;
            lr.positionCount = idx + 1;
            lr.SetPosition(idx, pos);
            EditorUtility.SetDirty(lr);

            return new
            {
                success = true,
                message = $"Added position at index {idx}",
                index = idx,
            };
        }

        public static object SetPosition(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            int index = @params["index"]?.ReadScalar<int?>() ?? -1;
            if (index < 0 || index >= lr.positionCount)
                return new { success = false, message = $"Invalid index {index}" };

            if (!TryParsePosition(@params["position"], allowDefault: true, out var pos))
                return new { success = false, message = "Invalid position: expected [x, y, z] or {x, y, z}" };

            RendererHelpers.EnsureMaterial(lr);

            Undo.RecordObject(lr, "Set Line Position");
            lr.SetPosition(index, pos);
            EditorUtility.SetDirty(lr);

            return new { success = true, message = $"Set position at index {index}" };
        }

        private static void RequireFinite(float value, string name)
        {
            if (float.IsNaN(value) || float.IsInfinity(value))
                throw new ArgumentException($"'{name}' must be finite.");
        }

        public static object SetWidth(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            var changes = new List<string>();
            var updates = new List<Action>();

            RendererHelpers.ApplyWidthProperties(
                @params,
                changes,
                v =>
                {
                    RequireFinite(v, "startWidth");
                    updates.Add(() => lr.startWidth = v);
                },
                v =>
                {
                    RequireFinite(v, "endWidth");
                    updates.Add(() => lr.endWidth = v);
                },
                v => updates.Add(() => lr.widthCurve = v),
                v =>
                {
                    RequireFinite(v, "widthMultiplier");
                    updates.Add(() => lr.widthMultiplier = v);
                },
                ManageVfxCommon.ParseAnimationCurve
            );

            RendererHelpers.EnsureMaterial(lr);
            Undo.RecordObject(lr, "Set Line Width");
            foreach (var update in updates)
                update();
            EditorUtility.SetDirty(lr);
            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }

        public static object SetColor(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            var changes = new List<string>();
            var apply = RendererHelpers.PrepareColorProperties(
                @params,
                changes,
                v => lr.startColor = v,
                v => lr.endColor = v,
                v => lr.colorGradient = v,
                ManageVfxCommon.ParseColor,
                ManageVfxCommon.ParseGradient,
                fadeEndAlpha: false
            );

            RendererHelpers.EnsureMaterial(lr);
            Undo.RecordObject(lr, "Set Line Color");
            apply();
            EditorUtility.SetDirty(lr);
            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }

        public static object SetMaterial(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params);
            return RendererHelpers.SetRendererMaterial(lr, @params, "Set Line Material", ManageVfxCommon.FindMaterialByPath);
        }

        public static object SetProperties(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            Vector3[] positions = null;
            int? positionCount = null;
            if (@params["positions"] != null)
            {
                // Explicit null retains the existing positions and suppresses positionCount.
                if (@params["positions"].Type != JTokenType.Null && !TryParsePositions(@params["positions"], out positions, out var error))
                    return new { success = false, message = error };
            }
            else if (@params["positionCount"] != null)
            {
                positionCount = @params["positionCount"].ReadScalar<int>();
                if (positionCount < 0)
                    return new { success = false, message = "positionCount must be non-negative" };
            }

            var rendererChanges = new List<string>();
            var updates = new List<Action>();
            RendererHelpers.ApplyLineTrailProperties(
                @params,
                rendererChanges,
                v => updates.Add(() => lr.loop = v),
                v => updates.Add(() => lr.useWorldSpace = v),
                v => updates.Add(() => lr.numCornerVertices = v),
                v => updates.Add(() => lr.numCapVertices = v),
                v => updates.Add(() => lr.alignment = v),
                v => updates.Add(() => lr.textureMode = v),
                v => updates.Add(() => lr.generateLightingData = v)
            );
            var applyCommon = RendererHelpers.PrepareCommonRendererProperties(lr, @params, rendererChanges);

            Material material = null;
            if (@params["materialPath"] != null)
            {
                material = ManageVfxCommon.FindMaterialByPath(@params["materialPath"].ToString());
                if (material == null)
                    McpLog.Warn($"Material not found: {@params["materialPath"]}");
            }
            if (material == null)
                RendererHelpers.EnsureMaterial(lr);

            Undo.RecordObject(lr, "Set Line Properties");
            var changes = new List<string>();

            // Handle material if provided
            if (material != null)
            {
                lr.sharedMaterial = material;
                changes.Add($"material={material.name}");
            }

            // Handle positions if provided
            if (positions != null)
            {
                lr.positionCount = positions.Length;
                lr.SetPositions(positions);
                changes.Add($"positions({positions.Length})");
            }
            else if (positionCount.HasValue)
            {
                lr.positionCount = positionCount.Value;
                changes.Add("positionCount");
            }

            foreach (var update in updates)
                update();
            applyCommon();
            changes.AddRange(rendererChanges);

            EditorUtility.SetDirty(lr);
            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }

        private static bool TryParsePositions(JToken token, out Vector3[] positions, out string error)
        {
            positions = null;
            error = "Positions array required";
            if (!(token is JArray array))
                return false;

            positions = new Vector3[array.Count];
            for (int i = 0; i < array.Count; i++)
            {
                if (!TryParsePosition(array[i], allowDefault: false, out positions[i]))
                {
                    error = $"Invalid positions[{i}]: expected [x, y, z] or {{x, y, z}}";
                    return false;
                }
            }
            error = null;
            return true;
        }

        private static bool TryParsePosition(JToken token, bool allowDefault, out Vector3 position)
        {
            position = Vector3.zero;
            if (token == null || token.Type == JTokenType.Null)
                return allowDefault;
            var parsed = VectorParsing.ParseVector3(token);
            if (!parsed.HasValue)
                return false;
            position = parsed.Value;
            return true;
        }

        public static object Clear(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            int count = lr.positionCount;
            Undo.RecordObject(lr, "Clear Line");
            lr.positionCount = 0;
            EditorUtility.SetDirty(lr);

            return new { success = true, message = $"Cleared {count} positions" };
        }
    }
}
