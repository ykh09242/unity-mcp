using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Animation
{
    internal static class ClipRoundTrip
    {
        private sealed class Binding
        {
            internal EditorCurveBinding Value;
            internal bool Objects;
            internal string Id =>
                Hash128
                    .Compute(
                        new JArray(Objects ? "object" : "float", Value.path, Value.type.AssemblyQualifiedName, Value.propertyName).ToString(
                            Newtonsoft.Json.Formatting.None
                        )
                    )
                    .ToString();
        }

        internal static object Handle(JObject request, string action)
        {
            AnimationAssetAccess.Stable();
            if (request.ToString(Newtonsoft.Json.Formatting.None).Length > 1048576)
                throw new ArgumentException("Animation round-trip request exceeds 1 MiB.");
            string path = AnimationAssetAccess.Path(request, "clipPath");
            AnimationClip clip = Load(request, path);
            var bindings = AnimationUtility
                .GetCurveBindings(clip)
                .Select(binding => new Binding { Value = binding })
                .Concat(AnimationUtility.GetObjectReferenceCurveBindings(clip).Select(binding => new Binding { Value = binding, Objects = true }))
                .OrderBy(binding => binding.Id, StringComparer.Ordinal)
                .ToArray();
            if (bindings.Length > 2048)
                throw new ArgumentException("Clip exceeds 2048 curve bindings.");
            string revision = AnimationAssetAccess.Revision(path, new UnityEngine.Object[] { clip });
            if (action == "get_curves")
            {
                return AnimationAssetAccess.Page(
                    bindings.Length,
                    index =>
                    {
                        var binding = bindings[index];
                        return new JObject
                        {
                            ["binding_id"] = binding.Id,
                            ["kind"] = binding.Objects ? "object" : "float",
                            ["path"] = binding.Value.path,
                            ["property"] = binding.Value.propertyName,
                            ["type"] = binding.Value.type.AssemblyQualifiedName,
                            ["clip_file_id"] = AnimationAssetAccess.Id(clip),
                        };
                    },
                    request,
                    revision,
                    "curves"
                );
            }
            string id = AnimationAssetAccess.Text(request, "bindingId");
            Binding selected = bindings.SingleOrDefault(binding => binding.Id == id);
            if (selected == null)
                throw new ArgumentException("binding_id is stale or does not belong to this clip.");
            if (action == "get_curve")
                return Read(clip, selected, request, revision);
            AnimationAssetAccess.Expected(request, revision);
            if (
                AssetImporter.GetAtPath(path) is ModelImporter
                || (!path.EndsWith(".anim", StringComparison.OrdinalIgnoreCase) && !path.EndsWith(".asset", StringComparison.OrdinalIgnoreCase))
            )
                throw new ArgumentException("Imported/read-only clips cannot be edited; use a native .anim/.asset clip.");
            AnimationCurve curve = null;
            ObjectReferenceKeyframe[] objects = null;
            if (action == "replace_curve")
            {
                if (!(request["keys"] is JArray keys) || keys.Count < 1 || keys.Count > 1024)
                    throw new ArgumentException("Replacement requires 1-1024 ordered keys; use clip_delete_curve to remove a curve.");
                if (selected.Objects)
                    objects = ObjectKeys(keys);
                else
                    curve = FloatKeys(keys, AnimationUtility.GetEditorCurve(clip, selected.Value));
            }
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            string name = action == "delete_curve" ? "Delete Animation Curve" : "Replace Animation Curve";
            Undo.SetCurrentGroupName(name);
            try
            {
                Undo.RegisterCompleteObjectUndo(clip, name);
                if (selected.Objects)
                    AnimationUtility.SetObjectReferenceCurve(clip, selected.Value, objects);
                else
                    AnimationUtility.SetEditorCurve(clip, selected.Value, curve);
                EditorUtility.SetDirty(clip);
                AssetDatabase.SaveAssetIfDirty(clip);
                Undo.CollapseUndoOperations(group);
                return new SuccessResponse(
                    "Selected curve edited; unrelated curves and asset identity retained.",
                    new
                    {
                        binding_id = id,
                        clip_file_id = AnimationAssetAccess.Id(clip),
                        revision = AnimationAssetAccess.Revision(path, new UnityEngine.Object[] { clip }),
                        undo_group = group,
                    }
                );
            }
            catch
            {
                Undo.RevertAllDownToGroup(group);
                throw;
            }
            finally
            {
                Undo.IncrementCurrentGroup();
            }
        }

        private static AnimationClip Load(JObject request, string path)
        {
            if (request["clipFileId"] != null)
            {
                string id = request["clipFileId"].ToString();
                AnimationClip[] matches = AssetDatabase
                    .LoadAllAssetsAtPath(path)
                    .OfType<AnimationClip>()
                    .Where(clip => AnimationAssetAccess.Id(clip) == id)
                    .ToArray();
                if (matches.Length != 1)
                    throw new ArgumentException("clip_file_id is stale or ambiguous.");
                return matches[0];
            }
            if (AssetDatabase.LoadMainAssetAtPath(path) is AnimationClip main)
                return main;
            var clips = AssetDatabase.LoadAllAssetsAtPath(path).OfType<AnimationClip>().ToArray();
            if (clips.Length != 1)
                throw new ArgumentException("Choose one existing AnimationClip with clip_file_id when the source contains multiple clips.");
            return clips[0];
        }

        private static object Read(AnimationClip clip, Binding binding, JObject request, string revision)
        {
            if (binding.Objects)
            {
                var keys = AnimationUtility.GetObjectReferenceCurve(clip, binding.Value);
                if (keys.Length > 16384)
                    throw new ArgumentException("Curve exceeds 16384 keys.");
                return AnimationAssetAccess.Page(
                    keys.Length,
                    index => new JObject { ["time"] = keys[index].time, ["value"] = AnimationAssetAccess.Reference(keys[index].value) },
                    request,
                    revision,
                    "keys"
                );
            }
            AnimationCurve curve = AnimationUtility.GetEditorCurve(clip, binding.Value);
            if (curve.length > 16384)
                throw new ArgumentException("Curve exceeds 16384 keys.");
            return AnimationAssetAccess.Page(
                curve.length,
                index =>
                {
                    var key = curve[index];
                    return new JObject
                    {
                        ["time"] = key.time,
                        ["value"] = key.value,
                        ["in_tangent"] = Tangent(key.inTangent),
                        ["out_tangent"] = Tangent(key.outTangent),
                        ["in_weight"] = key.inWeight,
                        ["out_weight"] = key.outWeight,
                        ["weighted_mode"] = (int)key.weightedMode,
                        ["broken"] = AnimationUtility.GetKeyBroken(curve, index),
                        ["left_tangent_mode"] = (int)AnimationUtility.GetKeyLeftTangentMode(curve, index),
                        ["right_tangent_mode"] = (int)AnimationUtility.GetKeyRightTangentMode(curve, index),
                    };
                },
                request,
                revision,
                "keys"
            );
        }

        private static JToken Tangent(float value) =>
            float.IsPositiveInfinity(value) ? new JValue("Infinity")
            : float.IsNegativeInfinity(value) ? new JValue("-Infinity")
            : new JValue(value);

        private static ObjectReferenceKeyframe[] ObjectKeys(JArray input)
        {
            var keys = new List<ObjectReferenceKeyframe>();
            float previous = -1;
            foreach (JToken token in input)
            {
                if (!(token is JObject key))
                    throw new ArgumentException("Every key must be an object.");
                AnimationAssetAccess.Keys(key, "time", "value");
                float time = Time(key, previous);
                previous = time;
                UnityEngine.Object value = AnimationAssetAccess.ResolveReference(key["value"]);
                keys.Add(new ObjectReferenceKeyframe { time = time, value = value });
            }
            return keys.ToArray();
        }

        private static AnimationCurve FloatKeys(JArray input, AnimationCurve previousCurve)
        {
            var keys = new List<Keyframe>();
            float previous = -1;
            foreach (JToken token in input)
            {
                if (!(token is JObject key))
                    throw new ArgumentException("Every key must be an object.");
                AnimationAssetAccess.Keys(
                    key,
                    "time",
                    "value",
                    "inTangent",
                    "outTangent",
                    "inWeight",
                    "outWeight",
                    "weightedMode",
                    "broken",
                    "leftTangentMode",
                    "rightTangentMode"
                );
                float time = Time(key, previous);
                previous = time;
                var item = new Keyframe(
                    time,
                    AnimationAssetAccess.Number(key["value"]),
                    key["inTangent"] == null ? 0 : AnimationAssetAccess.Number(key["inTangent"], true),
                    key["outTangent"] == null ? 0 : AnimationAssetAccess.Number(key["outTangent"], true)
                );
                item.weightedMode = (WeightedMode)AnimationAssetAccess.Integer(key, "weightedMode", 0, 0, 3);
                item.inWeight = key["inWeight"] == null ? 1f / 3 : AnimationAssetAccess.Number(key["inWeight"]);
                item.outWeight = key["outWeight"] == null ? 1f / 3 : AnimationAssetAccess.Number(key["outWeight"]);
                if (item.inWeight < 0 || item.inWeight > 1 || item.outWeight < 0 || item.outWeight > 1)
                    throw new ArgumentException("Tangent weights must be in [0,1].");
                keys.Add(item);
            }
            var curve = new AnimationCurve(keys.ToArray()) { preWrapMode = previousCurve.preWrapMode, postWrapMode = previousCurve.postWrapMode };
            for (int i = 0; i < input.Count; i++)
            {
                var key = (JObject)input[i];
                bool broken = key["broken"] == null || AnimationAssetAccess.Boolean(key["broken"]);
                var left = (AnimationUtility.TangentMode)AnimationAssetAccess.Integer(key, "leftTangentMode", 0, 0, 4);
                var right = (AnimationUtility.TangentMode)AnimationAssetAccess.Integer(key, "rightTangentMode", 0, 0, 4);
                AnimationUtility.SetKeyBroken(curve, i, broken);
                AnimationUtility.SetKeyLeftTangentMode(curve, i, left);
                AnimationUtility.SetKeyRightTangentMode(curve, i, right);
            }
            return curve;
        }

        private static float Time(JObject key, float previous)
        {
            float time = AnimationAssetAccess.Number(key["time"]);
            if (time < 0 || time <= previous)
                throw new ArgumentException("Key times must be nonnegative and strictly increasing.");
            return time;
        }
    }
}
