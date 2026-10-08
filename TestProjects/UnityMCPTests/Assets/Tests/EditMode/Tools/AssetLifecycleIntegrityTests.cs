using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;
#if UNITY_6000_0_OR_NEWER
using PhysicsMaterialType = UnityEngine.PhysicsMaterial;
#else
using PhysicsMaterialType = UnityEngine.PhysicMaterial;
#endif

namespace MCPForUnityTests.EditMode.Tools
{
    public class AssetLifecycleIntegrityTests
    {
        private string _root;
        private string _guid;
        private Object[] _selection;
        private Object _activeSelection;
        private Scene _scene;
        private bool _captured;

        [SetUp]
        public void SetUp()
        {
            _captured = false;
            _guid = null;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("The fixture does not modify an existing prefab stage.");
            string project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string cwd = Path.GetFullPath(Directory.GetCurrentDirectory());
            if (!string.Equals(project.TrimEnd('/', '\\'), cwd.TrimEnd('/', '\\'), StringComparison.OrdinalIgnoreCase))
                Assert.Ignore("The asset handler requires the current directory to be the owned Unity project.");

            _selection = Selection.objects;
            _activeSelection = Selection.activeObject;
            _scene = SceneManager.GetActiveScene();
            _captured = true;
            string name = "__McpAssetLifecycleIntegrity_" + Guid.NewGuid().ToString("N");
            _root = "Assets/" + name;
            Assert.IsFalse(AssetDatabase.IsValidFolder(_root));
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(_root));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(_root, AssetPathToGUIDOptions.OnlyExistingAssets));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, name)));
            _guid = AssetDatabase.CreateFolder("Assets", name);
            Assert.IsNotEmpty(_guid);
            Assert.AreEqual(_guid, AssetDatabase.AssetPathToGUID(_root));
            Assert.AreEqual(_root, AssetDatabase.GUIDToAssetPath(_guid));
        }

        [TearDown]
        public void TearDown()
        {
            if (!_captured)
                return;
            try
            {
                if (!string.IsNullOrEmpty(_guid))
                {
                    Assert.AreEqual(_guid, AssetDatabase.AssetPathToGUID(_root));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(_root));
                }
            }
            finally
            {
                if (_scene.IsValid() && _scene.isLoaded)
                    SceneManager.SetActiveScene(_scene);
                Selection.objects = _selection ?? Array.Empty<Object>();
                Selection.activeObject = _activeSelection;
                _captured = false;
            }
        }

        private static JObject Send(string action, string path, string type = null, JToken properties = null)
        {
            var request = new JObject { ["action"] = action, ["path"] = path };
            if (type != null)
                request["assetType"] = type;
            if (properties != null)
                request["properties"] = properties;
            return JObject.FromObject(ManageAsset.HandleCommand(request));
        }

        private string Absolute(string path)
        {
            Assert.IsTrue(path.StartsWith(_root + "/", StringComparison.Ordinal));
            return Path.Combine(Application.dataPath, path.Substring("Assets/".Length));
        }

        private void AssertPersistentResponse(JObject response, string path)
        {
            Assert.IsTrue((bool)response["success"], response.ToString());
            var asset = AssetDatabase.LoadMainAssetAtPath(path);
            Assert.IsNotNull(asset);
            Assert.IsTrue(EditorUtility.IsPersistent(asset));
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(asset));
            Assert.AreEqual(path, (string)response["data"]["path"]);
            Assert.AreEqual(AssetDatabase.AssetPathToGUID(path), (string)response["data"]["guid"]);
            Assert.AreEqual(asset.GetInstanceIDCompat(), (int)response["data"]["instanceID"]);
        }

        [TestCase("Unsupported")]
        [TestCase("Prefab")]
        public void UnsupportedTypeDoesNotPrepareDirectory(string type)
        {
            string parent = _root + "/Unprepared";
            Assert.IsFalse((bool)Send("create", parent + "/One.asset", type)["success"]);
            Assert.IsFalse(Directory.Exists(Absolute(parent)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(parent, AssetPathToGUIDOptions.OnlyExistingAssets));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("\"{bad\"")]
        [TestCase("\"[]\"")]
        [TestCase("\"null\"")]
        [TestCase("[]")]
        [TestCase("false")]
        [TestCase("0")]
        public void MalformedCreatePropertiesDoNotPrepareDirectory(string json)
        {
            string parent = _root + "/Unprepared";
            Assert.IsFalse((bool)Send("create", parent + "/One.mat", "Material", JToken.Parse(json))["success"]);
            Assert.IsFalse(Directory.Exists(Absolute(parent)));
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(parent + "/One.mat"));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("omitted")]
        [TestCase("null")]
        [TestCase("{}")]
        [TestCase("\"{}\"")]
        public void ValidMaterialContainersPreservePersistentIdentity(string json)
        {
            if (RenderPipelineUtility.ResolveShader(null) == null)
                Assert.Ignore("A compatible material shader is required for this positive control.");
            string path = _root + "/One.mat";
            AssertPersistentResponse(Send("create", path, "Material", json == "omitted" ? null : JToken.Parse(json)), path);
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void ExistingCollisionPreservesGuidObjectAndBytes()
        {
            string path = _root + "/One.physicMaterial";
            AssertPersistentResponse(Send("create", path, "PhysicsMaterial"), path);
            var asset = AssetDatabase.LoadMainAssetAtPath(path);
            string guid = AssetDatabase.AssetPathToGUID(path);
            byte[] bytes = File.ReadAllBytes(Absolute(path));
            Assert.IsFalse((bool)Send("create", path, "PhysicsMaterial")["success"]);
            Assert.AreSame(asset, AssetDatabase.LoadMainAssetAtPath(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Absolute(path)));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void DeletedDestinationCanBeRecreated()
        {
            string path = _root + "/One.physicMaterial";
            AssertPersistentResponse(Send("create", path, "PhysicsMaterial"), path);
            Assert.IsTrue(AssetDatabase.DeleteAsset(path));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
            AssertPersistentResponse(Send("create", path, "PhysicsMaterial"), path);
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void PhysicsMaterialFloatZeroAndDefaultsRemainSupported()
        {
            string path = _root + "/One.physicMaterial";
            AssertPersistentResponse(Send("create", path, "PhysicsMaterial", new JObject { ["dynamicFriction"] = 0.0f, ["staticFriction"] = .25f }), path);
            var asset = AssetDatabase.LoadMainAssetAtPath(path);
            Assert.AreEqual(0f, (float)asset.GetType().GetProperty("dynamicFriction").GetValue(asset));
            Assert.AreEqual(.25f, (float)asset.GetType().GetProperty("staticFriction").GetValue(asset));
            LogAssert.NoUnexpectedReceived();
        }

        [TestCase("dynamicFriction")]
        [TestCase("staticFriction")]
        [TestCase("bounciness")]
        public void MalformedPhysicsCoefficientDoesNotPrepareDirectoryOrAsset(string field)
        {
            foreach (string json in new[] { "[]", "{}", "false", "\"0.2\"", "1e100", "-1e100" })
                AssertPhysicsCreateRejected(new JObject { ["dynamicFriction"] = .2f, [field] = JToken.Parse(json) });
            foreach (double number in new[] { double.NaN, double.PositiveInfinity, double.NegativeInfinity })
                AssertPhysicsCreateRejected(new JObject { ["dynamicFriction"] = .2f, [field] = number });
        }

        [TestCase("frictionCombine")]
        [TestCase("bounceCombine")]
        public void MalformedPhysicsCombineDoesNotPrepareDirectoryOrAsset(string field)
        {
            foreach (string json in new[] { "[]", "{}", "false", "123", "\"NotACombineMode\"", "\"\"" })
                AssertPhysicsCreateRejected(new JObject { ["dynamicFriction"] = .2f, [field] = JToken.Parse(json) });
        }

        [TestCase("staticFriction", "1e100")]
        [TestCase("bounciness", "1e100")]
        [TestCase("frictionCombine", "{}")]
        [TestCase("bounceCombine", "\"NotACombineMode\"")]
        public void PhysicsPropertiesValidateBeforeAnySetter(string field, string json)
        {
            var material = new PhysicsMaterialType();
            try
            {
                float dynamicFriction = material.dynamicFriction;
                float staticFriction = material.staticFriction;
                float bounciness = material.bounciness;
                var frictionCombine = material.frictionCombine;
                var bounceCombine = material.bounceCombine;
                var apply = typeof(ManageAsset).GetMethod("ApplyPhysicsMaterialProperties", BindingFlags.NonPublic | BindingFlags.Static);
                var properties = new JObject { ["dynamicFriction"] = .2f, [field] = JToken.Parse(json) };

                var error = Assert.Throws<TargetInvocationException>(() => apply.Invoke(null, new object[] { material, properties }));

                Assert.IsInstanceOf<ArgumentException>(error.InnerException);
                Assert.AreEqual(dynamicFriction, material.dynamicFriction);
                Assert.AreEqual(staticFriction, material.staticFriction);
                Assert.AreEqual(bounciness, material.bounciness);
                Assert.AreEqual(frictionCombine, material.frictionCombine);
                Assert.AreEqual(bounceCombine, material.bounceCombine);
                LogAssert.NoUnexpectedReceived();
            }
            finally
            {
                Object.DestroyImmediate(material);
            }
        }

        [TestCase("ave", "Average")]
        [TestCase("Ave", "Average")]
        [TestCase("average", "Average")]
        [TestCase("Average", "Average")]
        [TestCase("mul", "Multiply")]
        [TestCase("Mul", "Multiply")]
        [TestCase("mult", "Multiply")]
        [TestCase("Mult", "Multiply")]
        [TestCase("multiply", "Multiply")]
        [TestCase("Multiply", "Multiply")]
        [TestCase("min", "Minimum")]
        [TestCase("Min", "Minimum")]
        [TestCase("minimum", "Minimum")]
        [TestCase("Minimum", "Minimum")]
        [TestCase("max", "Maximum")]
        [TestCase("Max", "Maximum")]
        [TestCase("maximum", "Maximum")]
        [TestCase("Maximum", "Maximum")]
        public void PhysicsCombineAliasesRemainSupported(string alias, string expected)
        {
            string path = _root + "/One.physicMaterial";
            AssertPersistentResponse(Send("create", path, "PhysicsMaterial", new JObject { ["frictionCombine"] = alias, ["bounceCombine"] = alias }), path);
            var asset = (PhysicsMaterialType)AssetDatabase.LoadMainAssetAtPath(path);
            Assert.AreEqual(expected, asset.frictionCombine.ToString());
            Assert.AreEqual(expected, asset.bounceCombine.ToString());
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void PhysicsNullPropertiesAndUnrelatedKeysPreserveDefaults()
        {
            var defaults = new PhysicsMaterialType();
            try
            {
                var properties = new JObject
                {
                    ["dynamicFriction"] = JValue.CreateNull(),
                    ["staticFriction"] = JValue.CreateNull(),
                    ["bounciness"] = JValue.CreateNull(),
                    ["frictionCombine"] = JValue.CreateNull(),
                    ["bounceCombine"] = JValue.CreateNull(),
                    ["unrelated"] = new JObject(),
                };
                string path = _root + "/One.physicMaterial";
                AssertPersistentResponse(Send("create", path, "PhysicsMaterial", properties), path);
                var asset = (PhysicsMaterialType)AssetDatabase.LoadMainAssetAtPath(path);
                Assert.AreEqual(defaults.dynamicFriction, asset.dynamicFriction);
                Assert.AreEqual(defaults.staticFriction, asset.staticFriction);
                Assert.AreEqual(defaults.bounciness, asset.bounciness);
                Assert.AreEqual(defaults.frictionCombine, asset.frictionCombine);
                Assert.AreEqual(defaults.bounceCombine, asset.bounceCombine);
                LogAssert.NoUnexpectedReceived();
            }
            finally
            {
                Object.DestroyImmediate(defaults);
            }
        }

        private void AssertPhysicsCreateRejected(JObject properties)
        {
            string parent = _root + "/Unprepared";
            string path = parent + "/One.physicMaterial";
            var entries = Directory.GetFileSystemEntries(Path.Combine(Application.dataPath, _root.Substring("Assets/".Length))).Length;

            var response = Send("create", path, "PhysicsMaterial", properties);

            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.IsFalse(Directory.Exists(Absolute(parent)));
            Assert.IsFalse(File.Exists(Absolute(parent) + ".meta"));
            Assert.IsFalse(File.Exists(Absolute(path)));
            Assert.IsFalse(File.Exists(Absolute(path) + ".meta"));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(parent, AssetPathToGUIDOptions.OnlyExistingAssets));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(path));
            Assert.AreEqual(entries, Directory.GetFileSystemEntries(Path.Combine(Application.dataPath, _root.Substring("Assets/".Length))).Length);
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void InvalidNativeExtensionCannotReportCreateSuccess()
        {
            string path = _root + "/One.txt";
            bool previous = LogAssert.ignoreFailingMessages;
            JObject response;
            try
            {
                // Unity2021.3 logs this error; later engines may throw. Both must produce failure.
                LogAssert.ignoreFailingMessages = true;
                response = Send("create", path, "PhysicsMaterial");
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previous;
            }
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(path));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets));
        }

        [Test]
        public void NestedFolderAndExistingFolderKeepGuid()
        {
            string path = _root + "/Parent/Child";
            Assert.IsTrue((bool)Send("create", path, "Folder")["success"]);
            Assert.IsTrue(AssetDatabase.IsValidFolder(path));
            string guid = AssetDatabase.AssetPathToGUID(path);
            Assert.IsTrue((bool)Send("create_folder", path)["success"]);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            LogAssert.NoUnexpectedReceived();
        }
    }
}
