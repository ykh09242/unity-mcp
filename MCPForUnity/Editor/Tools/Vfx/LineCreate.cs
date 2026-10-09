using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class LineCreate
    {
        public static object CreateLine(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            Vector3 start = ManageVfxCommon.ParseVector3(@params["start"]);
            Vector3 end = ManageVfxCommon.ParseVector3(@params["end"]);
            var applyAppearance = PrepareAppearance(@params, lr);

            Undo.RecordObject(lr, "Create Line");
            lr.positionCount = 2;
            lr.SetPosition(0, start);
            lr.SetPosition(1, end);

            RendererHelpers.EnsureMaterial(lr);

            applyAppearance();

            EditorUtility.SetDirty(lr);

            return new { success = true, message = "Created line" };
        }

        public static object CreateCircle(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            Vector3 center = ManageVfxCommon.ParseVector3(@params["center"]);
            float radius = @params["radius"]?.ReadScalar<float?>() ?? 1f;
            int segments = @params["segments"]?.ReadScalar<int?>() ?? 32;
            if (segments < 1)
                return new { success = false, message = "segments must be positive" };
            Vector3 normal = @params["normal"] != null ? ManageVfxCommon.ParseVector3(@params["normal"]).normalized : Vector3.up;

            Vector3 right = Vector3.Cross(normal, Vector3.forward);
            if (right.sqrMagnitude < 0.001f)
                right = Vector3.Cross(normal, Vector3.up);
            right = right.normalized;
            Vector3 forward = Vector3.Cross(right, normal).normalized;
            var applyAppearance = PrepareAppearance(@params, lr);

            var positions = new Vector3[segments];
            for (int i = 0; i < segments; i++)
            {
                float angle = (float)i / segments * Mathf.PI * 2f;
                Vector3 point = center + (right * Mathf.Cos(angle) + forward * Mathf.Sin(angle)) * radius;
                if (
                    float.IsNaN(point.x)
                    || float.IsInfinity(point.x)
                    || float.IsNaN(point.y)
                    || float.IsInfinity(point.y)
                    || float.IsNaN(point.z)
                    || float.IsInfinity(point.z)
                )
                    return new { success = false, message = $"Generated circle position at index {i} must be finite." };
                positions[i] = point;
            }

            Undo.RecordObject(lr, "Create Circle");
            lr.positionCount = positions.Length;
            lr.loop = true;
            lr.SetPositions(positions);

            RendererHelpers.EnsureMaterial(lr);

            applyAppearance();

            EditorUtility.SetDirty(lr);
            return new { success = true, message = $"Created circle with {segments} segments" };
        }

        public static object CreateArc(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            Vector3 center = ManageVfxCommon.ParseVector3(@params["center"]);
            float radius = @params["radius"]?.ReadScalar<float?>() ?? 1f;
            float startAngle = (@params["startAngle"]?.ReadScalar<float?>() ?? 0f) * Mathf.Deg2Rad;
            float endAngle = (@params["endAngle"]?.ReadScalar<float?>() ?? 180f) * Mathf.Deg2Rad;
            int segments = @params["segments"]?.ReadScalar<int?>() ?? 16;
            if (segments < 1 || segments == int.MaxValue)
                return new { success = false, message = "segments must be positive and leave room for the final position" };
            Vector3 normal = @params["normal"] != null ? ManageVfxCommon.ParseVector3(@params["normal"]).normalized : Vector3.up;

            Vector3 right = Vector3.Cross(normal, Vector3.forward);
            if (right.sqrMagnitude < 0.001f)
                right = Vector3.Cross(normal, Vector3.up);
            right = right.normalized;
            Vector3 forward = Vector3.Cross(right, normal).normalized;
            var applyAppearance = PrepareAppearance(@params, lr);

            var positions = new Vector3[segments + 1];
            for (int i = 0; i <= segments; i++)
            {
                float t = (float)i / segments;
                float angle = Mathf.Lerp(startAngle, endAngle, t);
                Vector3 point = center + (right * Mathf.Cos(angle) + forward * Mathf.Sin(angle)) * radius;
                if (
                    float.IsNaN(point.x)
                    || float.IsInfinity(point.x)
                    || float.IsNaN(point.y)
                    || float.IsInfinity(point.y)
                    || float.IsNaN(point.z)
                    || float.IsInfinity(point.z)
                )
                    return new { success = false, message = $"Generated arc position at index {i} must be finite." };
                positions[i] = point;
            }

            Undo.RecordObject(lr, "Create Arc");
            lr.positionCount = positions.Length;
            lr.loop = false;
            lr.SetPositions(positions);

            RendererHelpers.EnsureMaterial(lr);

            applyAppearance();

            EditorUtility.SetDirty(lr);
            return new { success = true, message = $"Created arc with {segments} segments" };
        }

        public static object CreateBezier(JObject @params)
        {
            LineRenderer lr = LineRead.FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            Vector3 start = ManageVfxCommon.ParseVector3(@params["start"]);
            Vector3 end = ManageVfxCommon.ParseVector3(@params["end"]);
            Vector3 cp1 = ManageVfxCommon.ParseVector3(@params["controlPoint1"] ?? @params["control1"]);
            Vector3 cp2 =
                @params["controlPoint2"] != null || @params["control2"] != null
                    ? ManageVfxCommon.ParseVector3(@params["controlPoint2"] ?? @params["control2"])
                    : cp1;
            int segments = @params["segments"]?.ReadScalar<int?>() ?? 32;
            if (segments < 1 || segments == int.MaxValue)
                return new { success = false, message = "segments must be positive and leave room for the final position" };
            bool isQuadratic = @params["controlPoint2"] == null && @params["control2"] == null;
            var applyAppearance = PrepareAppearance(@params, lr);

            Undo.RecordObject(lr, "Create Bezier");
            lr.positionCount = segments + 1;
            lr.loop = false;

            for (int i = 0; i <= segments; i++)
            {
                float t = (float)i / segments;
                Vector3 point;

                if (isQuadratic)
                {
                    float u = 1 - t;
                    point = u * u * start + 2 * u * t * cp1 + t * t * end;
                }
                else
                {
                    float u = 1 - t;
                    point = u * u * u * start + 3 * u * u * t * cp1 + 3 * u * t * t * cp2 + t * t * t * end;
                }

                lr.SetPosition(i, point);
            }

            RendererHelpers.EnsureMaterial(lr);

            applyAppearance();

            EditorUtility.SetDirty(lr);
            return new { success = true, message = $"Created {(isQuadratic ? "quadratic" : "cubic")} Bezier" };
        }

        private static Action PrepareAppearance(JObject @params, LineRenderer lr)
        {
            float? width = @params["width"]?.ReadScalar<float?>();
            float? startWidth = @params["startWidth"]?.ReadScalar<float?>();
            float? endWidth = @params["endWidth"]?.ReadScalar<float?>();
            Color? color = @params["color"] != null ? ManageVfxCommon.ParseColor(@params["color"]) : (Color?)null;
            Color? startColor = @params["startColor"] != null ? ManageVfxCommon.ParseColor(@params["startColor"]) : (Color?)null;
            Color? endColor = @params["endColor"] != null ? ManageVfxCommon.ParseColor(@params["endColor"]) : (Color?)null;

            return () =>
            {
                if (width.HasValue)
                {
                    lr.startWidth = width.Value;
                    lr.endWidth = width.Value;
                }
                if (startWidth.HasValue)
                    lr.startWidth = startWidth.Value;
                if (endWidth.HasValue)
                    lr.endWidth = endWidth.Value;
                if (color.HasValue)
                {
                    lr.startColor = color.Value;
                    lr.endColor = color.Value;
                }
                if (startColor.HasValue)
                    lr.startColor = startColor.Value;
                if (endColor.HasValue)
                    lr.endColor = endColor.Value;
            };
        }
    }
}
