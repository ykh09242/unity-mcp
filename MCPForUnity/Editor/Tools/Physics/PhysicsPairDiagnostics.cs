using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Physics3D = UnityEngine.Physics;

namespace MCPForUnity.Editor.Tools.Physics
{
    internal static class PhysicsPairDiagnostics
    {
        internal static object Diagnose(JObject parameters)
        {
            try
            {
                string dimension = ReadString(parameters, "dimension") ?? "3d";
                if (dimension != "2d" && dimension != "3d")
                    throw new ArgumentException("Pair dimension must be '2d' or '3d'.");
                int limit = ReadIndex(parameters, "contact_limit", 1, 128) ?? 16;
                bool? observe = ReadBoolean(parameters, "observe_contacts");
                bool is2D = dimension == "2d";
                var selectionError = Select(parameters, false, is2D, out Component first);
                if (selectionError != null)
                    return selectionError;
                selectionError = Select(parameters, true, is2D, out Component second);
                if (selectionError != null)
                    return selectionError;
                if (first == second)
                    return new ErrorResponse("same_collider", new { message = "Select two distinct collider components." });

                var blockers = new JArray();
                Add(blockers, !first.gameObject.activeInHierarchy, "first_inactive", "The first collider's GameObject is inactive in its hierarchy.");
                Add(blockers, !second.gameObject.activeInHierarchy, "second_inactive", "The second collider's GameObject is inactive in its hierarchy.");
                var a = is2D ? Describe2D((Collider2D)first) : Describe3D((Collider)first);
                var b = is2D ? Describe2D((Collider2D)second) : Describe3D((Collider)second);
                Add(blockers, !a.Value<bool>("enabled"), "first_collider_disabled", "The first collider is disabled.");
                Add(blockers, !b.Value<bool>("enabled"), "second_collider_disabled", "The second collider is disabled.");
                bool sameScene = is2D
                    ? first.gameObject.scene.GetPhysicsScene2D() == second.gameObject.scene.GetPhysicsScene2D()
                    : first.gameObject.scene.GetPhysicsScene() == second.gameObject.scene.GetPhysicsScene();
                bool layerIgnored = is2D
                    ? Physics2D.GetIgnoreLayerCollision(first.gameObject.layer, second.gameObject.layer)
                    : Physics3D.GetIgnoreLayerCollision(first.gameObject.layer, second.gameObject.layer);
                bool pairIgnored = is2D
                    ? Physics2D.GetIgnoreCollision((Collider2D)first, (Collider2D)second)
                    : Physics3D.GetIgnoreCollision((Collider)first, (Collider)second);
                bool trigger = a.Value<bool>("is_trigger") || b.Value<bool>("is_trigger");
                bool layerPolicyUnknown = LayerPolicyUnknown(a, second.gameObject.layer) || LayerPolicyUnknown(b, first.gameObject.layer);
                Add(blockers, !sameScene, "different_physics_scenes", "The colliders belong to different physics simulations.");
                Add(blockers, layerIgnored && !layerPolicyUnknown, "layer_collision_disabled", "The current layer collision matrix ignores this layer pair.");
                Add(blockers, pairIgnored, "pair_collision_ignored", "The native per-collider pair ignore flag is set.");
                if (is2D)
                    Explain2D((Collider2D)first, (Collider2D)second, trigger, blockers);
                else
                    Explain3D((Collider)first, (Collider)second, trigger, blockers);

                JObject observation = is2D
                    ? Observe2D((Collider2D)first, (Collider2D)second, sameScene, trigger, limit)
                    : PhysicsPairContactObserver.Read((Collider)first, (Collider)second, observe, sameScene, trigger);
                return new SuccessResponse(
                    "Read collider-pair configuration and bounded contact evidence without advancing simulation.",
                    new JObject
                    {
                        ["dimension"] = dimension,
                        ["first"] = a,
                        ["second"] = b,
                        ["configuration"] = new JObject
                        {
                            ["same_physics_scene"] = sameScene,
                            ["layer_ignored"] = layerIgnored,
                            ["pair_ignored"] = pairIgnored,
                            ["effective_layer_policy"] = layerPolicyUnknown ? "unavailable_with_layer_overrides" : "global_layer_matrix",
                            ["interaction"] = trigger ? "trigger" : "collision",
                        },
                        ["blocking_reasons"] = blockers,
                        ["configuration_allows_interaction"] =
                            blockers.Count != 0 ? new JValue(false)
                            : layerPolicyUnknown
                            || a["body"].Value<string>("motion_type") == "unavailable"
                            || b["body"].Value<string>("motion_type") == "unavailable"
                                ? JValue.CreateNull()
                            : new JValue(true),
                        ["contact_observation"] = observation,
                        ["observation_timing"] = new JObject
                        {
                            ["editor_time_seconds"] = EditorApplication.timeSinceStartup,
                            ["frame_count"] = Time.frameCount,
                            ["fixed_time_seconds"] = Time.fixedTimeAsDouble,
                            ["playing"] = EditorApplication.isPlaying,
                            ["simulation_advanced"] = false,
                            ["transforms_synchronized"] = false,
                            ["last_simulation_step_known"] = false,
                        },
                        ["limitations"] = new JArray(
                            "Configuration eligibility does not prove overlap, contacts, callback delivery or collision response.",
                            "No simulation, transform synchronization, movement, repair, collider property or physics setting changes are performed.",
                            "Contact caches and last-observed events may predate transform, layer or body changes. Sleeping bodies may omit stay events.",
                            "Per-collider ignore state is the native getter's current value; disabling/reactivating colliders can reset it.",
                            "Layer overrides, joints, custom contact modifications and special body models can add policy outside these static rules."
                        ),
                    }
                );
            }
            catch (ArgumentException error)
            {
                return new ErrorResponse(error.Message);
            }
        }

