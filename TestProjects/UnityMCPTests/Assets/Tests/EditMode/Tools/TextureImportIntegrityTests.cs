using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class TextureImportIntegrityTests
    {
        private string _root;
        private string _guid;
        private string _path;

        [SetUp]
        public void SetUp()
        {
            _guid = null;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("The fixture does not modify an existing prefab stage.");
            string project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string cwd = Path.GetFullPath(Directory.GetCurrentDirectory());
            if (!string.Equals(project.TrimEnd('/', '\\'), cwd.TrimEnd('/', '\\'), StringComparison.OrdinalIgnoreCase))
                Assert.Ignore("The texture handler must run inside the owned Unity project.");

            string name = "__McpTextureImportIntegrity_" + Guid.NewGuid().ToString("N");
            _root = "Assets/" + name;
            Assert.IsFalse(AssetDatabase.IsValidFolder(_root));
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(_root));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(_root, AssetPathToGUIDOptions.OnlyExistingAssets));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, name)));
            _guid = AssetDatabase.CreateFolder("Assets", name);
            Assert.IsNotEmpty(_guid);
            Assert.AreEqual(_root, AssetDatabase.GUIDToAssetPath(_guid));
            _path = _root + "/Fixture.png";
            File.WriteAllBytes(Absolute(_path), Convert.FromBase64String(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGMQkdP4DwAB/gFaQ7UOogAAAABJRU5ErkJggg=="));
            AssetDatabase.ImportAsset(_path, ImportAssetOptions.ForceSynchronousImport);
            Assert.IsNotNull(AssetImporter.GetAtPath(_path) as TextureImporter);
            LogAssert.NoUnexpectedReceived();
        }

        [TearDown]
        public void TearDown()
        {
            if (string.IsNullOrEmpty(_guid)) return;
            Assert.AreEqual(_root, AssetDatabase.GUIDToAssetPath(_guid));
            Assert.AreEqual(_guid, AssetDatabase.AssetPathToGUID(_root, AssetPathToGUIDOptions.OnlyExistingAssets));
            Assert.IsTrue(AssetDatabase.DeleteAsset(_root));
            _guid = null;
        }

        private string Absolute(string path)
        {
            Assert.IsTrue(path.StartsWith(_root + "/", StringComparison.Ordinal));
            return Path.Combine(Application.dataPath, path.Substring("Assets/".Length));
        }

        private JObject Send(string action, JObject options, string path = null)
        {
            var request = (JObject)options.DeepClone();
            request["action"] = action;
            request["path"] = path ?? _path;
            return JObject.FromObject(ManageTexture.HandleCommand(request));
        }

        private void AssertRejectedWithoutChanges(string action, JObject options)
        {
            var importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            string before = EditorJsonUtility.ToJson(importer);
            int dirtyCount = EditorUtility.GetDirtyCount(importer);
            var image = File.ReadAllBytes(Absolute(_path));
            var metadata = File.ReadAllBytes(Absolute(_path) + ".meta");
            var response = Send(action, options);
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(importer));
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(importer));
            CollectionAssert.AreEqual(image, File.ReadAllBytes(Absolute(_path)));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Absolute(_path) + ".meta"));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("{textureType:'Sprite',anisoLevel:'bad'}")]
        [TestCase("{isReadable:true,mipmapEnabled:'bad'}")]
        [TestCase("{spritePixelsPerUnit:5,spriteExtrude:'bad'}")]
        [TestCase("{sRGBTexture:false,maxTextureSize:'bad'}")]
        [TestCase("{compressionQuality:0,spritePivot:[0,'bad']}")]
        [TestCase("{isReadable:true,crunchedCompression:'bad'}")]
        public void LateImporterConversionsPreserveStateAndFiles(string json)
        {
            AssertRejectedWithoutChanges("set_import_settings",
                new JObject { ["import_settings"] = JObject.Parse(json) });
        }

        [TestCase("{pivot:[0,0],pixelsPerUnit:'bad'}")]
        [TestCase("{pivot:[0,'bad']}")]
        public void SpriteConversionsPreserveTypeAndPivot(string json)
        {
            AssertRejectedWithoutChanges("set_import_settings",
                new JObject { ["as_sprite"] = JObject.Parse(json) });
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RejectedSettingsPrecedePixelEditsAndReadableChanges(bool pixels)
        {
            var importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            Assert.IsFalse(importer.isReadable, "The fixture starts with an unreadable imported texture.");
            var options = new JObject
            {
                ["importSettings"] = JObject.Parse("{isReadable:true,anisoLevel:'bad'}")
            };
            if (pixels) options["setPixels"] = JObject.Parse("{color:[255,0,0,255]}");
            AssertRejectedWithoutChanges("modify", options);
        }

        [TestCase("create")]
        [TestCase("create_sprite")]
        [TestCase("apply_pattern")]
        [TestCase("apply_gradient")]
        [TestCase("apply_noise")]
        public void RejectedSpriteSettingsDoNotPrepareNewAssetDirectories(string action)
        {
            string parent = _root + "/Unprepared";
            string path = parent + "/New.png";
            var response = Send(action, new JObject
            {
                ["width"] = 1,
                ["height"] = 1,
                ["spriteSettings"] = JObject.Parse("{pivot:[0,0],pixelsPerUnit:'bad'}")
            }, path);
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.IsFalse(Directory.Exists(Absolute(parent)));
            Assert.IsFalse(File.Exists(Absolute(path)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("create")]
        [TestCase("create_sprite")]
        [TestCase("apply_pattern")]
        public void RejectedImportSettingsPreserveExistingDestination(string action)
        {
            AssertRejectedWithoutChanges(action, JObject.Parse(
                "{width:1,height:1,importSettings:{textureType:'Sprite',anisoLevel:'bad'}}"));
        }

        [Test]
        public void ValidFalseAndZeroImportSettingsRemainSupported()
        {
            var response = Send("set_import_settings", JObject.Parse(
                "{import_settings:{sRGBTexture:false,isReadable:false,mipmapEnabled:false,anisoLevel:0,compressionQuality:0}}"));
            Assert.IsTrue((bool)response["success"], response.ToString());
            var importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            Assert.IsFalse(importer.sRGBTexture);
            Assert.IsFalse(importer.isReadable);
            Assert.IsFalse(importer.mipmapEnabled);
            Assert.AreEqual(0, importer.anisoLevel);
            Assert.AreEqual(0, importer.compressionQuality);
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("image-conflict")]
        [TestCase("pattern-size")]
        [TestCase("missing-image")]
        public void InvalidGenerationInputDoesNotPrepareDirectories(string scenario)
        {
            string parent = _root + "/Unprepared";
            string path = parent + "/New.png";
            var options = new JObject { ["width"] = 1, ["height"] = 1 };
            if (scenario == "image-conflict")
            {
                options["imagePath"] = _path;
                options["fillColor"] = new JArray(1, 2, 3, 4);
            }
            else if (scenario == "pattern-size")
            {
                options["pattern"] = "checkerboard";
                options["patternSize"] = 0;
            }
            else
            {
                string missing = _root + "/Missing.png";
                Assert.IsFalse(File.Exists(Absolute(missing)));
                options["imagePath"] = missing;
            }
            var response = Send("create", options, path);
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.IsFalse(Directory.Exists(Absolute(parent)));
            Assert.IsFalse(File.Exists(Absolute(path)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("import_settings", "spritePivot")]
        [TestCase("as_sprite", "pivot")]
        public void PartialPivotStillKeepsExistingPivot(string container, string property)
        {
            var importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            importer.textureType = TextureImporterType.Sprite;
            importer.spritePivot = new Vector2(.25f, .75f);
            importer.SaveAndReimport();
            importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            Assert.AreEqual(new Vector2(.25f, .75f), importer.spritePivot,
                "The owned importer must retain the seeded pivot before dispatch.");
            var response = Send("set_import_settings", new JObject
            {
                [container] = new JObject { [property] = new JArray(0) }
            });
            Assert.IsTrue((bool)response["success"], response.ToString());
            importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            Assert.AreEqual(new Vector2(.25f, .75f), importer.spritePivot);
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void UnknownEnumStillLeavesTheTypeUnchanged()
        {
            var importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            var type = importer.textureType;
            var response = Send("set_import_settings", JObject.Parse(
                "{import_settings:{textureType:'future-enum',isReadable:true}}"));
            Assert.IsTrue((bool)response["success"], response.ToString());
            importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            Assert.AreEqual(type, importer.textureType);
            Assert.IsTrue(importer.isReadable);
            LogAssert.NoUnexpectedReceived();
        }
    }
}
