using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Sprite2D;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.U2D;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.U2D;

namespace MCPForUnityTests.Editor.Tools
{
    public class SpriteAtlasAuthoringTests
    {
        private string folder;
        private string folderGuid;
        private string atlasPath;
        private string variantPath;
        private string clipPath;
        private Sprite[] sprites;
        private SpritePackerMode oldMode;
        private bool modeCaptured;
        private readonly EditorCurveBinding binding = EditorCurveBinding.PPtrCurve("", typeof(SpriteRenderer), "m_Sprite");

        [SetUp]
        public void SetUp()
        {
            oldMode = EditorSettings.spritePackerMode;
            modeCaptured = true;
            EditorSettings.spritePackerMode = SpritePackerMode.SpriteAtlasV2;
            Assert.AreEqual(SpritePackerMode.SpriteAtlasV2, EditorSettings.spritePackerMode);
            folder = "Assets/__McpAtlas_" + Guid.NewGuid().ToString("N");
            folderGuid = AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            atlasPath = folder + "/Master.spriteatlasv2";
            variantPath = folder + "/Variant.spriteatlasv2";
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
            image.SetPixels(Enumerable.Repeat(color, 256).ToArray());
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
            Type tool = typeof(ManageSprite).Assembly.GetType("MCPForUnity.Editor.Tools.SpriteAtlasTools.ManageSpriteAtlas");
            Assert.IsNotNull(tool, "Public manage_sprite_atlas domain is not implemented.");
            var values = request == null ? new JObject() : (JObject)request.DeepClone();
            values["action"] = action;
            if (path != null)
                values["path"] = path;
            return JObject.FromObject(tool.GetMethod("HandleCommand", BindingFlags.Static | BindingFlags.Public).Invoke(null, new object[] { values }));
        }

        private static JObject Data(JObject result)
        {
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            return (JObject)result["data"];
        }

        private JObject Read(string path = null) => Data(Send("read", path ?? atlasPath));

        private JObject Create(string path = null, JObject properties = null) =>
            Data(
                Send(
                    "create",
                    path ?? atlasPath,
                    new JObject
                    {
                        ["properties"] =
                            properties
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

        [Test]
        public void CreatePackRepack_RetainsSourceSpriteAndClipIdentities()
        {
            // Given real source sprites, a saved clip, and explicit build exclusion.
            var ids = sprites.Select(x => Reference(x).ToString()).ToArray();
            var refs = References();
            byte[] clipBytes = File.ReadAllBytes(Full(clipPath));
            string[] sourcePaths = sprites.Select(AssetDatabase.GetAssetPath).ToArray();
            byte[][] sourceBytes = sourcePaths.Select(x => File.ReadAllBytes(Full(x))).ToArray();
            var created = Create();
            // When exactly this atlas is packed and then repacked for the active native target.
            var pack = Guard(created);
            pack["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();
            var first = Data(Send("pack", atlasPath, pack));
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

        [Test]
        public void VariantAndPlatformSettings_PersistThroughNativeImport()
        {
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
            var importer = (SpriteAtlasImporter)AssetImporter.GetAtPath(variantPath);
            Assert.AreEqual(.5f, importer.variantScale);
            var platform = importer.GetPlatformSettings("Standalone");
            Assert.IsTrue(platform.overridden);
            Assert.AreEqual(128, platform.maxTextureSize);
            Assert.AreEqual(TextureImporterFormat.RGBA32, platform.format);
            Assert.AreEqual(master["reference"]["guid"], configured["master"]["guid"]);
            var request = Guard(Read(variantPath));
            request["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString();
            var packed = Data(Send("pack", variantPath, request));
            Assert.Greater(packed.Value<int>("packed_sprite_count"), 0);
            Assert.AreEqual(2, ((JArray)packed["packed_atlases"]).Count, "Variant preview declares both its master and variant scope.");
        }

        [Test]
        public void AddRemoveAndPartialSettings_PreserveUnrelatedPackablesAndSettings()
        {
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
            // Given a stale identity/revision and an external native importer write.
            var before = Create();
            var importer = (SpriteAtlasImporter)AssetImporter.GetAtPath(atlasPath);
            var settings = importer.textureSettings;
            settings.anisoLevel = 3;
            importer.textureSettings = settings;
            importer.SaveAndReimport();
            byte[] metadata = File.ReadAllBytes(Full(atlasPath) + ".meta");
            byte[] source = File.ReadAllBytes(Full(atlasPath));
            // When a stale setting request is submitted, then the writer's state is retained.
            var result = Send("configure", atlasPath, Guard(before, JObject.Parse("{include_in_build:true}")));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(atlasPath) + ".meta"));
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(atlasPath)));
            Assert.AreEqual(3, ((SpriteAtlasImporter)AssetImporter.GetAtPath(atlasPath)).textureSettings.anisoLevel);
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
            var capabilities = Data(Send("capabilities"));
            Assert.IsFalse(capabilities.Value<bool>("packing_enabled"));
        }
    }
}