        private static ErrorResponse Select(JObject parameters, bool other, bool is2D, out Component selected)
        {
            selected = null;
            string targetField = other ? "other_target" : "target";
            JToken token = parameters[targetField];
            if (token == null || token.Type != JTokenType.String && token.Type != JTokenType.Integer || string.IsNullOrWhiteSpace(token.ToString()))
                throw new ArgumentException($"'{targetField}' requires a nonempty name, path or GameObject instance ID.");
            string method = ReadString(parameters, other ? "other_search_method" : "search_method");
            if (method != null && method != "by_id" && method != "by_name" && method != "by_path")
                throw new ArgumentException("Pair search methods must be by_id, by_name or by_path.");
            string term = token.ToString();
            GameObject go;
            if (method == "by_id" || method == null && int.TryParse(term, out _))
            {
                go = int.TryParse(term, out int id) ? GameObjectLookup.FindById(id) : null;
                if (go == null || EditorUtility.IsPersistent(go) || !go.scene.IsValid() || !go.scene.isLoaded)
                    throw new ArgumentException($"'{targetField}' must identify a loaded scene GameObject.");
            }
            else
            {
                bool path = method == "by_path" || method == null && term.Contains('/');
                var matches = LoadedObjects().Where(item => path ? GameObjectLookup.MatchesPath(item, term) : item.name == term).Take(2).ToArray();
                if (matches.Length != 1)
                    return new ErrorResponse(
                        "target_not_unique",
                        new
                        {
                            side = targetField,
                            matches = matches.Length,
                            message = "Use a unique path or instance ID across loaded scenes.",
                        }
                    );
                go = matches[0];
            }
            string type = ReadString(parameters, other ? "other_collider_type" : "collider_type");
            Component[] all = is2D ? go.GetComponents<Collider2D>().Cast<Component>().ToArray() : go.GetComponents<Collider>().Cast<Component>().ToArray();
            var candidates = all.Where(item => type == null || item.GetType().Name == type || item.GetType().FullName == type).ToArray();
            int? index = ReadIndex(parameters, other ? "otherComponentIndex" : "componentIndex", 0, int.MaxValue);
            if (candidates.Length == 0)
                return new ErrorResponse(
                    "collider_not_found",
                    new
                    {
                        side = targetField,
                        dimension = is2D ? "2d" : "3d",
                        collider_type = type,
                    }
                );
            if (!index.HasValue && candidates.Length != 1)
                return new ErrorResponse(
                    "collider_selection_required",
                    new
                    {
                        side = targetField,
                        candidates = candidates
                            .Select(
                                (item, i) =>
                                    new
                                    {
                                        index = i,
                                        collider_id = item.GetInstanceIDCompat(),
                                        type = item.GetType().Name,
                                    }
                            )
                            .ToArray(),
                    }
                );
            int chosen = index ?? 0;
            if (chosen >= candidates.Length)
                throw new ArgumentException($"Collider component index {chosen} is outside the {candidates.Length} matching colliders for {targetField}.");
            selected = candidates[chosen];
            return null;
        }

