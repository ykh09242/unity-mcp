using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.ProBuilder
{
    internal static class ProBuilderSmoothing
    {
        internal static object SetSmoothing(JObject @params)
        {
            var pbMesh = ManageProBuilder.RequireProBuilderMesh(@params);
            var props = ManageProBuilder.ExtractProperties(@params);

            var faceIndicesToken = props["faceIndices"] ?? props["face_indices"];
            if (faceIndicesToken == null)
                return new ErrorResponse("faceIndices parameter is required.");

            var smoothingGroup = props["smoothingGroup"]?.ReadScalar<int?>() ?? props["smoothing_group"]?.ReadScalar<int?>() ?? 0;

            var faces = ManageProBuilder.GetFacesByIndices(pbMesh, faceIndicesToken);
            var smProp = ManageProBuilder._faceType.GetProperty("smoothingGroup");
            if (smProp == null)
                return new ErrorResponse("Could not find smoothingGroup property on Face type.");

            Undo.RecordObject(pbMesh, "Set Smoothing Groups");

            foreach (var face in faces)
                smProp.SetValue(face, smoothingGroup);

            ManageProBuilder.RefreshMesh(pbMesh);

            return new SuccessResponse($"Set smoothing group {smoothingGroup} on {faces.Length} face(s)", new { facesModified = faces.Length, smoothingGroup });
        }

        internal static object AutoSmooth(JObject @params)
        {
            var pbMesh = ManageProBuilder.RequireProBuilderMesh(@params);
            var props = ManageProBuilder.ExtractProperties(@params);

            var angleThreshold = props["angleThreshold"]?.ReadScalar<float?>() ?? props["angle_threshold"]?.ReadScalar<float?>() ?? 30f;

            if (ManageProBuilder._smoothingType == null)
                return new ErrorResponse("Smoothing type not found in ProBuilder assembly.");

            // Check for faceIndices to limit scope
            var faceIndicesToken = props["faceIndices"] ?? props["face_indices"];
            object facesToSmooth;
            if (faceIndicesToken != null)
            {
                facesToSmooth = ManageProBuilder.GetFacesByIndices(pbMesh, faceIndicesToken);
            }
            else
            {
                facesToSmooth = ManageProBuilder.GetFacesArray(pbMesh);
            }

            // Smoothing.ApplySmoothingGroups(ProBuilderMesh mesh, IEnumerable<Face> faces, float angle)
            var applyMethod = ManageProBuilder._smoothingType.GetMethod("ApplySmoothingGroups", BindingFlags.Static | BindingFlags.Public);
            if (applyMethod == null)
                return new ErrorResponse("Smoothing.ApplySmoothingGroups method not found.");

            Undo.RecordObject(pbMesh, "Auto Smooth");
            applyMethod.Invoke(null, new object[] { pbMesh, facesToSmooth, angleThreshold });

            ManageProBuilder.RefreshMesh(pbMesh);

            return new SuccessResponse(
                $"Auto-smoothed with angle threshold {angleThreshold}°",
                new { angleThreshold, faceCount = ((System.Collections.IList)facesToSmooth).Count }
            );
        }
    }
}
