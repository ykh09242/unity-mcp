using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Sprite2D
{
    internal static class SpriteGeometry
    {
        internal static object Handle(JObject request, bool write, SpriteDiagnosticBuilder diagnostics)
        {
            try
            {
                if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                    throw new ArgumentException("Sprite geometry requires a stable Edit Mode importer.");
                if (request.ToString(Formatting.None).Length > 262144)
                    throw new ArgumentException("Geometry request exceeds 256 KiB.");
                if (!SpriteParams.TryReadAssetPath(request, "path", out string path, out string error))
                    throw new ArgumentException(error);
                var importer = AssetImporter.GetAtPath(path) as TextureImporter;
                if (importer == null)
                    throw new ArgumentException("No built-in TextureImporter exists at the requested path.");
                var provider = new SpriteGeometryProvider(importer);
                JArray rows = provider.Rows();
                string protectedState = provider.IdentityAndPhysics();
                string revision = Revision(path, importer, rows, protectedState);
                if (!write)
                    return Page(request, provider, rows, revision);
                string guid = Text(request, "guid");
                if (!string.Equals(guid, AssetDatabase.AssetPathToGUID(path), StringComparison.Ordinal))
                    throw new ArgumentException("Texture GUID identity is stale or does not match path.");
                Expected(request, revision);
                string id = Text(request, "sprite_id");
                string fileId = Text(request, "file_id");
                JObject selected = rows.OfType<JObject>()
                    .SingleOrDefault(row => row.Value<string>("sprite_id") == id && row.Value<string>("file_id") == fileId);
                if (selected == null)
                    throw new ArgumentException("Exact sprite_id/file_id identity is stale or mismatched; read get_geometry again.");
                if (
                    !(request["geometry"] is JObject changes)
                    || changes.Count < 1
                    || changes.Properties().Any(x => !new[] { "rect", "pivot", "border", "outlines" }.Contains(x.Name))
                )
                    throw new ArgumentException("geometry accepts only rect, pivot, border and render outlines; supply at least one.");
                provider.RequireCapabilities(changes);
                JObject before = (JObject)selected["geometry"];
                JObject after = Validate(changes, before, importer);
                bool changed = !JToken.DeepEquals(before, after);
                if (!changed)
                    return Receipt(path, provider.CapabilityMode, revision, selected, false);
                bool pivotChanged = !JToken.DeepEquals(before["pivot"], after["pivot"]);
                // Compare disk and loaded importer state immediately before the first provider mutation.
                Expected(request, Revision(path, importer, provider.Rows(), provider.IdentityAndPhysics()));
                bool attempted = false;
                try
                {
                    attempted = true;
                    provider.Change(id, after, pivotChanged);
                    provider.Save();
                    var fresh = new SpriteGeometryProvider((TextureImporter)AssetImporter.GetAtPath(path));
                    JArray actual = fresh.Rows();
                    JObject expected = (JObject)selected.DeepClone();
                    expected["geometry"] = after;
                    if (pivotChanged)
                        expected["alignment"] = "Custom";
                    var expectedRows = (JArray)rows.DeepClone();
                    expectedRows[rows.IndexOf(selected)] = expected;
                    string actualProtected = fresh.IdentityAndPhysics();
                    if (!JToken.DeepEquals(expectedRows, actual) || protectedState != actualProtected)
                        throw new InvalidOperationException("Reimport did not retain the requested geometry, sibling state, identities or physics outlines.");
                    string nextRevision = Revision(path, fresh.Importer, actual, actualProtected);
                    return Receipt(path, fresh.CapabilityMode, nextRevision, (JObject)actual[rows.IndexOf(selected)], true);
                }
                catch (Exception mutationError)
                {
                    bool restored = !attempted;
                    if (attempted)
                    {
                        try
                        {
                            // Restore only this tool's supported geometry, through the provider, never by rewriting .meta.
                            var restore = new SpriteGeometryProvider((TextureImporter)AssetImporter.GetAtPath(path));
                            restore.Change(id, before, false, selected.Value<string>("alignment"));
                            restore.Save();
                            var restoredProvider = new SpriteGeometryProvider((TextureImporter)AssetImporter.GetAtPath(path));
                            restored = JToken.DeepEquals(rows, restoredProvider.Rows()) && protectedState == restoredProvider.IdentityAndPhysics();
                        }
                        catch (Exception restoreError)
                        {
                            McpLog.Error("[SpriteGeometry] Geometry restore failed: " + restoreError);
                        }
                    }
                    McpLog.Error("[SpriteGeometry] Reimport/edit failed: " + mutationError);
                    return diagnostics.Fail(
                        "GEOMETRY_NOT_RETAINED",
                        "Sprite geometry edit failed; supported geometry restored="
                            + restored.ToString().ToLowerInvariant()
                            + ". Import callbacks and unrelated external writes are not transactional."
                    );
                }
            }
            catch (Exception error)
                when (error is ArgumentException
                    || error is InvalidOperationException
                    || error is System.Reflection.TargetInvocationException
                    || error is IOException
                    || error is UnauthorizedAccessException
                )
            {
                return diagnostics.Fail("GEOMETRY_REFUSED", error.GetBaseException().Message);
            }
        }

        private static object Page(JObject request, SpriteGeometryProvider provider, JArray rows, string revision)
        {
            if (!SpriteParams.TryReadWholeNumber(request, "page_size", 50, out int size, out string error) || size < 1 || size > 100)
                throw new ArgumentException(error ?? "Geometry page_size must be between 1 and 100.");
            if (!SpriteParams.TryReadWholeNumber(request, "cursor", 0, out int cursor, out error) || cursor < 0 || cursor > rows.Count)
                throw new ArgumentException(error ?? "Geometry cursor is outside the current sprite list.");
            if (cursor > 0)
                Expected(request, revision);
            var page = new JArray(rows.Skip(cursor).Take(size));
            provider.Importer.GetSourceTextureWidthAndHeight(out int width, out int height);
            var response = new JObject
            {
                ["success"] = true,
                ["path"] = provider.Importer.assetPath,
                ["guid"] = AssetDatabase.AssetPathToGUID(provider.Importer.assetPath),
                ["revision"] = revision,
                ["capability_mode"] = provider.CapabilityMode,
                ["width"] = width,
                ["height"] = height,
                ["sprite_count"] = rows.Count,
                ["sprites"] = page,
                ["next_cursor"] = cursor + page.Count < rows.Count ? new JValue(cursor + page.Count) : JValue.CreateNull(),
            };
            if (Encoding.UTF8.GetByteCount(response.ToString(Formatting.None)) > 262144)
                throw new ArgumentException("Geometry page exceeds 256 KiB; request a smaller page_size.");
            return response;
        }

        private static object Receipt(string path, string mode, string revision, JObject row, bool changed) =>
            new JObject
            {
                ["success"] = true,
                ["path"] = path,
                ["guid"] = AssetDatabase.AssetPathToGUID(path),
                ["capability_mode"] = mode,
                ["revision"] = revision,
                ["changed"] = changed,
                ["sprite"] = row,
            };

        private static string Text(JObject request, string field)
        {
            if (
                request[field]?.Type != JTokenType.String
                || string.IsNullOrWhiteSpace(request[field].Value<string>())
                || request[field].Value<string>().Length > 128
            )
                throw new ArgumentException(field + " requires a nonempty exact identity string of at most 128 characters.");
            return request[field].Value<string>();
        }

        private static void Expected(JObject request, string revision)
        {
            if (Text(request, "expected_revision") != revision)
                throw new ArgumentException("Sprite/importer revision drifted; read get_geometry again before editing or continuing.");
        }

        private static string Revision(string path, TextureImporter importer, JArray rows, string protectedState)
        {
            string full = AssetPathUtility.GetFullAssetPath(path);
            if (!File.Exists(full) || !File.Exists(full + ".meta"))
                throw new ArgumentException("Sprite source or importer metadata is unavailable.");
            if (new FileInfo(full).Length > 67108864 || new FileInfo(full + ".meta").Length > 8388608)
                throw new ArgumentException("Geometry source/metadata exceeds 64 MiB/8 MiB bounds.");
            string loaded = EditorJsonUtility.ToJson(importer) + rows.ToString(Formatting.None) + protectedState;
            if (Encoding.UTF8.GetByteCount(loaded) > 2097152)
                throw new ArgumentException("Geometry/importer state exceeds 2 MiB.");
            using (var sha = SHA256.Create())
            using (var source = File.OpenRead(full))
            using (var metadata = File.OpenRead(full + ".meta"))
            {
                string state =
                    AssetDatabase.AssetPathToGUID(path)
                    + Convert.ToBase64String(sha.ComputeHash(source))
                    + Convert.ToBase64String(sha.ComputeHash(metadata))
                    + loaded;
                return Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(state)));
            }
        }

        private static JObject Validate(JObject changes, JObject before, TextureImporter importer)
        {
            JObject result = (JObject)before.DeepClone();
            foreach (var field in changes.Properties())
            {
                if (field.Name == "outlines")
                    continue;
                int length = field.Name == "pivot" ? 2 : 4;
                result[field.Name] = Numbers(field.Value, length, field.Name);
            }
            float[] r = result["rect"].Values<float>().ToArray();
            float[] p = result["pivot"].Values<float>().ToArray();
            float[] b = result["border"].Values<float>().ToArray();
            importer.GetSourceTextureWidthAndHeight(out int width, out int height);
            if (r[0] < 0 || r[1] < 0 || r[2] <= 0 || r[3] <= 0 || (double)r[0] + r[2] > width || (double)r[1] + r[3] > height)
                throw new ArgumentException("rect must fit the original source texture with positive width and height.");
            if (p.Any(x => x < 0 || x > 1))
                throw new ArgumentException("pivot must be normalized between 0 and 1.");
            if (b.Any(x => x < 0) || (double)b[0] + b[2] > r[2] || (double)b[1] + b[3] > r[3])
                throw new ArgumentException("Opposing nonnegative border values must fit the sprite rect.");
            if (changes["outlines"] != null)
            {
                if (!(changes["outlines"] is JArray paths) || paths.Count < 1 || paths.Count > 16)
                    throw new ArgumentException("outlines requires 1-16 render polygons.");
                result["outlines"] = new JArray(
                    paths.Select(path =>
                    {
                        if (!(path is JArray points) || points.Count < 3 || points.Count > 256)
                            throw new ArgumentException("Each outline requires 3-256 points.");
                        return new JArray(points.Select(point => Numbers(point, 2, "outline point")));
                    })
                );
            }
            foreach (JArray polygon in (JArray)result["outlines"])
            {
                Vector2[] points = polygon.Select(point => new Vector2(point[0].Value<float>(), point[1].Value<float>())).ToArray();
                if (
                    points.Any(point => Math.Abs((double)point.x) > r[2] / 2d || Math.Abs((double)point.y) > r[3] / 2d)
                    || points.Distinct().Count() != points.Length
                )
                    throw new ArgumentException("Outline points must be distinct and fit the sprite-centered source-pixel rect.");
                double area = 0;
                for (int i = 0; i < points.Length; i++)
                {
                    Vector2 a = points[i],
                        next = points[(i + 1) % points.Length];
                    area += (double)a.x * next.y - (double)next.x * a.y;
                    for (int j = i + 1; j < points.Length; j++)
                    {
                        if (j == i + 1 || (i == 0 && j == points.Length - 1))
                            continue;
                        if (Intersects(a, next, points[j], points[(j + 1) % points.Length]))
                            throw new ArgumentException("Outline polygons must not self-intersect.");
                    }
                }
                if (points.Length < 3 || Math.Abs(area) < 0.000001)
                    throw new ArgumentException("Outline polygons require nonzero area.");
            }
            return result;
        }

        private static JArray Numbers(JToken token, int length, string name)
        {
            if (!(token is JArray values) || values.Count != length)
                throw new ArgumentException(name + " requires exactly " + length + " finite numbers.");
            var result = new JArray();
            foreach (JToken value in values)
            {
                if (value.Type != JTokenType.Integer && value.Type != JTokenType.Float)
                    throw new ArgumentException(name + " requires numbers without bool/string coercion.");
                double number = value.Value<double>();
                if (double.IsNaN(number) || double.IsInfinity(number) || Math.Abs(number) > float.MaxValue)
                    throw new ArgumentException(name + " requires finite 32-bit numbers.");
                result.Add((float)number);
            }
            return result;
        }

        private static double Cross(Vector2 a, Vector2 b, Vector2 c) => ((double)b.x - a.x) * ((double)c.y - a.y) - ((double)b.y - a.y) * ((double)c.x - a.x);

        private static bool Intersects(Vector2 a, Vector2 b, Vector2 c, Vector2 d)
        {
            double abC = Cross(a, b, c),
                abD = Cross(a, b, d),
                cdA = Cross(c, d, a),
                cdB = Cross(c, d, b);
            return abC * abD <= 0
                && cdA * cdB <= 0
                && Math.Max(a.x, b.x) >= Math.Min(c.x, d.x)
                && Math.Max(c.x, d.x) >= Math.Min(a.x, b.x)
                && Math.Max(a.y, b.y) >= Math.Min(c.y, d.y)
                && Math.Max(c.y, d.y) >= Math.Min(a.y, b.y);
        }
    }
}
