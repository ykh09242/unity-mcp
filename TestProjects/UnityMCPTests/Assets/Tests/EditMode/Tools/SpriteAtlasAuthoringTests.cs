using System;
using System.Collections;
using System.Globalization;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools.SpriteAtlasTools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.U2D;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.U2D;

namespace MCPForUnityTests.Editor.Tools
{
    [TestFixture("V1")]
    [TestFixture("V2")]
    public class SpriteAtlasAuthoringTests
    {
        private readonly string format;
        private bool IsV2 => format == "V2";
        private SpritePackerMode FormatMode => IsV2 ? SpritePackerMode.SpriteAtlasV2 : SpritePackerMode.AlwaysOnAtlas;
        private string folder;
        private string folderGuid;
        private string atlasPath;
        private string variantPath;
        private string clipPath;
        private Sprite[] sprites;
        private SpritePackerMode oldMode;
        private bool modeCaptured;
        private readonly EditorCurveBinding binding = EditorCurveBinding.PPtrCurve("", typeof(SpriteRenderer), "m_Sprite");

        public SpriteAtlasAuthoringTests(string format) => this.format = format;

        private JObject FormatCapabilities() => (JObject)Data(Send("capabilities"))["formats"][format];

        [SetUp]
        public void SetUp()
        {
            folderGuid = null;
            oldMode = EditorSettings.spritePackerMode;
            modeCaptured = true;
            EditorSettings.spritePackerMode = FormatMode;
            var capability = FormatCapabilities();
            var creation = capability["action_availability"]["create"];
            if (!creation.Value<bool>("available"))
                Assert.Ignore(creation.Value<string>("unavailable_reason"));
            folder = "Assets/__McpAtlas_" + Guid.NewGuid().ToString("N");
            folderGuid = AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            atlasPath = folder + "/Master" + capability.Value<string>("path_extension");
            variantPath = folder + "/Variant" + capability.Value<string>("path_extension");
            clipPath = folder + "/Motion.anim";
            sprites = new[] { CreateSprite("A", Color.red), CreateSprite("B", Color.blue) };
            var clip = new AnimationClip { name = "Motion" };
            AssetDatabase.CreateAsset(clip, clipPath);
            AnimationUtility.SetObjectReferenceCurve(
                clip,
                binding,
                new[]
                {
                    new ObjectReferenceKeyframe { time = 0, value = sprites[0] },
                    new ObjectReferenceKeyframe { time = 1, value = sprites[1] },
                }
            );
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (!string.IsNullOrEmpty(folderGuid) && AssetDatabase.AssetPathToGUID(folder) == folderGuid)
                    Assert.IsTrue(AssetDatabase.DeleteAsset(folder));
            }
            finally
            {
                if (modeCaptured)
                    EditorSettings.spritePackerMode = oldMode;
            }
            LogAssert.NoUnexpectedReceived();
        }

        private static string Full(string asset) => Path.Combine(Directory.GetParent(Application.dataPath).FullName, asset);

        private Sprite CreateSprite(string name, Color color)
        {
            var image = new Texture2D(16, 16, TextureFormat.RGBA32, false);
            image.SetPixels32(Enumerable.Repeat<Color32>(color, 256).ToArray());
            image.Apply();
            string path = folder + "/" + name + ".png";
            File.WriteAllBytes(Full(path), image.EncodeToPNG());
            UnityEngine.Object.DestroyImmediate(image);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            var importer = (TextureImporter)AssetImporter.GetAtPath(path);
            importer.textureType = TextureImporterType.Sprite;
            importer.spriteImportMode = SpriteImportMode.Single;
            importer.textureCompression = TextureImporterCompression.Uncompressed;
            importer.SaveAndReimport();
            var sprite = AssetDatabase.LoadAssetAtPath<Sprite>(path);
            Assert.IsNotNull(sprite, "Owned PNG must import as a real Sprite before the tool is invoked.");
            return sprite;
        }

