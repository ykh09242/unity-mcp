#if MCP_FOR_UNITY_TERRAIN
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Terrain
{
    internal static class TerrainEdit
    {
        // Narrow fault boundary: native writes and readback are real; tests inject cancellation/external drift here.
        internal static Action<TerrainData> AfterWriteForTests = null;
        private static readonly Regex RevisionPattern = new Regex(@"\Aterrain1:[0-9a-f]{64}\z");
        private static readonly Regex GuidPattern = new Regex(@"\A[0-9a-f]{32}\z");

        internal static object Handle(JObject request, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            TerrainState before = TerrainState.Capture(request);
            string action = request.Value<string>("action").ToLowerInvariant();
            bool write = action.StartsWith("set_", StringComparison.Ordinal);
            if (!write)
                return Bounded(new SuccessResponse("Terrain observation", before.Observation()));
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                TerrainState.Refuse("terrain_editor_unavailable", "Terrain authoring requires stable Edit Mode.");
            Expected(before, request);
            if (before.Metadata.Value<long>("undo_work_estimate") > 16777216)
                TerrainState.Refuse("terrain_budget_exceeded", "Native Undo snapshot dimensions exceed the16777216-unit authoring estimate.");
            if (((JArray)before.Metadata["loaded_users"]).Count > 1 && !TerrainState.Flag(request, "allow_shared_data"))
                TerrainState.Refuse(
                    "terrain_shared_data_ack_required",
                    "TerrainData has multiple loaded Terrain users; acknowledge shared changes explicitly."
                );
            if (before.Boundary && !TerrainState.Flag(request, "allow_boundary"))
                TerrainState.Refuse(
                    "terrain_boundary_ack_required",
                    "Boundary edits may create a seam; acknowledge it explicitly. Neighbors remain unchanged."
                );
            JToken intended = Prepare(before, request, out TerrainLayer layer);
            // Serialize the complete bounded receipt before Undo or native writes.
            Bounded(new SuccessResponse("Terrain observation", before.Observation()));
            if (JToken.DeepEquals(before.Values, intended))
                return Bounded(new SuccessResponse("Terrain unchanged", before.Observation()));
            token.ThrowIfCancellationRequested();
            TerrainState fresh = TerrainState.Capture(request);
            Expected(fresh, request);
            if (fresh.Data != before.Data || fresh.Terrain != before.Terrain)
                TerrainState.Refuse("stale_terrain_revision", "Target identity changed before the mutation.");
            token.ThrowIfCancellationRequested();
            int group = Undo.GetCurrentGroup();
            bool attempted = false;
            JToken ownedAfter = null;
            var verifiedTrees = new HashSet<int>();
            try
            {
                Undo.IncrementCurrentGroup();
                group = Undo.GetCurrentGroup();
                Undo.SetCurrentGroupName("Edit Terrain region or indices");
                var undoObjects = new List<UnityEngine.Object> { before.Data };
                if (before.Channel == "alphamaps")
                    for (int i = 0; i < before.Data.alphamapTextureCount; i++)
                        undoObjects.Add(before.Data.GetAlphamapTexture(i));
                Undo.RegisterCompleteObjectUndo(undoObjects.ToArray(), "Edit Terrain region or indices");
                attempted = true;
                if (before.Kind == "trees")
                {
                    ownedAfter = before.Values.DeepClone();
                    for (int i = 0; i < before.Indices.Length; i++)
                    {
                        token.ThrowIfCancellationRequested();
                        ApplyTree(before.Data, before.Indices[i], (JObject)intended[i]);
                        JToken rowAfter = TerrainState.Tree(before.Data.GetTreeInstance(before.Indices[i]));
                        if (!Equivalent(intended[i], rowAfter, 0.000001f))
                            throw new InvalidOperationException("Native tree readback did not match.");
                        ownedAfter[i] = rowAfter;
                        verifiedTrees.Add(i);
                    }
                }
                else if (before.Kind == "layer")
                {
                    TerrainLayer[] layers = before.Data.terrainLayers;
                    layers[before.LayerIndex] = layer;
                    before.Data.SetTerrainLayersRegisterUndo(layers, "Replace Terrain layer reference");
                }
                else
                    ApplyRegion(before, (JArray)intended);
                TerrainState after = TerrainState.Capture(request);
                if (
                    !ProtectedEqual(before, after)
                    || !Equivalent(
                        intended,
                        after.Values,
                        before.Channel == "heights" ? 0.00002f
                            : before.Channel == "alphamaps" ? 0.005f
                            : 0.000001f
                    )
                )
                    throw new InvalidOperationException("Native readback did not retain requested state and protected identities.");
                if (before.Kind == "layer" && !SameAlphamaps(before.FullAlphamaps, after.FullAlphamaps))
                    throw new InvalidOperationException("Native layer replacement changed splat weights.");
                ownedAfter = after.Values.DeepClone();
                AfterWriteForTests?.Invoke(before.Data);
                token.ThrowIfCancellationRequested();
                TerrainState result = TerrainState.Capture(request);
                if (result.Revision != after.Revision)
                    throw new InvalidOperationException("External state changed after native write verification.");
                EditorUtility.SetDirty(before.Data);
                Undo.CollapseUndoOperations(group);
                return Bounded(new SuccessResponse("Terrain edit applied", result.Observation(!JToken.DeepEquals(before.Values, result.Values), true)));
            }
            catch (Exception error)
            {
                JObject recovery = attempted
                    ? Recover(before, request, ownedAfter, verifiedTrees)
                    : new JObject
                    {
                        ["restored_cells"] = 0,
                        ["conflicted_cells"] = 0,
                        ["state"] = "not_started",
                    };
                Undo.CollapseUndoOperations(group);
                string code = error is OperationCanceledException ? "terrain_cancelled" : "terrain_edit_failed";
                return new ErrorResponse(
                    code,
                    new JObject
                    {
                        ["recovery"] = recovery,
                        ["undo_registered"] = attempted,
                        ["message"] = "The edit did not complete. Recovery only restores verified owned values; external changes are preserved.",
                        ["neighbors_untouched"] = true,
                    }
                );
            }
        }

        private static object Bounded(object response)
        {
            if (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(response)) > TerrainState.MaxBytes)
                TerrainState.Refuse("terrain_budget_exceeded", "Response exceeds256KiB.");
            return response;
        }

        private static void Expected(TerrainState state, JObject request)
        {
            string guid = TerrainState.Text(request["expected_data_guid"], "expected_data_guid", 32);
            string revision = TerrainState.Text(request["expected_revision"], "expected_revision", 73);
            if (!GuidPattern.IsMatch(guid) || !RevisionPattern.IsMatch(revision))
                TerrainState.Refuse("invalid_terrain_request", "Expected TerrainData GUID and scoped revision formats are required.");
            if (
                string.IsNullOrEmpty(state.Guid)
                || !AssetDatabase.GetAssetPath(state.Data).StartsWith("Assets/", StringComparison.Ordinal)
                || guid != state.Guid
            )
                TerrainState.Refuse("terrain_identity_mismatch", "Writes require the exact existing persistent project TerrainData GUID.");
            if (revision != state.Revision)
                TerrainState.Refuse("stale_terrain_revision", "Selected state or identity changed; read the same scope again.");
        }

        private static JToken Prepare(TerrainState state, JObject request, out TerrainLayer layer)
        {
            layer = null;
            if (state.Kind == "layer")
            {
                string path = TerrainState.Text(request["layer_path"], "layer_path");
                string guid = TerrainState.Text(request["layer_guid"], "layer_guid", 32);
                if (
                    (!path.StartsWith("Assets/", StringComparison.Ordinal) && !path.StartsWith("Packages/", StringComparison.Ordinal))
                    || path.Contains("\\")
                    || path.Split('/').Any(p => p == "" || p == "." || p == "..")
                    || !GuidPattern.IsMatch(guid)
                )
                    TerrainState.Refuse("invalid_terrain_request", "Layer path/GUID must identify a contained persistent asset.");
                layer = AssetDatabase.LoadAssetAtPath<TerrainLayer>(path);
                if (layer == null || AssetDatabase.AssetPathToGUID(path) != guid)
                    TerrainState.Refuse("terrain_layer_identity_mismatch", "Exact TerrainLayer path/GUID is missing or stale.");
                return TerrainState.Identity(layer);
            }
            if (!(request["values"] is JArray values))
                throw new TerrainRequestException("invalid_terrain_request", "values must be an array.");
            if (state.Kind == "trees")
            {
                if (values.Count != state.Indices.Length)
                    TerrainState.Refuse("invalid_terrain_request", "Tree value count must exactly match indices.");
                var result = (JArray)state.Values.DeepClone();
                for (int i = 0; i < values.Count; i++)
                {
                    if (!(values[i] is JObject changes) || changes.Count == 0)
                        throw new TerrainRequestException("invalid_terrain_request", "Each tree edit must contain supported fields.");
                    TerrainState.Keys(changes, "position", "width_scale", "height_scale", "rotation", "color", "lightmap_color");
                    foreach (var p in changes.Properties())
                    {
                        if (p.Name == "position")
                            ValidateVector(p.Value, 3, false);
                        else if (p.Name == "color" || p.Name == "lightmap_color")
                            ValidateVector(p.Value, 4, true);
                        else
                        {
                            float number = TerrainState.Number(p.Value, 0, p.Name == "rotation" ? 2 * Mathf.PI : 10, p.Name);
                            if (p.Name != "rotation" && number <= 0)
                                TerrainState.Refuse("invalid_terrain_request", "Tree scales must be positive.");
                        }
                        result[i][p.Name] = p.Value.DeepClone();
                    }
                }
                return result;
            }
            if (values.Count != state.Height)
                TerrainState.Refuse("invalid_terrain_request", "values must exactly match region height.");
            for (int y = 0; y < state.Height; y++)
            {
                if (!(values[y] is JArray row) || row.Count != state.Width)
                    throw new TerrainRequestException("invalid_terrain_request", "values must exactly match region width.");
                foreach (JToken cell in row)
                {
                    if (state.Channel == "holes")
                    {
                        if (cell.Type != JTokenType.Boolean)
                            TerrainState.Refuse("invalid_terrain_request", "Hole cells require booleans; true means surface exists.");
                    }
                    else if (state.Channel == "details")
                        TerrainState.Integer(cell, 0, 16, "detail density");
                    else if (state.Channel == "heights")
                        TerrainState.Number(cell, 0, 1, "height");
                    else
                    {
                        if (!(cell is JArray weights) || weights.Count != state.Data.alphamapLayers)
                            throw new TerrainRequestException("invalid_terrain_request", "Every splat cell requires all existing layer weights.");
                        double sum = 0;
                        foreach (var weight in weights)
                            sum += TerrainState.Number(weight, 0, 1, "weight");
                        if (Math.Abs(sum - 1) > .00001)
                            TerrainState.Refuse("invalid_terrain_request", "Each splat cell must sum to one.");
                    }
                }
            }
            return values.DeepClone();
        }

        private static void ValidateVector(JToken token, int count, bool bytes)
        {
            if (!(token is JArray values) || values.Count != count)
                throw new TerrainRequestException("invalid_terrain_request", "Invalid tree vector shape.");
            foreach (JToken value in values)
                if (bytes)
                    TerrainState.Integer(value, 0, 255, "color byte");
                else
                    TerrainState.Number(value, 0, 1, "normalized tree position");
        }

        private static void ApplyTree(TerrainData data, int index, JObject row)
        {
            TreeInstance value = data.GetTreeInstance(index);
            value.position = new Vector3(row["position"][0].Value<float>(), row["position"][1].Value<float>(), row["position"][2].Value<float>());
            value.widthScale = row.Value<float>("width_scale");
            value.heightScale = row.Value<float>("height_scale");
            value.rotation = row.Value<float>("rotation");
            value.color = Color(row["color"]);
            value.lightmapColor = Color(row["lightmap_color"]);
            data.SetTreeInstance(index, value);
        }

        private static Color32 Color(JToken token) =>
            new Color32(token[0].Value<byte>(), token[1].Value<byte>(), token[2].Value<byte>(), token[3].Value<byte>());

        private static void ApplyRegion(TerrainState state, JArray values)
        {
            if (state.Channel == "heights")
            {
                var patch = new float[state.Height, state.Width];
                for (int y = 0; y < state.Height; y++)
                for (int x = 0; x < state.Width; x++)
                    patch[y, x] = values[y][x].Value<float>();
                state.Data.SetHeights(state.X, state.Y, patch);
            }
            else if (state.Channel == "holes")
            {
                var patch = new bool[state.Height, state.Width];
                for (int y = 0; y < state.Height; y++)
                for (int x = 0; x < state.Width; x++)
                    patch[y, x] = values[y][x].Value<bool>();
                state.Data.SetHoles(state.X, state.Y, patch);
            }
            else if (state.Channel == "details")
            {
                var patch = new int[state.Height, state.Width];
                for (int y = 0; y < state.Height; y++)
                for (int x = 0; x < state.Width; x++)
                    patch[y, x] = values[y][x].Value<int>();
                state.Data.SetDetailLayer(state.X, state.Y, state.DetailLayer, patch);
            }
            else
            {
                var patch = new float[state.Height, state.Width, state.Data.alphamapLayers];
                for (int y = 0; y < state.Height; y++)
                for (int x = 0; x < state.Width; x++)
                for (int l = 0; l < patch.GetLength(2); l++)
                    patch[y, x, l] = values[y][x][l].Value<float>();
                state.Data.SetAlphamaps(state.X, state.Y, patch);
            }
        }

        private static bool Equivalent(JToken expected, JToken actual, float tolerance)
        {
            if (expected is JObject obj)
                return actual is JObject other
                    && obj.Count == other.Count
                    && obj.Properties().All(p => other.Property(p.Name) != null && Equivalent(p.Value, other[p.Name], tolerance));
            if (expected is JArray array)
                return actual is JArray values && array.Count == values.Count && array.Select((v, i) => Equivalent(v, values[i], tolerance)).All(v => v);
            if (
                expected != null
                && actual != null
                && (expected.Type == JTokenType.Float || expected.Type == JTokenType.Integer)
                && (actual.Type == JTokenType.Float || actual.Type == JTokenType.Integer)
            )
                return Math.Abs(expected.Value<double>() - actual.Value<double>()) <= tolerance;
            return JToken.DeepEquals(expected, actual);
        }

        private static bool SameAlphamaps(float[,,] a, float[,,] b) =>
            a != null
            && b != null
            && a.GetLength(0) == b.GetLength(0)
            && a.GetLength(1) == b.GetLength(1)
            && a.GetLength(2) == b.GetLength(2)
            && a.Cast<float>().SequenceEqual(b.Cast<float>());

        private static bool ProtectedEqual(TerrainState before, TerrainState current)
        {
            if (before.Data != current.Data || before.Terrain != current.Terrain)
                return false;
            JObject meta = (JObject)current.Metadata.DeepClone();
            if (before.Kind == "layer")
                meta["layers"][before.LayerIndex] = before.Metadata["layers"][before.LayerIndex].DeepClone();
            return JToken.DeepEquals(before.Metadata, meta);
        }

        private static JObject Recover(TerrainState before, JObject request, JToken ownedAfter, HashSet<int> verifiedTrees)
        {
            int restored = 0,
                conflicts = 0;
            if (ownedAfter == null)
                return new JObject
                {
                    ["state"] = "write_state_unverified",
                    ["restored_cells"] = 0,
                    ["conflicted_cells"] = 0,
                    ["message"] = "Native setter failed before verified readback; ownership is unproven and automatic restoration was refused.",
                };
            try
            {
                TerrainState current = TerrainState.Capture(request);
                if (!ProtectedEqual(before, current))
                    return new JObject
                    {
                        ["state"] = "protected_identity_changed",
                        ["restored_cells"] = 0,
                        ["conflicted_cells"] = 0,
                    };
                if (before.Kind == "region")
                {
                    var merged = (JArray)current.Values.DeepClone();
                    for (int y = 0; y < before.Height; y++)
                    for (int x = 0; x < before.Width; x++)
                    {
                        if (JToken.DeepEquals(before.Values[y][x], ownedAfter[y][x]))
                            continue;
                        if (JToken.DeepEquals(current.Values[y][x], ownedAfter[y][x]))
                        {
                            merged[y][x] = before.Values[y][x].DeepClone();
                            restored++;
                        }
                        else
                            conflicts++;
                    }
                    int planned = restored;
                    restored = 0;
                    if (planned > 0)
                    {
                        ApplyRegion(before, merged);
                        if (!JToken.DeepEquals(before.ReadRegion(), merged))
                            throw new InvalidOperationException("Recovery readback did not match the merged native cells.");
                        restored = planned;
                    }
                }
                else if (before.Kind == "trees")
                {
                    for (int i = 0; i < before.Indices.Length; i++)
                    {
                        if (!verifiedTrees.Contains(i))
                            continue;
                        var merged = (JObject)current.Values[i].DeepClone();
                        bool changed = false;
                        int plannedFields = 0;
                        foreach (var p in ((JObject)before.Values[i]).Properties())
                        {
                            if (JToken.DeepEquals(p.Value, ownedAfter[i][p.Name]))
                                continue;
                            if (JToken.DeepEquals(current.Values[i][p.Name], ownedAfter[i][p.Name]))
                            {
                                merged[p.Name] = p.Value.DeepClone();
                                plannedFields++;
                                changed = true;
                            }
                            else
                                conflicts++;
                        }
                        if (changed)
                        {
                            ApplyTree(before.Data, before.Indices[i], merged);
                            if (!JToken.DeepEquals(TerrainState.Tree(before.Data.GetTreeInstance(before.Indices[i])), merged))
                                throw new InvalidOperationException("Tree recovery readback did not match.");
                            restored += plannedFields;
                        }
                    }
                }
                else
                {
                    if (JToken.DeepEquals(current.Values, ownedAfter))
                    {
                        TerrainLayer[] layers = before.Data.terrainLayers;
                        string path = before.Values.Value<string>("path");
                        TerrainLayer prior = AssetDatabase.LoadAssetAtPath<TerrainLayer>(path);
                        if (!JToken.DeepEquals(TerrainState.Identity(prior), before.Values))
                            throw new InvalidOperationException("Prior layer identity changed.");
                        layers[before.LayerIndex] = prior;
                        before.Data.SetTerrainLayersRegisterUndo(layers, "Compensate Terrain layer reference");
                        if (!JToken.DeepEquals(TerrainState.Identity(before.Data.terrainLayers[before.LayerIndex]), before.Values))
                            throw new InvalidOperationException("Layer recovery identity did not match.");
                        restored++;
                    }
                    else
                        conflicts++;
                }
                EditorUtility.SetDirty(before.Data);
                return new JObject
                {
                    ["state"] = conflicts == 0 ? "restored_verified_values" : "external_changes_preserved",
                    ["restored_cells"] = restored,
                    ["conflicted_cells"] = conflicts,
                };
            }
            catch (Exception)
            {
                return new JObject
                {
                    ["state"] = "recovery_incomplete",
                    ["restored_cells"] = restored,
                    ["conflicted_cells"] = conflicts,
                };
            }
        }
    }
}
#endif
