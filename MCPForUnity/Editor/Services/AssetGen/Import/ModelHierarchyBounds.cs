using System.Collections.Generic;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    /// <summary>Bounded default-pose mesh envelopes, including every hierarchy transform. Never reads vertex arrays.</summary>
    internal static class ModelHierarchyBounds
    {
        internal static bool IsFinite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        internal static bool IsFinite(Vector3 value) => IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);

        internal static Matrix4x4 RootMatrix(GameObject root) =>
            Matrix4x4.TRS(root.transform.localPosition, root.transform.localRotation, root.transform.localScale);

        internal static bool TryMeasure(
            GameObject root,
            Matrix4x4 rootToMeasurement,
            out Bounds bounds,
            out string errorCode,
            int maxNodes = 250000,
            int maxDepth = 256,
            int maxMeshes = 250000
        )
        {
            bounds = default;
            errorCode = null;
            if (root == null)
            {
                errorCode = "normalization_invalid_bounds";
                return false;
            }
            var pending = new Stack<(Transform node, Matrix4x4 matrix, int child, int depth)>();
            pending.Push((root.transform, rootToMeasurement, -1, 1));
            int nodes = 0;
            int meshes = 0;
            bool any = false;
            Bounds accumulated = default;
            while (pending.Count > 0)
            {
                var frame = pending.Pop();
                if (frame.child < 0)
                {
                    if (++nodes > maxNodes || frame.depth > maxDepth)
                    {
                        errorCode = "normalization_bounds_limit";
                        return false;
                    }
                    for (int i = 0; i < 16; i++)
                    {
                        if (!IsFinite(frame.matrix[i]))
                        {
                            errorCode = "normalization_invalid_bounds";
                            return false;
                        }
                    }
                    var filter = frame.node.GetComponent<MeshFilter>();
                    var skinned = frame.node.GetComponent<SkinnedMeshRenderer>();
                    if (
                        !Add(filter != null ? filter.sharedMesh : null, frame.matrix, ref accumulated, ref any, ref meshes, maxMeshes, out errorCode)
                        || !Add(skinned != null ? skinned.sharedMesh : null, frame.matrix, ref accumulated, ref any, ref meshes, maxMeshes, out errorCode)
                    )
                        return false;
                    frame.child = 0;
                }
                if (frame.child < frame.node.childCount)
                {
                    // Budget is checked before descending; siblings are never pushed as an unbounded list.
                    if (nodes >= maxNodes || frame.depth >= maxDepth)
                    {
                        errorCode = "normalization_bounds_limit";
                        return false;
                    }
                    Transform child = frame.node.GetChild(frame.child);
                    pending.Push((frame.node, frame.matrix, frame.child + 1, frame.depth));
                    Matrix4x4 local = Matrix4x4.TRS(child.localPosition, child.localRotation, child.localScale);
                    pending.Push((child, frame.matrix * local, -1, frame.depth + 1));
                }
            }
            if (!any || !IsFinite(accumulated.center) || !IsFinite(accumulated.size))
            {
                errorCode = "normalization_invalid_bounds";
                return false;
            }
            bounds = accumulated;
            return true;
        }

        private static bool Add(Mesh mesh, Matrix4x4 matrix, ref Bounds total, ref bool any, ref int count, int maximum, out string error)
        {
            error = null;
            if (mesh == null || mesh.vertexCount == 0)
                return true;
            if (++count > maximum)
            {
                error = "normalization_bounds_limit";
                return false;
            }
            Bounds local = mesh.bounds;
            if (!IsFinite(local.center) || !IsFinite(local.extents) || local.extents.x < 0 || local.extents.y < 0 || local.extents.z < 0)
            {
                error = "normalization_invalid_bounds";
                return false;
            }
            for (int corner = 0; corner < 8; corner++)
            {
                var offset = new Vector3(
                    (corner & 1) == 0 ? -local.extents.x : local.extents.x,
                    (corner & 2) == 0 ? -local.extents.y : local.extents.y,
                    (corner & 4) == 0 ? -local.extents.z : local.extents.z
                );
                Vector3 point = matrix.MultiplyPoint3x4(local.center + offset);
                if (!IsFinite(point))
                {
                    error = "normalization_invalid_bounds";
                    return false;
                }
                if (!any)
                {
                    total = new Bounds(point, Vector3.zero);
                    any = true;
                }
                else
                    total.Encapsulate(point);
            }
            return true;
        }
    }
}
