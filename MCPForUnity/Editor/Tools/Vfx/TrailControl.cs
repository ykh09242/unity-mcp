using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class TrailControl
    {
        public static object Clear(JObject @params)
        {
            TrailRenderer tr = TrailRead.FindTrailRenderer(@params, out var componentError);
            if (tr == null)
                return new { success = false, message = componentError };

            Undo.RecordObject(tr, "Clear Trail");
            tr.Clear();
            return new { success = true, message = "Trail cleared" };
        }

        public static object Emit(JObject @params)
        {
            TrailRenderer tr = TrailRead.FindTrailRenderer(@params, out var componentError);
            if (tr == null)
                return new { success = false, message = componentError };

            Vector3 pos = ManageVfxCommon.ParseVector3(@params["position"]);
            RendererHelpers.EnsureMaterial(tr);
            tr.AddPosition(pos);
            return new { success = true, message = $"Emitted at ({pos.x}, {pos.y}, {pos.z})" };
        }
    }
}
