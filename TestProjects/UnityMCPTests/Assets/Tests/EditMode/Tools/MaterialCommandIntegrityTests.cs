using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class MaterialCommandIntegrityTests
    {
        private string _root;
        private bool _ownsRoot;
        private Scene _scene;
        private readonly PrefabTestSceneFixture _sceneFixture = new PrefabTestSceneFixture();
        private UnityEngine.Object[] _selection;
        private UnityEngine.Object _activeSelection;
        private Shader _shader;
        private Material _material;
        private GameObject _target;
        private MeshRenderer _renderer;
        private readonly List<UnityEngine.Object> _transient = new List<UnityEngine.Object>();
        private const string NativeInstanceLog =
            "Instantiating material due to calling renderer.material during edit mode. This will leak materials into the scene. You most likely want to use renderer.sharedMaterial instead.";

        [OneTimeSetUp]
        public void OneTimeSetUp() => _sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => _sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            _ownsRoot = false;
            _scene = default;
            _target = null;
            _renderer = null;
            _transient.Clear();
            _selection = Selection.objects;
            _activeSelection = Selection.activeObject;
            _root = "Assets/__McpMaterialCommandIntegrity_" + Guid.NewGuid().ToString("N");
            Assert.That(AssetDatabase.IsValidFolder(_root), Is.False);
            string guid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(_root));
            _ownsRoot = !string.IsNullOrEmpty(guid) && AssetDatabase.GUIDToAssetPath(guid) == _root;
            Assert.That(_ownsRoot, Is.True);
            string shaderName = "Hidden/McpMaterialCommandIntegrity_" + Guid.NewGuid().ToString("N");
            string shaderPath = _root + "/Fixture.shader";
            string absoluteShaderPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), shaderPath.Replace('/', Path.DirectorySeparatorChar));
            File.WriteAllText(
                absoluteShaderPath,
                "Shader \""
                    + shaderName
                    + "\" { Properties { "
                    + "_Color (\"Color\", Color) = (0.5,0.5,0.5,1) "
                    + "_Vector (\"Vector\", Vector) = (0,0,0,0) "
                    + "_Float (\"Float\", Float) = 0 "
                    + "_Integer (\"Integer\", Integer) = 17 "
                    + "_MainTex (\"Texture\", 2D) = \"white\" {} } SubShader { Pass {} } }"
            );
            AssetDatabase.ImportAsset(shaderPath, ImportAssetOptions.ForceSynchronousImport);
            _shader = AssetDatabase.LoadAssetAtPath<Shader>(shaderPath);
            Assert.That(_shader, Is.Not.Null);
            _material = new Material(_shader);
            _transient.Add(_material);
            AssetDatabase.CreateAsset(_material, _root + "/Fixture.mat");
            Assert.That(AssetDatabase.Contains(_material), Is.True);
            _scene = _sceneFixture.Create("McpMaterialCommandIntegrity_", Guid.NewGuid().ToString("N"));
            Assert.That(EditorSceneManager.SaveScene(_scene, _root + "/McpMaterialCommandIntegrity_Fixture.unity"), Is.True);
            _target = new GameObject("Fixture");
            _renderer = _target.AddComponent<MeshRenderer>();
            _renderer.sharedMaterials = new[] { _material, _material };
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (_renderer != null)
                    foreach (var material in _renderer.sharedMaterials)
                        if (material != null && !EditorUtility.IsPersistent(material) && !_transient.Contains(material))
                            _transient.Add(material);
                if (_target != null)
                    UnityEngine.Object.DestroyImmediate(_target);
                foreach (var item in _transient)
                    if (item != null && !EditorUtility.IsPersistent(item))
                        UnityEngine.Object.DestroyImmediate(item);
                _sceneFixture.Close();
                if (_ownsRoot)
                {
                    Assert.That(_root.StartsWith("Assets/__McpMaterialCommandIntegrity_", StringComparison.Ordinal), Is.True);
                    Assert.That(AssetDatabase.DeleteAsset(_root), Is.True);
                }
            }
            finally
            {
                Selection.objects = _selection;
                Selection.activeObject = _activeSelection;
                _ownsRoot = false;
                _transient.Clear();
            }
        }

        private JObject Call(JObject request) => JObject.FromObject(ManageMaterial.HandleCommand(request));

        private void Succeeds(JObject request) => Assert.That(Call(request).Value<bool>("success"), Is.True);

        private void Fails(JObject request) => Assert.That(Call(request).Value<bool>("success"), Is.False);

        private sealed class AssetIdentity
        {
            private readonly UnityEngine.Object _asset;
            private readonly string _path;
            private readonly string _guid;

            public AssetIdentity(UnityEngine.Object asset)
            {
                _asset = asset;
                _path = AssetDatabase.GetAssetPath(asset);
                _guid = AssetDatabase.AssetPathToGUID(_path);
            }

            public void AssertUnchanged(UnityEngine.Object actual)
            {
                Assert.That(actual == _asset, Is.True, "The native Unity object must be retained across asset reimport.");
                Assert.That(AssetDatabase.GetAssetPath(actual), Is.EqualTo(_path));
                Assert.That(AssetDatabase.AssetPathToGUID(_path), Is.EqualTo(_guid));
            }
        }

        private JObject Create(string path = null) =>
            new JObject
            {
                ["action"] = "create",
                ["materialPath"] = path ?? _root + "/Created.mat",
                ["shader"] = _shader.name,
            };

        private JObject ColorRequest(string mode, int slot) =>
            new JObject
            {
                ["action"] = "set_renderer_color",
                ["target"] = _target.GetInstanceIDCompat().ToString(),
                ["searchMethod"] = "by_id",
                ["mode"] = mode,
                ["slot"] = slot,
                ["color"] = new JArray(0, 0, 0, 0),
            };

        private string UniquePath(int slot = 0) =>
            _root + "/Materials/Fixture_" + _target.GetInstanceIDCompat() + (slot == 0 ? "" : "_slot" + slot) + "_mat.mat";

        private Material Persist(string path)
        {
            if (!AssetDatabase.IsValidFolder(_root + "/Materials"))
                Assert.That(AssetDatabase.CreateFolder(_root, "Materials"), Is.Not.Empty);
            var material = new Material(_shader);
            _transient.Add(material);
            AssetDatabase.CreateAsset(material, path);
            Assert.That(AssetDatabase.Contains(material), Is.True);
            return material;
        }

        private Texture2D PersistOccupiedTexture(string path)
        {
            var texture = new Texture2D(1, 1);
            _transient.Add(texture);
            var vendorStacks = new List<string>();
            void CaptureFixtureLog(string condition, string stack, LogType type)
            {
                if (
                    type == LogType.Exception
                    && condition == "NullReferenceException: Object reference not set to an instance of an object"
                    && stack.Contains("UnityEditor.Rendering.BuiltIn.MaterialPostprocessor.OnPostprocessAllAssets")
                    && stack.Contains("MaterialPostprocessor.cs:77")
                )
                {
                    vendorStacks.Add(stack);
                    LogAssert.Expect(LogType.Exception, condition);
                }
            }

            // The pinned ShaderGraph postprocessor assumes every .mat main asset is a
            // Material. Capture only its known setup exception for this non-Material fixture.
            Application.logMessageReceived += CaptureFixtureLog;
            try
            {
                AssetDatabase.CreateAsset(texture, path);
            }
            finally
            {
                Application.logMessageReceived -= CaptureFixtureLog;
            }
            Assert.That(vendorStacks.Count, Is.LessThanOrEqualTo(1));
            foreach (string stack in vendorStacks)
            {
                StringAssert.Contains("UnityEditor.Rendering.BuiltIn.MaterialPostprocessor.OnPostprocessAllAssets", stack);
                StringAssert.Contains("MaterialPostprocessor.cs:77", stack);
            }
            Assert.That(AssetDatabase.Contains(texture), Is.True);
            new AssetIdentity(texture).AssertUnchanged(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            LogAssert.NoUnexpectedReceived();
            return texture;
        }

        [Test]
        public void CreatePreservesUnrelatedAssetAtMaterialPath()
        {
            string path = _root + "/Occupied.mat";
            var texture = PersistOccupiedTexture(path);
            Assert.That(AssetDatabase.Contains(texture), Is.True);
            string guid = AssetDatabase.AssetPathToGUID(path);
            var identity = new AssetIdentity(texture);
            Fails(Create(path));
            identity.AssertUnchanged(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            Assert.That(AssetDatabase.AssetPathToGUID(path), Is.EqualTo(guid));
        }

        private JObject MissingTextureProperties() =>
            new JObject
            {
                ["shader"] = _shader.name,
                ["color"] = new JArray(1, 0, 0, 1),
                ["texture"] = new JObject { ["name"] = "_MainTex", ["path"] = _root + "/Missing.png" },
            };

        [Test]
        public void AssetModifyMissingStructuredTexturePreservesEarlierProperties()
        {
            string before = EditorJsonUtility.ToJson(_material);
            int dirty = EditorUtility.GetDirtyCount(_material);
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["path"] = _root + "/Fixture.mat",
                        ["properties"] = MissingTextureProperties(),
                    }
                )
            );
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(EditorJsonUtility.ToJson(_material), Is.EqualTo(before));
            Assert.That(EditorUtility.GetDirtyCount(_material), Is.EqualTo(dirty));
        }

        [Test]
        public void AssetCreateMissingStructuredTextureDoesNotImportExistingFolder()
        {
            string folder = _root + "/Unimported";
            string physical = AssetPathUtility.GetFullAssetPath(folder);
            Directory.CreateDirectory(physical);
            Assert.That(File.Exists(physical + ".meta"), Is.False);
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["assetType"] = "Material",
                        ["path"] = folder + "/Rejected.mat",
                        ["properties"] = MissingTextureProperties(),
                    }
                )
            );
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(Directory.Exists(physical), Is.True);
            Assert.That(File.Exists(physical + ".meta"), Is.False);
            Assert.That(File.Exists(physical + "/Rejected.mat"), Is.False);
            Assert.That(File.Exists(physical + "/Rejected.mat.meta"), Is.False);
        }

        [Test]
        public void AssetModifyMissingDirectTextureStillAppliesValidColor()
        {
            var texture = _material.GetTexture("_MainTex");
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["path"] = _root + "/Fixture.mat",
                        ["properties"] = new JObject { ["color"] = new JArray(1, 0, 0, 1), ["_MainTex"] = _root + "/Missing.png" },
                    }
                )
            );
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            Assert.That(_material.GetColor("_Color"), Is.EqualTo(Color.red));
            Assert.That(_material.GetTexture("_MainTex"), Is.SameAs(texture));
        }

        [TestCase("property_block")]
        [TestCase("shared")]
        [TestCase("create_unique")]
        public void NonzeroColorDoesNotPopulateUnrelatedNullSlot(string mode)
        {
            _renderer.sharedMaterials = new Material[] { null, _material };
            var identity = new AssetIdentity(_material);
            var untouched = new MaterialPropertyBlock();
            untouched.SetFloat("_Unrelated", 7);
            _renderer.SetPropertyBlock(untouched, 0);
            Succeeds(ColorRequest(mode, 1));
            Assert.That(_renderer.sharedMaterials[0], Is.Null);
            _renderer.GetPropertyBlock(untouched, 0);
            Assert.That(untouched.GetFloat("_Unrelated"), Is.EqualTo(7));
            if (mode == "shared" || mode == "property_block")
                identity.AssertUnchanged(_renderer.sharedMaterials[1]);
        }

        [Test]
        public void InstanceColorRetainsNativeMaterializationBehaviorForOtherSlots()
        {
            var control = new GameObject("NativeMaterializationControl");
            control.transform.SetParent(_target.transform);
            var controlRenderer = control.AddComponent<MeshRenderer>();
            controlRenderer.sharedMaterials = new Material[] { null, _material };
            // The pinned Editor emits the native warning once for each of these two slots.
            LogAssert.Expect(LogType.Error, NativeInstanceLog);
            LogAssert.Expect(LogType.Error, NativeInstanceLog);
            var nativeMaterials = controlRenderer.materials;
            LogAssert.NoUnexpectedReceived();
            foreach (var material in nativeMaterials)
                if (material != null && !EditorUtility.IsPersistent(material))
                    _transient.Add(material);
            string original = EditorJsonUtility.ToJson(_material);
            _renderer.sharedMaterials = new Material[] { null, _material };

            LogAssert.Expect(LogType.Error, NativeInstanceLog);
            LogAssert.Expect(LogType.Error, NativeInstanceLog);
            Succeeds(ColorRequest("instance", 1));
            LogAssert.NoUnexpectedReceived();

            var actual = _renderer.sharedMaterials;
            Assert.That(actual.Length, Is.EqualTo(nativeMaterials.Length));
            Assert.That(actual[0] == null, Is.EqualTo(nativeMaterials[0] == null));
            if (actual[0] != null)
                Assert.That(EditorJsonUtility.ToJson(actual[0]), Is.EqualTo(EditorJsonUtility.ToJson(nativeMaterials[0])));
            Assert.That(EditorUtility.IsPersistent(actual[1]), Is.False);
            Assert.That(actual[1] != _material, Is.True);
            Assert.That(actual[1].GetColor("_Color"), Is.EqualTo(new Color(0, 0, 0, 0)));
            Assert.That(EditorJsonUtility.ToJson(_material), Is.EqualTo(original));
        }

        [TestCase("shared")]
        [TestCase("instance")]
        public void MissingSelectedSlotFailsWithoutMaterialInstantiation(string mode)
        {
            _renderer.sharedMaterials = new Material[] { _material, null };
            var identity = new AssetIdentity(_material);
            int dirty = EditorUtility.GetDirtyCount(_renderer);
            Fails(ColorRequest(mode, 1));
            identity.AssertUnchanged(_renderer.sharedMaterials[0]);
            Assert.That(_renderer.sharedMaterials[1], Is.Null);
            Assert.That(EditorUtility.GetDirtyCount(_renderer), Is.EqualTo(dirty));
        }

        [Test]
        public void UniqueEmptySlotIsAssignedOnlyPersistentMaterial()
        {
            _renderer.sharedMaterials = Array.Empty<Material>();
            Succeeds(ColorRequest("create_unique", 0));
            Assert.That(_renderer.sharedMaterials.Length, Is.EqualTo(1));
            Assert.That(AssetDatabase.Contains(_renderer.sharedMaterial), Is.True);
            Assert.That(AssetDatabase.GetAssetPath(_renderer.sharedMaterial), Is.EqualTo(UniquePath()));
        }

        [Test]
        public void UniqueBlockedMaterialFolderPreservesFileAndCreatesNoAlternateFolder()
        {
            string blocking = AssetPathUtility.GetFullAssetPath(_root + "/Materials");
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                File.WriteAllText(blocking, "Existing file at the requested folder.");
                string[] before = Directory.GetFileSystemEntries(Path.GetDirectoryName(blocking));

                Fails(ColorRequest("create_unique", 0));

                CollectionAssert.AreEquivalent(before, Directory.GetFileSystemEntries(Path.GetDirectoryName(blocking)));
                Assert.That(File.ReadAllText(blocking), Is.EqualTo("Existing file at the requested folder."));
                Assert.That(_renderer.sharedMaterials, Is.EqualTo(new[] { _material, _material }));
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [TestCase(0, 1)]
        [TestCase(1, 0)]
        public void GeneratedColorsDoNotAliasAnotherSlot(int first, int second)
        {
            Succeeds(ColorRequest("create_unique", first));
            var original = _renderer.sharedMaterials[first];
            var identity = new AssetIdentity(original);
            string before = EditorJsonUtility.ToJson(original);
            var request = ColorRequest("create_unique", second);
            request["color"] = new JArray(1, 0, 0, 1);
            Succeeds(request);
            identity.AssertUnchanged(_renderer.sharedMaterials[first]);
            Assert.That(_renderer.sharedMaterials[second] != original, Is.True);
            Assert.That(EditorJsonUtility.ToJson(original), Is.EqualTo(before));
        }

        [Test]
        public void SharedLegacyRetryPreservesOtherSlotAndThenReusesSafeMaterial()
        {
            var legacy = Persist(UniquePath());
            var legacyIdentity = new AssetIdentity(legacy);
            _renderer.sharedMaterials = new[] { legacy, legacy };
            string before = EditorJsonUtility.ToJson(legacy);
            Succeeds(ColorRequest("create_unique", 0));
            var selected = _renderer.sharedMaterials[0];
            Assert.That(selected != legacy, Is.True);
            var selectedIdentity = new AssetIdentity(selected);
            Succeeds(ColorRequest("create_unique", 0));
            selectedIdentity.AssertUnchanged(_renderer.sharedMaterials[0]);
            legacyIdentity.AssertUnchanged(_renderer.sharedMaterials[1]);
            Assert.That(EditorJsonUtility.ToJson(legacy), Is.EqualTo(before));
        }

        [Test]
        public void ValidLegacyRetryRetainsMaterialIdentityAndGuid()
        {
            var legacy = Persist(UniquePath());
            var identity = new AssetIdentity(legacy);
            string guid = AssetDatabase.AssetPathToGUID(UniquePath());
            Succeeds(ColorRequest("create_unique", 0));
            identity.AssertUnchanged(_renderer.sharedMaterials[0]);
            Assert.That(AssetDatabase.AssetPathToGUID(UniquePath()), Is.EqualTo(guid));
        }

        [Test]
        public void UniquePreservesUnrelatedOccupiedAsset()
        {
            Persist(_root + "/Materials/FolderControl.mat");
            var texture = PersistOccupiedTexture(UniquePath());
            Assert.That(AssetDatabase.Contains(texture), Is.True);
            var identity = new AssetIdentity(texture);
            var materialIdentity = new AssetIdentity(_material);
            identity.AssertUnchanged(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(UniquePath()));
            string guid = AssetDatabase.AssetPathToGUID(UniquePath());
            Fails(ColorRequest("create_unique", 0));
            identity.AssertUnchanged(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(UniquePath()));
            Assert.That(AssetDatabase.AssetPathToGUID(UniquePath()), Is.EqualTo(guid));
            materialIdentity.AssertUnchanged(_renderer.sharedMaterials[0]);
        }

        [TestCase(-1)]
        [TestCase(2)]
        public void InvalidAssignmentLeavesSlotsAndDirtyCountUnchanged(int slot)
        {
            int dirty = EditorUtility.GetDirtyCount(_renderer);
            Fails(
                new JObject
                {
                    ["action"] = "assign_material_to_renderer",
                    ["target"] = _target.GetInstanceIDCompat().ToString(),
                    ["searchMethod"] = "by_id",
                    ["materialPath"] = _root + "/Fixture.mat",
                    ["slot"] = slot,
                }
            );
            Assert.That(_renderer.sharedMaterials, Is.EqualTo(new[] { _material, _material }));
            Assert.That(EditorUtility.GetDirtyCount(_renderer), Is.EqualTo(dirty));
        }

        [TestCase("_Float", false)]
        [TestCase("_MainTex", false)]
        [TestCase("_Float", true)]
        [TestCase("_MainTex", true)]
        public void WrongDeclaredColorTypeIsRejectedBeforeMutation(string property, bool create)
        {
            string before = EditorJsonUtility.ToJson(_material);
            int dirty = EditorUtility.GetDirtyCount(_material);
            var request = create ? Create() : new JObject { ["action"] = "set_material_color", ["materialPath"] = _root + "/Fixture.mat" };
            request["property"] = property;
            request["color"] = new JArray(1, 0, 0);
            Fails(request);
            Assert.That(EditorJsonUtility.ToJson(_material), Is.EqualTo(before));
            Assert.That(EditorUtility.GetDirtyCount(_material), Is.EqualTo(dirty));
            Assert.That(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(_root + "/Created.mat"), Is.Null);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DeclaredVectorRetainsAcceptedColorAlpha(bool create)
        {
            var request = create ? Create() : new JObject { ["action"] = "set_material_color", ["materialPath"] = _root + "/Fixture.mat" };
            request["property"] = "_Vector";
            request["color"] = new JArray(1, 0, -2);
            Succeeds(request);
            var material = create ? AssetDatabase.LoadAssetAtPath<Material>(_root + "/Created.mat") : _material;
            Assert.That(material.GetVector("_Vector"), Is.EqualTo(new Vector4(1, 0, -2, 1)));
        }

        [Test]
        public void MissingPropertyDoesNotDirtyMaterial()
        {
            int dirty = EditorUtility.GetDirtyCount(_material);
            Fails(
                new JObject
                {
                    ["action"] = "set_material_shader_property",
                    ["materialPath"] = _root + "/Fixture.mat",
                    ["property"] = "_Missing",
                    ["value"] = 1,
                }
            );
            Fails(
                new JObject
                {
                    ["action"] = "set_material_color",
                    ["materialPath"] = _root + "/Fixture.mat",
                    ["property"] = "_Missing",
                    ["color"] = new JArray(0, 0, 0),
                }
            );
            Assert.That(EditorUtility.GetDirtyCount(_material), Is.EqualTo(dirty));
        }

        [Test]
        public void TrueIntegerSetAndInfoPreserveExactValue()
        {
            Assert.That(_shader.GetPropertyType(_shader.FindPropertyIndex("_Integer")), Is.EqualTo(UnityEngine.Rendering.ShaderPropertyType.Int));
            Succeeds(
                new JObject
                {
                    ["action"] = "set_material_shader_property",
                    ["materialPath"] = _root + "/Fixture.mat",
                    ["property"] = "_Integer",
                    ["value"] = 16777217,
                }
            );
            var response = Call(new JObject { ["action"] = "get_material_info", ["materialPath"] = _root + "/Fixture.mat" });
            Assert.That(response.Value<bool>("success"), Is.True);
            Assert.That(_material.GetInteger("_Integer"), Is.EqualTo(16777217));
            Assert.That(response["data"]["properties"].First(p => p.Value<string>("name") == "_Integer").Value<int>("value"), Is.EqualTo(16777217));
        }

        [Test]
        public void PredictablyMissingTextureDoesNotPersistCreatedMaterial()
        {
            var request = Create();
            request["properties"] = new JObject
            {
                ["texture"] = new JObject { ["name"] = "_MainTex", ["path"] = _root + "/Missing.png" },
            };
            Fails(request);
            Assert.That(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(_root + "/Created.mat"), Is.Null);
        }

        [Test]
        public void CreatePropertiesOverrideSeparateColorIncludingZeroAlpha()
        {
            var request = Create();
            request["color"] = new JArray(1, 0, 0);
            request["properties"] = new JObject { ["_Color"] = new JArray(0, 0, 0, 0) };
            Succeeds(request);
            Assert.That(AssetDatabase.LoadAssetAtPath<Material>(_root + "/Created.mat").GetColor("_Color"), Is.EqualTo(new Color(0, 0, 0, 0)));
        }

        [TestCase(0, 0)]
        [TestCase(1, 1)]
        public void NativeIntegerSlotTokenPreservesIntegerIdentity(object token, int expected)
        {
            if (expected == 0 && (!_shader.isSupported || RenderPipelineUtility.IsMaterialInvalidForActivePipeline(_material, out _)))
                Assert.Ignore("The owned shader is not compatible with the active pipeline; do not create a cached fallback.");
            var request = ColorRequest("property_block", 0);
            request["slot"] = JToken.FromObject(token);
            Succeeds(request);
            var selected = new MaterialPropertyBlock();
            _renderer.GetPropertyBlock(selected, expected);
            Assert.That(selected.isEmpty, Is.False);
            var other = new MaterialPropertyBlock();
            _renderer.GetPropertyBlock(other, 1 - expected);
            Assert.That(other.isEmpty, Is.True);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void BooleanSlotTokenIsRejectedBeforeRendererMutation(bool token)
        {
            var before = _renderer.sharedMaterials;
            int dirty = EditorUtility.GetDirtyCount(_renderer);
            var request = ColorRequest("property_block", 0);
            request["slot"] = token;
            Fails(request);
            CollectionAssert.AreEqual(before, _renderer.sharedMaterials);
            Assert.That(EditorUtility.GetDirtyCount(_renderer), Is.EqualTo(dirty));
            var block = new MaterialPropertyBlock();
            for (int slot = 0; slot < before.Length; slot++)
            {
                _renderer.GetPropertyBlock(block, slot);
                Assert.That(block.isEmpty, Is.True);
            }
        }

        [Test]
        public void InvalidRendererModeDoesNotPopulateNullSlot()
        {
            _renderer.sharedMaterials = new Material[] { null, _material };
            int dirty = EditorUtility.GetDirtyCount(_renderer);
            Fails(ColorRequest("invalid", 0));
            Assert.That(_renderer.sharedMaterials[0], Is.Null);
            Assert.That(EditorUtility.GetDirtyCount(_renderer), Is.EqualTo(dirty));
        }
    }
}
