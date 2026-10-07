using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Physics
{
    internal static class PhysicsSettingsOps
    {
        public static object Ping(JObject @params)
        {
            var gravity3d = UnityEngine.Physics.gravity;
            var gravity2d = Physics2D.gravity;
            var simMode = UnityPhysicsCompat.GetPhysicsSimulationMode().ToString();

            return new
            {
                success = true,
                message = "Physics tool ready.",
                data = new
                {
                    gravity3d = new[] { gravity3d.x, gravity3d.y, gravity3d.z },
                    gravity2d = new[] { gravity2d.x, gravity2d.y },
                    simulationMode = simMode,
                    defaultSolverIterations = UnityEngine.Physics.defaultSolverIterations,
                    defaultSolverVelocityIterations = UnityEngine.Physics.defaultSolverVelocityIterations,
                    bounceThreshold = UnityEngine.Physics.bounceThreshold,
                    sleepThreshold = UnityEngine.Physics.sleepThreshold,
                    defaultContactOffset = UnityEngine.Physics.defaultContactOffset,
                    queriesHitTriggers = UnityEngine.Physics.queriesHitTriggers,
                },
            };
        }

        public static object GetSettings(JObject @params)
        {
            var p = new ToolParams(@params);
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();

            if (dimension == "2d")
            {
                var g = Physics2D.gravity;
                return new
                {
                    success = true,
                    message = "Physics2D settings retrieved.",
                    data = new
                    {
                        dimension = "2d",
                        gravity = new[] { g.x, g.y },
                        velocityIterations = Physics2D.velocityIterations,
                        positionIterations = Physics2D.positionIterations,
                        queriesHitTriggers = Physics2D.queriesHitTriggers,
                        queriesStartInColliders = Physics2D.queriesStartInColliders,
                        callbacksOnDisable = Physics2D.callbacksOnDisable,
                        autoSyncTransforms = UnityPhysicsCompat.GetPhysics2DAutoSyncTransforms(),
                    },
                };
            }

            if (dimension != "3d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            var g3 = UnityEngine.Physics.gravity;
            var simMode = UnityPhysicsCompat.GetPhysicsSimulationMode().ToString();

            return new
            {
                success = true,
                message = "Physics3D settings retrieved.",
                data = new
                {
                    dimension = "3d",
                    gravity = new[] { g3.x, g3.y, g3.z },
                    defaultContactOffset = UnityEngine.Physics.defaultContactOffset,
                    sleepThreshold = UnityEngine.Physics.sleepThreshold,
                    defaultSolverIterations = UnityEngine.Physics.defaultSolverIterations,
                    defaultSolverVelocityIterations = UnityEngine.Physics.defaultSolverVelocityIterations,
                    bounceThreshold = UnityEngine.Physics.bounceThreshold,
                    defaultMaxAngularSpeed = UnityEngine.Physics.defaultMaxAngularSpeed,
                    queriesHitTriggers = UnityEngine.Physics.queriesHitTriggers,
                    queriesHitBackfaces = UnityEngine.Physics.queriesHitBackfaces,
                    simulationMode = simMode,
                    autoSyncTransforms = UnityPhysicsCompat.GetPhysicsAutoSyncTransforms(),
                },
            };
        }

        public static object SetSettings(JObject @params)
        {
            var p = new ToolParams(@params);
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();
            var settings = p.GetRaw("settings") as JObject;

            if (settings == null || settings.Count == 0)
                return new ErrorResponse("'settings' parameter is required and must be a non-empty object.");

            if (dimension != "3d" && dimension != "2d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            if (dimension == "2d")
                return SetSettings2D(settings);

            return SetSettings3D(settings);
        }

        private static readonly HashSet<string> Valid3DKeys = new HashSet<string>
        {
            "gravity",
            "defaultcontactoffset",
            "sleepthreshold",
            "defaultsolveriterations",
            "defaultsolvervelocityiterations",
            "bouncethreshold",
            "defaultmaxangularspeed",
            "querieshittriggers",
            "querieshitbackfaces",
            "simulationmode",
            "autosynctransforms",
        };

        private static object SetSettings3D(JObject settings)
        {
            // Validate all keys before applying any changes
            var unknown = new List<string>();
            foreach (var prop in settings.Properties())
            {
                if (!Valid3DKeys.Contains(prop.Name.ToLowerInvariant()))
                    unknown.Add(prop.Name);
            }
            if (unknown.Count > 0)
                return new ErrorResponse($"Unknown 3D physics setting(s): {string.Join(", ", unknown)}.");

            var changed = new List<string>();
            var changes = new List<Action>();

            foreach (var prop in settings.Properties())
            {
                string key = prop.Name.ToLowerInvariant();
                switch (key)
                {
                    case "gravity":
                    {
                        var arr = prop.Value as JArray;
                        if (arr == null || arr.Count < 3)
                            return new ErrorResponse("3D gravity requires [x, y, z] array.");
                        var newGravity = new Vector3(arr[0].ReadScalar<float>(), arr[1].ReadScalar<float>(), arr[2].ReadScalar<float>());
                        changes.Add(() =>
                        {
                            UnityEngine.Physics.gravity = newGravity;
                            changed.Add("gravity");
                        });
                        break;
                    }
                    case "defaultcontactoffset":
                        var newDefaultContactOffset = prop.Value.ReadScalar<float>();
                        changes.Add(() =>
                        {
                            UnityEngine.Physics.defaultContactOffset = newDefaultContactOffset;
                            changed.Add("defaultContactOffset");
                        });
                        break;
                    case "sleepthreshold":
                        var newSleepThreshold = prop.Value.ReadScalar<float>();
                        changes.Add(() =>
                        {
                            UnityEngine.Physics.sleepThreshold = newSleepThreshold;
                            changed.Add("sleepThreshold");
                        });
                        break;
                    case "defaultsolveriterations":
                        var newDefaultSolverIterations = prop.Value.ReadScalar<int>();
                        changes.Add(() =>
                        {
                            UnityEngine.Physics.defaultSolverIterations = newDefaultSolverIterations;
                            changed.Add("defaultSolverIterations");
                        });
                        break;
                    case "defaultsolvervelocityiterations":
                        var newDefaultSolverVelocityIterations = prop.Value.ReadScalar<int>();
                        changes.Add(() =>
                        {
                            UnityEngine.Physics.defaultSolverVelocityIterations = newDefaultSolverVelocityIterations;
                            changed.Add("defaultSolverVelocityIterations");
                        });
                        break;
                    case "bouncethreshold":
                        var newBounceThreshold = prop.Value.ReadScalar<float>();
                        changes.Add(() =>
                        {
                            UnityEngine.Physics.bounceThreshold = newBounceThreshold;
                            changed.Add("bounceThreshold");
                        });
                        break;
                    case "defaultmaxangularspeed":
                        var newDefaultMaxAngularSpeed = prop.Value.ReadScalar<float>();
                        changes.Add(() =>
                        {
                            UnityEngine.Physics.defaultMaxAngularSpeed = newDefaultMaxAngularSpeed;
                            changed.Add("defaultMaxAngularSpeed");
                        });
                        break;
                    case "querieshittriggers":
                        var newQueriesHitTriggers = prop.Value.ReadScalar<bool>();
                        changes.Add(() =>
                        {
                            UnityEngine.Physics.queriesHitTriggers = newQueriesHitTriggers;
                            changed.Add("queriesHitTriggers");
                        });
                        break;
                    case "querieshitbackfaces":
                        var newQueriesHitBackfaces = prop.Value.ReadScalar<bool>();
                        changes.Add(() =>
                        {
                            UnityEngine.Physics.queriesHitBackfaces = newQueriesHitBackfaces;
                            changed.Add("queriesHitBackfaces");
                        });
                        break;
                    case "simulationmode":
                    {
                        string modeStr = prop.Value.ToString();
                        if (
                            !System.Enum.TryParse<UnityPhysicsCompat.SimulationMode>(modeStr, true, out var mode)
                            || mode == UnityPhysicsCompat.SimulationMode.Unknown
                        )
                        {
                            return new ErrorResponse($"Invalid simulationMode: '{modeStr}'. Valid: FixedUpdate, Update, Script.");
                        }
                        if (!UnityPhysicsCompat.CanSetPhysicsSimulationMode(mode))
                            return new ErrorResponse($"simulationMode '{modeStr}' is not supported on this Unity version.");
                        changes.Add(() =>
                        {
                            if (!UnityPhysicsCompat.TrySetPhysicsSimulationMode(mode))
                                throw new InvalidOperationException($"Could not apply simulationMode '{modeStr}'.");
                            changed.Add("simulationMode");
                        });
                        break;
                    }
                    case "autosynctransforms":
                    {
                        bool autoSync = prop.Value.ReadScalar<bool>();
                        changes.Add(() =>
                        {
                            if (UnityPhysicsCompat.TrySetPhysicsAutoSyncTransforms(autoSync))
                                changed.Add("autoSyncTransforms");
                        });
                        break;
                    }
                }
            }

            foreach (var change in changes)
                change();
            MarkDynamicsManagerDirty();

            return new
            {
                success = true,
                message = $"Updated {changed.Count} physics 3D setting(s).",
                data = new { changed },
            };
        }

        private static readonly HashSet<string> Valid2DKeys = new HashSet<string>
        {
            "gravity",
            "velocityiterations",
            "positioniterations",
            "querieshittriggers",
            "queriesstartincolliders",
            "callbacksondisable",
            "autosynctransforms",
        };

        private static object SetSettings2D(JObject settings)
        {
            // Validate all keys before applying any changes
            var unknown = new List<string>();
            foreach (var prop in settings.Properties())
            {
                if (!Valid2DKeys.Contains(prop.Name.ToLowerInvariant()))
                    unknown.Add(prop.Name);
            }
            if (unknown.Count > 0)
                return new ErrorResponse($"Unknown 2D physics setting(s): {string.Join(", ", unknown)}.");

            var changed = new List<string>();
            var changes = new List<Action>();

            foreach (var prop in settings.Properties())
            {
                string key = prop.Name.ToLowerInvariant();
                switch (key)
                {
                    case "gravity":
                    {
                        var arr = prop.Value as JArray;
                        if (arr == null || arr.Count < 2)
                            return new ErrorResponse("2D gravity requires [x, y] array.");
                        var newGravity = new Vector2(arr[0].ReadScalar<float>(), arr[1].ReadScalar<float>());
                        changes.Add(() =>
                        {
                            Physics2D.gravity = newGravity;
                            changed.Add("gravity");
                        });
                        break;
                    }
                    case "velocityiterations":
                        var newVelocityIterations = prop.Value.ReadScalar<int>();
                        changes.Add(() =>
                        {
                            Physics2D.velocityIterations = newVelocityIterations;
                            changed.Add("velocityIterations");
                        });
                        break;
                    case "positioniterations":
                        var newPositionIterations = prop.Value.ReadScalar<int>();
                        changes.Add(() =>
                        {
                            Physics2D.positionIterations = newPositionIterations;
                            changed.Add("positionIterations");
                        });
                        break;
                    case "querieshittriggers":
                        var newQueriesHitTriggers = prop.Value.ReadScalar<bool>();
                        changes.Add(() =>
                        {
                            Physics2D.queriesHitTriggers = newQueriesHitTriggers;
                            changed.Add("queriesHitTriggers");
                        });
                        break;
                    case "queriesstartincolliders":
                        var newQueriesStartInColliders = prop.Value.ReadScalar<bool>();
                        changes.Add(() =>
                        {
                            Physics2D.queriesStartInColliders = newQueriesStartInColliders;
                            changed.Add("queriesStartInColliders");
                        });
                        break;
                    case "callbacksondisable":
                        var newCallbacksOnDisable = prop.Value.ReadScalar<bool>();
                        changes.Add(() =>
                        {
                            Physics2D.callbacksOnDisable = newCallbacksOnDisable;
                            changed.Add("callbacksOnDisable");
                        });
                        break;
                    case "autosynctransforms":
                    {
                        bool autoSync = prop.Value.ReadScalar<bool>();
                        changes.Add(() =>
                        {
                            if (UnityPhysicsCompat.TrySetPhysics2DAutoSyncTransforms(autoSync))
                                changed.Add("autoSyncTransforms");
                        });
                        break;
                    }
                }
            }

            foreach (var change in changes)
                change();
            MarkPhysics2DSettingsDirty();

            return new
            {
                success = true,
                message = $"Updated {changed.Count} physics 2D setting(s).",
                data = new { changed },
            };
        }

        private static void MarkDynamicsManagerDirty()
        {
            var assets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/DynamicsManager.asset");
            if (assets != null && assets.Length > 0)
                EditorUtility.SetDirty(assets[0]);
        }

        private static void MarkPhysics2DSettingsDirty()
        {
            var assets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/Physics2DSettings.asset");
            if (assets != null && assets.Length > 0)
                EditorUtility.SetDirty(assets[0]);
        }
    }
}
