using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Sprite2D
{
    // Reflection keeps the optional 2D Sprite package out of the base package's assembly dependencies.
    // Only Unity's verified TextureImporter provider is allowed; custom factories cannot broaden this contract.
    internal sealed class SpriteGeometryProvider
    {
        private const string Prefix = "UnityEditor.U2D.Sprites.";
        private readonly Assembly assembly;
        private readonly Type contract;
        private readonly MethodInfo nativeSpriteId;
        private readonly object provider;
        private readonly Type outlineContract;
        private readonly object outlines;
        private readonly Type physicsContract;
        private readonly object physics;
        private readonly Type namesContract;
        private readonly object names;
        private readonly Type capabilityContract;
        private readonly object capabilities;
        internal readonly Array Rects;
        internal readonly TextureImporter Importer;
        internal readonly string CapabilityMode;

        internal SpriteGeometryProvider(TextureImporter importer)
        {
            Importer = importer;
            if (
                importer.GetType() != typeof(TextureImporter)
                || importer.textureType != TextureImporterType.Sprite
                || importer.spriteImportMode != SpriteImportMode.Multiple
            )
                throw new ArgumentException(
                    "Geometry supports only existing built-in TextureImporter Multiple sprites; custom importers and mode conversion are refused."
                );
            assembly = AppDomain.CurrentDomain.GetAssemblies().SingleOrDefault(x => x.GetName().Name == "Unity.2D.Sprite.Editor");
            if (assembly == null)
                throw new ArgumentException("Optional 2D Sprite editor package is unavailable; no import settings were changed.");
            contract = RequiredType("ISpriteEditorDataProvider");
            Type nativeExtension = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(x => x.GetType("UnityEditor.U2D.SpriteEditorExtension"))
                .FirstOrDefault(x => x != null);
            nativeSpriteId = nativeExtension?.GetMethod("GetSpriteID", BindingFlags.Public | BindingFlags.Static, null, new[] { typeof(Sprite) }, null);
            if (nativeSpriteId == null)
                throw new ArgumentException("Native persistent Sprite ID API is unavailable in this Editor version.");
            Type factoryType = RequiredType("SpriteDataProviderFactories");
            object factory = Activator.CreateInstance(factoryType);
            Invoke(factoryType, factory, "Init");
            provider = Invoke(factoryType, factory, "GetSpriteEditorDataProviderFromObject", importer);
            if (provider == null || provider.GetType().Assembly != assembly || provider.GetType().FullName != Prefix + "TextureImporterDataProvider")
                throw new ArgumentException("Unknown or missing Sprite data provider; only Unity's built-in TextureImporter provider is supported.");
            Invoke(contract, provider, "InitSpriteEditorDataProvider");
            if ((UnityEngine.Object)contract.GetProperty("targetObject").GetValue(provider) != importer)
                throw new ArgumentException("Sprite provider target identity did not match the TextureImporter.");
            capabilityContract = assembly.GetType(Prefix + "ISpriteFrameEditCapability");
            if (capabilityContract != null)
            {
                capabilities = Extra(capabilityContract);
                CapabilityMode = "advertised";
            }
            else
                CapabilityMode = "verified_builtin_legacy";
            outlineContract = RequiredType("ISpriteOutlineDataProvider");
            outlines = Extra(outlineContract);
            physicsContract = RequiredType("ISpritePhysicsOutlineDataProvider");
            physics = Extra(physicsContract);
            namesContract = RequiredType("ISpriteNameFileIdDataProvider");
            names = Extra(namesContract);
            Rects = (Array)Invoke(contract, provider, "GetSpriteRects");
            if (Rects.Length < 1 || Rects.Length > 4096)
                throw new ArgumentException("Geometry supports 1-4096 existing sprites per texture.");
        }

        private Type RequiredType(string name) =>
            assembly.GetType(Prefix + name) ?? throw new ArgumentException("2D Sprite provider API is unsupported: " + name + ".");

        private object Extra(Type type)
        {
            if (!(bool)Invoke(contract, provider, "HasDataProvider", type))
                throw new ArgumentException("Required Sprite provider is unavailable: " + type.Name + ".");
            object result = contract.GetMethod("GetDataProvider").MakeGenericMethod(type).Invoke(provider, null);
            return result ?? throw new ArgumentException("Required Sprite provider is missing: " + type.Name + ".");
        }

        private static object Invoke(Type type, object target, string method, params object[] args)
        {
            MethodInfo member = type.GetMethods().SingleOrDefault(x => x.Name == method && x.GetParameters().Length == args.Length);
            if (member == null)
                throw new ArgumentException("2D Sprite provider method is unsupported: " + method + ".");
            return member.Invoke(target, args);
        }

        internal static object Field(object rect, string name) => rect.GetType().GetProperty(name).GetValue(rect);

        private static void Field(object rect, string name, object value) => rect.GetType().GetProperty(name).SetValue(rect, value);

        internal static string SpriteId(object rect) => Field(rect, "spriteID").ToString();

        internal static string Name(object rect) => (string)Field(rect, "name");

        internal static string FileId(UnityEngine.Object sprite)
        {
            if (!AssetDatabase.TryGetGUIDAndLocalFileIdentifier(sprite, out string guid, out long id) || id == 0)
                throw new ArgumentException("Sprite is not a persistent subasset.");
            return id.ToString(CultureInfo.InvariantCulture);
        }

        private List<Vector2[]> Outline(object rect, bool isPhysics) =>
            (List<Vector2[]>)Invoke(isPhysics ? physicsContract : outlineContract, isPhysics ? physics : outlines, "GetOutlines", Field(rect, "spriteID"));

        private static JArray OutlineJson(List<Vector2[]> paths)
        {
            if (paths.Count > 16 || paths.Any(x => x.Length > 256))
                throw new ArgumentException("Existing Sprite outlines exceed 16 polygons or 256 points per polygon.");
            return new JArray(paths.Select(path => new JArray(path.Select(point => new JArray(point.x, point.y)))));
        }

        internal JObject Geometry(object rect)
        {
            var area = (Rect)Field(rect, "rect");
            var pivot = (Vector2)Field(rect, "pivot");
            var border = (Vector4)Field(rect, "border");
            return new JObject
            {
                ["rect"] = new JArray(area.x, area.y, area.width, area.height),
                ["pivot"] = new JArray(pivot.x, pivot.y),
                ["border"] = new JArray(border.x, border.y, border.z, border.w),
                ["outlines"] = OutlineJson(Outline(rect, false)),
            };
        }

        internal JArray Rows()
        {
            Sprite[] sprites = AssetDatabase.LoadAllAssetsAtPath(Importer.assetPath).OfType<Sprite>().ToArray();
            if (sprites.Length != Rects.Length || sprites.Select(x => x.name).Distinct().Count() != sprites.Length)
                throw new ArgumentException("Sprite subassets must map one-to-one to unique provider names.");
            var ids = new HashSet<string>(StringComparer.Ordinal);
            var rows = new JArray();
            int bytes = 0;
            foreach (object rect in Rects)
            {
                string id = SpriteId(rect);
                if (!ids.Add(id) || id.Length != 32 || id.All(x => x == '0'))
                    throw new ArgumentException("Sprite IDs must be unique, nonempty persistent identities.");
                Sprite sprite = sprites.SingleOrDefault(x => x.name == Name(rect));
                if (sprite == null || nativeSpriteId.Invoke(null, new object[] { sprite }).ToString() != id)
                    throw new ArgumentException("Sprite provider ID does not match the existing native Sprite subasset identity.");
                var row = new JObject
                {
                    ["sprite_id"] = id,
                    ["file_id"] = FileId(sprite),
                    ["name"] = Name(rect),
                    ["alignment"] = Field(rect, "alignment").ToString(),
                    ["geometry"] = Geometry(rect),
                };
                bytes += Encoding.UTF8.GetByteCount(row.ToString(Newtonsoft.Json.Formatting.None));
                if (bytes > 2097152)
                    throw new ArgumentException("Geometry state exceeds 2 MiB.");
                rows.Add(row);
            }
            return rows;
        }

        internal string IdentityAndPhysics()
        {
            var pairs = new JArray();
            int bytes = 0;
            foreach (object pair in (IEnumerable)Invoke(namesContract, names, "GetNameFileIdPairs"))
            {
                if (pairs.Count >= 8192)
                    throw new ArgumentException("Sprite name/fileID history exceeds 8192 pairs.");
                var value = JToken.FromObject(pair);
                bytes += Encoding.UTF8.GetByteCount(value.ToString(Newtonsoft.Json.Formatting.None));
                if (bytes > 2097152)
                    throw new ArgumentException("Sprite identity state exceeds 2 MiB.");
                pairs.Add(value);
            }
            var shapes = new JArray();
            foreach (object rect in Rects)
            {
                var shape = new JObject { ["id"] = SpriteId(rect), ["outlines"] = OutlineJson(Outline(rect, true)) };
                bytes += Encoding.UTF8.GetByteCount(shape.ToString(Newtonsoft.Json.Formatting.None));
                if (bytes > 2097152)
                    throw new ArgumentException("Sprite physics/identity state exceeds 2 MiB.");
                shapes.Add(shape);
            }
            return new JObject { ["names"] = pairs, ["physics"] = shapes }.ToString(Newtonsoft.Json.Formatting.None);
        }

        internal void RequireCapabilities(JObject changes)
        {
            if (capabilityContract == null)
                return; // Explicit compatibility policy: verified built-in provider, API absent from installed assembly.
            object current = Invoke(capabilityContract, capabilities, "GetEditCapability");
            Type flagType = RequiredType("EEditCapability");
            MethodInfo has = current.GetType().GetMethod("HasCapability", new[] { flagType });
            if (has == null)
                throw new ArgumentException("Sprite edit capability API is unsupported; no legacy fallback is allowed.");
            foreach (string field in changes.Properties().Select(x => x.Name))
            {
                // Render-outline editing needs its provider and the sprite-frame rect editing permission.
                string flag =
                    field == "pivot" ? "EditPivot"
                    : field == "border" ? "EditBorder"
                    : "EditSpriteRect";
                if (!(bool)has.Invoke(current, new[] { Enum.Parse(flagType, flag) }))
                    throw new ArgumentException("Sprite provider denied edit capability: " + flag + ".");
            }
        }

        internal void Change(string id, JObject geometry, bool pivotChanged, string restoreAlignment = null)
        {
            object rect = Rects.Cast<object>().Single(x => SpriteId(x) == id);
            float[] r = geometry["rect"].Values<float>().ToArray();
            float[] p = geometry["pivot"].Values<float>().ToArray();
            float[] b = geometry["border"].Values<float>().ToArray();
            Field(rect, "rect", new Rect(r[0], r[1], r[2], r[3]));
            Field(rect, "pivot", new Vector2(p[0], p[1]));
            Field(rect, "border", new Vector4(b[0], b[1], b[2], b[3]));
            if (pivotChanged || restoreAlignment != null)
                Field(rect, "alignment", Enum.Parse(Field(rect, "alignment").GetType(), restoreAlignment ?? "Custom"));
            Invoke(contract, provider, "SetSpriteRects", Rects);
            var polygons = ((JArray)geometry["outlines"])
                .Select(path => ((JArray)path).Select(point => new Vector2(point[0].Value<float>(), point[1].Value<float>())).ToArray())
                .ToList();
            Invoke(outlineContract, outlines, "SetOutlines", Field(rect, "spriteID"), polygons);
        }

        internal void Save()
        {
            Invoke(contract, provider, "Apply");
            EditorUtility.SetDirty(Importer);
            Importer.SaveAndReimport();
        }
    }
}
