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
        private static readonly string[] Actions = { "create", "read", "configure", "add_packables", "remove_packables", "pack" };
        private static readonly string[] ImporterWrites = { "include_in_build", "variant_scale", "packing", "texture", "platforms" };

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

        public static object HandleCommand(JObject request) => Execute(request, AtlasSettings.ImporterApi.Native);

        internal static object Execute(JObject request, AtlasSettings.ImporterApi api)
        {
            bool mutationStarted = false;
            try
            {
                if (request == null || request.ToString(Newtonsoft.Json.Formatting.None).Length > 65536)
                    throw new ArgumentException("SpriteAtlas request is required and must fit 64 KiB.");
                string action = AtlasAccess.Text(request, "action");
                if (action == "capabilities")
                    return new SuccessResponse("SpriteAtlas capabilities.", Capabilities(api));
                if (!Actions.Contains(action))
                    throw new ArgumentException("Unknown SpriteAtlas action; use capabilities/create/read/configure/add_packables/remove_packables/pack.");
                AtlasAccess.Stable();
                string path = AtlasAccess.Path(request);
                bool v2 = AtlasAccess.IsV2(path);
                string unavailable = ActionUnavailableReason(action, v2, api);
                if (unavailable != null)
                {
                    if (!v2 && action == "pack" && AtlasAccess.PackingEnabled(false) && !AtlasAccess.V1PreviewProviderAvailable)
                        throw new V1PreviewUnavailableException("v1_preview_provider_unavailable", unavailable);
                    throw new ArgumentException(unavailable);
                }
                if (action == "read")
                    return new SuccessResponse("SpriteAtlas state.", AtlasAccess.Read(request, path, api));
                if (action == "create")
                    return Create(request, path, api, ref mutationStarted);
                AtlasAccess.Guard(request, path);
                if (action == "pack")
                    return Pack(request, path, api, ref mutationStarted);
                JObject properties = Properties(request);
                var atlas = AtlasAccess.Load(path);
                if (action == "configure")
                {
                    CheckKeys(properties, SettingsKeys);
                    var settings = new AtlasSettings(properties);
                    settings.RequireWritable(v2 ? api : null);
                    SpriteAtlas master = Master(properties, path);
                    bool variant = atlas.isVariant || master != null;
                    settings.ValidateVariant(variant);
                    if (master != null && !atlas.isVariant && AtlasAccess.Packables(atlas).Length != 0)
                        throw new ArgumentException("Linking a populated master as a variant is refused; create a separate variant to preserve its packables.");
                    bool sameMaster = master == null || atlas.GetMasterAtlas() == master;
                    bool sameSettings = AtlasAccess.WithSettings(path, atlas, api, native =>
                    {
                        settings.RequireScaleReadback(native);
                        return sameMaster && settings.Matches(native);
                    });
                    if (sameSettings)
                        return Result(path, false, api);
                    AtlasAccess.Guard(request, path);
                    ValidateMasterRevision(properties, master);
                    if (!v2)
                    {
                        RequireCleanV1(atlas);
                        mutationStarted = true;
                        if (!sameMaster)
                        {
                            atlas.SetIsVariant(true);
                            atlas.SetMasterAtlas(master);
                        }
                        settings.Apply(new AtlasSettings.NativeSettings(atlas));
                        SaveV1(atlas, path);
                    }
                    else
                    {
                        if (!sameMaster || api.UsesSource)
                            EditV2Source(
                                path,
                                source =>
                                {
                                    if (!sameMaster)
                                    {
                                        source.SetIsVariant(true);
                                        source.SetMasterAtlas(master);
                                    }
                                    if (api.UsesSource)
                                        settings.Apply(new AtlasSettings.NativeSettings(source, api));
                                },
                                ref mutationStarted
                            );
                        if (!api.UsesSource)
                            SaveV2Importer(path, api, settings, ref mutationStarted);
                    }
                    var fresh = AtlasAccess.Load(path);
                    if (!AtlasAccess.WithSettings(path, fresh, api, settings.Matches) || (master != null && fresh.GetMasterAtlas() != master))
                        throw new InvalidOperationException("Native import did not retain the requested atlas settings/master link.");
                    return Result(path, true, api);
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
                    return Result(path, false, api);
                string[] expectedIds = expected.Select(AtlasAccess.Key).OrderBy(x => x, StringComparer.Ordinal).ToArray();
                AtlasAccess.Guard(request, path);
                if (v2)
                {
                    EditV2Source(
                        path,
                        source =>
                        {
                            if (action == "add_packables")
                                source.Add(delta);
                            else
                                source.Remove(delta);
                        },
                        ref mutationStarted
                    );
                }
                else
                {
                    RequireCleanV1(atlas);
                    mutationStarted = true;
                    if (action == "add_packables")
                        atlas.Add(delta);
                    else
                        atlas.Remove(delta);
                    SaveV1(atlas, path);
                }
                string[] actual = AtlasAccess.Packables(AtlasAccess.Load(path)).Select(AtlasAccess.Key).OrderBy(x => x, StringComparer.Ordinal).ToArray();
                if (!expectedIds.SequenceEqual(actual))
                    throw new InvalidOperationException("Native import did not retain the exact requested packable membership.");
                return Result(path, true, api);
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
                var details = new JObject
                {
                    ["mutation_may_have_occurred"] = mutationStarted,
                    ["recovery"] = mutationStarted
                        ? "Read current state before retrying; native import/pack caches and callbacks are not transactional."
                        : "No authoring or pack call began.",
                };
                if (error is V1PreviewUnavailableException previewUnavailable)
                    details["code"] = previewUnavailable.Code;
                return new ErrorResponse(error.GetBaseException().Message, details);
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
                throw new ArgumentException("Master must be a separate nonvariant SpriteAtlas. Nested variants and cycles are refused.");
            if (AtlasAccess.IsV2(AssetDatabase.GetAssetPath(master)) != AtlasAccess.IsV2(path))
                throw new ArgumentException("A variant and its master must use the same SpriteAtlas format.");
            ValidateMasterRevision(properties, master);
            return master;
        }

        private static void ValidateMasterRevision(JObject properties, SpriteAtlas master)
        {
            if (master != null && AtlasAccess.Text(properties, "master_revision") != AtlasAccess.Revision(AssetDatabase.GetAssetPath(master)))
                throw new ArgumentException("Master atlas revision drifted; read the master again before linking.");
        }

        private static object Create(JObject request, string path, AtlasSettings.ImporterApi api, ref bool mutationStarted)
        {
            JObject properties = Properties(request);
            var allowed = new HashSet<string>(SettingsKeys, StringComparer.Ordinal) { "packables" };
            CheckKeys(properties, allowed);
            var settings = new AtlasSettings(properties);
            if (!settings.Include.HasValue)
                throw new ArgumentException("create requires explicit include_in_build true or false.");
            bool v2 = AtlasAccess.IsV2(path);
            settings.RequireWritable(v2 ? api : null);
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
            void RecheckDestination()
            {
                ValidateMasterRevision(properties, master);
                if (File.Exists(full) || Directory.Exists(full) || File.Exists(full + ".meta"))
                    throw new ArgumentException("Another writer created the atlas destination; creation was refused.");
            }
            if (v2)
                CreateV2(path, api, settings, master, packables, RecheckDestination, ref mutationStarted);
            else
                CreateV1(path, settings, master, packables, RecheckDestination, ref mutationStarted);
            var fresh = AtlasAccess.Load(path);
            if (!AtlasAccess.WithSettings(path, fresh, api, settings.Matches) || (master != null && fresh.GetMasterAtlas() != master))
                throw new InvalidOperationException("Native import did not retain the requested atlas creation settings.");
            if (
                master == null
                && !packables
                    .Select(AtlasAccess.Key)
                    .OrderBy(x => x, StringComparer.Ordinal)
                    .SequenceEqual(AtlasAccess.Packables(fresh).Select(AtlasAccess.Key).OrderBy(x => x, StringComparer.Ordinal))
            )
                throw new InvalidOperationException("Native import did not retain the requested atlas creation packables.");
            return Result(path, true, api);
        }

        private static void CreateV1(string path, AtlasSettings settings, SpriteAtlas master, Object[] packables, Action recheck, ref bool mutationStarted)
        {
            var atlas = new SpriteAtlas();
            try
            {
                var native = new AtlasSettings.NativeSettings(atlas);
                settings.RequireScaleReadback(native);
                if (master != null)
                {
                    atlas.SetIsVariant(true);
                    atlas.SetMasterAtlas(master);
                }
                else if (packables.Length > 0)
                    atlas.Add(packables);
                settings.Apply(native);
                recheck();
                mutationStarted = true;
                AssetDatabase.CreateAsset(atlas, path);
                AtlasPreviewImportWatcher.RememberCreatedAtlas(path);
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            }
            finally
            {
                if (atlas != null && !EditorUtility.IsPersistent(atlas))
                    Object.DestroyImmediate(atlas);
            }
        }

        private static void CreateV2(
            string path,
            AtlasSettings.ImporterApi api,
            AtlasSettings settings,
            SpriteAtlas master,
            Object[] packables,
            Action recheck,
            ref bool mutationStarted
        )
        {
            var authoring = new SpriteAtlasAsset();
            try
            {
                var native = new AtlasSettings.NativeSettings(authoring, api);
                if (api.UsesSource)
                    settings.RequireScaleReadback(native);
                if (master != null)
                {
                    authoring.SetIsVariant(true);
                    authoring.SetMasterAtlas(master);
                }
                else if (packables.Length > 0)
                    authoring.Add(packables);
                if (api.UsesSource)
                    settings.Apply(native);
                recheck();
                mutationStarted = true;
                SpriteAtlasAsset.Save(authoring, path);
            }
            finally
            {
                Object.DestroyImmediate(authoring);
            }
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            if (!api.UsesSource)
                SaveV2Importer(path, api, settings, ref mutationStarted);
        }

        private static void SaveV2Importer(string path, AtlasSettings.ImporterApi api, AtlasSettings settings, ref bool mutationStarted)
        {
            var importer = AssetImporter.GetAtPath(path) as SpriteAtlasImporter;
            if (importer == null)
                throw new InvalidOperationException("V2 authoring did not create a supported SpriteAtlasImporter.");
            mutationStarted = true;
            settings.Apply(new AtlasSettings.NativeSettings(importer, api));
            EditorUtility.SetDirty(importer);
            importer.SaveAndReimport();
        }

        private static void RequireCleanV1(SpriteAtlas atlas)
        {
            if (EditorUtility.IsDirty(atlas))
                throw new ArgumentException("The V1 atlas has unsaved Editor changes. Save them explicitly and read the atlas again before authoring.");
        }

        private static void SaveV1(SpriteAtlas atlas, string path)
        {
            EditorUtility.SetDirty(atlas);
            AssetDatabase.SaveAssetIfDirty(atlas);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
        }

        private static void EditV2Source(string path, Action<SpriteAtlasAsset> edit, ref bool mutationStarted)
        {
            var source = AtlasAccess.LoadSource(path);
            try
            {
                edit(source);
                mutationStarted = true;
                SpriteAtlasAsset.Save(source, path);
            }
            finally
            {
                Object.DestroyImmediate(source);
            }
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
        }

        private static object Result(string path, bool changed, AtlasSettings.ImporterApi api)
        {
            JObject data = AtlasAccess.Read(new JObject { ["platforms"] = new JArray(AtlasAccess.Platforms) }, path, api);
            data["changed"] = changed;
            return new SuccessResponse(changed ? "Atlas authoring saved and reimported." : "Atlas request is unchanged; no save or reimport.", data);
        }

        private static object Pack(JObject request, string path, AtlasSettings.ImporterApi api, ref bool mutationStarted)
        {
            if (request["properties"] != null && request["properties"].Type != JTokenType.Null)
                throw new ArgumentException("pack accepts no authoring properties; configure explicitly before packing.");
            bool v2 = AtlasAccess.IsV2(path);
            void RequireReadyPreviewRevision()
            {
                if (!v2 && !AtlasAccess.ObserveV1PreviewRevision(AtlasAccess.Text(request, "expected_revision")))
                    throw new V1PreviewUnavailableException(
                        "v1_preview_scope_unsettled",
                        "V1 preview is waiting for a completed Editor update after observing this revision. Retry after Unity settles."
                    );
            }
            string targetName = AtlasAccess.Text(request, "build_target");
            if (
                !Enum.TryParse(targetName, out BuildTarget target)
                || !Enum.IsDefined(typeof(BuildTarget), target)
                || target != EditorUserBuildSettings.activeBuildTarget
            )
                throw new ArgumentException("Preview pack supports only the explicit active BuildTarget; this tool never switches build targets.");
            RequireReadyPreviewRevision();
            SpriteAtlas atlas = AtlasAccess.Load(path),
                master = atlas.GetMasterAtlas();
            if (atlas.isVariant && (master == null || master.isVariant))
                throw new ArgumentException("Variant preview requires an existing nonvariant master; nested variants are unsupported.");
            if (master != null)
            {
                string masterPath = AssetDatabase.GetAssetPath(master);
                if (AssetPathUtility.GetContainedAssetPath(masterPath) == null || AtlasAccess.IsV2(masterPath) != AtlasAccess.IsV2(path))
                    throw new ArgumentException("Variant preview requires a saved Assets master of the same SpriteAtlas format.");
                AssetPathUtility.GetFullAssetPath(masterPath);
                if (AtlasAccess.Load(masterPath) != master)
                    throw new ArgumentException("Variant preview could not verify the current master atlas identity.");
            }
            var scope = master != null ? new[] { master, atlas } : new[] { atlas };
            Sprite[] values = AtlasAccess.ExpandPackables(AtlasAccess.Packables(master != null ? master : atlas));
            if (values.Length == 0)
                throw new ArgumentException("Scoped packing requires 1-256 resolved Sprite sources.");
            var refs = values.Select(AtlasAccess.Reference).ToArray();
            var sourceImages = values.GroupBy(AssetDatabase.GetAssetPath, StringComparer.Ordinal);
            var provider =
                !v2 && AtlasAccess.V1PreviewRequiresEditorUpdate
                    ? AtlasAccess.V1PreviewProvider.Find()
                        ?? throw new V1PreviewUnavailableException(
                            "v1_preview_provider_unavailable",
                            "The live V1 preview TextureImporter provider is unavailable."
                        )
                    : null;
            long pixels = 0;
            foreach (var source in sourceImages)
            {
                var importer = AssetImporter.GetAtPath(source.Key) as TextureImporter;
                if (importer == null)
                    throw new ArgumentException("Scoped packing supports TextureImporter Sprite sources only.");
                if (provider != null)
                {
                    AtlasAccess.RequireEstablishedV1Source(source.Key);
                    provider.RequireSource(source.First(), importer);
                }
                importer.GetSourceTextureWidthAndHeight(out int width, out int height);
                if (width < 1 || height < 1 || width > 4096 || height > 4096)
                    throw new ArgumentException("Scoped pack source images must fit 4096 pixels per side.");
                pixels += (long)width * height;
            }
            if (pixels > 16777216)
                throw new ArgumentException("Scoped packing exceeds the 16-megapixel source-image budget.");
            string[] paths = scope.Select(AssetDatabase.GetAssetPath).ToArray();
            if (provider != null)
                foreach (string scopedPath in paths)
                    AtlasAccess.RequireEstablishedV1Atlas(scopedPath);
            AtlasAccess.Guard(request, path);
            RequireReadyPreviewRevision();
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
            if (packed.spriteCount != refs.Length || bindings.Any(value => !value.Value<bool>("can_bind")))
                throw new InvalidOperationException("Native preview did not retain the exact resolved Sprite scope. Packing caches may have changed.");
            JObject data = AtlasAccess.Read(new JObject(), path, api);
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

        private static string ActionUnavailableReason(string action, bool v2, AtlasSettings.ImporterApi api)
        {
            if (v2 && api.ReadUnavailableReason != null)
                return api.ReadUnavailableReason;
            if (action == "pack")
                return AtlasAccess.PackingUnavailableReason(v2);
            if (!v2 && action != "read" && !AtlasAccess.V1AuthoringMode)
                return "V1 authoring requires a project mode that will preserve the .spriteatlas file. This tool never changes the mode.";
            return v2 && action == "create" ? api.WriteUnavailableReason("include_in_build") : null;
        }

        private static object Capabilities(AtlasSettings.ImporterApi api)
        {
            object Describe(bool v2)
            {
                var reasons = Actions.ToDictionary(action => action, action => ActionUnavailableReason(action, v2, api), StringComparer.Ordinal);
                return new
                {
                    path_extension = v2 ? ".spriteatlasv2" : ".spriteatlas",
                    authoring_available = new[] { "create", "configure", "add_packables", "remove_packables" }.Any(action => reasons[action] == null),
                    settings_backend = !v2 ? "SpriteAtlasExtensions"
                    : api.ReadUnavailableReason != null ? null
                    : api.UsesSource ? "SpriteAtlasAsset"
                    : "SpriteAtlasImporter",
                    action_availability = reasons.ToDictionary(
                        pair => pair.Key,
                        pair => new { available = pair.Value == null, unavailable_reason = pair.Value }
                    ),
                    setting_writes = ImporterWrites.ToDictionary(setting => setting, setting => !v2 || api.WriteUnavailableReason(setting) == null),
                    membership_write_packable_types = new[] { "Sprite" },
                    preview_packable_types = new[] { "Sprite", "Texture2D", "Assets folder" },
                    packing_enabled = AtlasAccess.PackingEnabled(v2),
                    preview_requires_editor_update = !v2 && AtlasAccess.V1PreviewRequiresEditorUpdate,
                    preview_sources_must_predate_editor_session = !v2 && AtlasAccess.V1PreviewRequiresEditorUpdate,
                    preview_atlas_must_predate_editor_session = !v2 && AtlasAccess.V1PreviewRequiresEditorUpdate,
                    required_packer_modes = AtlasAccess.PackerModes(v2),
                    packing_unavailable_reason = reasons["pack"],
                };
            }
            var reasons = Actions.ToDictionary(action => action, action => ActionUnavailableReason(action, true, api), StringComparer.Ordinal);
            return new
            {
                legacy_fields_format = "V2",
                authoring_available = api.ReadUnavailableReason == null,
                authoring_unavailable_reason = api.ReadUnavailableReason,
                authoring_format = "V2 .spriteatlasv2",
                formats = new { V1 = Describe(false), V2 = Describe(true) },
                action_availability = reasons.ToDictionary(pair => pair.Key, pair => new { available = pair.Value == null, unavailable_reason = pair.Value }),
                importer_setting_writes = ImporterWrites.ToDictionary(setting => setting, setting => api.WriteUnavailableReason(setting) == null),
                packing_enabled = AtlasAccess.PackingEnabled(true),
                required_packer_modes = AtlasAccess.PackerModes(true),
                packing_unavailable_reason = reasons["pack"],
                packer_mode = EditorSettings.spritePackerMode.ToString(),
                active_build_target = EditorUserBuildSettings.activeBuildTarget.ToString(),
                pack_scope = "one atlas plus its nonvariant master for variants",
                direct_sprite_packables_only = true,
                preview_expands_texture_and_folder_packables = true,
                max_packables = 256,
                max_pack_folder_entries = 1024,
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
}
