using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Animation
{
    internal static class AnimationObservation
    {
        internal static JObject Curve(AnimationClip clip, EditorCurveBinding binding, bool objects, string path, string revision = null)
        {
            try
            {
                var values = new JObject
                {
                    ["clip_instance_id"] = clip.GetInstanceIDCompat(),
                    ["path"] = binding.path,
                    ["property"] = binding.propertyName,
                    ["type"] = binding.type.FullName,
                };
                using var serialized = new SerializedObject(clip);
                string vectorStorage =
                    binding.type == typeof(Transform)
                        ? binding.propertyName.StartsWith("m_LocalPosition.", StringComparison.Ordinal)
                            ? "m_PositionCurves"
                            : binding.propertyName.StartsWith("m_LocalScale.", StringComparison.Ordinal)
                                ? "m_ScaleCurves"
                                : binding.propertyName.StartsWith("m_LocalRotation.", StringComparison.Ordinal)
                                    ? "m_RotationCurves"
                                    : null
                        : null;
                string[] stores =
                    objects ? new[] { "m_PPtrCurves" }
                    : vectorStorage != null ? new[] { vectorStorage, "m_FloatCurves", "m_EditorCurves" }
                    : new[] { "m_FloatCurves", "m_EditorCurves" };
                bool complete = true;
                int count = -1,
                    matches = 0,
                    scanned = 0;
                foreach (string store in stores)
                {
                    using var bindings = serialized.FindProperty(store);
                    if (bindings == null || !bindings.isArray)
                        continue;
                    if (bindings.arraySize > 2048 - scanned)
                    {
                        complete = false;
                        break;
                    }
                    scanned += bindings.arraySize;
                    for (int i = 0; i < bindings.arraySize; i++)
                    {
                        using var entry = bindings.GetArrayElementAtIndex(i);
                        using var storedPath = entry.FindPropertyRelative("path");
                        using var property = entry.FindPropertyRelative("attribute");
                        if (storedPath == null || (store != vectorStorage && property == null))
                        {
                            complete = false;
                            break;
                        }
                        if (storedPath.stringValue != binding.path || (store != vectorStorage && property.stringValue != binding.propertyName))
                            continue;
                        matches++;
                        using var curve = entry.FindPropertyRelative("curve");
                        using var keys = objects ? curve?.Copy() : curve?.FindPropertyRelative("m_Curve");
                        if (keys != null && keys.isArray)
                            count = keys.arraySize;
                    }
                    if (matches > 0 || !complete)
                        break;
                }
                values["binding_matches"] = matches;
                values["exists"] = JValue.CreateNull();
                values["key_count"] = count >= 0 ? new JValue(count) : JValue.CreateNull();
                // A missing scalar row can be a packed Transform curve or an unsupported layout,
                // not proof of successful deletion. Only native exact-binding readback can pass.
                complete &= matches == 1 && count >= 0 && count <= 64;
                if (complete)
                {
                    var retained = new JArray();
                    if (objects)
                    {
                        var keys = AnimationUtility.GetObjectReferenceCurve(clip, binding);
                        if (keys == null || keys.Length > 64)
                            complete = false;
                        else
                        {
                            values["key_count"] = keys.Length;
                            values["exists"] = true;
                            foreach (var key in keys)
                                retained.Add(new JObject { ["time"] = key.time, ["value"] = AnimationAssetAccess.Reference(key.value) });
                        }
                    }
                    else
                    {
                        var curve = AnimationUtility.GetEditorCurve(clip, binding);
                        if (curve == null || curve.length > 64)
                            complete = false;
                        else
                        {
                            values["key_count"] = curve.length;
                            values["exists"] = true;
                            for (int i = 0; i < curve.length; i++)
                                retained.Add(new JObject { ["time"] = curve[i].time, ["value"] = curve[i].value });
                        }
                    }
                    values["keys"] = retained;
                }
                var report = PostEditVerification.Summary("exact_animation_binding; max2048_bindings_64_keys", values, complete: complete);
                report["revision"] = revision ?? AssetDatabase.GetAssetDependencyHash(path).ToString();
                report["revision_kind"] = revision == null ? "asset_dependency_hash" : "animation_round_trip";
                return report;
            }
            catch
            {
                var report = PostEditVerification.Unavailable("exact_animation_binding", "observation_unavailable");
                report["revision"] = revision;
                return report;
            }
        }

        internal static JObject Group(params JObject[] reports)
        {
            var values = new JArray();
            var findings = new JArray();
            bool complete = true;
            foreach (JObject report in reports)
            {
                values.Add(report);
                complete &= (bool)report["complete"];
                foreach (JToken finding in (JArray)report["findings"])
                    if (findings.Count < PostEditVerification.MaxFindings)
                        findings.Add(finding.DeepClone());
            }
            return PostEditVerification.Summary("exact_changed_animation_bindings", values, findings, complete);
        }

        internal static JObject Object(UnityEngine.Object target, string path, params string[] fields)
        {
            try
            {
                JObject report =
                    target is Component || target is ScriptableObject ? PostEditVerification.Readback(target, fields) : ReadNativeAsset(target, fields);
                report["asset_dependency_hash"] = AssetDatabase.GetAssetDependencyHash(path).ToString();
                report["file_id"] = EditorUtility.IsPersistent(target) ? AnimationAssetAccess.Id(target) : null;
                report["target_instance_id"] = target.GetInstanceIDCompat();
                return report;
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_animation_object", "observation_unavailable");
            }
        }

        private sealed class NativeBudget
        {
            internal int Nodes,
                Characters;
            internal bool Complete = true;
            internal readonly JArray Findings = new JArray();
        }

        private static JObject ReadNativeAsset(UnityEngine.Object target, string[] paths)
        {
            if (
                !(
                    target is AnimationClip
                    || target is AnimatorController
                    || target is AnimatorState
                    || target is AnimatorStateMachine
                    || target is AnimatorTransitionBase
                    || target is BlendTree
                )
            )
                return PostEditVerification.Unavailable("native_animation_asset", "unsupported_asset_type");
            var budget = new NativeBudget();
            var records = new JArray();
            using var serialized = new SerializedObject(target);
            serialized.Update();
            for (int i = 0; i < Math.Min(64, paths.Length); i++)
            {
                using var property = serialized.FindProperty(paths[i]);
                JToken value = property == null ? null : ReadNativeValue(property, budget, 0);
                bool available = property != null && value != null;
                budget.Complete &= available;
                records.Add(
                    new JObject
                    {
                        ["property_path"] = paths[i],
                        ["value"] = value,
                        ["value_status"] = available ? "available" : "unsupported",
                    }
                );
            }
            return PostEditVerification.Summary(
                "native_animation_asset; max64_fields_512_nodes_8192_characters",
                records,
                budget.Findings,
                budget.Complete && paths.Length <= 64
            );
        }

        private static JToken ReadNativeValue(SerializedProperty property, NativeBudget budget, int depth)
        {
            if (++budget.Nodes > 512 || depth > 6)
            {
                budget.Complete = false;
                return null;
            }
            if (property.isArray && property.propertyType != SerializedPropertyType.String)
            {
                int count = property.arraySize;
                if (count > 64)
                {
                    budget.Complete = false;
                    return null;
                }
                var values = new JArray();
                for (int i = 0; i < count; i++)
                {
                    using var item = property.GetArrayElementAtIndex(i);
                    values.Add(ReadNativeValue(item, budget, depth + 1));
                }
                return values;
            }
            switch (property.propertyType)
            {
                case SerializedPropertyType.Integer:
                    return new JValue(property.longValue);
                case SerializedPropertyType.Boolean:
                    return new JValue(property.boolValue);
                case SerializedPropertyType.Enum:
                    return new JValue(property.intValue);
                case SerializedPropertyType.Float:
                    double number = property.doubleValue;
                    if (double.IsNaN(number) || double.IsInfinity(number))
                    {
                        budget.Complete = false;
                        return null;
                    }
                    return new JValue(number);
                case SerializedPropertyType.String:
                    string text = property.stringValue;
                    if (text.Length > 1024 || text.Length > 8192 - budget.Characters)
                    {
                        budget.Complete = false;
                        return null;
                    }
                    budget.Characters += text.Length;
                    return new JValue(text);
                case SerializedPropertyType.ObjectReference:
                    UnityEngine.Object value = property.objectReferenceValue;
#if UNITY_6000_4_OR_NEWER
                    bool missing = value == null && EntityId.ToULong(property.objectReferenceEntityIdValue) != 0;
#else
                    bool missing = value == null && property.objectReferenceInstanceIDValue != 0;
#endif
                    if (missing)
                    {
                        if (budget.Findings.Count < 32)
                            budget.Findings.Add(new JObject { ["code"] = "missing_reference", ["path"] = property.propertyPath });
                        else
                            budget.Complete = false;
                    }
                    return new JObject { ["instance_id"] = value != null ? value.GetInstanceIDCompat() : 0 };
                case SerializedPropertyType.Vector2:
                    Vector2 v2 = property.vector2Value;
                    return NativeVector(budget, v2.x, v2.y);
                case SerializedPropertyType.Vector3:
                    Vector3 v3 = property.vector3Value;
                    return NativeVector(budget, v3.x, v3.y, v3.z);
                case SerializedPropertyType.Vector4:
                    Vector4 v4 = property.vector4Value;
                    return NativeVector(budget, v4.x, v4.y, v4.z, v4.w);
                case SerializedPropertyType.Quaternion:
                    Quaternion rotation = property.quaternionValue;
                    return NativeVector(budget, rotation.x, rotation.y, rotation.z, rotation.w);
                case SerializedPropertyType.Generic:
                    var fields = new JObject();
                    using (var child = property.Copy())
                    {
                        int parentDepth = property.depth;
                        if (child.Next(true))
                            do
                            {
                                if (child.depth != parentDepth + 1)
                                    break;
                                fields[child.name] = ReadNativeValue(child, budget, depth + 1);
                                if (budget.Nodes >= 512)
                                {
                                    budget.Complete = false;
                                    break;
                                }
                            } while (child.Next(false));
                    }
                    return fields;
                default:
                    budget.Complete = false;
                    return null;
            }
        }

        private static JToken NativeVector(NativeBudget budget, params float[] values)
        {
            foreach (float value in values)
                if (float.IsNaN(value) || float.IsInfinity(value))
                {
                    budget.Complete = false;
                    return null;
                }
            return new JArray(values);
        }

        internal static JObject Events(AnimationClip clip, string path)
        {
            try
            {
                using var serialized = new SerializedObject(clip);
                using var events = serialized.FindProperty("m_Events");
                if (events == null || !events.isArray)
                    return PostEditVerification.Unavailable("exact_animation_events", "unsupported_event_storage");
                var values = new JObject { ["clip_instance_id"] = clip.GetInstanceIDCompat(), ["event_count"] = events.arraySize };
                bool complete = events.arraySize <= 16;
                if (complete)
                {
                    var retained = new JArray();
                    foreach (var item in AnimationUtility.GetAnimationEvents(clip))
                    {
                        if (retained.Count == 16)
                        {
                            complete = false;
                            break;
                        }
                        if (item.functionName.Length > 1024 || item.stringParameter.Length > 1024)
                        {
                            complete = false;
                            break;
                        }
                        retained.Add(
                            new JObject
                            {
                                ["time"] = item.time,
                                ["function"] = item.functionName,
                                ["string"] = item.stringParameter,
                                ["float"] = item.floatParameter,
                                ["int"] = item.intParameter,
                            }
                        );
                    }
                    values["events"] = retained;
                }
                var report = PostEditVerification.Summary("exact_animation_events; max16", values, complete: complete);
                report["asset_dependency_hash"] = AssetDatabase.GetAssetDependencyHash(path).ToString();
                return report;
            }
            catch
            {
                return PostEditVerification.Unavailable("exact_animation_events", "observation_unavailable");
            }
        }
    }
}
