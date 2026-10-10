using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Physics
{
    internal static class PhysicsSimulationOps
    {
        public static object SimulateStep(JObject @params)
        {
            var p = new ToolParams(@params);
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();
            int steps = Mathf.Clamp(p.GetInt("steps") ?? 1, 1, 100);
            var stepSizeToken = p.GetRaw("step_size");
            float? requestedStepSize = p.GetFloat("step_size");
            if (stepSizeToken != null && stepSizeToken.Type != JTokenType.Null && !requestedStepSize.HasValue)
                return new ErrorResponse("'step_size' must be a positive finite float.");
            float stepSize = requestedStepSize ?? Time.fixedDeltaTime;
            string targetStr = p.Get("target");
            string searchMethod = p.Get("search_method");

            if (dimension != "3d" && dimension != "2d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            if (stepSize <= 0f || float.IsNaN(stepSize) || float.IsInfinity(stepSize))
                return new ErrorResponse("'step_size' must be a positive finite float.");

            int stepsExecuted = 0;
            if (dimension == "2d")
            {
                Physics2D.SyncTransforms();
                for (int i = 0; i < steps; i++)
                {
                    if (!Physics2D.Simulate(stepSize))
                        return new ErrorResponse(
                            "Physics2D simulation did not run.",
                            new
                            {
                                steps_executed = stepsExecuted,
                                step_size = stepSize,
                                dimension,
                            }
                        );
                    stepsExecuted++;
                }
            }
            else
            {
                var prevMode = UnityPhysicsCompat.GetPhysicsSimulationMode();
                if (prevMode == UnityPhysicsCompat.SimulationMode.Unknown)
                    return new ErrorResponse(
                        "Cannot read the current physics simulation mode; no steps were run.",
                        new
                        {
                            steps_executed = 0,
                            step_size = stepSize,
                            dimension,
                        }
                    );

                bool changedMode = prevMode != UnityPhysicsCompat.SimulationMode.Script;
                if (changedMode && !UnityPhysicsCompat.TrySetPhysicsSimulationMode(UnityPhysicsCompat.SimulationMode.Script))
                    return new ErrorResponse(
                        "Could not enable Script simulation mode; no steps were run.",
                        new
                        {
                            steps_executed = 0,
                            step_size = stepSize,
                            dimension,
                        }
                    );

                Exception simulationError = null;
                bool modeRestored = true;
                try
                {
                    UnityEngine.Physics.SyncTransforms();
                    for (int i = 0; i < steps; i++)
                    {
                        UnityEngine.Physics.Simulate(stepSize);
                        stepsExecuted++;
                    }
                }
                catch (Exception ex)
                {
                    simulationError = ex;
                }
                finally
                {
                    if (changedMode)
                        modeRestored = UnityPhysicsCompat.TrySetPhysicsSimulationMode(prevMode);
                }

                if (!modeRestored)
                    return new ErrorResponse(
                        "Could not restore the original physics simulation mode.",
                        new
                        {
                            steps_executed = stepsExecuted,
                            step_size = stepSize,
                            dimension,
                            previous_mode = prevMode.ToString(),
                            mode_restored = false,
                            simulation_error = simulationError?.Message,
                        }
                    );

                if (simulationError != null)
                    return new ErrorResponse(
                        $"Physics simulation failed: {simulationError.Message}",
                        new
                        {
                            steps_executed = stepsExecuted,
                            step_size = stepSize,
                            dimension,
                            mode_restored = true,
                        }
                    );
            }

            // Collect rigidbody states after simulation
            List<object> rigidbodies;
            if (!string.IsNullOrEmpty(targetStr))
            {
                rigidbodies = CollectTargetRigidbody(targetStr, searchMethod, dimension);
            }
            else
            {
                rigidbodies = CollectActiveRigidbodies(dimension);
            }

            return new
            {
                success = true,
                message = $"Executed {steps} physics step(s) ({dimension.ToUpper()}, step_size={stepSize:F4}s).",
                data = new
                {
                    steps_executed = steps,
                    step_size = stepSize,
                    dimension,
                    rigidbodies,
                },
            };
        }

        private static List<object> CollectTargetRigidbody(string targetStr, string searchMethod, string dimension)
        {
            var results = new List<object>();
            GameObject go;
            if (!string.IsNullOrEmpty(searchMethod))
                go = GameObjectLookup.FindByTarget(JToken.FromObject(targetStr), searchMethod);
            else if (int.TryParse(targetStr, out int instanceId) && GameObjectLookup.FindById(instanceId) is GameObject byId)
                go = byId.activeInHierarchy ? byId : null;
            else
                go = GameObjectLookup.FindByTarget(JToken.FromObject(targetStr), "by_name");
            if (go == null)
                return results;

            if (dimension == "2d")
            {
                var rb2d = go.GetComponent<Rigidbody2D>();
                if (rb2d != null)
                {
                    results.Add(
                        new
                        {
                            name = go.name,
                            instanceID = go.GetInstanceIDCompat(),
                            position = ToArray(rb2d.position),
#if UNITY_6000_0_OR_NEWER
                            velocity = ToArray(rb2d.linearVelocity),
#else
                            velocity = ToArray(rb2d.velocity),
#endif
                            angularVelocity = rb2d.angularVelocity,
                        }
                    );
                }
            }
            else
            {
                var rb = go.GetComponent<Rigidbody>();
                if (rb != null)
                {
                    results.Add(
                        new
                        {
                            name = go.name,
                            instanceID = go.GetInstanceIDCompat(),
                            position = ToArray(rb.position),
#if UNITY_6000_0_OR_NEWER
                            velocity = ToArray(rb.linearVelocity),
#else
                            velocity = ToArray(rb.velocity),
#endif
                            angularVelocity = ToArray(rb.angularVelocity),
                        }
                    );
                }
            }

            return results;
        }

        private static List<object> CollectActiveRigidbodies(string dimension)
        {
            var results = new List<object>();
            const int maxResults = 50;

            if (dimension == "2d")
            {
                var allRb2d = UnityFindObjectsCompat.FindAll<Rigidbody2D>();
                foreach (var rb2d in allRb2d)
                {
                    if (results.Count >= maxResults)
                        break;
                    if (rb2d.bodyType == RigidbodyType2D.Static)
                        continue;
                    if (rb2d.IsSleeping())
                        continue;

                    var go = rb2d.gameObject;
                    results.Add(
                        new
                        {
                            name = go.name,
                            instanceID = go.GetInstanceIDCompat(),
                            position = ToArray(rb2d.position),
#if UNITY_6000_0_OR_NEWER
                            velocity = ToArray(rb2d.linearVelocity),
#else
                            velocity = ToArray(rb2d.velocity),
#endif
                            angularVelocity = rb2d.angularVelocity,
                        }
                    );
                }
            }
            else
            {
                var allRb = UnityFindObjectsCompat.FindAll<Rigidbody>();
                foreach (var rb in allRb)
                {
                    if (results.Count >= maxResults)
                        break;
                    if (rb.isKinematic)
                        continue;
                    if (rb.IsSleeping())
                        continue;

                    var go = rb.gameObject;
                    results.Add(
                        new
                        {
                            name = go.name,
                            instanceID = go.GetInstanceIDCompat(),
                            position = ToArray(rb.position),
#if UNITY_6000_0_OR_NEWER
                            velocity = ToArray(rb.linearVelocity),
#else
                            velocity = ToArray(rb.velocity),
#endif
                            angularVelocity = ToArray(rb.angularVelocity),
                        }
                    );
                }
            }

            return results;
        }

        private static float[] ToArray(Vector2 value) => new[] { value.x, value.y };

        private static float[] ToArray(Vector3 value) => new[] { value.x, value.y, value.z };
    }
}
