#nullable disable
using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    internal static class GameObjectPlacement
    {
        private const int MaximumPreviews = 32;
        private const double LifetimeSeconds = 300;

        private sealed class Plan
        {
            internal GameObject[] Targets;
            internal Vector3[] Positions;
            internal string[] States;
            internal string Scope;
            internal bool World;
            internal double Created;
        }

        // Editor-main-thread requests only. Domain reload invalidates all outstanding tokens.
        private static readonly Dictionary<string, Plan> previews = new Dictionary<string, Plan>();

        internal static object Handle(JObject parameters)
        {
            try
            {
                return Execute(parameters);
            }
            catch (ArgumentException error)
            {
                return new ErrorResponse(error.Message);
            }
        }

        private static object Execute(JObject parameters)
        {
            bool preview = GameObjectTransformOptions.ReadBoolean(parameters, "preview", true);
            bool world = GameObjectTransformOptions.ReadWorld(parameters);
            string mode = ReadChoice(parameters, "placement_mode", "align", "align", "distribute");
            string axis = ReadChoice(parameters, "axis", "x", "x", "y", "z");
            int coordinate =
                axis == "x" ? 0
                : axis == "y" ? 1
                : 2;
            if (!(parameters["targets"] is JArray selectors) || selectors.Count < 2 || selectors.Count > 128)
                throw new ArgumentException("'targets' must contain 2–128 unique ordered scene-object selectors.");

            var targets = new GameObject[selectors.Count];
            for (int i = 0; i < selectors.Count; i++)
            {
                JToken selector = selectors[i];
                if (selector.Type != JTokenType.String && selector.Type != JTokenType.Integer)
                    throw new ArgumentException("Each placement target must be a name, path or instance ID.");
                var matches = ManageGameObjectCommon.FindObjectsInternal(selector, null, true, new JObject { ["searchInactive"] = true });
                if (matches.Count != 1)
                    throw new ArgumentException($"Placement target '{selector}' must resolve uniquely; found {matches.Count} matches.");
                targets[i] = matches[0];
                for (int j = 0; j < i; j++)
                {
                    if (
                        targets[j] == targets[i]
                        || targets[j].transform.IsChildOf(targets[i].transform)
                        || targets[i].transform.IsChildOf(targets[j].transform)
                    )
                        throw new ArgumentException("Placement targets must be unique and cannot contain ancestor/descendant pairs.");
                }
                if (!world && (targets[i].transform.parent != targets[0].transform.parent || targets[i].scene != targets[0].scene))
                    throw new ArgumentException("Local placement requires targets in the same scene with a common parent.");
            }

            string scope = new JObject
            {
                ["targets"] = selectors.DeepClone(),
                ["space"] = world ? "world" : "local",
                ["mode"] = mode,
                ["axis"] = axis,
            }.ToString(Formatting.None);
            double now = EditorApplication.timeSinceStartup;
            foreach (string expired in previews.Where(entry => now - entry.Value.Created >= LifetimeSeconds).Select(entry => entry.Key).ToArray())
                previews.Remove(expired);

            if (preview)
            {
                var positions = targets.Select(go => world ? go.transform.position : go.transform.localPosition).ToArray();
                float first = positions[0][coordinate];
                float last = positions[positions.Length - 1][coordinate];
                for (int i = 0; i < positions.Length; i++)
                {
                    positions[i][coordinate] = mode == "align" ? first : Mathf.Lerp(first, last, (float)i / (positions.Length - 1));
                    if (!Finite(positions[i]))
                        throw new ArgumentException("Placement would produce a non-finite position.");
                }
                var plan = new Plan
                {
                    Targets = targets,
                    Positions = positions,
                    States = targets.Select(Capture).ToArray(),
                    World = world,
                    Scope = scope,
                    Created = now,
                };
                if (previews.Count >= MaximumPreviews)
                    previews.Remove(previews.OrderBy(entry => entry.Value.Created).First().Key);
                string token = Guid.NewGuid().ToString("N");
                previews.Add(token, plan);
                return new SuccessResponse(
                    "Placement preview only; no scene objects changed.",
                    new
                    {
                        preview = true,
                        preview_token = token,
                        expires_in_seconds = LifetimeSeconds,
                        space = world ? "world" : "local",
                        placements = targets
                            .Select((go, index) => new { instanceID = go.GetInstanceIDCompat(), position = Vector(plan.Positions[index]) })
                            .ToArray(),
                    }
                );
            }

            JToken tokenValue = parameters["preview_token"];
            if (tokenValue?.Type != JTokenType.String || !previews.TryGetValue(tokenValue.Value<string>(), out Plan accepted))
                return new ErrorResponse("Missing, expired, consumed or reloaded placement preview; request a new preview.");
            if (accepted.Scope != scope)
                return new ErrorResponse("Placement scope changed; request a new preview with the intended target order and options.");
            for (int i = 0; i < targets.Length; i++)
            {
                if (targets[i] != accepted.Targets[i] || Capture(targets[i]) != accepted.States[i])
                    return new ErrorResponse("Placement target or parent state changed; request a new preview.");
            }
            previews.Remove(tokenValue.Value<string>());
            return Apply(accepted);
        }

        private static object Apply(Plan plan)
        {
            var outcomes = new JArray();
            var finalizationErrors = new JArray();
            bool failed = false;
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Apply GameObject Placement");
            try
            {
                for (int i = 0; i < plan.Targets.Length; i++)
                {
                    GameObject target = plan.Targets[i];
                    var outcome = new JObject
                    {
                        ["index"] = i,
                        ["position"] = Vector(plan.Positions[i]),
                        ["status"] = "not_applied",
                    };
                    outcomes.Add(outcome);
                    if (failed)
                        continue;
                    try
                    {
                        // A previous write can invoke user callbacks. Recheck each remaining target.
                        if (target == null || Capture(target) != plan.States[i])
                            throw new InvalidOperationException("Target changed during application.");
                        outcome["instanceID"] = target.GetInstanceIDCompat();
                        Undo.RecordObject(target.transform, "Place GameObject");
                        if (plan.World)
                            target.transform.position = plan.Positions[i];
                        else
                            target.transform.localPosition = plan.Positions[i];
                        outcome["status"] = "applied";
                        if (PrefabUtility.IsPartOfPrefabInstance(target.transform))
                            PrefabUtility.RecordPrefabInstancePropertyModifications(target.transform);
                        EditorUtility.SetDirty(target.transform);
                        if (!EditorApplication.isPlaying)
                            EditorSceneManager.MarkSceneDirty(target.scene);
                    }
                    catch (Exception error)
                    {
                        outcome["status"] = "failed";
                        outcome["error"] = error.Message;
                        failed = true;
                    }
                }
            }
            finally
            {
                try
                {
                    Undo.FlushUndoRecordObjects();
                }
                catch (Exception error)
                {
                    finalizationErrors.Add(error.Message);
                    failed = true;
                }
                try
                {
                    Undo.CollapseUndoOperations(group);
                }
                catch (Exception error)
                {
                    finalizationErrors.Add(error.Message);
                    failed = true;
                }
            }
            // Undo flush invokes user callbacks too. Observe the final retained positions afterward.
            for (int i = 0; i < plan.Targets.Length; i++)
            {
                if (outcomes[i].Value<string>("status") != "applied")
                    continue;
                GameObject target = plan.Targets[i];
                Vector3 actual =
                    target == null ? Vector3.zero
                    : plan.World ? target.transform.position
                    : target.transform.localPosition;
                if (target == null || !Finite(actual) || Vector3.Distance(actual, plan.Positions[i]) > 0.0001f)
                {
                    outcomes[i]["status"] = "changed_after_apply";
                    failed = true;
                }
            }
            var data = new
            {
                preview = false,
                outcomes,
                applied_count = outcomes.Count(row => row.Value<string>("status") == "applied"),
                transactionality = "best_effort",
                finalization_errors = finalizationErrors,
            };
            return failed
                ? new ErrorResponse("Placement was only partially applied; inspect per-target outcomes and use Undo if needed.", data)
                : new SuccessResponse("Applied the previewed placement.", data);
        }

        private static string ReadChoice(JObject parameters, string field, string fallback, params string[] choices)
        {
            JToken token = parameters[field];
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type == JTokenType.String && choices.Contains(token.Value<string>()))
                return token.Value<string>();
            throw new ArgumentException($"'{field}' must be one of: {string.Join(", ", choices)}.");
        }

        private static bool Finite(Vector3 value) =>
            !float.IsNaN(value.x)
            && !float.IsInfinity(value.x)
            && !float.IsNaN(value.y)
            && !float.IsInfinity(value.y)
            && !float.IsNaN(value.z)
            && !float.IsInfinity(value.z);

        private static JArray Vector(Vector3 value) => new JArray(value.x, value.y, value.z);

        private static string Capture(GameObject target)
        {
            var ancestry = new JArray();
            for (Transform current = target.transform; current != null; current = current.parent)
            {
                Quaternion rotation = current.localRotation;
                var matrix = new JArray();
                for (int i = 0; i < 16; i++)
                    matrix.Add(current.localToWorldMatrix[i]);
                ancestry.Add(
                    new JObject
                    {
                        ["id"] = current.gameObject.GetInstanceIDCompat(),
                        ["name"] = current.name,
                        ["position"] = Vector(current.localPosition),
                        ["rotation"] = new JArray(rotation.x, rotation.y, rotation.z, rotation.w),
                        ["scale"] = Vector(current.localScale),
                        ["sibling"] = current.GetSiblingIndex(),
                        ["matrix"] = matrix,
                    }
                );
            }
            return new JObject
            {
                ["scene"] = target.scene.handle,
                ["path"] = target.scene.path,
                ["loaded"] = target.scene.isLoaded,
                ["playing"] = EditorApplication.isPlaying,
                ["ancestry"] = ancestry,
            }.ToString(Formatting.None);
        }
    }
}
