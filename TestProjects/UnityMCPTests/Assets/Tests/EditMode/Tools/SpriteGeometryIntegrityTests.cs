using System;
using System.Collections;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools.Sprite2D;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class SpriteGeometryIntegrityTests
    {
        private string folder;
        private string folderGuid;
        private string path;
        private string clipPath;
        private readonly EditorCurveBinding binding = EditorCurveBinding.PPtrCurve("", typeof(SpriteRenderer), "m_Sprite");

        [SetUp]
        public void SetUp()
        {
            folder = "Assets/__McpSpriteGeometry_" + Guid.NewGuid().ToString("N");
            folderGuid = AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            path = folder + "/Sheet.png";
            clipPath = folder + "/Motion.anim";
            var texture = new Texture2D(64, 32, TextureFormat.RGBA32, false);
            texture.SetPixels(Enumerable.Repeat(Color.white, 64 * 32).ToArray());
            texture.Apply();
            File.WriteAllBytes(Full(path), texture.EncodeToPNG());
            UnityEngine.Object.DestroyImmediate(texture);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            var sliced = Send("slice_sheet", new JObject { ["cols"] = 2, ["rows"] = 1 });
            Assert.IsTrue(sliced.Value<bool>("success"), sliced.ToString());
            if (AppDomain.CurrentDomain.GetAssemblies().Any(x => x.GetName().Name == "Unity.2D.Sprite.Editor"))
                Physics(true);
            var sprites = Sprites();
            Assert.AreEqual(2, sprites.Length, "Owned fixture must contain two imported sprites.");
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
            if (!string.IsNullOrEmpty(folderGuid) && AssetDatabase.AssetPathToGUID(folder) == folderGuid)
                Assert.IsTrue(AssetDatabase.DeleteAsset(folder));
            LogAssert.NoUnexpectedReceived();
        }

        private static string Full(string asset) => Path.Combine(Directory.GetParent(Application.dataPath).FullName, asset);

        private Sprite[] Sprites() => AssetDatabase.LoadAllAssetsAtPath(path).OfType<Sprite>().OrderBy(x => x.name).ToArray();

        private JObject Send(string action, JObject args = null)
        {
            var request = args == null ? new JObject() : (JObject)args.DeepClone();
            request["action"] = action;
            request["path"] = path;
            return JObject.FromObject(ManageSprite.HandleCommand(request));
        }

        private JObject Read()
        {
            var result = Send("get_geometry");
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            return result;
        }

        private JObject First(JObject read) => (JObject)((JArray)read["sprites"]).Single(x => x.Value<string>("name") == "Sheet_0");

        private JObject Write(JObject read, JObject geometry)
        {
            var sprite = First(read);
            return Send(
                "set_geometry",
                new JObject
                {
                    ["guid"] = read["guid"],
                    ["sprite_id"] = sprite["sprite_id"],
                    ["file_id"] = sprite["file_id"],
                    ["expected_revision"] = read["revision"],
                    ["geometry"] = geometry,
                }
            );
        }

        private static string Persistent(UnityEngine.Object value)
        {
            Assert.IsNotNull(value);
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long id));
            return guid + ":" + id.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        private string[] ClipReferences() =>
            AnimationUtility
                .GetObjectReferenceCurve(AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath), binding)
                .Select(key => key.time + ":" + Persistent(key.value))
                .ToArray();

        private string Physics(bool seed = false)
        {
            var assembly = AppDomain.CurrentDomain.GetAssemblies().Single(x => x.GetName().Name == "Unity.2D.Sprite.Editor");
            var factoryType = assembly.GetType("UnityEditor.U2D.Sprites.SpriteDataProviderFactories", true);
            var factory = Activator.CreateInstance(factoryType);
            factoryType.GetMethod("Init").Invoke(factory, null);
            var provider = factoryType.GetMethod("GetSpriteEditorDataProviderFromObject").Invoke(factory, new object[] { AssetImporter.GetAtPath(path) });
            var contract = assembly.GetType("UnityEditor.U2D.Sprites.ISpriteEditorDataProvider", true);
            contract.GetMethod("InitSpriteEditorDataProvider").Invoke(provider, null);
            var outlineType = assembly.GetType("UnityEditor.U2D.Sprites.ISpritePhysicsOutlineDataProvider", true);
            var outlines = contract.GetMethod("GetDataProvider").MakeGenericMethod(outlineType).Invoke(provider, null);
            var rows = new JArray();
            foreach (var rect in (IEnumerable)contract.GetMethod("GetSpriteRects").Invoke(provider, null))
            {
                var id = rect.GetType().GetProperty("spriteID").GetValue(rect);
                if (seed)
                    outlineType
                        .GetMethod("SetOutlines")
                        .Invoke(
                            outlines,
                            new object[]
                            {
                                id,
                                new System.Collections.Generic.List<Vector2[]> { new[] { new Vector2(-6, -6), new Vector2(6, -6), new Vector2(0, 6) } },
                            }
                        );
                var polygons = (System.Collections.Generic.List<Vector2[]>)outlineType.GetMethod("GetOutlines").Invoke(outlines, new[] { id });
                rows.Add(new JArray(polygons.Select(polygon => new JArray(polygon.Select(point => new JArray(point.x, point.y))))));
            }
            if (seed)
            {
                contract.GetMethod("Apply").Invoke(provider, null);
                ((TextureImporter)AssetImporter.GetAtPath(path)).SaveAndReimport();
            }
            return rows.ToString(Newtonsoft.Json.Formatting.None);
        }

        [Test]
        public void AllGeometryReimports_PreservingIdsSiblingPhysicsAndClipReferences()
        {
            // Given two real imported sprites, a real clip, and exact provider identities.
            var before = Read();
            var first = First(before);
            var sibling = ((JArray)before["sprites"]).Single(x => x.Value<string>("name") == "Sheet_1").DeepClone();
            var references = ClipReferences();
            var source = File.ReadAllBytes(Full(path));
            var clipBytes = File.ReadAllBytes(Full(clipPath));
            string physics = Physics();
            var geometry = JObject.Parse(
                "{rect:[0.0,0.0,28.0,30.0],pivot:[0.25,0.75],border:[1.0,2.0,3.0,4.0],outlines:[[[-10.0,-10.0],[10.0,-10.0],[0.0,10.0]]]}"
            );
            // When all supported fields are changed through the public handler and reimported.
            var response = Write(before, geometry);
            // Then fresh native readback retains exact identities and all unrelated data.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(response.Value<bool>("changed"));
            var after = Read();
            Assert.AreEqual(before["guid"], after["guid"]);
            Assert.AreEqual(first["sprite_id"], First(after)["sprite_id"]);
            Assert.AreEqual(first["file_id"], First(after)["file_id"]);
            Assert.IsTrue(JToken.DeepEquals(geometry, First(after)["geometry"]), after.ToString());
            Assert.IsTrue(JToken.DeepEquals(sibling, ((JArray)after["sprites"]).Single(x => x.Value<string>("name") == "Sheet_1")));
            CollectionAssert.AreEqual(references, ClipReferences());
            CollectionAssert.AreEqual(clipBytes, File.ReadAllBytes(Full(clipPath)));
            CollectionAssert.AreEqual(source, File.ReadAllBytes(Full(path)));
            Assert.AreEqual(physics, Physics());
            Assert.That(after.Value<string>("capability_mode"), Is.EqualTo("advertised").Or.EqualTo("verified_builtin_legacy"));
        }

        [Test]
        public void OmittedFieldsAndNoOp_PreserveMetadataBytes()
        {
            // Given geometry from a fresh read and unchanged pivot values.
            var before = Read();
            byte[] metadata = File.ReadAllBytes(Full(path) + ".meta");
            // When only that same pivot is submitted.
            var response = Write(before, new JObject { ["pivot"] = First(before)["geometry"]["pivot"].DeepClone() });
            // Then no reimport or metadata change is reported or retained.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(response.Value<bool>("changed"));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(path) + ".meta"));
            Assert.IsTrue(JToken.DeepEquals(before["sprites"], Read()["sprites"]));
        }

        [Test]
        public void ExternalImporterDrift_RefusesBeforeOverwritingWriter()
        {
            // Given a revision that another writer changes after the read.
            var before = Read();
            var importer = (TextureImporter)AssetImporter.GetAtPath(path);
            importer.spritePixelsPerUnit = 73;
            importer.SaveAndReimport();
            byte[] metadata = File.ReadAllBytes(Full(path) + ".meta");
            // When a stale geometry edit is attempted.
            var response = Write(before, JObject.Parse("{pivot:[0.2,0.3]}"));
            // Then the external writer's bytes and effective setting remain.
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("revision", response.Value<string>("message"));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(path) + ".meta"));
            Assert.AreEqual(73, ((TextureImporter)AssetImporter.GetAtPath(path)).spritePixelsPerUnit);
        }

        [TestCase("{pivot:[0.2,0.3],border:[20,0,20,0]}")]
        [TestCase("{pivot:[0.2,0.3],rect:[0,0,100,10]}")]
        [TestCase("{pivot:[0.2,0.3],outlines:[[[0,0],[1,1]]]} ")]
        [TestCase("{pivot:[true,0]}")]
        [TestCase("{name:'Replacement'}")]
        public void InvalidSuppliedGeometry_RefusesBeforeAnyChange(string json)
        {
            // Given one invalid supplied field, possibly after a valid pivot.
            var before = Read();
            byte[] metadata = File.ReadAllBytes(Full(path) + ".meta");
            // When all fields are preflighted together.
            var response = Write(before, JObject.Parse(json));
            // Then no earlier field, sprite reference, or importer byte changes.
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(path) + ".meta"));
            Assert.IsTrue(JToken.DeepEquals(before["sprites"], Read()["sprites"]));
        }

        [Test]
        public void ExactFileIdMismatch_RefusesWithoutChanges()
        {
            // Given a valid sprite ID paired with another sprite's file ID.
            var before = Read();
            var metadata = File.ReadAllBytes(Full(path) + ".meta");
            var row = First(before);
            row["file_id"] = ((JArray)before["sprites"]).Single(x => x.Value<string>("name") == "Sheet_1")["file_id"].DeepClone();
            // When the mismatched identity is submitted.
            var response = Write(before, JObject.Parse("{pivot:[0.2,0.3]}"));
            // Then the importer is unchanged.
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(path) + ".meta"));
        }

        [Test]
        public void PivotOnlyEdit_PreservesEveryOmittedFieldAndClipReference()
        {
            // Given a fresh identity and unrelated geometry and references.
            var before = Read();
            var geometry = (JObject)First(before)["geometry"].DeepClone();
            var references = ClipReferences();
            // When only a new pivot is requested.
            var response = Write(before, JObject.Parse("{pivot:[0.2,0.8]}"));
            // Then the omitted rect, border and outlines persist through reimport.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var actual = First(Read())["geometry"];
            foreach (string key in new[] { "rect", "border", "outlines" })
                Assert.IsTrue(JToken.DeepEquals(geometry[key], actual[key]), key);
            Assert.AreEqual(.2f, actual["pivot"][0].Value<float>());
            CollectionAssert.AreEqual(references, ClipReferences());
        }

        [Test]
        public void GeometryUsesSourcePixels_WhenImportedTextureIsScaled()
        {
            // Given source pixels twice the imported maximum size.
            var importer = (TextureImporter)AssetImporter.GetAtPath(path);
            importer.maxTextureSize = 32;
            importer.SaveAndReimport();
            Assert.AreEqual(32, AssetDatabase.LoadAssetAtPath<Texture2D>(path).width);
            var before = Read();
            Assert.AreEqual(64, before.Value<int>("width"));
            // When a valid source-space rect would lie outside imported texture coordinates.
            var response = Write(before, JObject.Parse("{rect:[32,0,30,30]}"));
            // Then the native provider retains that exact source rect.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(32, First(Read())["geometry"]["rect"][0].Value<float>());
            Assert.AreEqual(30, First(Read())["geometry"]["rect"][2].Value<float>());
        }

        [Test]
        public void ContinuationPage_UsesRevisionAndEnumeratesExactSpriteIds()
        {
            // Given a one-sprite page from the two-sprite texture.
            var first = Send("get_geometry", new JObject { ["page_size"] = 1 });
            Assert.IsTrue(first.Value<bool>("success"), first.ToString());
            Assert.AreEqual(1, first.Value<int>("next_cursor"));
            // When the continuation includes its exact revision.
            var next = Send(
                "get_geometry",
                new JObject
                {
                    ["page_size"] = 1,
                    ["cursor"] = 1,
                    ["expected_revision"] = first["revision"],
                }
            );
            // Then every sprite is enumerated once and the final page terminates.
            Assert.IsTrue(next.Value<bool>("success"), next.ToString());
            Assert.AreEqual(JTokenType.Null, next["next_cursor"].Type);
            Assert.AreNotEqual(first["sprites"][0]["sprite_id"], next["sprites"][0]["sprite_id"]);
            Assert.AreEqual(2, next.Value<int>("sprite_count"));
            Assert.IsFalse(
                Send(
                        "get_geometry",
                        new JObject
                        {
                            ["page_size"] = 1,
                            ["cursor"] = 1,
                            ["expected_revision"] = "stale",
                        }
                    )
                    .Value<bool>("success")
            );
        }

        [Test]
        public void ProviderAvailability_IsExplicitAndReadOnly()
        {
            // Given an owned importer, whether or not the optional package is enabled.
            bool available = AppDomain.CurrentDomain.GetAssemblies().Any(x => x.GetName().Name == "Unity.2D.Sprite.Editor");
            byte[] metadata = File.ReadAllBytes(Full(path) + ".meta");
            // When geometry discovery is requested.
            var response = Send("get_geometry");
            // Then availability is explicit and discovery never writes metadata.
            Assert.AreEqual(available, response.Value<bool>("success"), response.ToString());
            if (!available)
                StringAssert.Contains("2D Sprite", response.Value<string>("message"));
            CollectionAssert.AreEqual(metadata, File.ReadAllBytes(Full(path) + ".meta"));
        }
    }
}
