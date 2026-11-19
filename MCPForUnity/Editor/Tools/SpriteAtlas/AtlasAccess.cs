using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.AssetImporters;
using UnityEditor.U2D;
using UnityEngine;
using UnityEngine.U2D;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.SpriteAtlasTools
{
    internal static class AtlasAccess
    {
        internal static readonly string[] Platforms = { "Standalone", "Android", "iPhone", "WebGL" };
        private static readonly string[] V1PackerModes = NativePackerModes("AlwaysOnAtlas", "BuildTimeOnlyAtlas");
        private static readonly string[] V2PackerModes = NativePackerModes("SpriteAtlasV2", "SpriteAtlasV2Build");
        private static readonly Dictionary<string, bool> PreviewRevisions = new Dictionary<string, bool>(StringComparer.Ordinal);
        private static readonly Queue<string> PreviewRevisionOrder = new Queue<string>();
        private static readonly Lazy<DateTime> EditorStartUtc = new Lazy<DateTime>(() =>
        {
            using var current = Process.GetCurrentProcess();
            return current.StartTime.ToUniversalTime();
        });
        private static bool previewUpdatePending;

        private static string[] NativePackerModes(params string[] supported) =>
            supported.Where(mode => Enum.IsDefined(typeof(SpritePackerMode), mode)).ToArray();

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
            string path = AssetPathUtility.GetContainedAssetPath(Text(values, "path"));
            if (string.IsNullOrEmpty(path) || (!IsV2(path) && !path.EndsWith(".spriteatlas", StringComparison.Ordinal)))
                throw new ArgumentException("A contained Assets/*.spriteatlas or Assets/*.spriteatlasv2 path is required.");
            AssetPathUtility.GetFullAssetPath(path);
            return path;
        }

        internal static bool IsV2(string path) => path.EndsWith(".spriteatlasv2", StringComparison.Ordinal);

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
            bool v2 = IsV2(path);
            var importer = AssetImporter.GetAtPath(path);
            if (importer == null || (importer is SpriteAtlasImporter) != v2 || (!v2 && !path.EndsWith(".spriteatlas", StringComparison.Ordinal)))
                throw new ArgumentException(
                    v2 ? "A saved SpriteAtlas V2 asset with SpriteAtlasImporter is required." : "A saved native SpriteAtlas V1 asset is required."
                );
            var atlas = AssetDatabase.LoadAssetAtPath<SpriteAtlas>(path);
            return atlas != null && (v2 || AssetDatabase.IsMainAsset(atlas))
                ? atlas
                : throw new ArgumentException(
                    v2
                        ? "The V2 importer did not generate a SpriteAtlas. Inspect importer capability and packing mode."
                        : "The V1 path has no native SpriteAtlas main asset."
                );
        }

        internal static SpriteAtlasAsset LoadSource(string path)
        {
            var source = SpriteAtlasAsset.Load(path);
            return source != null ? source : throw new ArgumentException("V2 authoring source could not be loaded.");
        }

        internal static TResult WithSettings<TResult>(
            string path,
            SpriteAtlas atlas,
            AtlasSettings.ImporterApi api,
            Func<AtlasSettings.NativeSettings, TResult> use
        )
        {
            if (!IsV2(path))
                return use(new AtlasSettings.NativeSettings(atlas));
            if (!api.UsesSource)
                return use(new AtlasSettings.NativeSettings((SpriteAtlasImporter)AssetImporter.GetAtPath(path), api));
            var source = LoadSource(path);
            try
            {
                return use(new AtlasSettings.NativeSettings(source, api));
            }
            finally
            {
                Object.DestroyImmediate(source);
            }
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

        private static bool CanExpand(Object value) =>
            value is Sprite
            || (value is Texture2D && AssetImporter.GetAtPath(AssetDatabase.GetAssetPath(value)) is TextureImporter { textureType: TextureImporterType.Sprite })
            || AssetDatabase.IsValidFolder(AssetDatabase.GetAssetPath(value));

        internal static Sprite[] ExpandPackables(Object[] packables)
        {
            var sprites = new Dictionary<string, Sprite>(StringComparer.Ordinal);
            var textures = new HashSet<string>(StringComparer.Ordinal);
            int visitedEntries = 0;

            void AddSprite(Sprite sprite)
            {
                sprites[Key(sprite)] = sprite;
                if (sprites.Count > 256)
                    throw new ArgumentException("Scoped packing supports at most 256 resolved Sprites.");
            }

            void AddTexture(string path, bool required)
            {
                AssetImporter importer = AssetImporter.GetAtPath(path);
                if (importer is not TextureImporter { textureType: TextureImporterType.Sprite })
                {
                    if (importer is ScriptedImporter || AssetDatabase.GetMainAssetTypeAtPath(path) == typeof(Sprite))
                        throw new ArgumentException("Scoped preview cannot expand non-TextureImporter Sprite sources.");
                    if (required)
                        throw new ArgumentException("Texture packables must be imported as Sprite textures.");
                    return;
                }
                if (!textures.Add(path))
                    return;
                Object[] entries = AssetDatabase.LoadAllAssetsAtPath(path);
                if (entries.Length > 257)
                    throw new ArgumentException("A packable texture exceeds the bounded Sprite subasset scope.");
                Sprite[] found = entries.OfType<Sprite>().ToArray();
                if (found.Length == 0)
                    throw new ArgumentException("An imported Sprite texture has no resolved source Sprites.");
                foreach (Sprite sprite in found)
                    AddSprite(sprite);
            }

            foreach (Object value in packables)
            {
                string path = AssetDatabase.GetAssetPath(value);
                if (value is Sprite sprite)
                    AddSprite(sprite);
                else if (value is Texture2D)
                    AddTexture(path, true);
                else if (AssetDatabase.IsValidFolder(path))
                {
                    var folders = new Stack<string>();
                    folders.Push(path);
                    while (folders.Count > 0)
                    {
                        string parent = folders.Pop();
                        foreach (string full in Directory.EnumerateFileSystemEntries(AssetPathUtility.GetFullAssetPath(parent)))
                        {
                            if (++visitedEntries > 1024)
                                throw new ArgumentException("Scoped pack folder inspection exceeds 1024 filesystem entries.");
                            FileAttributes attributes = File.GetAttributes(full);
                            if ((attributes & FileAttributes.ReparsePoint) != 0)
                                throw new ArgumentException("Scoped pack folders cannot contain symbolic links or junctions.");
                            string child = parent + "/" + System.IO.Path.GetFileName(full);
                            if ((attributes & FileAttributes.Directory) != 0)
                            {
                                if (AssetDatabase.IsValidFolder(child))
                                    folders.Push(child);
                            }
                            else if (!child.EndsWith(".meta", StringComparison.OrdinalIgnoreCase))
                                AddTexture(child, false);
                        }
                    }
                }
                else
                    throw new ArgumentException("Scoped packables must be Sprites, imported Sprite textures or folders contained in Assets.");
            }
            return sprites.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => pair.Value).ToArray();
        }

        private static string ExpandedDependencies(SpriteAtlas atlas, Object[] ownPackables)
        {
            try
            {
                SpriteAtlas master = atlas.isVariant ? atlas.GetMasterAtlas() : null;
                Object[] packables = master != null ? Packables(master) : ownPackables;
                if (packables.All(value => value is Sprite))
                    return string.Empty;
                return string.Join(
                    "\n",
                    ExpandPackables(packables)
                        .Select(AssetDatabase.GetAssetPath)
                        .Distinct(StringComparer.Ordinal)
                        .OrderBy(path => path, StringComparer.Ordinal)
                        .Select(path => path + "=" + AssetDatabase.GetAssetDependencyHash(path))
                );
            }
            catch (ArgumentException)
            {
                // Settings and membership inspection remain available when the expanded preview scope is unsupported.
                return "preview_source_scope_unavailable";
            }
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
            Object authoring = IsV2(path) ? (Object)AssetImporter.GetAtPath(path) : atlas;
            string full = AssetPathUtility.GetFullAssetPath(path);
            if (!File.Exists(full) || !File.Exists(full + ".meta") || new FileInfo(full).Length > 1048576 || new FileInfo(full + ".meta").Length > 1048576)
                throw new ArgumentException("Atlas source/metadata must exist and each fit 1 MiB.");
            Object[] packables = Packables(atlas);
            string[] dependencies = AssetDatabase.GetDependencies(path, true);
            if (dependencies.Length > 512)
                throw new ArgumentException("Atlas dependency scope exceeds 512 assets.");
            string loaded =
                EditorJsonUtility.ToJson(authoring)
                + string.Join("\n", packables.Select(Key))
                + ExpandedDependencies(atlas, packables)
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

        internal static JObject Read(JObject request, string path, AtlasSettings.ImporterApi api)
        {
            var atlas = Load(path);
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
                        ["scoped_pack_supported"] = CanExpand(value),
                    })
            );
            string[] profiles = RequestedPlatforms(request);
            JObject data = WithSettings(path, atlas, api, native =>
            {
                float? scale = native.Scale;
                var platforms = new JObject { ["DefaultTexturePlatform"] = AtlasSettings.ReadPlatform(native.Platform("DefaultTexturePlatform")) };
                foreach (string profile in profiles)
                    platforms[profile] = AtlasSettings.ReadPlatform(native.Platform(profile));
                return new JObject
                {
                    ["variant_scale"] = scale.HasValue ? new JValue(scale.Value) : JValue.CreateNull(),
                    ["include_in_build"] = native.Include,
                    ["packing"] = AtlasSettings.ReadPacking(native.Packing),
                    ["texture"] = AtlasSettings.ReadTexture(native.Texture),
                    ["platforms"] = platforms,
                    ["platform_values"] = "stored profiles; nonoverridden profiles inherit defaults during native packing",
                };
            });
            SpriteAtlas master = atlas.GetMasterAtlas();
            data["path"] = path;
            data["reference"] = Reference(atlas);
            data["revision"] = revision;
            data["is_variant"] = atlas.isVariant;
            data["master"] = master != null ? Reference(master) : (JToken)JValue.CreateNull();
            data["packable_count"] = packables.Length;
            data["packables"] = rows;
            data["next_offset"] = offset + rows.Count < packables.Length ? new JValue(offset + rows.Count) : JValue.CreateNull();
            data["packed_sprite_count"] = atlas.spriteCount;
            data["packer_mode"] = EditorSettings.spritePackerMode.ToString();
            data["authoring_format"] = IsV2(path) ? "V2" : "V1";
            if (Encoding.UTF8.GetByteCount(data.ToString(Formatting.None)) > 262144)
                throw new ArgumentException("Atlas read exceeds 256 KiB; request a smaller page_size.");
            if (!IsV2(path))
            {
                data["preview_atlas_established"] = IsEstablishedV1Atlas(path);
                data["preview_scope_settled"] = ObserveV1PreviewRevision(revision);
            }
            return data;
        }

        internal static int Whole(JObject values, string key, int fallback, int min, int max)
        {
            if (values[key] == null)
                return fallback;
            if (values[key].Type != JTokenType.Integer || !int.TryParse(values[key].ToString(), out int result) || result < min || result > max)
                throw new ArgumentException(key + " must be an integer between " + min + " and " + max + ".");
            return result;
        }

        internal static string[] PackerModes(bool v2) => v2 ? V2PackerModes : V1PackerModes;

        internal static bool PackingEnabled(bool v2) => PackerModes(v2).Contains(EditorSettings.spritePackerMode.ToString());

        internal static bool V1AuthoringMode =>
            PackingEnabled(false) || EditorSettings.spritePackerMode.ToString() is "Disabled" or "AlwaysOn" or "BuildTimeOnly";

        internal static bool V1PreviewRequiresEditorUpdate => !AtlasSettings.ImporterApi.Native.UsesSource;

        internal static bool V1PreviewProviderAvailable => !V1PreviewRequiresEditorUpdate || V1PreviewProvider.Find() != null;

        internal static bool IsEstablishedV1Atlas(string path)
        {
            if (!V1PreviewRequiresEditorUpdate)
                return true;
            string guid = AssetDatabase.AssetPathToGUID(path);
            if (AtlasPreviewImportWatcher.WasAtlasCreated(guid))
                return false;
            if (AtlasPreviewImportWatcher.IsEstablishedAtlas(guid))
                return true;
            string full = AssetPathUtility.GetFullAssetPath(path);
            return PredatesEditor(full) && PredatesEditor(full + ".meta") && AtlasPreviewImportWatcher.TryRememberEstablishedAtlas(guid);
        }

        internal static void RequireEstablishedV1Atlas(string path)
        {
            if (!IsEstablishedV1Atlas(path))
                throw new V1PreviewUnavailableException(
                    "v1_preview_atlas_requires_reopen",
                    "The V1 atlas or its master has no established native backing in this Editor session. Reopen the project before previewing."
                );
        }

        internal static void RequireEstablishedV1Source(string path)
        {
            if (AtlasPreviewImportWatcher.ImportHistoryOverflowed)
                throw new V1PreviewUnavailableException(
                    "v1_preview_source_requires_reopen",
                    "The Editor session exceeded the V1 preview source import-history limit. Reopen the project before previewing."
                );
            string full = AssetPathUtility.GetFullAssetPath(path);
            if (AtlasPreviewImportWatcher.WasImportedAfterStartup(path) || !PredatesEditor(full) || !PredatesEditor(full + ".meta"))
                throw new V1PreviewUnavailableException(
                    "v1_preview_source_requires_reopen",
                    "A V1 Sprite source was imported or changed during this Editor session. Reopen the project before previewing these sources."
                );
        }

        private static bool PredatesEditor(string path)
        {
            var info = new FileInfo(path);
            return info.Exists && info.CreationTimeUtc < EditorStartUtc.Value && info.LastWriteTimeUtc < EditorStartUtc.Value;
        }

        internal sealed class V1PreviewProvider
        {
            private readonly ConstructorInfo factory;
            private readonly MethodInfo create;
            private readonly Type providerType;
            private readonly PropertyInfo target;
            private object instance;

            private V1PreviewProvider(ConstructorInfo factory, MethodInfo create, Type providerType, PropertyInfo target)
            {
                this.factory = factory;
                this.create = create;
                this.providerType = providerType;
                this.target = target;
            }

            internal static V1PreviewProvider Find()
            {
                const string prefix = "UnityEditor.U2D.Sprites.";
                var assembly = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(value => value.GetName().Name == "Unity.2D.Sprite.Editor");
                var factoryType = assembly?.GetType(prefix + "TextureImporterDataProviderFactory");
                var providerType = assembly?.GetType(prefix + "TextureImporterDataProvider");
                var contract = assembly?.GetType(prefix + "ISpriteEditorDataProvider");
                var factory = factoryType?.GetConstructor(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance, null, Type.EmptyTypes, null);
                var create = factoryType?.GetMethod("CreateDataProvider", new[] { typeof(Sprite) });
                var target = contract?.GetProperty("targetObject");
                return
                    factory != null
                    && contract != null
                    && create?.ReturnType == contract
                    && !create.IsStatic
                    && create.GetParameters()[0].ParameterType == typeof(Sprite)
                    && providerType != null
                    && contract.IsAssignableFrom(providerType)
                    && target?.GetGetMethod() != null
                    && target.PropertyType == typeof(Object)
                    ? new V1PreviewProvider(factory, create, providerType, target)
                    : null;
            }

            internal void RequireSource(Sprite sprite, TextureImporter importer)
            {
                instance ??= factory.Invoke(null);
                object provider = create.Invoke(instance, new object[] { sprite });
                if (provider?.GetType() != providerType || (Object)target.GetValue(provider) != importer)
                    throw new V1PreviewUnavailableException(
                        "v1_preview_source_provider_mismatch",
                        "The Sprite source does not resolve to Unity's native V1 preview TextureImporter provider."
                    );
            }
        }

        internal static bool ObserveV1PreviewRevision(string revision)
        {
            if (!V1PreviewRequiresEditorUpdate)
                return true;
            if (PreviewRevisions.TryGetValue(revision, out bool settled))
                return settled;
            if (PreviewRevisions.Count == 128)
                PreviewRevisions.Remove(PreviewRevisionOrder.Dequeue());
            PreviewRevisions.Add(revision, false);
            PreviewRevisionOrder.Enqueue(revision);
            if (!previewUpdatePending)
            {
                previewUpdatePending = true;
                EditorApplication.update += CompletePreviewUpdate;
            }
            return false;
        }

        private static void CompletePreviewUpdate()
        {
            if (
                EditorApplication.isUpdating
                || EditorApplication.isCompiling
                || EditorApplication.isPlayingOrWillChangePlaymode
                || BuildPipeline.isBuildingPlayer
            )
                return;
            EditorApplication.update -= CompletePreviewUpdate;
            previewUpdatePending = false;
            // Importer-backed Editors can have complete primary artifacts before V1's native pack inputs are ready.
            foreach (string revision in PreviewRevisionOrder)
                PreviewRevisions[revision] = true;
        }

        internal static void InvalidatePreviewRevisions()
        {
            if (PreviewRevisions.Count == 0)
                return;
            PreviewRevisions.Clear();
            PreviewRevisionOrder.Clear();
            EditorApplication.update -= CompletePreviewUpdate;
            previewUpdatePending = false;
        }

        internal static string PackingUnavailableReason(bool v2)
        {
            string format = v2 ? "V2" : "V1";
            string[] modes = PackerModes(v2);
            if (modes.Length == 0)
                return $"The Editor does not expose a compatible {format} Sprite Packer mode.";
            if (!PackingEnabled(v2))
                return $"Scoped {format} preview packing requires {string.Join(" or ", modes)}. The current mode is "
                    + EditorSettings.spritePackerMode
                    + ". This tool does not change global packing settings.";
            if (!v2 && !V1PreviewProviderAvailable)
                return "This native V1 preview path requires Unity's 2D Sprite TextureImporter provider.";
            BuildTarget target = EditorUserBuildSettings.activeBuildTarget;
            return BuildPipeline.IsBuildTargetSupported(BuildPipeline.GetBuildTargetGroup(target), target)
                ? null
                : "The selected active target's native build support is unavailable.";
        }
    }

    internal sealed class V1PreviewUnavailableException : ArgumentException
    {
        internal readonly string Code;

        internal V1PreviewUnavailableException(string code, string message)
            : base(message) => Code = code;
    }

    internal sealed class AtlasPreviewImportWatcher : AssetPostprocessor
    {
        private const string StartupKey = "MCPForUnity.SpriteAtlas.V1Preview.StartupComplete";
        private const string ImportsKey = "MCPForUnity.SpriteAtlas.V1Preview.ImportedSourceGuids";
        private const string OverflowKey = "MCPForUnity.SpriteAtlas.V1Preview.ImportHistoryOverflow";
        private const string EstablishedAtlasesKey = "MCPForUnity.SpriteAtlas.V1Preview.EstablishedAtlasGuids";
        private const string CreatedAtlasesKey = "MCPForUnity.SpriteAtlas.V1Preview.CreatedAtlasGuids";
        private const string AtlasHistoryOverflowKey = "MCPForUnity.SpriteAtlas.V1Preview.AtlasHistoryOverflow";
        private const int ImportHistoryLimit = 4096;
        private const int AtlasHistoryLimit = 1024;
        private static readonly HashSet<string> ImportedSources = ReadSessionEntries(ImportsKey);
        private static readonly HashSet<string> EstablishedAtlases = ReadSessionEntries(EstablishedAtlasesKey);
        private static readonly HashSet<string> CreatedAtlases = ReadSessionEntries(CreatedAtlasesKey);

        private static HashSet<string> ReadSessionEntries(string key) =>
            new HashSet<string>(SessionState.GetString(key, string.Empty).Split(new[] { '\n' }, StringSplitOptions.RemoveEmptyEntries), StringComparer.Ordinal);

        internal static bool IsEstablishedAtlas(string guid) => EstablishedAtlases.Contains(guid);

        internal static bool WasAtlasCreated(string guid) => SessionState.GetBool(AtlasHistoryOverflowKey, false) || CreatedAtlases.Contains(guid);

        internal static bool TryRememberEstablishedAtlas(string guid)
        {
            if (string.IsNullOrEmpty(guid) || EstablishedAtlases.Count >= AtlasHistoryLimit)
                return false;
            if (EstablishedAtlases.Add(guid))
                SessionState.SetString(EstablishedAtlasesKey, string.Join("\n", EstablishedAtlases));
            return true;
        }

        internal static void RememberCreatedAtlas(string path)
        {
            if (!AtlasAccess.V1PreviewRequiresEditorUpdate)
                return;
            string guid = AssetDatabase.AssetPathToGUID(path);
            if (string.IsNullOrEmpty(guid) || CreatedAtlases.Contains(guid))
                return;
            if (CreatedAtlases.Count >= AtlasHistoryLimit)
                SessionState.SetBool(AtlasHistoryOverflowKey, true);
            else if (CreatedAtlases.Add(guid))
                SessionState.SetString(CreatedAtlasesKey, string.Join("\n", CreatedAtlases));
        }

        [InitializeOnLoadMethod]
        private static void ObserveStartup()
        {
            if (AtlasAccess.V1PreviewRequiresEditorUpdate && !SessionState.GetBool(StartupKey, false))
                EditorApplication.update += CompleteStartup;
        }

        private static void CompleteStartup()
        {
            if (EditorApplication.isUpdating || EditorApplication.isCompiling)
                return;
            EditorApplication.update -= CompleteStartup;
            SessionState.SetBool(StartupKey, true);
        }

        internal static bool ImportHistoryOverflowed => SessionState.GetBool(OverflowKey, false);

        internal static bool WasImportedAfterStartup(string path) => ImportedSources.Contains(AssetDatabase.AssetPathToGUID(path));

        private static void OnPostprocessAllAssets(string[] importedAssets, string[] deletedAssets, string[] movedAssets, string[] movedFromAssetPaths)
        {
            AtlasAccess.InvalidatePreviewRevisions();
            if (!SessionState.GetBool(StartupKey, false) || SessionState.GetBool(OverflowKey, false))
                return;
            bool changed = false;
            foreach (string path in importedAssets.Concat(movedAssets))
            {
                if (!path.StartsWith("Assets/", StringComparison.Ordinal))
                    continue;
                if (path.EndsWith(".spriteatlas", StringComparison.Ordinal))
                {
                    if (!IsEstablishedAtlas(AssetDatabase.AssetPathToGUID(path)))
                        RememberCreatedAtlas(path);
                    continue;
                }
                if (AssetImporter.GetAtPath(path) is not TextureImporter { textureType: TextureImporterType.Sprite })
                    continue;
                string guid = AssetDatabase.AssetPathToGUID(path);
                if (string.IsNullOrEmpty(guid) || !ImportedSources.Add(guid))
                    continue;
                if (ImportedSources.Count > ImportHistoryLimit)
                {
                    SessionState.SetBool(OverflowKey, true);
                    return;
                }
                changed = true;
            }
            if (changed)
                SessionState.SetString(ImportsKey, string.Join("\n", ImportedSources));
        }
    }
}
