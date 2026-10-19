using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.ProBuilder
{
    /// <summary>Bounded snapshots retain the same native index ordering used by existing mutations.</summary>
    internal static class ProBuilderTopology
    {
        private const int MaxVertices = 32768;
        private const int MaxFaces = 16384;
        private const int MaxIndices = 131072;
        private const int MaxEdges = 65536;
        private const int MaxFaceIndices = 512;
        private const int MaxResponseBytes = 256 * 1024;
        private static readonly string Session = Guid.NewGuid().ToString("N");
        private static readonly HashSet<string> GuardedActions = new HashSet<string>(StringComparer.Ordinal)
        {
            "extrude_faces",
            "extrude_edges",
            "bevel_edges",
            "subdivide",
            "delete_faces",
            "bridge_edges",
            "connect_elements",
            "detach_faces",
            "flip_normals",
            "merge_faces",
            "duplicate_and_flip",
            "create_polygon",
            "merge_vertices",
            "weld_vertices",
            "split_vertices",
            "move_vertices",
            "insert_vertex",
            "append_vertices_to_edge",
            "set_face_material",
            "set_face_color",
            "set_face_uvs",
            "set_smoothing",
            "auto_smooth",
        };

        private sealed class FaceSnapshot
        {
            internal int[] Indices;
            internal int SmoothingGroup;
            internal int SubmeshIndex;
            internal bool ManualUV;
        }

        private sealed class Snapshot
        {
            internal Vector3[] Positions;
            internal FaceSnapshot[] Faces;
            internal int[][] Edges;
            internal string Revision;
        }

        private sealed class TopologyException : Exception
        {
            internal readonly string Code;

            internal TopologyException(string code, string message)
                : base(message) => Code = code;
        }

        private static void Refuse(string code, string message) => throw new TopologyException(code, message);

        private static ErrorResponse Error(TopologyException ex) => new ErrorResponse(ex.Code, new { message = ex.Message });

        internal static object CheckGuard(string action, JObject request)
        {
            if (action == "get_topology")
                return null;
            // Do not change parsing or validation of unguarded legacy payloads.
            JObject props = request["properties"] as JObject;
            if (request["properties"]?.Type == JTokenType.String)
            {
                string text = request["properties"].Value<string>();
                try
                {
                    props = JObject.Parse(text);
                }
                catch (JsonException)
                {
                    return null;
                }
            }
            props = props ?? request;
            if (props.Property("mesh_revision") == null)
                return null;
            try
            {
                string revision = ReadRevision(props["mesh_revision"]);
                if (!GuardedActions.Contains(action))
                    Refuse("unsupported_mesh_revision", "This action does not support a mesh revision guard.");
                var snapshot = Capture(ManageProBuilder.RequireProBuilderMesh(request));
                if (!string.Equals(snapshot.Revision, revision, StringComparison.Ordinal))
                    Refuse("stale_mesh_revision", "Mesh state changed; read topology again before applying native indices.");
                return null;
            }
            catch (TopologyException ex)
            {
                return Error(ex);
            }
        }

        internal static object GetTopology(JObject request)
        {
            try
            {
                JObject props = ReadProperties(request);
                foreach (var property in props.Properties())
                    if (property.Name != "kind" && property.Name != "page_size" && property.Name != "cursor" && property.Name != "mesh_revision")
                        Refuse("invalid_topology_request", "Unknown topology property: " + property.Name);
                string kind = "faces";
                if (props.Property("kind") != null)
                {
                    if (props["kind"].Type != JTokenType.String)
                        Refuse("invalid_topology_request", "kind must be faces, edges or vertices.");
                    kind = props.Value<string>("kind");
                }
                if (kind != "faces" && kind != "edges" && kind != "vertices")
                    Refuse("invalid_topology_request", "kind must be faces, edges or vertices.");
                int size = 100;
                if (props.Property("page_size") != null)
                {
                    if (props["page_size"].Type != JTokenType.Integer || !int.TryParse(props["page_size"].ToString(), out size) || size < 1 || size > 100)
                        Refuse("invalid_topology_request", "page_size must be an integer from 1 through 100.");
                }
                string revision = props.Property("mesh_revision") == null ? null : ReadRevision(props["mesh_revision"]);
                string cursor = null;
                if (props.Property("cursor") != null)
                {
                    if (
                        props["cursor"].Type != JTokenType.String
                        || string.IsNullOrEmpty(props.Value<string>("cursor"))
                        || props.Value<string>("cursor").Length > 2048
                    )
                        Refuse("invalid_topology_cursor", "cursor must be a nonempty string of at most 2048 characters.");
                    if (revision == null)
                        Refuse("invalid_topology_cursor", "Continuation requires mesh_revision.");
                    cursor = props.Value<string>("cursor");
                }
                int offset = cursor == null ? 0 : ReadCursor(cursor, revision, kind, size);
                Component mesh = ManageProBuilder.RequireProBuilderMesh(request);
                Snapshot snapshot = Capture(mesh);
                if (revision != null && !string.Equals(revision, snapshot.Revision, StringComparison.Ordinal))
                    Refuse("stale_mesh_revision", "Mesh state changed; restart topology paging.");
                int total =
                    kind == "faces" ? snapshot.Faces.Length
                    : kind == "edges" ? snapshot.Edges.Length
                    : snapshot.Positions.Length;
                if (offset > total || (cursor != null && (offset == 0 || offset >= total || offset % size != 0)))
                    Refuse("invalid_topology_cursor", "Cursor offset is outside the requested topology.");
                var rows = new JArray();
                for (int i = offset; i < Math.Min(total, offset + size); i++)
                    rows.Add(Row(snapshot, kind, i));
                int next = offset + rows.Count;
                var data = new JObject
                {
                    ["instance_id"] = mesh.gameObject.GetInstanceIDCompat(),
                    ["kind"] = kind,
                    ["coordinate_space"] = "local",
                    ["total"] = total,
                    ["offset"] = offset,
                    ["page_size"] = size,
                    ["mesh_revision"] = snapshot.Revision,
                    ["next_cursor"] = next < total ? EncodeCursor(snapshot.Revision, kind, size, next) : null,
                    ["items"] = rows,
                    ["limits"] = new JObject
                    {
                        ["vertices"] = MaxVertices,
                        ["faces"] = MaxFaces,
                        ["face_indices"] = MaxIndices,
                        ["indices_per_face"] = MaxFaceIndices,
                        ["unique_edges"] = MaxEdges,
                        ["response_bytes"] = MaxResponseBytes,
                    },
                    ["limitations"] = new JArray(
                        "Revision covers native index order, positions, shared vertex groups and returned face attributes; it is session scoped.",
                        "Local-space geometry excludes transform, material assets, UV coordinates and vertex colors.",
                        "Reflection property getters may materialize native collections indivisibly; these limits do not impose a hard wall-clock timeout."
                    ),
                };
                var response = new SuccessResponse("ProBuilder topology page", data);
                if (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(response)) > MaxResponseBytes)
                    Refuse("topology_budget_exceeded", "Page exceeds the byte budget; retry with a smaller page_size.");
                return response;
            }
            catch (TopologyException ex)
            {
                return Error(ex);
            }
            catch (JsonException)
            {
                return new ErrorResponse("invalid_topology_request", new { message = "Topology properties must be a JSON object." });
            }
        }

        private static JObject ReadProperties(JObject request)
        {
            if (request["properties"] is JObject props)
                return props;
            if (request["properties"]?.Type == JTokenType.String)
            {
                string text = request["properties"].Value<string>();
                if (text.Length > MaxResponseBytes)
                    Refuse("invalid_topology_request", "Topology properties exceed the request budget.");
                return JObject.Parse(text);
            }
            Refuse("invalid_topology_request", "Topology properties must be an object.");
            return null;
        }

        private static string ReadRevision(JToken token)
        {
            if (token?.Type != JTokenType.String)
                Refuse("invalid_mesh_revision", "mesh_revision must have the pb1: revision format.");
            string value = token.Value<string>();
            if (
                value == null
                || value.Length != 68
                || !value.StartsWith("pb1:", StringComparison.Ordinal)
                || value.Skip(4).Any(c => !(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f'))
            )
                Refuse("invalid_mesh_revision", "mesh_revision must have the pb1: revision format.");
            return value;
        }

        private static string EncodeCursor(string revision, string kind, int size, int offset) =>
            Convert.ToBase64String(
                Encoding.UTF8.GetBytes(
                    string.Join("|", "pb1", revision, kind, size.ToString(CultureInfo.InvariantCulture), offset.ToString(CultureInfo.InvariantCulture))
                )
            );

        private static int ReadCursor(string cursor, string revision, string kind, int size)
        {
            string[] parts;
            try
            {
                parts = Encoding.UTF8.GetString(Convert.FromBase64String(cursor)).Split('|');
            }
            catch (FormatException)
            {
                Refuse("invalid_topology_cursor", "Malformed topology cursor.");
                return 0;
            }
            int offset;
            if (
                parts.Length != 5
                || parts[0] != "pb1"
                || parts[1] != revision
                || parts[2] != kind
                || parts[3] != size.ToString(CultureInfo.InvariantCulture)
                || !int.TryParse(parts[4], NumberStyles.None, CultureInfo.InvariantCulture, out offset)
                || offset <= 0
            )
                Refuse("invalid_topology_cursor", "Cursor does not match the target revision, category, page size or offset.");
            else
                return offset;
            return 0;
        }

        private static Snapshot Capture(Component mesh)
        {
            var positions = ManageProBuilder._proBuilderMeshType.GetProperty("positions")?.GetValue(mesh) as IList<Vector3>;
            var faces = ManageProBuilder.GetFacesArray(mesh) as IList;
            if (positions == null || faces == null)
                Refuse("topology_unavailable", "The installed ProBuilder package does not expose required topology collections.");
            if (positions.Count > MaxVertices || faces.Count > MaxFaces)
                Refuse("topology_budget_exceeded", "Mesh exceeds the vertex or face scan budget.");
            var snapshot = new Snapshot { Positions = new Vector3[positions.Count], Faces = new FaceSnapshot[faces.Count] };
            for (int i = 0; i < positions.Count; i++)
            {
                Vector3 p = positions[i];
                if (!Finite(p.x) || !Finite(p.y) || !Finite(p.z))
                    Refuse("topology_unavailable", "Mesh contains non-finite vertex positions.");
                snapshot.Positions[i] = p;
            }
            var indicesProperty = ManageProBuilder._faceType.GetProperty("indexes");
            var smoothingProperty = ManageProBuilder._faceType.GetProperty("smoothingGroup");
            var manualProperty = ManageProBuilder._faceType.GetProperty("manualUV");
            var submeshProperty = ManageProBuilder._faceType.GetProperty("submeshIndex");
            if (indicesProperty == null || smoothingProperty == null || manualProperty == null || submeshProperty == null)
                Refuse("topology_unavailable", "The installed ProBuilder face API lacks required attributes.");
            int count = 0;
            for (int i = 0; i < faces.Count; i++)
            {
                object face = faces[i];
                var indices = indicesProperty.GetValue(face) as IList<int>;
                if (indices == null)
                    Refuse("topology_unavailable", "Could not read face triangle indices.");
                if (indices.Count > MaxFaceIndices || count + indices.Count > MaxIndices)
                    Refuse("topology_budget_exceeded", "Mesh exceeds the face-index scan budget.");
                count += indices.Count;
                int[] copied = new int[indices.Count];
                for (int j = 0; j < indices.Count; j++)
                {
                    int index = indices[j];
                    if (index < 0 || index >= snapshot.Positions.Length)
                        Refuse("topology_unavailable", "Mesh contains an invalid vertex index.");
                    copied[j] = index;
                }
                snapshot.Faces[i] = new FaceSnapshot
                {
                    Indices = copied,
                    SmoothingGroup = (int)smoothingProperty.GetValue(face),
                    ManualUV = (bool)manualProperty.GetValue(face),
                    SubmeshIndex = (int)submeshProperty.GetValue(face),
                };
            }
            // Validate shared-group work before invoking the existing native edge-order provider.
            var shared = ManageProBuilder._proBuilderMeshType.GetProperty("sharedVertices")?.GetValue(mesh) as IList;
            if (shared == null)
                Refuse("topology_unavailable", "Could not read native shared vertex groups.");
            if (shared.Count > MaxVertices)
                Refuse("topology_budget_exceeded", "Shared vertex groups exceed the scan budget.");
            var groups = new List<int[]>();
            int sharedCount = 0;
            foreach (object group in shared)
            {
                var members = group as IEnumerable;
                if (members == null)
                    Refuse("topology_unavailable", "Could not read shared vertex membership.");
                var copied = new List<int>();
                foreach (object member in members)
                {
                    if (++sharedCount > MaxVertices)
                        Refuse("topology_budget_exceeded", "Shared vertex membership exceeds the scan budget.");
                    if (!(member is int index) || index < 0 || index >= snapshot.Positions.Length)
                        Refuse("topology_unavailable", "Invalid shared vertex membership.");
                    copied.Add((int)member);
                }
                groups.Add(copied.ToArray());
            }
            var edges = ManageProBuilder.CollectUniqueEdges(mesh);
            if (edges.Count > MaxEdges)
                Refuse("topology_budget_exceeded", "Unique edges exceed the scan budget.");
            snapshot.Edges = new int[edges.Count][];
            for (int i = 0; i < edges.Count; i++)
            {
                object e = edges[i];
                int a = ReadEdgeVertex(e, "a"),
                    b = ReadEdgeVertex(e, "b");
                if (a < 0 || b < 0 || a >= snapshot.Positions.Length || b >= snapshot.Positions.Length)
                    Refuse("topology_unavailable", "Mesh contains an invalid edge endpoint.");
                snapshot.Edges[i] = new[] { a, b };
            }
            snapshot.Revision = Revision(mesh, snapshot, groups);
            return snapshot;
        }

        private static int ReadEdgeVertex(object edge, string name)
        {
            var field = edge.GetType().GetField(name);
            if (field != null)
                return (int)field.GetValue(edge);
            var property = edge.GetType().GetProperty(name);
            if (property == null)
                Refuse("topology_unavailable", "Could not read native edge endpoints.");
            return (int)property.GetValue(edge);
        }

        private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        private static string Revision(Component mesh, Snapshot snapshot, List<int[]> groups)
        {
            using (var stream = new MemoryStream())
            using (var writer = new BinaryWriter(stream, Encoding.UTF8, true))
            {
                writer.Write(Session);
                writer.Write(mesh.GetInstanceIDCompat());
                writer.Write(mesh.gameObject.GetInstanceIDCompat());
                writer.Write(snapshot.Positions.Length);
                foreach (Vector3 p in snapshot.Positions)
                {
                    writer.Write(p.x);
                    writer.Write(p.y);
                    writer.Write(p.z);
                }
                writer.Write(snapshot.Faces.Length);
                foreach (FaceSnapshot f in snapshot.Faces)
                {
                    writer.Write(f.Indices.Length);
                    foreach (int index in f.Indices)
                        writer.Write(index);
                    writer.Write(f.SmoothingGroup);
                    writer.Write(f.SubmeshIndex);
                    writer.Write(f.ManualUV);
                }
                writer.Write(groups.Count);
                foreach (int[] group in groups)
                {
                    writer.Write(group.Length);
                    foreach (int index in group)
                        writer.Write(index);
                }
                writer.Write(snapshot.Edges.Length);
                foreach (int[] edge in snapshot.Edges)
                {
                    writer.Write(edge[0]);
                    writer.Write(edge[1]);
                }
                writer.Flush();
                using (var hash = SHA256.Create())
                    return "pb1:" + BitConverter.ToString(hash.ComputeHash(stream.ToArray())).Replace("-", "").ToLowerInvariant();
            }
        }

        private static JObject Row(Snapshot snapshot, string kind, int index)
        {
            var row = new JObject { ["index"] = index };
            if (kind == "vertices")
                row["position"] = Vector(snapshot.Positions[index]);
            else if (kind == "edges")
            {
                int[] e = snapshot.Edges[index];
                row["vertexA"] = e[0];
                row["vertexB"] = e[1];
                row["positionA"] = Vector(snapshot.Positions[e[0]]);
                row["positionB"] = Vector(snapshot.Positions[e[1]]);
            }
            else
            {
                FaceSnapshot f = snapshot.Faces[index];
                row["indices"] = new JArray(f.Indices);
                row["smoothingGroup"] = f.SmoothingGroup;
                row["submeshIndex"] = f.SubmeshIndex;
                row["manualUV"] = f.ManualUV;
            }
            return row;
        }

        private static JArray Vector(Vector3 p) => new JArray(p.x, p.y, p.z);
    }
}
