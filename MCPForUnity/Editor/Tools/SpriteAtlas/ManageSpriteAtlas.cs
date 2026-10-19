using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.U2D;
using UnityEngine;
using UnityEngine.U2D;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.SpriteAtlasTools
{
    [McpForUnityTool("manage_sprite_atlas", AutoRegister = false, Group = "animation")]
    public static class ManageSpriteAtlas
    {
        private static readonly HashSet<string> SettingsKeys = new HashSet<string>(StringComparer.Ordinal)
        {
            "include_in_build",
            "master",
            "master_revision",
            "variant_scale",
            "packing",
            "texture",
            "platforms",
        };

        public static object HandleCommand(JObject request)
        {
            bool mutationStarted = false;
            try
            {
                if (request == null || request.ToString(Newtonsoft.Json.Formatting.None).Length > 65536)
                    throw new ArgumentException("SpriteAtlas request is required and must fit 64 KiB.");
                string action = AtlasAccess.Text(request, "action");
                if (action == "capabilities")
                    return new SuccessResponse("SpriteAtlas V2 capabilities.", Capabilities());
                AtlasAccess.Stable();
                string path = AtlasAccess.Path(request);
                if (action == "read")
                    return new SuccessResponse("SpriteAtlas V2 state.", AtlasAccess.Read(request, path));
                if (action == "create")
                    return Create(request, path, ref mutationStarted);
                if (!new[] { "configure", "add_packables", "remove_packables", "pack" }.Contains(action))
                    throw new ArgumentException("Unknown SpriteAtlas action; use capabilities/create/read/configure/add_packables/remove_packables/pack.");
                AtlasAccess.Guard(request, path);
                if (action == "pack")
                    return Pack(request, path, ref mutationStarted);
                JObject properties = Properties(request);
                var atlas = AtlasAccess.Load(path);
                var importer = (SpriteAtlasImporter)AssetImporter.GetAtPath(path);
                if (action == "configure")
                {
                    CheckKeys(properties, SettingsKeys);
                    var settings = new AtlasSettings(properties);
                    SpriteAtlas master = Master(properties, path);
                    bool variant = atlas.isVariant || master != null;
                    settings.ValidateVariant(variant);
                    if (master != null && !atlas.isVariant && AtlasAccess.Packables(atlas).Length != 0)
                        throw new ArgumentException("Linking a populated master as a variant is refused; create a separate variant to preserve its packables.");
                    bool sameMaster = master == null || atlas.GetMasterAtlas() == master;
                    if (sameMaster && settings.Matches(importer))
                        return Result(path, false);
                    AtlasAccess.Guard(request, path);
                    ValidateMasterRevision(properties, master);
                    if (!sameMaster)
                    {
                        mutationStarted = true;
                        EditAsset(
                            path,
                            value =>
                            {
                                value.SetIsVariant(true);
                                value.SetMasterAtlas(master);
                            }
                        );
                        importer = (SpriteAtlasImporter)AssetImporter.GetAtPath(path);
                    }
                    mutationStarted = true;
                    settings.Apply(importer);
                    var fresh = AtlasAccess.Load(path);
                    if (!settings.Matches((SpriteAtlasImporter)AssetImporter.GetAtPath(path)) || (master != null && fresh.GetMasterAtlas() != master))
                        throw new InvalidOperationException("Native import did not retain the requested atlas settings/master link.");
                    return Result(path, true);
                }
                CheckKeys(properties, new HashSet<string> { "packables" });
                if (atlas.isVariant)
                    throw new ArgumentException("Variant packables inherit from the master; edit its master explicitly.");
                Object[] wanted = AtlasAccess.RequestedPackables(properties);
                Object[] before = AtlasAccess.Packables(atlas);
                if (before.Any(value => !(value is Sprite)))
                    throw new ArgumentException(
                        "Packable authoring supports direct Sprite lists only; existing folder/texture entries are preserved by refusal."
                    );
                var prior = new HashSet<string>(before.Select(AtlasAccess.Key), StringComparer.Ordinal);
                Object[] delta = wanted
                    .Where(value => action == "add_packables" ? !prior.Contains(AtlasAccess.Key(value)) : prior.Contains(AtlasAccess.Key(value)))
                    .ToArray();
                Object[] expected =
                    action == "add_packables"
                        ? before.Concat(delta).ToArray()
                        : before.Where(value => !delta.Select(AtlasAccess.Key).Contains(AtlasAccess.Key(value))).ToArray();
                if (expected.Length > 256)
                    throw new ArgumentException("Atlas direct Sprite packables cannot exceed 256 entries.");
                if (delta.Length == 0)
                    return Result(path, false);
                string[] expectedIds = expected.Select(AtlasAccess.Key).OrderBy(x => x, StringComparer.Ordinal).ToArray();
                AtlasAccess.Guard(request, path);
                mutationStarted = true;
                EditAsset(
                    path,
                    value =>
                    {
                        if (action == "add_packables")
                            value.Add(delta);
                        else
                            value.Remove(delta);
                    }
                );
                string[] actual = AtlasAccess.Packables(AtlasAccess.Load(path)).Select(AtlasAccess.Key).OrderBy(x => x, StringComparer.Ordinal).ToArray();
                if (!expectedIds.SequenceEqual(actual))
                    throw new InvalidOperationException("Native import did not retain the exact requested packable membership.");
                return Result(path, true);
            }
            catch (Exception error)
                when (error is ArgumentException
                    || error is InvalidOperationException
                    || error is IOException
                    || error is OverflowException
                    || error is UnauthorizedAccessException
                    || error is System.Reflection.TargetInvocationException
                )
            {
                return new ErrorResponse(
                    error.GetBaseException().Message,
                    new
                    {
                        mutation_may_have_occurred = mutationStarted,
                        recovery = mutationStarted
                            ? "Read current state before retrying; native import/pack caches and callbacks are not transactional."
                            : "No authoring or pack call began.",
                    }
                );
            }
        }

        private static JObject Properties(JObject request)
        {
            if (!(request["properties"] is JObject values) || values.Count == 0)
                throw new ArgumentException("A nonempty properties object is required for atlas authoring.");
            return values;
        }

        private static void CheckKeys(JObject values, HashSet<string> allowed)
        {
            if (values.Properties().Any(value => !allowed.Contains(value.Name)))
                throw new ArgumentException("Unsupported SpriteAtlas authoring property.");
            if (values["master_revision"] != null && values["master"] == null)
                throw new ArgumentException("master_revision requires an explicit master reference.");
        }

        private static SpriteAtlas Master(JObject properties, string path)
        {
            if (properties["master"] == null)
                return null;
            var master = (SpriteAtlas)AtlasAccess.Resolve(properties["master"], true);
            if (master.isVariant || AssetDatabase.GetAssetPath(master) == path)
                throw new ArgumentException("Master must be a separate nonvariant V2 atlas; nested variants/cycles are refused.");
            ValidateMasterRevision(properties, master);
            return master;
        }

        private static void ValidateMasterRevision(JObject properties, SpriteAtlas master)
        {
            if (master != null && AtlasAccess.Text(properties, "master_revision") != AtlasAccess.Revision(AssetDatabase.GetAssetPath(master)))
                throw new ArgumentException("Master atlas revision drifted; read the master again before linking.");
        }

        private static object Create(JObject request, string path, ref bool mutationStarted)
        {
            JObject properties = Properties(request);
            var allowed = new HashSet<string>(SettingsKeys, StringComparer.Ordinal) { "packables" };
            CheckKeys(properties, allowed);
            var settings = new AtlasSettings(properties);
            if (!settings.Include.HasValue)
                throw new ArgumentException("create requires explicit include_in_build true or false.");
            string parent = System.IO.Path.GetDirectoryName(path)?.Replace('\\', '/');
            if (!AssetDatabase.IsValidFolder(parent))
                throw new ArgumentException("The atlas parent Assets folder must already exist; folders are not created implicitly.");
            string full = AssetPathUtility.GetFullAssetPath(path);
            if (
                File.Exists(full)
                || Directory.Exists(full)
                || File.Exists(full + ".meta")
                || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets))
            )
                throw new ArgumentException("Atlas creation refuses an existing asset or metadata; overwrite is unsupported.");
            SpriteAtlas master = Master(properties, path);
            Object[] packables = properties["packables"] == null ? Array.Empty<Object>() : AtlasAccess.RequestedPackables(properties);
            if (master != null && packables.Length > 0)
                throw new ArgumentException("A variant inherits packables from its master; own packables are refused.");
            settings.ValidateVariant(master != null);
            var authoring = new SpriteAtlasAsset();
            try
            {
                if (master != null)
                {
                    authoring.SetIsVariant(true);
                    authoring.SetMasterAtlas(master);
                }
                else if (packables.Length > 0)
                    authoring.Add(packables);
                ValidateMasterRevision(properties, master);
                // Recheck the destination immediately before the first durable write.
                if (File.Exists(full) || File.Exists(full + ".meta"))
                    throw new ArgumentException("Another writer created the atlas destination; creation was refused.");
                mutationStarted = true;
                SpriteAtlasAsset.Save(authoring, path);
            }
            finally
            {
                Object.DestroyImmediate(authoring);
            }
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            var importer = AssetImporter.GetAtPath(path) as SpriteAtlasImporter;
            if (importer == null)
                throw new InvalidOperationException("V2 authoring did not create a supported SpriteAtlasImporter.");
            settings.Apply(importer);
            var fresh = AtlasAccess.Load(path);
            if (!settings.Matches((SpriteAtlasImporter)AssetImporter.GetAtPath(path)) || (master != null && fresh.GetMasterAtlas() != master))
                throw new InvalidOperationException("Native import did not retain the requested atlas creation settings.");
            return Result(path, true);
        }

        private static void EditAsset(string path, Action<SpriteAtlasAsset> edit)
        {
            SpriteAtlasAsset source = SpriteAtlasAsset.Load(path);
            if (source == null)
                throw new ArgumentException("V2 authoring source could not be loaded.");
            try
            {
                edit(source);
                SpriteAtlasAsset.Save(source, path);
            }
            finally
            {
                Object.DestroyImmediate(source);
            }
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
        }

        private static object Result(string path, bool changed)
        {
            JObject data = AtlasAccess.Read(new JObject { ["platforms"] = new JArray(AtlasAccess.Platforms) }, path);
            data["changed"] = changed;
            return new SuccessResponse(changed ? "Atlas authoring saved and reimported." : "Atlas request is unchanged; no save or reimport.", data);
        }

        private static object Pack(JObject request, string path, ref bool mutationStarted)
        {
            if (request["properties"] != null && request["properties"].Type != JTokenType.Null)
                throw new ArgumentException("pack accepts no authoring properties; configure explicitly before packing.");
            if (!AtlasAccess.PackingEnabled)
                throw new ArgumentException(
                    "SpriteAtlas V2 packing is disabled or unavailable in the selected global mode; enable it explicitly outside this tool."
                );
            string targetName = AtlasAccess.Text(request, "build_target");
            if (
                !Enum.TryParse(targetName, out BuildTarget target)
                || !Enum.IsDefined(typeof(BuildTarget), target)
                || target != EditorUserBuildSettings.activeBuildTarget
            )
                throw new ArgumentException("Preview pack supports only the explicit active BuildTarget; this tool never switches build targets.");
            if (!BuildPipeline.IsBuildTargetSupported(BuildPipeline.GetBuildTargetGroup(target), target))
                throw new ArgumentException("The selected active target's native build support is unavailable.");
            SpriteAtlas atlas = AtlasAccess.Load(path),
                master = atlas.GetMasterAtlas();
            if (atlas.isVariant && (master == null || master.isVariant))
                throw new ArgumentException("Variant preview requires an existing nonvariant master; nested variants are unsupported.");
            var scope = master != null ? new[] { master, atlas } : new[] { atlas };
            Object[] values = AtlasAccess.Packables(master != null ? master : atlas);
            if (values.Length == 0 || values.Any(value => !(value is Sprite)))
                throw new ArgumentException("Scoped packing requires 1-256 direct Sprite packables; folder/texture expansion is unsupported.");
            var refs = values.Select(AtlasAccess.Reference).ToArray();
            var sourceImages = values.Select(AssetDatabase.GetAssetPath).Distinct().ToArray();
            long pixels = 0;
            foreach (string source in sourceImages)
            {
                var importer = AssetImporter.GetAtPath(source) as TextureImporter;
                if (importer == null)
                    throw new ArgumentException("Scoped packing supports TextureImporter Sprite sources only.");
                importer.GetSourceTextureWidthAndHeight(out int width, out int height);
                if (width < 1 || height < 1 || width > 4096 || height > 4096)
                    throw new ArgumentException("Scoped pack source images must fit 4096 pixels per side.");
                pixels += (long)width * height;
            }
            if (pixels > 16777216)
                throw new ArgumentException("Scoped packing exceeds the 16-megapixel source-image budget.");
            string[] paths = scope.Select(AssetDatabase.GetAssetPath).ToArray();
            AtlasAccess.Guard(request, path);
            mutationStarted = true;
            SpriteAtlasUtility.PackAtlases(scope, target, false);
            SpriteAtlas packed = AtlasAccess.Load(path);
            var bindings = new JArray(
                refs.Select(reference => new JObject
                {
                    ["reference"] = reference,
                    ["can_bind"] = packed.CanBindTo((Sprite)AtlasAccess.Resolve(reference, false)),
                })
            );
            if (packed.spriteCount < 1 || bindings.Any(value => !value.Value<bool>("can_bind")))
                throw new InvalidOperationException("Native preview did not pack/bind every requested source Sprite; packing caches may have changed.");
            JObject data = AtlasAccess.Read(new JObject(), path);
            data["packed_atlases"] = new JArray(
                paths.Select(assetPath => new JObject { ["path"] = assetPath, ["reference"] = AtlasAccess.Reference(AtlasAccess.Load(assetPath)) })
            );
            data["bindings"] = bindings;
            data["build_target"] = target.ToString();
            data["completed"] = true;
            data["interruptible"] = false;
            data["scope"] =
                "editor preview; native import/packing caches may change and source Sprite objects may reload; runtime delivery/build inclusion is not established";
            return new SuccessResponse("Scoped native SpriteAtlas preview packed.", data);
        }

        private static object Capabilities() =>
            new
            {
                authoring_available = true,
                authoring_format = "V2 .spriteatlasv2",
                packing_enabled = AtlasAccess.PackingEnabled,
                packer_mode = EditorSettings.spritePackerMode.ToString(),
                active_build_target = EditorUserBuildSettings.activeBuildTarget.ToString(),
                pack_scope = "one atlas plus its nonvariant master for variants",
                direct_sprite_packables_only = true,
                max_packables = 256,
                max_source_pixels = 16777216,
                max_source_side = 4096,
                platform_profiles = AtlasAccess.Platforms,
                native_pack_interruptible = false,
                runtime_delivery_verified = false,
                persistent_undo_supported = false,
                global_settings_changed = false,
            };
    }
}
