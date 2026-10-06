using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class TrailWrite
    {
        public static object SetTime(JObject @params)
        {
            TrailRenderer tr = TrailRead.FindTrailRenderer(@params);
            if (tr == null) return new { success = false, message = TrailRead.FindTrailRendererError(@params) };

            float time = @params["time"]?.ReadScalar<float?>() ?? 5f;
            RequireFinite(time, "time");

            RendererHelpers.EnsureMaterial(tr);
            Undo.RecordObject(tr, "Set Trail Time");
            tr.time = time;
            EditorUtility.SetDirty(tr);

            return new { success = true, message = $"Set trail time to {time}s" };
        }

        private static void RequireFinite(float value, string name)
        {
            if (float.IsNaN(value) || float.IsInfinity(value))
                throw new ArgumentException($"'{name}' must be finite.");
        }

        public static object SetWidth(JObject @params)
        {
            TrailRenderer tr = TrailRead.FindTrailRenderer(@params);
            if (tr == null) return new { success = false, message = TrailRead.FindTrailRendererError(@params) };

            var changes = new List<string>();
            var apply = new List<Action>();

            RendererHelpers.ApplyWidthProperties(@params, changes,
                v => { RequireFinite(v, "startWidth"); apply.Add(() => tr.startWidth = v); },
                v => { RequireFinite(v, "endWidth"); apply.Add(() => tr.endWidth = v); },
                v => apply.Add(() => tr.widthCurve = v),
                v => { RequireFinite(v, "widthMultiplier"); apply.Add(() => tr.widthMultiplier = v); },
                ManageVfxCommon.ParseAnimationCurve);

            RendererHelpers.EnsureMaterial(tr);
            Undo.RecordObject(tr, "Set Trail Width");
            foreach (var update in apply) update();
            EditorUtility.SetDirty(tr);
            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }

        public static object SetColor(JObject @params)
        {
            TrailRenderer tr = TrailRead.FindTrailRenderer(@params);
            if (tr == null) return new { success = false, message = TrailRead.FindTrailRendererError(@params) };

            RendererHelpers.EnsureMaterial(tr);

            Undo.RecordObject(tr, "Set Trail Color");
            var changes = new List<string>();

            RendererHelpers.ApplyColorProperties(@params, changes,
                v => tr.startColor = v, v => tr.endColor = v,
                v => tr.colorGradient = v,
                ManageVfxCommon.ParseColor, ManageVfxCommon.ParseGradient, fadeEndAlpha: true);

            EditorUtility.SetDirty(tr);
            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }

        public static object SetMaterial(JObject @params)
        {
            TrailRenderer tr = TrailRead.FindTrailRenderer(@params);
            return RendererHelpers.SetRendererMaterial(tr, @params, "Set Trail Material", ManageVfxCommon.FindMaterialByPath);
        }

        public static object SetProperties(JObject @params)
        {
            TrailRenderer tr = TrailRead.FindTrailRenderer(@params);
            if (tr == null) return new { success = false, message = TrailRead.FindTrailRendererError(@params) };

            var changes = new List<string>();
            var apply = new List<Action>();
            bool hasSuppliedMaterial = false;

            // Handle material if provided
            if (@params["materialPath"] != null)
            {
                Material mat = ManageVfxCommon.FindMaterialByPath(@params["materialPath"].ToString());
                if (mat != null)
                {
                    hasSuppliedMaterial = true;
                    apply.Add(() => tr.sharedMaterial = mat);
                    changes.Add($"material={mat.name}");
                }
                else
                {
                    McpLog.Warn($"Material not found: {@params["materialPath"]}");
                }
            }

            // Handle time if provided
            if (@params["time"] != null) { float value = @params["time"].ReadScalar<float>(); RequireFinite(value, "time"); apply.Add(() => tr.time = value); changes.Add("time"); }

            // Handle width properties if provided
            if (@params["width"] != null || @params["startWidth"] != null || @params["endWidth"] != null)
            {
                if (@params["width"] != null)
                {
                    float w = @params["width"].ReadScalar<float>();
                    RequireFinite(w, "width");
                    apply.Add(() => { tr.startWidth = w; tr.endWidth = w; });
                    changes.Add("width");
                }
                if (@params["startWidth"] != null) { float value = @params["startWidth"].ReadScalar<float>(); RequireFinite(value, "startWidth"); apply.Add(() => tr.startWidth = value); changes.Add("startWidth"); }
                if (@params["endWidth"] != null) { float value = @params["endWidth"].ReadScalar<float>(); RequireFinite(value, "endWidth"); apply.Add(() => tr.endWidth = value); changes.Add("endWidth"); }
            }

            if (@params["minVertexDistance"] != null) { float value = @params["minVertexDistance"].ReadScalar<float>(); RequireFinite(value, "minVertexDistance"); apply.Add(() => tr.minVertexDistance = value); changes.Add("minVertexDistance"); }
            if (@params["autodestruct"] != null) { bool value = @params["autodestruct"].ReadScalar<bool>(); apply.Add(() => tr.autodestruct = value); changes.Add("autodestruct"); }
            if (@params["emitting"] != null) { bool value = @params["emitting"].ReadScalar<bool>(); apply.Add(() => tr.emitting = value); changes.Add("emitting"); }

            RendererHelpers.ApplyLineTrailProperties(@params, changes,
                null, null,
                v => apply.Add(() => tr.numCornerVertices = v), v => apply.Add(() => tr.numCapVertices = v),
                v => apply.Add(() => tr.alignment = v), v => apply.Add(() => tr.textureMode = v),
                v => apply.Add(() => tr.generateLightingData = v));

            apply.Add(RendererHelpers.PrepareCommonRendererProperties(tr, @params, changes));

            if (!hasSuppliedMaterial) RendererHelpers.EnsureMaterial(tr);
            Undo.RecordObject(tr, "Set Trail Properties");
            foreach (var update in apply) update();
            EditorUtility.SetDirty(tr);
            return new { success = true, message = $"Updated: {string.Join(", ", changes)}" };
        }
    }
}
