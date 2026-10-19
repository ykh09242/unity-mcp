#if MCP_FOR_UNITY_TERRAIN
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using NativeTerrain = UnityEngine.Terrain;

namespace MCPForUnity.Editor.Tools.Terrain
{
    internal sealed class TerrainRequestException : Exception
    {
        internal readonly string Code;

        internal TerrainRequestException(string code, string message)
            : base(message) => Code = code;
    }

    internal sealed class TerrainState
    {
        internal const int MaxBytes = 262144;
        internal NativeTerrain Terrain;
        internal TerrainData Data;
        internal string Guid;
        internal string Revision;
        internal string Kind;
        internal string Channel;
        internal int X,
            Y,
            Width,
            Height,
            DetailLayer,
            LayerIndex;
        internal int[] Indices;
        internal bool Boundary;
        internal JObject Metadata;
        internal JObject Scope;
        internal JToken Values;
        internal float[,,] FullAlphamaps;
        private static readonly string Session = System.Guid.NewGuid().ToString("N");

        internal static void Refuse(string code, string message) => throw new TerrainRequestException(code, message);

        internal static int Integer(JToken value, int min, int max, string name)
        {
            if (
                value?.Type != JTokenType.Integer
                || !int.TryParse(value.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed)
                || parsed < min
                || parsed > max
            )
                throw new TerrainRequestException("invalid_terrain_request", name + " is not a bounded strict integer.");
            return parsed;
        }

        internal static float Number(JToken value, float min, float max, string name)
        {
            if (value == null || (value.Type != JTokenType.Integer && value.Type != JTokenType.Float))
                throw new TerrainRequestException("invalid_terrain_request", name + " must be a finite number.");
            double parsed = value.Value<double>();
            if (double.IsNaN(parsed) || double.IsInfinity(parsed) || parsed < min || parsed > max)
                Refuse("invalid_terrain_request", name + " is outside its supported bounds.");
            return (float)parsed;
        }

        internal static string Text(JToken value, string name, int max = 512)
        {
            if (value?.Type != JTokenType.String || string.IsNullOrEmpty(value.Value<string>()) || value.Value<string>().Length > max)
                throw new TerrainRequestException("invalid_terrain_request", name + " must be a bounded nonempty string.");
            return value.Value<string>();
        }

        internal static bool Flag(JObject request, string name)
        {
            if (request.Property(name) == null)
                return false;
            if (request[name].Type != JTokenType.Boolean)
                Refuse("invalid_terrain_request", name + " must be a boolean.");
            return request.Value<bool>(name);
        }

        internal static void Keys(JObject row, params string[] names)
        {
            if (row.Properties().Any(p => !names.Contains(p.Name)))
                Refuse("invalid_terrain_request", "A supplied field does not apply to this action.");
        }

        internal static JObject Identity(UnityEngine.Object value)
        {
            if (value == null)
                return new JObject
                {
                    ["instance_id"] = 0,
                    ["guid"] = "",
                    ["local_id"] = "",
                    ["path"] = "",
                };
            AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long localId);
            return new JObject
            {
                ["instance_id"] = value.GetInstanceIDCompat(),
                ["guid"] = guid ?? "",
                ["local_id"] = localId.ToString(CultureInfo.InvariantCulture),
                ["path"] = AssetDatabase.GetAssetPath(value),
            };
        }

        private static JArray Vector(Vector3 value) => new JArray(value.x, value.y, value.z);

        private static JArray Vector(Vector2 value) => new JArray(value.x, value.y);

