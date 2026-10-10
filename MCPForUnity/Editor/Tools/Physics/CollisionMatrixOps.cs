using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Physics
{
    internal static class CollisionMatrixOps
    {
        public static object GetCollisionMatrix(JObject @params)
        {
            var p = new ToolParams(@params);
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();

            if (dimension != "3d" && dimension != "2d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            var layers = new List<object>();
            var populatedLayers = new List<(int index, string name)>();

            for (int i = 0; i < 32; i++)
            {
                string name = LayerMask.LayerToName(i);
                if (string.IsNullOrEmpty(name))
                    continue;
                layers.Add(new { index = i, name });
                populatedLayers.Add((i, name));
            }

            var matrix = new Dictionary<string, Dictionary<string, bool>>();

            foreach (var layerA in populatedLayers)
            {
                int i = layerA.index;
                string nameA = layerA.name;
                var row = new Dictionary<string, bool>();

                foreach (var layerB in populatedLayers)
                {
                    int j = layerB.index;
                    if (j > i)
                        continue;
                    string nameB = layerB.name;
                    bool collides = dimension == "2d" ? !Physics2D.GetIgnoreLayerCollision(i, j) : !UnityEngine.Physics.GetIgnoreLayerCollision(i, j);
                    row[nameB] = collides;
                }

                matrix[nameA] = row;
            }

            return new
            {
                success = true,
                message = $"Collision matrix retrieved ({dimension}).",
                data = new { layers, matrix },
            };
        }

        public static object SetCollisionMatrix(JObject @params)
        {
            var p = new ToolParams(@params);
            string dimension = (p.Get("dimension") ?? "3d").ToLowerInvariant();

            if (dimension != "3d" && dimension != "2d")
                return new ErrorResponse($"Invalid dimension: '{dimension}'. Use '3d' or '2d'.");

            var layerAToken = p.GetRaw("layer_a");
            var layerBToken = p.GetRaw("layer_b");

            if (layerAToken == null)
                return new ErrorResponse("'layer_a' parameter is required.");
            if (layerBToken == null)
                return new ErrorResponse("'layer_b' parameter is required.");

            int layerA = ResolveLayer(layerAToken);
            int layerB = ResolveLayer(layerBToken);

            if (layerA < 0 || layerA >= 32)
                return new ErrorResponse($"Invalid layer_a: '{layerAToken}'. Layer not found or out of range.");
            if (layerB < 0 || layerB >= 32)
                return new ErrorResponse($"Invalid layer_b: '{layerBToken}'. Layer not found or out of range.");

            var collideToken = p.GetRaw("collide");
            bool collide = true;
            if (collideToken != null && collideToken.Type != JTokenType.Null && !string.IsNullOrWhiteSpace(collideToken.ToString()))
            {
                bool? parsedCollide = ParamCoercion.CoerceBoolNullable(collideToken);
                if (!parsedCollide.HasValue)
                    return new ErrorResponse("Invalid 'collide' parameter. Use a boolean value.");
                collide = parsedCollide.Value;
            }

            if (dimension == "2d")
            {
                Physics2D.IgnoreLayerCollision(layerA, layerB, !collide);
                MarkSettingsDirty("ProjectSettings/Physics2DSettings.asset");
            }
            else
            {
                UnityEngine.Physics.IgnoreLayerCollision(layerA, layerB, !collide);
                MarkSettingsDirty("ProjectSettings/DynamicsManager.asset");
            }

            string nameA = LayerMask.LayerToName(layerA);
            string nameB = LayerMask.LayerToName(layerB);
            if (string.IsNullOrEmpty(nameA))
                nameA = layerA.ToString();
            if (string.IsNullOrEmpty(nameB))
                nameB = layerB.ToString();

            return new
            {
                success = true,
                message = $"Collision between '{nameA}' and '{nameB}' set to {(collide ? "enabled" : "disabled")} ({dimension}).",
                data = new
                {
                    layer_a = nameA,
                    layer_b = nameB,
                    collide,
                    dimension,
                },
            };
        }

        private static void MarkSettingsDirty(string assetPath)
        {
            var assets = AssetDatabase.LoadAllAssetsAtPath(assetPath);
            if (assets != null && assets.Length > 0)
                EditorUtility.SetDirty(assets[0]);
        }

        private static int ResolveLayer(JToken token)
        {
            if (token.Type == JTokenType.Integer)
            {
                int idx = token.ReadScalar<int>();
                return idx >= 0 && idx < 32 ? idx : -1;
            }
            string name = token.ToString();
            if (int.TryParse(name, out int parsed))
                return parsed >= 0 && parsed < 32 ? parsed : -1;
            return LayerMask.NameToLayer(name);
        }
    }
}
