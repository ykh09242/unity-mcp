using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Physics
{
    internal static class PhysicsForceOps
    {
        public static object ApplyForce(JObject @params)
        {
            var p = new ToolParams(@params);

            var targetResult = p.GetRequired("target");
            var errorObj = targetResult.GetOrError(out string targetStr);
            if (errorObj != null)
                return errorObj;

            string searchMethod = p.Get("search_method");

            GameObject go = FindTarget(@params["target"], searchMethod);
            if (go == null)
                return new ErrorResponse($"Target GameObject '{targetStr}' not found.");

            // Detect dimension
            string dimensionParam = p.Get("dimension")?.ToLowerInvariant();
            var rb = go.GetComponent<Rigidbody>();
            var rb2d = go.GetComponent<Rigidbody2D>();
            bool has3DRb = rb != null;
            bool has2DRb = rb2d != null;
            bool is2D;

            if (dimensionParam == "2d")
                is2D = true;
            else if (dimensionParam == "3d")
                is2D = false;
            else if (!string.IsNullOrEmpty(dimensionParam))
                return new ErrorResponse($"Invalid dimension: '{dimensionParam}'. Use '3d' or '2d'.");
            else
                is2D = has2DRb && !has3DRb;

            // Validate rigidbody exists
            if (is2D && !has2DRb)
                return new ErrorResponse($"Target '{go.name}' has no Rigidbody2D. Add one before applying force.");
            if (!is2D && !has3DRb)
                return new ErrorResponse($"Target '{go.name}' has no Rigidbody. Add one before applying force.");

            // Validate not kinematic
            if (is2D)
            {
                if (rb2d.bodyType == RigidbodyType2D.Kinematic)
                    return new ErrorResponse($"Cannot apply force to kinematic Rigidbody on '{go.name}'.");
            }
            else
            {
                if (rb.isKinematic)
                    return new ErrorResponse($"Cannot apply force to kinematic Rigidbody on '{go.name}'.");
            }

            string forceType = (p.Get("force_type") ?? "normal").ToLowerInvariant();

            if (forceType == "explosion")
                return ApplyExplosionForce(p, go, is2D, rb);

            if (forceType == "normal")
                return ApplyNormalForce(p, go, is2D, rb, rb2d);

            return new ErrorResponse($"Unknown force_type: '{forceType}'. Valid types: normal, explosion.");
        }

        private static object ApplyNormalForce(ToolParams p, GameObject go, bool is2D, Rigidbody rb, Rigidbody2D rb2d)
        {
            var forceToken = p.GetRaw("force");
            if (forceToken?.Type == JTokenType.Null)
                forceToken = null;
            var torqueToken = p.GetRaw("torque");

            if (forceToken == null && torqueToken == null)
                return new ErrorResponse("Either 'force' or 'torque' (or both) must be provided.");

            string modeStr = p.Get("force_mode");
            var positionToken = p.GetRaw("position");
            if (positionToken?.Type == JTokenType.Null)
                positionToken = null;

            // Parse the whole request before any AddForce/AddTorque call. A bad
            // torque must not leave a force queued on a rejected request.
            int dimensions = is2D ? 2 : 3;
            float[] force = null;
            float[] position = null;
            float[] torque = null;
            if (forceToken != null && !TryReadVector(forceToken, dimensions, out force))
                return new ErrorResponse($"'force' array must contain at least {dimensions} finite floats for {(is2D ? "2D" : "3D")}.");
            if (forceToken != null && positionToken != null && !TryReadVector(positionToken, dimensions, out position))
                return new ErrorResponse($"'position' array must contain at least {dimensions} finite floats for {(is2D ? "2D" : "3D")}.");
            if (torqueToken != null)
            {
                if (is2D)
                {
                    // MCP/CLI use [z]; retain the original raw scalar form too.
                    var torqueArray = torqueToken as JArray;
                    if (torqueArray != null && torqueArray.Count != 1)
                        return new ErrorResponse("2D 'torque' requires [z] or a scalar finite float.");
                    if (!TryReadFloat(torqueArray != null ? torqueArray[0] : torqueToken, out float value))
                        return new ErrorResponse("2D 'torque' requires [z] or a scalar finite float.");
                    torque = new[] { value };
                }
                else if (!TryReadVector(torqueToken, 3, out torque))
                    return new ErrorResponse("'torque' array must contain at least 3 finite floats for 3D.");
            }

            var applied = new List<string>();
            var responseData = new Dictionary<string, object>
            {
                ["target"] = go.name,
                ["dimension"] = is2D ? "2d" : "3d",
                ["force_type"] = "normal",
            };

            if (is2D)
            {
                ForceMode2D mode2d = ForceMode2D.Force;
                if (!string.IsNullOrEmpty(modeStr))
                {
                    if (!Enum.TryParse<ForceMode2D>(modeStr, true, out mode2d))
                        return new ErrorResponse($"Invalid ForceMode2D: '{modeStr}'. Valid values: Force, Impulse.");

                    if (mode2d != ForceMode2D.Force && mode2d != ForceMode2D.Impulse)
                        return new ErrorResponse($"ForceMode2D only supports Force and Impulse, not '{modeStr}'.");
                }

                responseData["force_mode"] = mode2d.ToString();

                if (force != null)
                {
                    var forceVec = new Vector2(force[0], force[1]);

                    if (position != null)
                    {
                        var posVec = new Vector2(position[0], position[1]);
                        rb2d.AddForceAtPosition(forceVec, posVec, mode2d);
                    }
                    else
                    {
                        rb2d.AddForce(forceVec, mode2d);
                    }

                    responseData["force"] = new[] { forceVec.x, forceVec.y };
                    applied.Add("force");
                }

                if (torque != null)
                {
                    float torqueFloat = torque[0];
                    rb2d.AddTorque(torqueFloat, mode2d);
                    responseData["torque"] = torqueFloat;
                    applied.Add("torque");
                }
            }
            else
            {
                ForceMode mode = ForceMode.Force;
                if (!string.IsNullOrEmpty(modeStr))
                {
                    if (!Enum.TryParse<ForceMode>(modeStr, true, out mode) || !Enum.IsDefined(typeof(ForceMode), mode))
                        return new ErrorResponse($"Invalid ForceMode: '{modeStr}'. Valid values: Force, Impulse, Acceleration, VelocityChange.");
                }

                responseData["force_mode"] = mode.ToString();

                if (force != null)
                {
                    var forceVec = new Vector3(force[0], force[1], force[2]);

                    if (position != null)
                    {
                        var posVec = new Vector3(position[0], position[1], position[2]);
                        rb.AddForceAtPosition(forceVec, posVec, mode);
                    }
                    else
                    {
                        rb.AddForce(forceVec, mode);
                    }

                    responseData["force"] = new[] { forceVec.x, forceVec.y, forceVec.z };
                    applied.Add("force");
                }

                if (torque != null)
                {
                    var torqueVec = new Vector3(torque[0], torque[1], torque[2]);
                    rb.AddTorque(torqueVec, mode);
                    responseData["torque"] = new[] { torqueVec.x, torqueVec.y, torqueVec.z };
                    applied.Add("torque");
                }
            }

            string appliedStr = string.Join(" and ", applied);
            return new
            {
                success = true,
                message = $"Applied {appliedStr} to '{go.name}'.",
                data = responseData,
            };
        }

        private static bool TryReadVector(JToken token, int dimensions, out float[] values)
        {
            values = null;
            if (!(token is JArray array) || array.Count < dimensions)
                return false;
            var parsed = new float[dimensions];
            for (int i = 0; i < dimensions; i++)
                if (!TryReadFloat(array[i], out parsed[i]))
                    return false;
            values = parsed;
            return true;
        }

        private static bool TryReadFloat(JToken token, out float value)
        {
            value = 0f;
            if (!(token is JValue) || token.Type == JTokenType.Null)
                return false;
            try
            {
                value = token.ReadScalar<float>();
                return !float.IsNaN(value) && !float.IsInfinity(value);
            }
            catch (Exception ex) when (ex is FormatException || ex is InvalidCastException || ex is ArgumentException || ex is OverflowException)
            {
                return false;
            }
        }

        private static object ApplyExplosionForce(ToolParams p, GameObject go, bool is2D, Rigidbody rb)
        {
            if (is2D)
                return new ErrorResponse("Explosion force is only available for 3D physics.");

            float? explosionForce = p.GetFloat("explosion_force");
            if (!explosionForce.HasValue || float.IsNaN(explosionForce.Value) || float.IsInfinity(explosionForce.Value))
                return new ErrorResponse("'explosion_force' must be a finite float for explosion force type.");

            if (!TryReadVector(p.GetRaw("explosion_position"), 3, out float[] explosionPosition))
                return new ErrorResponse("'explosion_position' must contain at least 3 finite floats for explosion force type.");

            float? explosionRadius = p.GetFloat("explosion_radius");
            if (!explosionRadius.HasValue || float.IsNaN(explosionRadius.Value) || float.IsInfinity(explosionRadius.Value))
                return new ErrorResponse("'explosion_radius' must be a finite float for explosion force type.");

            float? requestedUpwardsModifier = p.GetFloat("upwards_modifier");
            var upwardsToken = p.GetRaw("upwards_modifier");
            if (upwardsToken != null && upwardsToken.Type != JTokenType.Null && !requestedUpwardsModifier.HasValue)
                return new ErrorResponse("'upwards_modifier' must be a finite float.");
            float upwardsModifier = requestedUpwardsModifier ?? 0f;
            if (float.IsNaN(upwardsModifier) || float.IsInfinity(upwardsModifier))
                return new ErrorResponse("'upwards_modifier' must be a finite float.");

            string modeStr = p.Get("force_mode");
            ForceMode mode = ForceMode.Force;
            if (!string.IsNullOrEmpty(modeStr))
            {
                if (!Enum.TryParse<ForceMode>(modeStr, true, out mode) || !Enum.IsDefined(typeof(ForceMode), mode))
                    return new ErrorResponse($"Invalid ForceMode: '{modeStr}'. Valid values: Force, Impulse, Acceleration, VelocityChange.");
            }

            var explosionPos = new Vector3(explosionPosition[0], explosionPosition[1], explosionPosition[2]);

            rb.AddExplosionForce(explosionForce.Value, explosionPos, explosionRadius.Value, upwardsModifier, mode);

            return new
            {
                success = true,
                message = $"Applied explosion force to '{go.name}'.",
                data = new
                {
                    target = go.name,
                    dimension = "3d",
                    force_type = "explosion",
                    force_mode = mode.ToString(),
                    explosion_position = new[] { explosionPos.x, explosionPos.y, explosionPos.z },
                    explosion_force = explosionForce.Value,
                    explosion_radius = explosionRadius.Value,
                    upwards_modifier = upwardsModifier,
                },
            };
        }

        private static GameObject FindTarget(JToken targetToken, string searchMethod)
        {
            if (targetToken == null)
                return null;

            if (!string.IsNullOrEmpty(searchMethod))
                return GameObjectLookup.FindByTarget(targetToken, searchMethod, true);

            if (targetToken.Type == JTokenType.Integer)
            {
                int instanceId = targetToken.ReadScalar<int>();
                return GameObjectLookup.FindById(instanceId);
            }

            string targetStr = targetToken.ToString();

            if (int.TryParse(targetStr, out int parsedId))
            {
                var byId = GameObjectLookup.FindById(parsedId);
                if (byId != null)
                    return byId;
            }

            return GameObjectLookup.FindByTarget(targetToken, searchMethod ?? "by_name", true);
        }
    }
}
