using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class LineRead
    {
        public static LineRenderer FindLineRenderer(JObject @params) => ManageVfxCommon.FindComponent<LineRenderer>(@params);

        public static LineRenderer FindLineRenderer(JObject @params, out string error) => ManageVfxCommon.FindComponent<LineRenderer>(@params, out error);

        public static string FindLineRendererError(JObject @params) => ManageVfxCommon.FindComponentError<LineRenderer>(@params);

        public static object GetInfo(JObject @params)
        {
            LineRenderer lr = FindLineRenderer(@params, out var componentError);
            if (lr == null)
                return new { success = false, message = componentError };

            int total = lr.positionCount;
            if (
                !PaginationBounds.TryRead(@params["positionOffset"], 0, 0, total, "position_offset", out int offset, out string error)
                || !PaginationBounds.TryRead(
                    @params["positionLimit"],
                    LinePointBudget.MaxPoints,
                    1,
                    LinePointBudget.MaxPoints,
                    "position_limit",
                    out int limit,
                    out error
                )
            )
                return new { success = false, message = error };
            var positions = new Vector3[Math.Min(total - offset, limit)];
            if (positions.Length == total)
                lr.GetPositions(positions);
            else
                for (int i = 0; i < positions.Length; i++)
                    positions[i] = lr.GetPosition(offset + i);

            var material = lr.sharedMaterial;
            return new
            {
                success = true,
                data = new
                {
                    gameObject = lr.gameObject.name,
                    positionCount = total,
                    positionOffset = offset,
                    nextPositionOffset = offset + positions.Length < total ? offset + positions.Length : (int?)null,
                    positions = positions
                        .Select(p => new
                        {
                            x = p.x,
                            y = p.y,
                            z = p.z,
                        })
                        .ToArray(),
                    startWidth = lr.startWidth,
                    endWidth = lr.endWidth,
                    loop = lr.loop,
                    useWorldSpace = lr.useWorldSpace,
                    alignment = lr.alignment.ToString(),
                    textureMode = lr.textureMode.ToString(),
                    numCornerVertices = lr.numCornerVertices,
                    numCapVertices = lr.numCapVertices,
                    generateLightingData = lr.generateLightingData,
                    material = material != null ? material.name : null,
                    shadowCastingMode = lr.shadowCastingMode.ToString(),
                    receiveShadows = lr.receiveShadows,
                    lightProbeUsage = lr.lightProbeUsage.ToString(),
                    reflectionProbeUsage = lr.reflectionProbeUsage.ToString(),
                    sortingOrder = lr.sortingOrder,
                    sortingLayerName = lr.sortingLayerName,
                    renderingLayerMask = lr.renderingLayerMask,
                },
            };
        }
    }
}
