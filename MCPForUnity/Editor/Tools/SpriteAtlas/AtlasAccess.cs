using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.U2D;
using UnityEngine;
using UnityEngine.U2D;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.SpriteAtlasTools
{
    internal static class AtlasAccess
    {
        internal static readonly string[] Platforms = { "Standalone", "Android", "iPhone", "WebGL" };

        internal static bool ImporterSettingsSupported
        {
            get
            {
#if UNITY_2022_1_OR_NEWER
                return true;
#else
                return false;
#endif
            }
        }

        internal static ArgumentException ImporterSettingsUnavailable() =>
            new ArgumentException(
                "SpriteAtlas V2 importer authoring requires Unity2022.1 or newer; this Editor lacks the public V2 importer settings API. Legacy V1 SpriteAtlas APIs are unchanged."
            );

        private static void RequireImporterSettings()
        {
            if (!ImporterSettingsSupported)
                throw ImporterSettingsUnavailable();
        }

        internal static void Stable()
        {
            if (
                EditorApplication.isPlayingOrWillChangePlaymode
                || EditorApplication.isCompiling
                || EditorApplication.isUpdating
                || BuildPipeline.isBuildingPlayer
            )
                throw new ArgumentException("Atlas authoring requires stable Edit Mode outside a Player build.");
        }

        internal static string Text(JObject values, string key)
        {
            if (values[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(values[key].Value<string>()) || values[key].Value<string>().Length > 1024)
                throw new ArgumentException(key + " requires a nonempty string of at most 1024 characters.");
            return values[key].Value<string>();
        }

        internal static string Path(JObject values)
        {
            RequireImporterSettings();
            string path = AssetPathUtility.GetContainedAssetPath(Text(values, "path"));
            if (string.IsNullOrEmpty(path) || !path.EndsWith(".spriteatlasv2", StringComparison.Ordinal))
                throw new ArgumentException("Atlas authoring supports contained Assets/*.spriteatlasv2 paths only; no implicit V1 conversion.");
            AssetPathUtility.GetFullAssetPath(path);
            return path;
        }

        internal static JObject Reference(Object asset)
        {
            if (asset == null || !AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string guid, out long id) || id == 0)
                throw new ArgumentException("A persistent saved asset GUID/fileID is required.");
            return new JObject { ["guid"] = guid, ["file_id"] = id.ToString(CultureInfo.InvariantCulture) };
        }

        internal static string Key(Object asset) => Reference(asset).ToString(Formatting.None);

        internal static Object Resolve(JToken token, bool atlas)
        {
            if (!(token is JObject reference) || reference.Count != 2)
                throw new ArgumentException("Exact {guid,file_id} asset reference is required.");
            string guid = Text(reference, "guid"),
                fileId = Text(reference, "file_id");
            if (
                !System.Text.RegularExpressions.Regex.IsMatch(guid, "^[0-9a-fA-F]{32}$")
                || !long.TryParse(fileId, NumberStyles.Integer, CultureInfo.InvariantCulture, out long id)
                || id == 0
            )
                throw new ArgumentException("Reference requires exact GUID and nonzero integer fileID string.");
            string path = AssetDatabase.GUIDToAssetPath(guid);
            if (string.IsNullOrEmpty(path) || AssetPathUtility.GetContainedAssetPath(path) == null)
                throw new ArgumentException("Reference must identify an existing contained Assets asset.");
            AssetPathUtility.GetFullAssetPath(path);
            if (atlas)
            {
                var result = Load(path);
                if (Reference(result).Value<string>("file_id") != fileId)
                    throw new ArgumentException("Atlas reference fileID is stale or mismatched.");
                return result;
            }
            var assets = AssetDatabase.LoadAllAssetsAtPath(path);
            if (assets.Length > 4096)
                throw new ArgumentException("Sprite reference source exceeds 4096 subassets.");
            Sprite sprite = assets.OfType<Sprite>().SingleOrDefault(x => Reference(x).Value<string>("file_id") == fileId);
            if (sprite == null)
                throw new ArgumentException("Packables must be exact existing Sprite references; folders and texture expansion are unsupported.");
            return sprite;
        }

        internal static SpriteAtlas Load(string path)
        {
            RequireImporterSettings();
            if (!path.EndsWith(".spriteatlasv2", StringComparison.Ordinal) || !(AssetImporter.GetAtPath(path) is SpriteAtlasImporter))
                throw new ArgumentException("A saved SpriteAtlas V2 asset with SpriteAtlasImporter is required.");
            var atlas = AssetDatabase.LoadAssetAtPath<SpriteAtlas>(path);
            return atlas != null
                ? atlas
                : throw new ArgumentException("The V2 importer did not generate a SpriteAtlas; inspect importer capability and packing mode.");
        }

        internal static Object[] Packables(SpriteAtlas atlas)
        {
            Object[] values = atlas.GetPackables();
            if (values.Length > 256 || values.Any(x => x == null))
                throw new ArgumentException("Atlas packables must contain at most 256 nonmissing entries.");
            foreach (Object value in values)
            {
                string path = AssetDatabase.GetAssetPath(value);
                if (AssetPathUtility.GetContainedAssetPath(path) == null)
                    throw new ArgumentException("Atlas contains a packable outside contained Assets; this authoring scope refuses it.");
                AssetPathUtility.GetFullAssetPath(path);
            }
            return values;
        }

        internal static Object[] RequestedPackables(JObject properties)
        {
            if (!(properties["packables"] is JArray list) || list.Count < 1 || list.Count > 128)
                throw new ArgumentException("packables requires 1-128 exact Sprite references.");
            Object[] result = list.Select(x => Resolve(x, false)).ToArray();
            if (result.Select(Key).Distinct().Count() != result.Length)
                throw new ArgumentException("Duplicate Sprite references in one request are refused.");
            return result;
        }

        internal static string Revision(string path)
        {
            var atlas = Load(path);
            var importer = (SpriteAtlasImporter)AssetImporter.GetAtPath(path);
            string full = AssetPathUtility.GetFullAssetPath(path);
            if (!File.Exists(full) || !File.Exists(full + ".meta") || new FileInfo(full).Length > 1048576 || new FileInfo(full + ".meta").Length > 1048576)
                throw new ArgumentException("Atlas source/metadata must exist and each fit 1 MiB.");
            Object[] packables = Packables(atlas);
            string[] dependencies = AssetDatabase.GetDependencies(path, true);
            if (dependencies.Length > 512)
                throw new ArgumentException("Atlas dependency scope exceeds 512 assets.");
            string loaded =
                EditorJsonUtility.ToJson(importer)
                + string.Join("\n", packables.Select(Key))
                + EditorSettings.spritePackerMode
                + EditorUserBuildSettings.activeBuildTarget;
            if (Encoding.UTF8.GetByteCount(loaded) > 262144)
                throw new ArgumentException("Atlas loaded settings exceed 256 KiB.");
            using (var sha = SHA256.Create())
            using (var source = File.OpenRead(full))
            using (var metadata = File.OpenRead(full + ".meta"))
            {
                string state =
                    Key(atlas)
                    + AssetDatabase.GetAssetDependencyHash(path)
                    + Convert.ToBase64String(sha.ComputeHash(source))
                    + Convert.ToBase64String(sha.ComputeHash(metadata))
                    + loaded;
                return Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(state)));
            }
        }

        internal static void Guard(JObject request, string path)
        {
            JObject current = Reference(Load(path));
            if (Text(request, "guid") != current.Value<string>("guid") || Text(request, "file_id") != current.Value<string>("file_id"))
                throw new ArgumentException("Atlas GUID/fileID identity drifted; read again before authoring.");
            if (Text(request, "expected_revision") != Revision(path))
                throw new ArgumentException("Atlas/source/dependency revision drifted; read again before authoring.");
        }

        internal static string[] RequestedPlatforms(JObject request)
        {
            if (request["platforms"] == null)
                return new[] { "Standalone" };
            if (
                !(request["platforms"] is JArray list)
                || list.Count > 4
                || list.Any(x => x.Type != JTokenType.String || !Platforms.Contains(x.Value<string>()))
                || list.Select(x => x.Value<string>()).Distinct().Count() != list.Count
            )
                throw new ArgumentException("platforms requires distinct Standalone/Android/iPhone/WebGL names, at most four.");
            return list.Values<string>().ToArray();
        }

        internal static JObject Read(JObject request, string path)
        {
#if UNITY_2022_1_OR_NEWER
            var atlas = Load(path);
            var importer = (SpriteAtlasImporter)AssetImporter.GetAtPath(path);
            Object[] packables = Packables(atlas);
            int offset = Whole(request, "offset", 0, 0, packables.Length),
                size = Whole(request, "page_size", 50, 1, 100);
            string revision = Revision(path);
            if (offset > 0 && Text(request, "expected_revision") != revision)
                throw new ArgumentException("Atlas continuation revision drifted; restart the read.");
            var rows = new JArray(
                packables
                    .Skip(offset)
                    .Take(size)
                    .Select(value => new JObject
                    {
                        ["reference"] = Reference(value),
                        ["name"] = value.name,
                        ["path"] = AssetDatabase.GetAssetPath(value),
                        ["type"] = value.GetType().Name,
                        ["scoped_pack_supported"] = value is Sprite,
                    })
            );
            string[] profiles = RequestedPlatforms(request);
            var platforms = new JObject { ["DefaultTexturePlatform"] = AtlasSettings.ReadPlatform(importer.GetPlatformSettings("DefaultTexturePlatform")) };
            foreach (string profile in profiles)
                platforms[profile] = AtlasSettings.ReadPlatform(importer.GetPlatformSettings(profile));
            SpriteAtlas master = atlas.GetMasterAtlas();
            var data = new JObject
            {
                ["path"] = path,
                ["reference"] = Reference(atlas),
                ["revision"] = revision,
                ["is_variant"] = atlas.isVariant,
                ["master"] = master != null ? Reference(master) : (JToken)JValue.CreateNull(),
                ["variant_scale"] = importer.variantScale,
                ["include_in_build"] = importer.includeInBuild,
                ["packing"] = AtlasSettings.ReadPacking(importer.packingSettings),
                ["texture"] = AtlasSettings.ReadTexture(importer.textureSettings),
                ["platforms"] = platforms,
                ["platform_values"] = "stored profiles; nonoverridden profiles inherit defaults during native packing",
                ["packable_count"] = packables.Length,
                ["packables"] = rows,
                ["next_offset"] = offset + rows.Count < packables.Length ? new JValue(offset + rows.Count) : JValue.CreateNull(),
                ["packed_sprite_count"] = atlas.spriteCount,
                ["packer_mode"] = EditorSettings.spritePackerMode.ToString(),
                ["authoring_format"] = "V2",
            };
            if (Encoding.UTF8.GetByteCount(data.ToString(Formatting.None)) > 262144)
                throw new ArgumentException("Atlas read exceeds 256 KiB; request a smaller page_size.");
            return data;
#else
            throw ImporterSettingsUnavailable();
#endif
        }

        internal static int Whole(JObject values, string key, int fallback, int min, int max)
        {
            if (values[key] == null)
                return fallback;
            if (values[key].Type != JTokenType.Integer || !int.TryParse(values[key].ToString(), out int result) || result < min || result > max)
                throw new ArgumentException(key + " must be an integer between " + min + " and " + max + ".");
            return result;
        }

        internal static bool PackingEnabled =>
            EditorSettings.spritePackerMode.ToString() == "SpriteAtlasV2" || EditorSettings.spritePackerMode.ToString() == "SpriteAtlasV2Build";
    }
}