        internal static TerrainState Capture(JObject request)
        {
            if (request == null || Encoding.UTF8.GetByteCount(request.ToString(Formatting.None)) > MaxBytes)
                throw new TerrainRequestException("terrain_budget_exceeded", "Request exceeds 256 KiB.");
            string action = Text(request["action"], "action", 32).ToLowerInvariant();
            string kind;
            switch (action)
            {
                case "get_info":
                    kind = "info";
                    break;
                case "get_region":
                case "set_region":
                    kind = "region";
                    break;
                case "get_trees":
                case "set_trees":
                    kind = "trees";
                    break;
                case "get_layer":
                case "set_layer":
                    kind = "layer";
                    break;
                default:
                    throw new TerrainRequestException("invalid_terrain_action", "Unknown Terrain action.");
            }
            var allowed = new List<string> { "action", "target", "searchMethod", "allow_shared_data", "allow_boundary" };
            if (action.StartsWith("set_", StringComparison.Ordinal))
                allowed.AddRange(new[] { "expected_revision", "expected_data_guid", "values" });
            if (kind == "region")
                allowed.AddRange(new[] { "channel", "region", "detail_layer" });
            if (kind == "trees")
                allowed.Add("indices");
            if (kind == "layer")
            {
                allowed.Add("index");
                if (action == "set_layer")
                {
                    allowed.Remove("values");
                    allowed.AddRange(new[] { "layer_path", "layer_guid" });
                }
            }
            Keys(request, allowed.ToArray());
            Flag(request, "allow_shared_data");
            Flag(request, "allow_boundary");
            string method = request["searchMethod"] == null ? null : Text(request["searchMethod"], "searchMethod", 16);
            if (method != null && method != "by_id" && method != "by_name" && method != "by_path")
                Refuse("invalid_terrain_request", "Terrain supports by_id, by_name and by_path selection.");
            Text(request["target"], "target");
            GameObject go = ObjectResolver.ResolveGameObject(request["target"], method);
            NativeTerrain terrain = go == null ? null : go.GetComponent<NativeTerrain>();
            if (terrain == null || terrain.terrainData == null || !go.scene.IsValid() || !go.scene.isLoaded)
                Refuse("terrain_not_found", "Target must be a loaded scene Terrain with existing TerrainData.");
            var state = new TerrainState
            {
                Terrain = terrain,
                Data = terrain.terrainData,
                Kind = kind,
            };
            state.Metadata = state.ReadMetadata();
            state.Guid = state.Metadata["data"].Value<string>("guid");
            state.Scope = new JObject { ["kind"] = kind };
            if (kind == "region")
            {
                state.Channel = Text(request["channel"], "channel", 16);
                if (!new[] { "heights", "alphamaps", "holes", "details" }.Contains(state.Channel))
                    Refuse("invalid_terrain_request", "Unsupported Terrain channel.");
                if (!(request["region"] is JObject r))
                    throw new TerrainRequestException("invalid_terrain_request", "An exact region is required.");
                Keys(r, "x", "y", "width", "height");
                state.X = Integer(r["x"], 0, 65535, "x");
                state.Y = Integer(r["y"], 0, 65535, "y");
                state.Width = Integer(r["width"], 1, 32, "width");
                state.Height = Integer(r["height"], 1, 32, "height");
                int resolution =
                    state.Channel == "heights" ? state.Data.heightmapResolution
                    : state.Channel == "holes" ? state.Data.holesResolution
                    : state.Channel == "alphamaps" ? state.Data.alphamapResolution
                    : state.Data.detailResolution;
                if ((long)state.X + state.Width > resolution || (long)state.Y + state.Height > resolution)
                    Refuse("invalid_terrain_request", "Region is outside the selected channel's resolution.");
                int layers = state.Channel == "alphamaps" ? state.Data.alphamapLayers : 1;
                if (layers < 1 || (long)state.Width * state.Height * layers > 4096)
                    Refuse("terrain_budget_exceeded", "Region exceeds 4096 scalar values.");
                state.DetailLayer =
                    state.Channel == "details" ? Integer(request["detail_layer"], 0, state.Data.detailPrototypes.Length - 1, "detail_layer") : -1;
                if (state.Channel != "details" && request.Property("detail_layer") != null)
                    Refuse("invalid_terrain_request", "detail_layer applies only to details.");
                state.Boundary = state.X == 0 || state.Y == 0 || state.X + state.Width == resolution || state.Y + state.Height == resolution;
                state.Scope["channel"] = state.Channel;
                state.Scope["region"] = new JObject
                {
                    ["x"] = state.X,
                    ["y"] = state.Y,
                    ["width"] = state.Width,
                    ["height"] = state.Height,
                };
                if (state.DetailLayer >= 0)
                    state.Scope["detail_layer"] = state.DetailLayer;
                state.Values = state.ReadRegion();
            }
            else if (kind == "trees")
            {
                if (!(request["indices"] is JArray indices) || indices.Count < 1 || indices.Count > 64)
                    throw new TerrainRequestException("invalid_terrain_request", "Supply 1..64 distinct tree indices.");
                state.Indices = indices.Select(t => Integer(t, 0, Math.Min(10000000, state.Data.treeInstanceCount - 1), "tree index")).ToArray();
                if (state.Indices.Distinct().Count() != state.Indices.Length)
                    Refuse("invalid_terrain_request", "Tree indices must be distinct.");
                state.Scope["indices"] = new JArray(state.Indices);
                state.Values = new JArray(state.Indices.Select(i => Tree(state.Data.GetTreeInstance(i))));
            }
            else if (kind == "layer")
            {
                state.LayerIndex = Integer(request["index"], 0, state.Data.terrainLayers.Length - 1, "index");
                if ((long)state.Data.alphamapWidth * state.Data.alphamapHeight * state.Data.alphamapLayers > 65536)
                    Refuse("terrain_budget_exceeded", "Layer reference edits require full splat verification within 65536 scalars.");
                state.Scope["index"] = state.LayerIndex;
                state.FullAlphamaps = state.Data.GetAlphamaps(0, 0, state.Data.alphamapWidth, state.Data.alphamapHeight);
                state.Values = Identity(state.Data.terrainLayers[state.LayerIndex]);
            }
            else
                state.Values = JValue.CreateNull();
            state.Revision = state.Hash();
            return state;
        }