        private static JObject Reference(UnityEngine.Object asset)
        {
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string guid, out long id));
            return new JObject { ["guid"] = guid, ["file_id"] = id.ToString(CultureInfo.InvariantCulture) };
        }

        private static JObject Send(string action, string path = null, JObject request = null)
        {
            var values = request == null ? new JObject() : (JObject)request.DeepClone();
            values["action"] = action;
            if (path != null)
                values["path"] = path;
            return JObject.FromObject(ManageSpriteAtlas.HandleCommand(values));
        }

        private static JObject Data(JObject result)
        {
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            return (JObject)result["data"];
        }

        private JObject Read(string path = null) => Data(Send("read", path ?? atlasPath));

        private void RequireSettingWrites(params string[] settings)
        {
            var writers = FormatCapabilities()["setting_writes"];
            foreach (string setting in settings)
                if (!writers.Value<bool>(setting))
                    Assert.Ignore("This Editor lacks the native importer write API for " + setting + ".");
        }

        private void RequirePreview()
        {
            var preview = FormatCapabilities()["action_availability"]["pack"];
            if (!preview.Value<bool>("available"))
                Assert.Ignore(preview.Value<string>("unavailable_reason"));
        }

        private JObject Create(string path = null, JObject properties = null) =>
            Data(
                Send(
                    "create",
                    path ?? atlasPath,
                    new JObject
                    {
                        ["properties"] = properties
                            ?? new JObject { ["include_in_build"] = false, ["packables"] = new JArray(Reference(sprites[0]), Reference(sprites[1])) },
                    }
                )
            );

        private static JObject Guard(JObject state, JObject properties = null) =>
            new JObject
            {
                ["guid"] = state["reference"]["guid"],
                ["file_id"] = state["reference"]["file_id"],
                ["expected_revision"] = state["revision"],
                ["properties"] = properties,
            };

        private string[] References() =>
            AnimationUtility
                .GetObjectReferenceCurve(AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath), binding)
                .Select(key => key.time.ToString(CultureInfo.InvariantCulture) + ":" + Reference(key.value).ToString(Newtonsoft.Json.Formatting.None))
                .ToArray();

        [UnityTest]
        public IEnumerator CreatePackRepack_RetainsSourceSpriteAndClipIdentities()
        {
            if (!IsV2 && AtlasAccess.V1PreviewRequiresEditorUpdate)
                Assert.Ignore("This pack roundtrip uses newly imported sources. The importer-backed V1 path requires established sources.");
            RequirePreview();
            // Given real source sprites, a saved clip, and explicit build exclusion.
            var ids = sprites.Select(x => Reference(x).ToString()).ToArray();
            var refs = References();
            byte[] clipBytes = File.ReadAllBytes(Full(clipPath));
            string[] sourcePaths = sprites.Select(AssetDatabase.GetAssetPath).ToArray();
            byte[][] sourceBytes = sourcePaths.Select(x => File.ReadAllBytes(Full(x))).ToArray();
            var created = Create();
            Assert.AreEqual(format, created.Value<string>("authoring_format"));
            Assert.AreEqual(IsV2, AssetImporter.GetAtPath(atlasPath) is SpriteAtlasImporter);
            if (!IsV2)
                yield return SettlePreview(atlasPath);
            // When exactly this atlas is packed and then repacked for the active native target.
            var pack = Guard(created);
            pack["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();
            var first = Data(Send("pack", atlasPath, pack));
            if (!IsV2)
                yield return SettlePreview(atlasPath);
            var secondRequest = Guard(Read());
            secondRequest["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();
            var second = Data(Send("pack", atlasPath, secondRequest));
            // Then native packed membership exists without rewriting source references.
            sprites = sourcePaths.Select(AssetDatabase.LoadAssetAtPath<Sprite>).ToArray();
            Assert.AreEqual(2, first.Value<int>("packed_sprite_count"));
            Assert.AreEqual(2, second.Value<int>("packed_sprite_count"));
            var atlas = AssetDatabase.LoadAssetAtPath<SpriteAtlas>(atlasPath);
            Assert.IsNotNull(atlas);
            foreach (var sprite in sprites)
                Assert.IsTrue(atlas.CanBindTo(sprite));
            Assert.AreEqual(created["reference"]["guid"], Read()["reference"]["guid"]);
            CollectionAssert.AreEqual(ids, sprites.Select(x => Reference(x).ToString()).ToArray());
            CollectionAssert.AreEqual(refs, References());
            CollectionAssert.AreEqual(clipBytes, File.ReadAllBytes(Full(clipPath)));
            for (int i = 0; i < sprites.Length; i++)
                CollectionAssert.AreEqual(sourceBytes[i], File.ReadAllBytes(Full(AssetDatabase.GetAssetPath(sprites[i]))));
        }

        [UnityTest]
        public IEnumerator VariantAndPlatformSettings_PersistThroughNativeImport()
        {
            RequireSettingWrites("variant_scale", "platforms");
            // Given an explicit master reference and revision.
            var master = Create();
            var properties = new JObject
            {
                ["include_in_build"] = false,
                ["master"] = master["reference"],
                ["master_revision"] = master["revision"],
                ["variant_scale"] = .5,
            };
            // When a variant is authored and a platform override configured.
            var variant = Create(variantPath, properties);
            var configured = Data(
                Send("configure", variantPath, Guard(variant, JObject.Parse("{platforms:{Standalone:{overridden:true,maxTextureSize:128,format:'RGBA32'}}}")))
            );
            // Then native variant/importer readback exposes the exact link, scale and override.
            var native = AssetDatabase.LoadAssetAtPath<SpriteAtlas>(variantPath);
            Assert.IsTrue(native.isVariant);
            Assert.AreEqual(AssetDatabase.LoadAssetAtPath<SpriteAtlas>(atlasPath), native.GetMasterAtlas());
            Assert.AreEqual(.5f, configured.Value<float>("variant_scale"));
            var platform = AtlasAccess.WithSettings(variantPath, native, AtlasSettings.ImporterApi.Native, settings => settings.Platform("Standalone"));
            Assert.IsTrue(platform.overridden);
            Assert.AreEqual(128, platform.maxTextureSize);
            Assert.AreEqual(TextureImporterFormat.RGBA32, platform.format);
            Assert.AreEqual(master["reference"]["guid"], configured["master"]["guid"]);
            if ((IsV2 || !AtlasAccess.V1PreviewRequiresEditorUpdate) && FormatCapabilities()["action_availability"]["pack"].Value<bool>("available"))
            {
                if (!IsV2)
                    yield return SettlePreview(variantPath);
                var request = Guard(Read(variantPath));
                request["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();
                var packed = Data(Send("pack", variantPath, request));
                Assert.Greater(packed.Value<int>("packed_sprite_count"), 0);
                Assert.AreEqual(2, ((JArray)packed["packed_atlases"]).Count, "Variant preview declares both its master and variant scope.");
            }
        }

        private IEnumerator SettlePreview(string path)
        {
            for (int update = 0; update < 4; update++)
            {
                if (Read(path).Value<bool>("preview_scope_settled"))
                    yield break;
                yield return null;
            }
            Assert.IsTrue(Read(path).Value<bool>("preview_scope_settled"), "V1 preview did not settle after native Editor updates.");
        }

        [UnityTest]
        public IEnumerator ExistingV1FolderAndTexturePackablesResolveExactSources()
        {
            if (IsV2)
                Assert.Ignore("This fixture creates native V1 folder and texture membership.");
            RequirePreview();
            string sourcePath = AssetDatabase.GetAssetPath(sprites[0]);
            var atlas = new SpriteAtlas();
            atlas.Add(new UnityEngine.Object[] { AssetDatabase.LoadMainAssetAtPath(folder), AssetDatabase.LoadAssetAtPath<Texture2D>(sourcePath) });
            AssetDatabase.CreateAsset(atlas, atlasPath);
            AssetDatabase.ImportAsset(atlasPath, ImportAssetOptions.ForceSynchronousImport);
            var initial = Read();
            Assert.AreEqual(2, initial.Value<int>("packable_count"));
            Assert.IsTrue(initial["packables"].All(value => value.Value<bool>("scoped_pack_supported")));

            var sourceImporter = (TextureImporter)AssetImporter.GetAtPath(sourcePath);
            sourceImporter.filterMode = FilterMode.Point;
            sourceImporter.SaveAndReimport();
            yield return SettlePreview(atlasPath);
            var current = Read();
            Assert.AreNotEqual(initial.Value<string>("revision"), current.Value<string>("revision"));
            Assert.IsTrue(JToken.DeepEquals(initial["packables"], current["packables"]));

            var request = Guard(current);
            request["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();
            var result = Send("pack", atlasPath, request);
            if (AtlasAccess.V1PreviewRequiresEditorUpdate)
            {
                Assert.AreEqual("v1_preview_source_requires_reopen", result["data"].Value<string>("code"), result.ToString());
                Assert.IsFalse(result["data"].Value<bool>("mutation_may_have_occurred"));
                yield break;
            }
            var packed = Data(result);
            Assert.AreEqual(2, ((JArray)packed["bindings"]).Count);
            Assert.IsTrue(packed["bindings"].All(value => value.Value<bool>("can_bind")));
            Assert.IsTrue(JToken.DeepEquals(initial["packables"], packed["packables"]));
        }

        [Test]
        public void AddRemoveAndPartialSettings_PreserveUnrelatedPackablesAndSettings()
        {
            RequireSettingWrites("packing", "texture");
            // Given a master with one sprite and custom unrelated settings.
            var created = Create(
                properties: new JObject
                {
                    ["include_in_build"] = false,
                    ["packables"] = new JArray(Reference(sprites[0])),
                    ["packing"] = JObject.Parse("{padding:4,enableRotation:false}"),
                    ["texture"] = JObject.Parse("{sRGB:false,generateMipMaps:false}"),
                }
            );
            // When an exact second sprite is added, then the first and unrelated settings remain.
            var added = Data(Send("add_packables", atlasPath, Guard(created, new JObject { ["packables"] = new JArray(Reference(sprites[1])) })));
            Assert.AreEqual(2, added.Value<int>("packable_count"));
            Assert.AreEqual(4, added["packing"].Value<int>("padding"));
            Assert.IsFalse(added["texture"].Value<bool>("sRGB"));
            var removed = Data(Send("remove_packables", atlasPath, Guard(Read(), new JObject { ["packables"] = new JArray(Reference(sprites[1])) })));
            Assert.AreEqual(1, removed.Value<int>("packable_count"));
            Assert.IsTrue(JToken.DeepEquals(Reference(sprites[0]), ((JArray)removed["packables"])[0]["reference"]));
        }

        [Test]
        public void ExternalWriterRevisionDrift_RejectsBeforeMutation()
        {
            RequireSettingWrites("texture");
            // Given a stale identity/revision and an external native importer write.
            var before = Create();
            if (IsV2 && AtlasSettings.ImporterApi.Native.UsesSource)
            {
                var asset = SpriteAtlasAsset.Load(atlasPath);
                try
                {
                    var native = new AtlasSettings.NativeSettings(asset, AtlasSettings.ImporterApi.Native);
                    var texture = native.Texture;
                    texture.anisoLevel = 3;
                    native.SetTexture(texture);
                    SpriteAtlasAsset.Save(asset, atlasPath);
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(asset);
                }
                AssetDatabase.ImportAsset(atlasPath, ImportAssetOptions.ForceSynchronousImport);
            }
            else if (IsV2)
            {
                var importer = (SpriteAtlasImporter)AssetImporter.GetAtPath(atlasPath);
                var nativeTexture = typeof(SpriteAtlasImporter).GetProperty("textureSettings");
                var settings = (SpriteAtlasTextureSettings)nativeTexture.GetValue(importer);
                settings.anisoLevel = 3;
                nativeTexture.SetValue(importer, settings);
                importer.SaveAndReimport();
            }
            else
            {
                var atlas = AssetDatabase.LoadAssetAtPath<SpriteAtlas>(atlasPath);
                var settings = atlas.GetTextureSettings();
                settings.anisoLevel = 3;
                atlas.SetTextureSettings(settings);
                EditorUtility.SetDirty(atlas);
                AssetDatabase.SaveAssetIfDirty(atlas);
                AssetDatabase.ImportAsset(atlasPath, ImportAssetOptions.ForceSynchronousImport);
            }
            byte[] metadata = File.ReadAllBytes(Full(atlasPath) + ".meta");
            byte[] source = File.ReadAllBytes(Full(atlasPath));
            // When a stale setting request is submitted, then the writer's state is retained.
            var result = Send("configure", atlasPath, Guard(before, JObject.Parse("{include_in_build:true}")));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(atlasPath) + ".meta"));
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(atlasPath)));
            Assert.AreEqual(3, Read()["texture"].Value<int>("anisoLevel"));
        }

        [TestCase("{include_in_build:true,packing:{padding:3}}")]
        [TestCase("{include_in_build:true,texture:{readable:'true'}}")]
        [TestCase("{include_in_build:true,platforms:{Standalone:{maxTextureSize:33}}}")]
        [TestCase("{include_in_build:true,platforms:{Standalone:{format:'future-format'}}}")]
        public void InvalidLateSetting_RejectsBeforeAnyAuthoring(string json)
        {
            // Given a valid early setting followed by an invalid supplied value.
            var before = Create();
            byte[] source = File.ReadAllBytes(Full(atlasPath));
            byte[] metadata = File.ReadAllBytes(Full(atlasPath) + ".meta");
            // When the complete request is preflighted, then both files remain identical.
            var result = Send("configure", atlasPath, Guard(before, JObject.Parse(json)));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(atlasPath)));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(atlasPath) + ".meta"));
        }

        [Test]
        public void MissingRequestedNativeWriter_DoesNotChangeExistingAtlas()
        {
            if (!IsV2)
                Assert.Ignore("This test supplies an alternate V2 importer API.");
            var before = Create();
            byte[] source = File.ReadAllBytes(Full(atlasPath));
            byte[] metadata = File.ReadAllBytes(Full(atlasPath) + ".meta");
            var request = Guard(before, JObject.Parse("{include_in_build:true,platforms:{Standalone:{overridden:true}}}"));
            request["action"] = "configure";
            request["path"] = atlasPath;
            var api = new AtlasSettings.ImporterApi(typeof(SpriteAtlasImporterCompatibilityTests.ReadbackWithWrongPlatformWriter));

            var result = JObject.FromObject(ManageSpriteAtlas.Execute(request, api));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("public writer for platforms", result.Value<string>("error"));
            Assert.IsFalse(result["data"].Value<bool>("mutation_may_have_occurred"));
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(atlasPath)));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(atlasPath) + ".meta"));
        }

        [Test]
        public void V1PackingCapabilityExplainsV2RequirementAndRefusesWithoutMutation()
        {
            if (!IsV2)
                Assert.Ignore("This test uses a V2 asset under a V1 packing mode.");
            Create();
            EditorSettings.spritePackerMode = SpritePackerMode.AlwaysOnAtlas;
            var request = Guard(Read());
            request["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();
            byte[] source = File.ReadAllBytes(Full(atlasPath));
            byte[] metadata = File.ReadAllBytes(Full(atlasPath) + ".meta");
            var capabilities = Data(Send("capabilities"));
            Assert.IsFalse(capabilities.Value<bool>("packing_enabled"));
            Assert.IsFalse(capabilities["action_availability"]["pack"].Value<bool>("available"));
            Assert.AreEqual("AlwaysOnAtlas", capabilities.Value<string>("packer_mode"));
            CollectionAssert.AreEqual(
                new[] { "SpriteAtlasV2", "SpriteAtlasV2Build" }.Where(mode => Enum.IsDefined(typeof(SpritePackerMode), mode)).ToArray(),
                capabilities["required_packer_modes"].Values<string>().ToArray()
            );
            StringAssert.Contains("V2", capabilities.Value<string>("packing_unavailable_reason"));
            StringAssert.Contains("AlwaysOnAtlas", capabilities.Value<string>("packing_unavailable_reason"));
            var result = Send("pack", atlasPath, request);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("V2", result.Value<string>("error"));
            Assert.AreEqual(SpritePackerMode.AlwaysOnAtlas, EditorSettings.spritePackerMode);
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(atlasPath)));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(atlasPath) + ".meta"));
        }

        [Test]
        public void PackingCapabilityReportsNativeBuildSupportAndPreservesGlobalMode()
        {
            var capabilities = FormatCapabilities();
            Assert.IsTrue(capabilities.Value<bool>("packing_enabled"));
            BuildTarget target = EditorUserBuildSettings.activeBuildTarget;
            bool targetSupported = BuildPipeline.IsBuildTargetSupported(BuildPipeline.GetBuildTargetGroup(target), target);
            bool available = targetSupported && (IsV2 || AtlasAccess.V1PreviewProviderAvailable);
            Assert.AreEqual(available, capabilities["action_availability"]["pack"].Value<bool>("available"));
            Assert.AreEqual(available, capabilities["packing_unavailable_reason"].Type == JTokenType.Null);
            CollectionAssert.Contains(capabilities["required_packer_modes"].Values<string>().ToArray(), FormatMode.ToString());
            Assert.AreEqual(FormatMode, EditorSettings.spritePackerMode);
        }

        [Test]
        public void DisabledPacking_RefusesWithoutChangingGlobalMode()
        {
            // Given an authored atlas and explicitly disabled global packing.
            var before = Create();
            EditorSettings.spritePackerMode = SpritePackerMode.Disabled;
            var fresh = Read();
            var request = Guard(fresh);
            request["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();
            // When preview packing is requested, then product leaves the disabled mode alone.
            var result = Send("pack", atlasPath, request);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(SpritePackerMode.Disabled, EditorSettings.spritePackerMode);
            var capabilities = FormatCapabilities();
            Assert.IsFalse(capabilities.Value<bool>("packing_enabled"));
        }

        [Test]
        public void VariantUsingDifferentAtlasFormat_RefusesBeforeCreatingAnyFile()
        {
            if (IsV2)
                Assert.Ignore("The target is a V2 variant linked to a saved V1 master.");
            var master = Create();
            var target = folder + "/WrongFormat.spriteatlasv2";
            var response = Send(
                "create",
                target,
                new JObject
                {
                    ["properties"] = new JObject
                    {
                        ["include_in_build"] = false,
                        ["master"] = master["reference"],
                        ["master_revision"] = master["revision"],
                    },
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("same SpriteAtlas format", response.Value<string>("error"));
            Assert.IsFalse(response["data"].Value<bool>("mutation_may_have_occurred"));
            Assert.IsFalse(File.Exists(Full(target)));
            Assert.IsFalse(File.Exists(Full(target) + ".meta"));
            Assert.AreEqual(master["revision"], Read()["revision"]);
        }

        [Test]
        public void V2ModeRefusesV1CreationWithoutMigratingOrChangingMode()
        {
            if (!IsV2)
                Assert.Ignore("This test asserts the V1 refusal under native V2 mode.");
            var target = folder + "/PreserveLegacy.spriteatlas";
            var capabilities = Data(Send("capabilities"))["formats"]["V1"];
            Assert.IsFalse(capabilities["action_availability"]["create"].Value<bool>("available"));
            var result = Send("create", target, new JObject { ["properties"] = new JObject { ["include_in_build"] = false } });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("preserve the .spriteatlas file", result.Value<string>("error"));
            Assert.IsFalse(result["data"].Value<bool>("mutation_may_have_occurred"));
            Assert.IsFalse(File.Exists(Full(target)));
            Assert.IsFalse(File.Exists(Full(target) + ".meta"));
            Assert.IsFalse(File.Exists(Full(target) + "v2"));
            Assert.IsFalse(File.Exists(Full(target) + "v2.meta"));
            Assert.AreEqual(FormatMode, EditorSettings.spritePackerMode);
        }

        [Test]
        public void V1PreviewBeforeEditorUpdateRefusesWithoutCallingTheNativePacker()
        {
            if (IsV2 || !AtlasAccess.V1PreviewRequiresEditorUpdate || !AtlasAccess.V1PreviewProviderAvailable)
                Assert.Ignore("This test covers immediate V1 preview preflight on the importer-backed Editor.");
            var state = Create();
            byte[] source = File.ReadAllBytes(Full(atlasPath));
            byte[] metadata = File.ReadAllBytes(Full(atlasPath) + ".meta");
            var request = Guard(state);
            request["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();

            var result = Send("pack", atlasPath, request);

            Assert.IsFalse(state.Value<bool>("preview_scope_settled"));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("completed Editor update", result.Value<string>("error"));
            Assert.AreEqual("v1_preview_scope_unsettled", result["data"].Value<string>("code"));
            Assert.IsFalse(result["data"].Value<bool>("mutation_may_have_occurred"));
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(atlasPath)));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(atlasPath) + ".meta"));
        }

        [UnityTest]
        public IEnumerator V1PreviewWithoutProviderRefusesAfterEditorUpdate()
        {
            if (IsV2 || AtlasAccess.V1PreviewProviderAvailable)
                Assert.Ignore("This test covers a missing provider on the importer-backed V1 preview path.");
            Create();
            yield return SettlePreview(atlasPath);
            var state = Read();
            byte[] source = File.ReadAllBytes(Full(atlasPath));
            byte[] metadata = File.ReadAllBytes(Full(atlasPath) + ".meta");
            var request = Guard(state);
            request["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();

            var result = Send("pack", atlasPath, request);

            Assert.IsTrue(state.Value<bool>("preview_scope_settled"));
            Assert.IsFalse(FormatCapabilities()["action_availability"]["pack"].Value<bool>("available"));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("2D Sprite TextureImporter provider", result.Value<string>("error"));
            Assert.AreEqual("v1_preview_provider_unavailable", result["data"].Value<string>("code"));
            Assert.IsFalse(result["data"].Value<bool>("mutation_may_have_occurred"));
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(atlasPath)));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(atlasPath) + ".meta"));
        }

        [UnityTest]
        public IEnumerator V1PreviewTracksBackdatedImportsInTheCurrentSession()
        {
            if (IsV2 || !AtlasAccess.V1PreviewRequiresEditorUpdate || !AtlasAccess.V1PreviewProviderAvailable)
                Assert.Ignore("This test covers current-session V1 source imports on an importer-backed Editor.");
            Create();
            yield return SettlePreview(atlasPath);
            DateTime backdated = DateTime.UtcNow.AddDays(-7);
            foreach (string sourcePath in sprites.Select(AssetDatabase.GetAssetPath).ToArray())
            {
                string sourceFile = Full(sourcePath);
                string metadataFile = sourceFile + ".meta";
                AssetDatabase.ImportAsset(sourcePath, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
                File.SetLastWriteTimeUtc(sourceFile, backdated);
                File.SetLastWriteTimeUtc(metadataFile, backdated);
                if (Application.platform == RuntimePlatform.WindowsEditor)
                {
                    File.SetCreationTimeUtc(sourceFile, backdated);
                    File.SetCreationTimeUtc(metadataFile, backdated);
                }
                Assert.IsTrue(AtlasPreviewImportWatcher.WasImportedAfterStartup(sourcePath));
            }
            yield return SettlePreview(atlasPath);
            var state = Read();
            var request = Guard(state);
            request["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();

            var result = Send("pack", atlasPath, request);

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("v1_preview_source_requires_reopen", result["data"].Value<string>("code"), result.ToString());
            Assert.IsFalse(result["data"].Value<bool>("mutation_may_have_occurred"));
        }

        [UnityTest]
        public IEnumerator ExternallyImportedV1AtlasWithBackdatedFilesStillRequiresReopen()
        {
            if (IsV2 || !AtlasAccess.V1PreviewRequiresEditorUpdate)
                Assert.Ignore("This test covers target provenance on the importer-backed V1 path.");
            yield return null;
            var external = new SpriteAtlas();
            external.Add(sprites);
            AssetDatabase.CreateAsset(external, atlasPath);
            AssetDatabase.ImportAsset(atlasPath, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            DateTime backdated = DateTime.UtcNow.AddDays(-7);
            string source = Full(atlasPath);
            foreach (string file in new[] { source, source + ".meta" })
            {
                File.SetLastWriteTimeUtc(file, backdated);
                if (Application.platform == RuntimePlatform.WindowsEditor)
                    File.SetCreationTimeUtc(file, backdated);
            }

            Assert.IsTrue(AtlasPreviewImportWatcher.WasAtlasCreated(AssetDatabase.AssetPathToGUID(atlasPath)));
            Assert.IsFalse(Read().Value<bool>("preview_atlas_established"));
            var refused = Assert.Throws<V1PreviewUnavailableException>(() => AtlasAccess.RequireEstablishedV1Atlas(atlasPath));
            Assert.AreEqual("v1_preview_atlas_requires_reopen", refused.Code);
        }

        [Test]
        public void V1ConfigureDoesNotCommitUnrelatedUnsavedEdits()
        {
            if (IsV2)
                Assert.Ignore("The V1 atlas is itself the persistent authoring object.");
            Create();
            var atlas = AssetDatabase.LoadAssetAtPath<SpriteAtlas>(atlasPath);
            atlas.SetIncludeInBuild(true);
            EditorUtility.SetDirty(atlas);
            var state = Read();
            byte[] source = File.ReadAllBytes(Full(atlasPath));

            var result = Send("configure", atlasPath, Guard(state, new JObject { ["include_in_build"] = false }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("unsaved Editor changes", result.Value<string>("error"));
            Assert.IsFalse(result["data"].Value<bool>("mutation_may_have_occurred"));
            Assert.IsTrue(atlas.IsIncludeInBuild(), "Current unsaved edit must remain present.");
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(atlasPath)));
        }
    }

    public class SpriteAtlasImporterCompatibilityTests
    {
        internal class ReadbackWithWrongPlatformWriter
        {
            public bool includeInBuild { get; set; }
            public float variantScale { get; set; }
            public SpriteAtlasPackingSettings packingSettings { get; set; }
            public SpriteAtlasTextureSettings textureSettings { get; set; }

            public TextureImporterPlatformSettings GetPlatformSettings(string name) => throw new InvalidOperationException("Probes must never call getters.");

            public void SetPlatformSettings(object settings) => throw new InvalidOperationException("The unsupported overload must never be called.");
        }

        private sealed class MissingPublicReadback : ReadbackWithWrongPlatformWriter
        {
            public new bool includeInBuild { private get; set; }
        }

        [TestCase(typeof(MissingPublicReadback), false, "includeInBuild")]
        [TestCase(typeof(ReadbackWithWrongPlatformWriter), true, "public writer for platforms")]
        public void UnsupportedV2ApiRefusesBeforeWritingSourceOrMetadata(Type shape, bool readersPresent, string expectedReason)
        {
            var api = new AtlasSettings.ImporterApi(shape);
            var capabilities = JObject.FromObject(ManageSpriteAtlas.Execute(new JObject { ["action"] = "capabilities" }, api));
            Assert.IsTrue(capabilities.Value<bool>("success"), capabilities.ToString());
            JObject data = (JObject)capabilities["data"];
            Assert.IsNull(data["minimum_authoring_unity_version"]);
            Assert.AreEqual(readersPresent, data["formats"]["V2"]["action_availability"]["read"].Value<bool>("available"));
            Assert.AreEqual(readersPresent, data["formats"]["V2"]["action_availability"]["add_packables"].Value<bool>("available"));
            Assert.IsFalse(data["formats"]["V2"]["setting_writes"].Value<bool>("platforms"));
            Assert.IsTrue(data["formats"]["V1"]["action_availability"]["read"].Value<bool>("available"));
            SpritePackerMode mode = EditorSettings.spritePackerMode;
            string path = "Assets/__McpAtlasCompat_" + Guid.NewGuid().ToString("N") + ".spriteatlasv2";
            string full = Path.Combine(Directory.GetParent(Application.dataPath).FullName, path);
            Assert.IsFalse(File.Exists(full));
            Assert.IsFalse(File.Exists(full + ".meta"));
            var response = JObject.FromObject(
                ManageSpriteAtlas.Execute(
                    new JObject
                    {
                        ["action"] = "create",
                        ["path"] = path,
                        ["properties"] = JObject.Parse("{include_in_build:false,platforms:{Standalone:{overridden:true}}}"),
                    },
                    api
                )
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(expectedReason, response.Value<string>("error"));
            Assert.IsFalse(response["data"].Value<bool>("mutation_may_have_occurred"), response.ToString());
            Assert.IsFalse(File.Exists(full), "Unavailable V2 authoring must refuse before creating a source file.");
            Assert.IsFalse(File.Exists(full + ".meta"), "Unavailable V2 authoring must refuse before creating metadata.");
            Assert.IsTrue(string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets)));
            Assert.AreEqual(mode, EditorSettings.spritePackerMode, "Capability checks/refusal must preserve the caller's global packing mode.");
        }

        [Test]
        public void NativeV2SettingsProbeRejectsObsoleteSourceStubs()
        {
            var native = AtlasSettings.ImporterApi.Native;
            var source = new AtlasSettings.ImporterApi(typeof(SpriteAtlasAsset), true);
            var sourceReader = typeof(SpriteAtlasAsset).GetMethod("IsIncludeInBuild", Type.EmptyTypes);
            if (sourceReader?.IsDefined(typeof(ObsoleteAttribute), false) == true)
                Assert.IsNotNull(source.ReadUnavailableReason, "Obsolete no-op source getters are not a usable native API.");
            if (native.UsesSource)
                Assert.IsNull(source.ReadUnavailableReason, "The older source backend is selected only when its readback contract is present.");
        }

        [Test]
        public void V1ModeAdvertisesAuthoringWhenV2BackendIsUnavailable()
        {
            SpritePackerMode original = EditorSettings.spritePackerMode;
            try
            {
                EditorSettings.spritePackerMode = SpritePackerMode.AlwaysOnAtlas;
                var api = new AtlasSettings.ImporterApi(typeof(MissingPublicReadback));
                var response = JObject.FromObject(ManageSpriteAtlas.Execute(new JObject { ["action"] = "capabilities" }, api));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var capabilities = response["data"];
                Assert.AreEqual("V2", capabilities.Value<string>("legacy_fields_format"));
                Assert.IsFalse(capabilities.Value<bool>("authoring_available"));
                Assert.IsFalse(capabilities["formats"]["V2"].Value<bool>("authoring_available"));

                var legacy = capabilities["formats"]["V1"];
                Assert.IsTrue(legacy.Value<bool>("authoring_available"));
                Assert.AreEqual(".spriteatlas", legacy.Value<string>("path_extension"));
                Assert.IsTrue(legacy["action_availability"]["create"].Value<bool>("available"));
                Assert.IsTrue(legacy["action_availability"]["configure"].Value<bool>("available"));
                CollectionAssert.AreEqual(new[] { "Sprite" }, legacy["membership_write_packable_types"].Values<string>());
                CollectionAssert.AreEqual(new[] { "Sprite", "Texture2D", "Assets folder" }, legacy["preview_packable_types"].Values<string>());
                Assert.AreEqual(SpritePackerMode.AlwaysOnAtlas, EditorSettings.spritePackerMode);
            }
            finally
            {
                EditorSettings.spritePackerMode = original;
            }
        }
    }
}
