using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
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
            File.WriteAllBytes(
                Absolute(_path),
                Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGMQkdP4DwAB/gFaQ7UOogAAAABJRU5ErkJggg==")
            );
            AssetDatabase.ImportAsset(_path, ImportAssetOptions.ForceSynchronousImport);
            Assert.IsNotNull(AssetImporter.GetAtPath(_path) as TextureImporter);
            LogAssert.NoUnexpectedReceived();
        }

        [TearDown]
        public void TearDown()
        {
            if (string.IsNullOrEmpty(_guid))
                return;
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

        [TestCase("{fillColor:['bad',0,0]}")]
        [TestCase("{pattern:'checkerboard',palette:[[1,2,3],['bad',0,0]]}")]
        [TestCase("{pixels:[[1,2,3],['bad',0,0]]}")]
        [TestCase("{pixels:'base64:!!!!'}")]
        public void TextureContentsRejectMalformedInputsWithoutAllocatingNativeTexture(string json)
        {
            var prepare = typeof(ManageTexture).GetMethod("PrepareTextureContents", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(prepare);
            int count = UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Length;
            var error = Assert.Throws<TargetInvocationException>(() => prepare.Invoke(null, new object[] { JObject.Parse(json), 2, 1, 8 }));
            Assert.That(error.InnerException, Is.InstanceOf<ArgumentException>().Or.InstanceOf<FormatException>());
            Assert.AreEqual(count, UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Length);
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void PreparedTextureContentsApplyCachedPixelsAfterPayloadChanges()
        {
            if (SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null)
                Assert.Ignore("Pixel application requires a graphics device.");
            var prepare = typeof(ManageTexture).GetMethod("PrepareTextureContents", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(prepare);
            var request = JObject.Parse("{pixels:[[1,2,3,4],[5,6,7,8]]}");
            var apply = (Action<Texture2D>)prepare.Invoke(null, new object[] { request, 2, 1, 8 });
            ((JArray)request["pixels"])[0] = new JValue("changed after preparation");
            var texture = new Texture2D(2, 1, TextureFormat.RGBA32, false);
            try
            {
                apply(texture);
                CollectionAssert.AreEqual(new[] { new Color32(1, 2, 3, 4), new Color32(5, 6, 7, 8) }, texture.GetPixels32());
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        [TestCase("{color:[1]}", "Pixel colors must contain")]
        [TestCase("{}", "setPixels requires")]
        [TestCase("{pixels:[[1,2,'bad']]}", "Invalid parameter")]
        public void InvalidPixelEditRejectsBeforeInspectingOrDecodingImage(string options, string errorText)
        {
            byte[] incompleteImage = new byte[] { 1, 2, 3, 4 };
            File.WriteAllBytes(Absolute(_path), incompleteImage);
            var response = Send("modify", new JObject { ["setPixels"] = JObject.Parse(options) });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(errorText, response.Value<string>("error"));
            CollectionAssert.AreEqual(incompleteImage, File.ReadAllBytes(Absolute(_path)));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("[[1,2,3,4],[5,6,7,8]]")]
        [TestCase("'base64:AQIDBAUGBwg='")]
        public void PreparedPixelDataCachesConvertedColorsWithoutNativeAllocation(string json)
        {
            var prepare = typeof(TextureOps).GetMethod("PreparePixelData", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(prepare);
            int count = UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Length;
            var colors = (Color32[])prepare.Invoke(null, new object[] { JToken.Parse(json), 2, 1 });
            CollectionAssert.AreEqual(new[] { new Color32(1, 2, 3, 4), new Color32(5, 6, 7, 8) }, colors);
            Assert.AreEqual(count, UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Length);
        }

        [TestCase("create", 4097, 1, 1)]
        [TestCase("create_sprite", 1, 4097, 1)]
        [TestCase("apply_pattern", 4097, 4097, 1)]
        [TestCase("apply_gradient", int.MaxValue, int.MaxValue, 1)]
        [TestCase("apply_noise", 4096, 4096, 3)]
        [TestCase("apply_noise", 1, 1, int.MaxValue)]
        public void OverBudgetGenerationDoesNotReplaceExistingTexture(string action, int width, int height, int octaves)
        {
            byte[] before = File.ReadAllBytes(Absolute(_path));
            var response = Send(
                action,
                new JObject
                {
                    ["width"] = width,
                    ["height"] = height,
                    ["octaves"] = octaves,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Absolute(_path)));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void OverBudgetPixelRegionDoesNotReplaceExistingTexture()
        {
            byte[] before = File.ReadAllBytes(Absolute(_path));
            var response = Send(
                "modify",
                new JObject
                {
                    ["setPixels"] = new JObject
                    {
                        ["width"] = int.MaxValue,
                        ["height"] = int.MaxValue,
                        ["color"] = new JArray(255, 0, 0, 255),
                    },
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Absolute(_path)));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("create")]
        [TestCase("create_sprite")]
        public void ImageHeaderOverBudgetRejectsBeforeReplacingTexture(string action)
        {
            byte[] before = File.ReadAllBytes(Absolute(_path));
            byte[] image = (byte[])before.Clone();
            // Change PNG IHDR width to 4097 and repair its CRC so size is the rejection reason.
            image[16] = 0;
            image[17] = 0;
            image[18] = 16;
            image[19] = 1;
            uint crc = 0xffffffff;
            for (int i = 12; i < 29; i++)
            {
                crc ^= image[i];
                for (int bit = 0; bit < 8; bit++)
                    crc = (crc >> 1) ^ ((crc & 1) != 0 ? 0xedb88320u : 0u);
            }
            crc ^= 0xffffffff;
            for (int i = 0; i < 4; i++)
                image[29 + i] = (byte)(crc >> (24 - 8 * i));
            string source = _root + "/OverBudget.png";
            File.WriteAllBytes(Absolute(source), image);
            var response = Send(action, new JObject { ["imagePath"] = source });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("dimensions exceed max", response.Value<string>("error"));
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Absolute(_path)));
            LogAssert.NoUnexpectedReceived();
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
            AssertRejectedWithoutChanges("set_import_settings", new JObject { ["import_settings"] = JObject.Parse(json) });
        }

        [TestCase("{pivot:[0,0],pixelsPerUnit:'bad'}")]
        [TestCase("{pivot:[0,'bad']}")]
        public void SpriteConversionsPreserveTypeAndPivot(string json)
        {
            AssertRejectedWithoutChanges("set_import_settings", new JObject { ["as_sprite"] = JObject.Parse(json) });
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RejectedSettingsPrecedePixelEditsAndReadableChanges(bool pixels)
        {
            var importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            Assert.IsFalse(importer.isReadable, "The fixture starts with an unreadable imported texture.");
            var options = new JObject { ["importSettings"] = JObject.Parse("{isReadable:true,anisoLevel:'bad'}") };
            if (pixels)
                options["setPixels"] = JObject.Parse("{color:[255,0,0,255]}");
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
            var response = Send(
                action,
                new JObject
                {
                    ["width"] = 1,
                    ["height"] = 1,
                    ["spriteSettings"] = JObject.Parse("{pivot:[0,0],pixelsPerUnit:'bad'}"),
                },
                path
            );
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
            AssertRejectedWithoutChanges(action, JObject.Parse("{width:1,height:1,importSettings:{textureType:'Sprite',anisoLevel:'bad'}}"));
        }

        [Test]
        public void ValidFalseAndZeroImportSettingsRemainSupported()
        {
            var response = Send(
                "set_import_settings",
                JObject.Parse("{import_settings:{sRGBTexture:false,isReadable:false,mipmapEnabled:false,anisoLevel:0,compressionQuality:0}}")
            );
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

        [TestCase("create")]
        [TestCase("create_sprite")]
        [TestCase("apply_pattern")]
        [TestCase("apply_gradient")]
        [TestCase("apply_noise")]
        public void OccupiedOutputParentRejectsWithoutChangingExistingFiles(string action)
        {
            string parent = _root + "/Occupied.txt";
            File.WriteAllText(Absolute(parent), "Preserve existing parent file.");
            byte[] image = File.ReadAllBytes(Absolute(_path));
            var response = Send(
                action,
                new JObject
                {
                    ["width"] = 2,
                    ["height"] = 2,
                    ["pattern"] = "CHECKERBOARD",
                },
                parent + "/New.png"
            );

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("occupied by a file", response.Value<string>("error"));
            Assert.AreEqual("Preserve existing parent file.", File.ReadAllText(Absolute(parent)));
            CollectionAssert.AreEqual(image, File.ReadAllBytes(Absolute(_path)));
            Assert.IsFalse(File.Exists(Absolute(parent + "/New.png")));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("create")]
        [TestCase("create_sprite")]
        [TestCase("apply_pattern")]
        [TestCase("apply_gradient")]
        [TestCase("apply_noise")]
        public void InvalidGenerationPaletteRejectsBeforePreparingOutputFolders(string action)
        {
            string parent = _root + "/InvalidPalette";
            var response = Send(action, JObject.Parse("{width:2,height:2,pattern:'checkerboard',palette:[[1,2,3],['bad',2,3]]}"), parent + "/New.png");

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("Invalid parameter", response.Value<string>("error"));
            Assert.IsFalse(Directory.Exists(Absolute(parent)));
            Assert.IsFalse(File.Exists(Absolute(parent + ".meta")));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(parent, AssetPathToGUIDOptions.OnlyExistingAssets));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("create")]
        [TestCase("create_sprite")]
        public void MalformedImageHeaderRejectsBeforePreparingOutputFolders(string action)
        {
            string source = _root + "/Malformed.png";
            string parent = _root + "/MalformedImageOutput";
            File.WriteAllBytes(Absolute(source), new byte[] { 1, 2, 3, 4 });
            var response = Send(action, new JObject { ["imagePath"] = source }, parent + "/New.png");

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("PNG or JPEG", response.Value<string>("error"));
            Assert.IsFalse(Directory.Exists(Absolute(parent)));
            Assert.IsFalse(File.Exists(Absolute(parent + ".meta")));
            CollectionAssert.AreEqual(new byte[] { 1, 2, 3, 4 }, File.ReadAllBytes(Absolute(source)));
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
            Assert.AreEqual(new Vector2(.25f, .75f), importer.spritePivot, "The owned importer must retain the seeded pivot before dispatch.");
            var response = Send("set_import_settings", new JObject { [container] = new JObject { [property] = new JArray(0) } });
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
            var response = Send("set_import_settings", JObject.Parse("{import_settings:{textureType:'future-enum',isReadable:true}}"));
            Assert.IsTrue((bool)response["success"], response.ToString());
            importer = AssetImporter.GetAtPath(_path) as TextureImporter;
            Assert.AreEqual(type, importer.textureType);
            Assert.IsTrue(importer.isReadable);
            LogAssert.NoUnexpectedReceived();
        }
    }
}