        private static IEnumerable<GameObject> LoadedObjects()
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null)
            {
                foreach (Transform transform in stage.prefabContentsRoot.GetComponentsInChildren<Transform>(true))
                    yield return transform.gameObject;
                yield break;
            }
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                if (!scene.isLoaded)
                    continue;
                foreach (GameObject root in scene.GetRootGameObjects())
                foreach (Transform transform in root.GetComponentsInChildren<Transform>(true))
                    yield return transform.gameObject;
            }
        }

        private static JObject Common(Component collider, bool enabled, bool trigger)
        {
            GameObject go = collider.gameObject;
            return new JObject
            {
                ["collider_id"] = collider.GetInstanceIDCompat(),
                ["game_object_id"] = go.GetInstanceIDCompat(),
                ["name"] = go.name,
                ["collider_type"] = collider.GetType().Name,
                ["enabled"] = enabled,
                ["active_self"] = go.activeSelf,
                ["active_in_hierarchy"] = go.activeInHierarchy,
                ["is_trigger"] = trigger,
                ["layer"] = go.layer,
                ["layer_name"] = LayerMask.LayerToName(go.layer),
                ["editor_static_flag"] = go.isStatic,
                ["layer_overrides"] = LayerOverrides(collider),
                ["scene"] = new JObject
                {
#if UNITY_6000_4_OR_NEWER
                    ["handle"] = go.scene.handle.GetRawData(),
#else
                    ["handle"] = (int)go.scene.handle,
#endif
                    ["name"] = go.scene.name,
                    ["path"] = go.scene.path,
                },
            };
        }

        private static JObject Describe3D(Collider collider)
        {
            JObject result = Common(collider, collider.enabled, collider.isTrigger);
            Rigidbody body = collider.attachedRigidbody;
            bool specialBody = collider.attachedArticulationBody != null || collider is CharacterController;
            result["body"] = new JObject
            {
                ["instance_id"] = body == null ? JValue.CreateNull() : new JValue(body.GetInstanceIDCompat()),
                ["motion_type"] =
                    specialBody ? "unavailable"
                    : body == null ? "static"
                    : body.isKinematic ? "kinematic"
                    : "dynamic",
                ["detect_collisions"] = specialBody ? JValue.CreateNull() : new JValue(body == null || body.detectCollisions),
                ["sleeping"] = body == null ? JValue.CreateNull() : new JValue(body.IsSleeping()),
                ["special_body_model"] =
                    collider.attachedArticulationBody != null ? "articulation"
                    : collider is CharacterController ? "character_controller"
                    : "none",
            };
            result["physics_scene"] = collider.gameObject.scene.GetPhysicsScene().ToString();
#if UNITY_2022_2_OR_NEWER
            result["provides_contacts"] = collider.providesContacts;
#endif
            return result;
        }

        private static JObject Describe2D(Collider2D collider)
        {
            JObject result = Common(collider, collider.enabled, collider.isTrigger);
            Rigidbody2D body = collider.attachedRigidbody;
            result["body"] = new JObject
            {
                ["instance_id"] = body == null ? JValue.CreateNull() : new JValue(body.GetInstanceIDCompat()),
                ["motion_type"] = body == null ? "static" : body.bodyType.ToString().ToLowerInvariant(),
                ["simulated"] = body == null || body.simulated,
                ["use_full_kinematic_contacts"] = body != null && body.useFullKinematicContacts,
                ["sleeping"] = body == null ? JValue.CreateNull() : new JValue(body.IsSleeping()),
            };
            result["physics_scene"] = collider.gameObject.scene.GetPhysicsScene2D().ToString();
            return result;
        }

        private static void Explain3D(Collider a, Collider b, bool trigger, JArray blockers)
        {
            if (a.attachedArticulationBody != null || b.attachedArticulationBody != null || a is CharacterController || b is CharacterController)
                return;
            Rigidbody first = a.attachedRigidbody;
            Rigidbody second = b.attachedRigidbody;
            Add(blockers, first != null && first == second, "same_rigidbody", "Colliders attached to the same Rigidbody do not collide with each other.");
            Add(
                blockers,
                first != null && !first.detectCollisions || second != null && !second.detectCollisions,
                "body_collision_detection_disabled",
                "A Rigidbody has detectCollisions disabled."
            );
            Add(blockers, first == null && second == null, "static_static_pair", "Two static colliders do not generate collision or trigger callbacks.");
            if (!trigger)
                Add(
                    blockers,
                    (first == null || first.isKinematic) && (second == null || second.isKinematic) && (first != null || second != null),
                    "no_dynamic_body",
                    "Collision messages/response need a dynamic body; kinematic triggers are a separate valid interaction."
                );
        }

        private static void Explain2D(Collider2D a, Collider2D b, bool trigger, JArray blockers)
        {
            Rigidbody2D first = a.attachedRigidbody;
            Rigidbody2D second = b.attachedRigidbody;
            RigidbodyType2D aType = first == null ? RigidbodyType2D.Static : first.bodyType;
            RigidbodyType2D bType = second == null ? RigidbodyType2D.Static : second.bodyType;
            Add(blockers, first != null && first == second, "same_rigidbody", "Colliders attached to the same Rigidbody2D do not collide with each other.");
            Add(
                blockers,
                first != null && !first.simulated || second != null && !second.simulated,
                "body_not_simulated",
                "A Rigidbody2D has simulated disabled."
            );
            Add(
                blockers,
                aType == RigidbodyType2D.Static && bType == RigidbodyType2D.Static,
                "static_static_pair",
                "Two static 2D bodies do not generate pair contacts."
            );
            bool kinematic = aType == RigidbodyType2D.Kinematic || bType == RigidbodyType2D.Kinematic;
            bool full =
                first != null && aType == RigidbodyType2D.Kinematic && first.useFullKinematicContacts
                || second != null && bType == RigidbodyType2D.Kinematic && second.useFullKinematicContacts;
            if (!trigger)
                Add(
                    blockers,
                    kinematic && aType != RigidbodyType2D.Dynamic && bType != RigidbodyType2D.Dynamic && !full,
                    "kinematic_contact_filter",
                    "Non-trigger kinematic/static or kinematic/kinematic pairs require useFullKinematicContacts for collision callbacks."
                );
        }

        private static JObject Observe2D(Collider2D a, Collider2D b, bool sameScene, bool trigger, int limit)
        {
            if (!sameScene)
                return Unavailable("different_physics_scenes");
            if (trigger)
                return Unavailable("Trigger pairs have no contact points; cached touching is reported separately.", a.IsTouching(b));
            var points = new ContactPoint2D[limit + 1];
            int count = Physics2D.GetContacts(a, b, new ContactFilter2D().NoFilter(), points);
            return new JObject
            {
                ["status"] = "available",
                ["basis"] = "last_physics_update_cache",
                ["current_state_proven"] = false,
                ["touching"] = a.IsTouching(b),
                ["contact_count"] = count > limit ? JValue.CreateNull() : new JValue(count),
                ["returned_count"] = Math.Min(count, limit),
                ["truncated"] = count > limit,
                ["points"] = new JArray(
                    points
                        .Take(Math.Min(count, limit))
                        .Select(point => new JObject
                        {
                            ["point"] = new JArray(point.point.x, point.point.y),
                            ["normal"] = new JArray(point.normal.x, point.normal.y),
                            ["separation"] = point.separation,
                        })
                ),
            };
        }

        private static bool LayerPolicyUnknown(JObject collider, int otherLayer)
        {
            JToken overrides = collider["layer_overrides"];
            return overrides.Value<string>("status") == "unavailable" || (overrides.Value<int?>("combined_mask").GetValueOrDefault() & (1 << otherLayer)) != 0;
        }

        private static JObject LayerOverrides(Component collider)
        {
#if UNITY_6000_0_OR_NEWER
            int include;
            int exclude;
            int priority;
            int bodyInclude;
            int bodyExclude;
            if (collider is Collider a)
            {
                include = a.includeLayers.value;
                exclude = a.excludeLayers.value;
                priority = a.layerOverridePriority;
                bodyInclude = a.attachedRigidbody == null ? 0 : a.attachedRigidbody.includeLayers.value;
                bodyExclude = a.attachedRigidbody == null ? 0 : a.attachedRigidbody.excludeLayers.value;
            }
            else
            {
                var a2D = (Collider2D)collider;
                include = a2D.includeLayers.value;
                exclude = a2D.excludeLayers.value;
                priority = a2D.layerOverridePriority;
                bodyInclude = a2D.attachedRigidbody == null ? 0 : a2D.attachedRigidbody.includeLayers.value;
                bodyExclude = a2D.attachedRigidbody == null ? 0 : a2D.attachedRigidbody.excludeLayers.value;
            }
            return new JObject
            {
                ["status"] = "available",
                ["collider_include_mask"] = include,
                ["collider_exclude_mask"] = exclude,
                ["body_include_mask"] = bodyInclude,
                ["body_exclude_mask"] = bodyExclude,
                ["priority"] = priority,
                ["combined_mask"] = include | exclude | bodyInclude | bodyExclude,
            };
#elif UNITY_2022_2_OR_NEWER
            return new JObject { ["status"] = "unavailable", ["reason"] = "Layer-override inspection is not verified on this editor version." };
#else
            return new JObject { ["status"] = "not_supported", ["reason"] = "This editor predates per-collider layer overrides." };
#endif
        }

        internal static JObject Unavailable(string reason, bool? touching = null) =>
            new JObject
            {
                ["status"] = "unavailable",
                ["reason"] = reason,
                ["current_state_proven"] = false,
                ["touching"] = touching.HasValue ? new JValue(touching.Value) : JValue.CreateNull(),
            };

        private static void Add(JArray reasons, bool condition, string code, string message)
        {
            if (condition)
                reasons.Add(new JObject { ["code"] = code, ["message"] = message });
        }

        private static string ReadString(JObject parameters, string field)
        {
            JToken token = parameters[field];
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (token.Type != JTokenType.String)
                throw new ArgumentException($"'{field}' must be a string.");
            return token.Value<string>();
        }

        private static int? ReadIndex(JObject parameters, string field, int minimum, int maximum)
        {
            JToken token = parameters[field];
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int value) || value < minimum || value > maximum)
                throw new ArgumentException($"'{field}' must be an integer from {minimum} to {maximum}.");
            return value;
        }

        private static bool? ReadBoolean(JObject parameters, string field)
        {
            JToken token = parameters[field];
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (token.Type == JTokenType.Boolean)
                return token.Value<bool>();
            if (token.Type == JTokenType.String && string.Equals(token.Value<string>(), "true", StringComparison.OrdinalIgnoreCase))
                return true;
            if (token.Type == JTokenType.String && string.Equals(token.Value<string>(), "false", StringComparison.OrdinalIgnoreCase))
                return false;
            throw new ArgumentException($"'{field}' must be true or false.");
        }
    }
}