        private JObject ReadMetadata()
        {
            TerrainLayer[] layers = Data.terrainLayers;
            DetailPrototype[] details = Data.detailPrototypes;
            TreePrototype[] trees = Data.treePrototypes;
            if (layers.Length > 16 || details.Length > 16 || trees.Length > 16)
                Refuse("terrain_budget_exceeded", "At most16 layers and16 prototypes of each kind are supported.");
            NativeTerrain[] all = UnityEngine.Resources.FindObjectsOfTypeAll<NativeTerrain>();
            if (all.Length > 512)
                Refuse("terrain_budget_exceeded", "Loaded Terrain discovery exceeds512 objects.");
            var users = all.Where(t => t != null && t.terrainData == Data && t.gameObject.scene.IsValid() && t.gameObject.scene.isLoaded)
                .OrderBy(t => t.GetInstanceIDCompat())
                .ToArray();
            if (users.Length > 128)
                Refuse("terrain_budget_exceeded", "Shared loaded users exceed128 objects.");
            var layerRows = new JArray();
            for (int i = 0; i < layers.Length; i++)
            {
                TerrainLayer l = layers[i];
                layerRows.Add(
                    new JObject
                    {
                        ["index"] = i,
                        ["asset"] = Identity(l),
                        ["tile_size"] = l == null ? null : Vector(l.tileSize),
                        ["tile_offset"] = l == null ? null : Vector(l.tileOffset),
                        ["diffuse_texture"] = Identity(l == null ? null : l.diffuseTexture),
                        ["normal_texture"] = Identity(l == null ? null : l.normalMapTexture),
                    }
                );
            }
            return new JObject
            {
                ["terrain_id"] = Terrain.gameObject.GetInstanceIDCompat(),
                ["component_id"] = Terrain.GetInstanceIDCompat(),
                ["data"] = Identity(Data),
                ["height_resolution"] = Data.heightmapResolution,
                ["holes_resolution"] = Data.holesResolution,
                ["alphamap_resolution"] = Data.alphamapResolution,
                ["detail_resolution"] = Data.detailResolution,
                ["size"] = Vector(Data.size),
                ["position"] = Vector(Terrain.transform.position),
                ["rotation"] = Vector(Terrain.transform.eulerAngles),
                ["scale"] = Vector(Terrain.transform.lossyScale),
                ["layers"] = layerRows,
                ["detail_prototypes"] = new JArray(
                    details.Select(p => new JObject
                    {
                        ["prototype"] = Identity(p.prototype),
                        ["texture"] = Identity(p.prototypeTexture),
                        ["render_mode"] = p.renderMode.ToString(),
                        ["use_mesh"] = p.usePrototypeMesh,
                        ["min_width"] = p.minWidth,
                        ["max_width"] = p.maxWidth,
                        ["min_height"] = p.minHeight,
                        ["max_height"] = p.maxHeight,
                        ["noise_spread"] = p.noiseSpread,
                    })
                ),
                ["tree_prototypes"] = new JArray(trees.Select(p => new JObject { ["prefab"] = Identity(p.prefab), ["bend_factor"] = p.bendFactor })),
                ["tree_count"] = Data.treeInstanceCount,
                ["undo_work_estimate"] =
                    (long)Data.heightmapResolution * Data.heightmapResolution
                    + (long)Data.holesResolution * Data.holesResolution
                    + (long)Data.alphamapResolution * Data.alphamapResolution * Data.alphamapLayers
                    + (long)Data.detailResolution * Data.detailResolution * details.Length
                    + (long)Data.treeInstanceCount * 16,
                ["loaded_users"] = new JArray(
                    users.Select(t => new JObject { ["terrain_id"] = t.gameObject.GetInstanceIDCompat(), ["component_id"] = t.GetInstanceIDCompat() })
                ),
                ["neighbors"] = new JArray(
                    new[] { Terrain.leftNeighbor, Terrain.topNeighbor, Terrain.rightNeighbor, Terrain.bottomNeighbor }.Select(t => new JObject
                    {
                        ["terrain_id"] = t == null ? 0 : t.gameObject.GetInstanceIDCompat(),
                        ["data"] = Identity(t == null ? null : t.terrainData),
                    })
                ),
            };
        }

        internal JArray ReadRegion()
        {
            var result = new JArray();
            float[,] heights = Channel == "heights" ? Data.GetHeights(X, Y, Width, Height) : null;
            bool[,] holes = Channel == "holes" ? Data.GetHoles(X, Y, Width, Height) : null;
            int[,] details = Channel == "details" ? Data.GetDetailLayer(X, Y, Width, Height, DetailLayer) : null;
            float[,,] alpha = Channel == "alphamaps" ? Data.GetAlphamaps(X, Y, Width, Height) : null;
            for (int y = 0; y < Height; y++)
            {
                var row = new JArray();
                for (int x = 0; x < Width; x++)
                {
                    if (heights != null)
                        row.Add(heights[y, x]);
                    else if (holes != null)
                        row.Add(holes[y, x]);
                    else if (details != null)
                        row.Add(details[y, x]);
                    else
                    {
                        var cell = new JArray();
                        for (int l = 0; l < alpha.GetLength(2); l++)
                            cell.Add(alpha[y, x, l]);
                        row.Add(cell);
                    }
                }
                result.Add(row);
            }
            return result;
        }

        internal static JObject Tree(TreeInstance tree) =>
            new JObject
            {
                ["position"] = Vector(tree.position),
                ["width_scale"] = tree.widthScale,
                ["height_scale"] = tree.heightScale,
                ["rotation"] = tree.rotation,
                ["prototype_index"] = tree.prototypeIndex,
                ["color"] = new JArray(tree.color.r, tree.color.g, tree.color.b, tree.color.a),
                ["lightmap_color"] = new JArray(tree.lightmapColor.r, tree.lightmapColor.g, tree.lightmapColor.b, tree.lightmapColor.a),
            };

        private string Hash()
        {
            using (var hash = SHA256.Create())
            {
                var document = new JObject
                {
                    ["session"] = Session,
                    ["metadata"] = Metadata,
                    ["scope"] = Scope,
                    ["values"] = Values,
                };
                if (FullAlphamaps != null)
                {
                    var values = new JArray();
                    foreach (float value in FullAlphamaps)
                        values.Add(value);
                    document["full_alphamaps"] = values;
                }
                return "terrain1:"
                    + BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(document.ToString(Formatting.None)))).Replace("-", "").ToLowerInvariant();
            }
        }

        internal JObject Observation(bool changed = false, bool undo = false) =>
            new JObject
            {
                ["data_guid"] = Guid,
                ["revision"] = Revision,
                ["scope"] = Scope.DeepClone(),
                ["metadata"] = Metadata.DeepClone(),
                ["values"] = Values.DeepClone(),
                ["changed"] = changed,
                ["undo_registered"] = undo,
                ["boundary_touched"] = Boundary,
                ["neighbors_untouched"] = true,
                ["save_behavior"] = "Edits mark only this TerrainData dirty in memory; this tool does not call global SaveAssets.",
                ["revision_scope"] =
                    "Selected region or indices plus target identity, resolutions, layer/prototype metadata and loaded users; unrelated regions are excluded.",
                ["limits"] = new JObject
                {
                    ["region_side"] = 32,
                    ["region_scalars"] = 4096,
                    ["tree_indices"] = 64,
                    ["layers_and_prototypes"] = 16,
                    ["layer_splat_scan_scalars"] = 65536,
                    ["wire_bytes"] = MaxBytes,
                    ["native_undo_work_estimate"] = 16777216,
                },
                ["limitations"] = new JArray(
                    "Loaded Terrain users only; unloaded assets may also share TerrainData. No neighboring TerrainData is changed or seam synchronized.",
                    "Native getters/setters and Undo snapshots are indivisible; size limits do not impose a hard timeout or atomic transaction.",
                    "Height and splat storage may quantize values. Holes true means surface exists. Existing tree indices/prototypes only; tree rotation is radians."
                ),
            };
    }
}
#endif
