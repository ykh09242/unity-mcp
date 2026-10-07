using System;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Cameras;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class CameraContractTests
    {
        private GameObject _rig;
        private GameObject _cameraGo;
        private Camera _camera;
        private readonly PrefabTestSceneFixture _sceneFixture = new PrefabTestSceneFixture();
        private Scene _ownedScene;
        private string _assetRoot;
        private string _assetRootGuid;
        private Object[] _originalSelection;
        private Object _originalActiveObject;

        [OneTimeSetUp]
        public void OneTimeSetUp() => _sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => _sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            _rig = null;
            _cameraGo = null;
            _camera = null;
            _assetRoot = null;
            _assetRootGuid = null;
            _originalSelection = Selection.objects;
            _originalActiveObject = Selection.activeObject;
            _ownedScene = _sceneFixture.Create("McpCameraContract_", Guid.NewGuid().ToString("N"));
            _rig = new GameObject("CameraContract_" + Guid.NewGuid().ToString("N"));
            _cameraGo = new GameObject("Cam");
            _cameraGo.transform.SetParent(_rig.transform);
            _camera = _cameraGo.AddComponent<Camera>();
            _camera.enabled = false;
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (_camera != null)
                    Undo.ClearUndo(_camera);
                if (_cameraGo != null)
                    Undo.ClearUndo(_cameraGo);
                if (_rig != null)
                    Object.DestroyImmediate(_rig);
                _sceneFixture.Close();
                if (!string.IsNullOrEmpty(_assetRootGuid))
                {
                    StringAssert.StartsWith("Assets/__McpCameraContract_", _assetRoot);
                    Assert.AreEqual(_assetRootGuid, AssetDatabase.AssetPathToGUID(_assetRoot));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(_assetRoot));
                }
            }
            finally
            {
                Selection.objects = _originalSelection;
                Selection.activeObject = _originalActiveObject;
            }
        }

        private JObject SetBasicLens(JToken properties)
        {
            if (CameraHelpers.HasCinemachine)
                Assert.Ignore("The public basic Camera fallback requires Cinemachine to be absent.");
            return JObject.FromObject(
                ManageCamera.HandleCommand(
                    new JObject
                    {
                        ["action"] = "set_lens",
                        ["target"] = _cameraGo.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["properties"] = properties,
                    }
                )
            );
        }

        [TestCase("fieldOfView", "true")]
        [TestCase("nearClipPlane", "true")]
        [TestCase("farClipPlane", "true")]
        [TestCase("orthographicSize", "true")]
        [TestCase("nearClipPlane", "{}")]
        [TestCase("farClipPlane", "[]")]
        [TestCase("orthographicSize", "\"Infinity\"")]
        public void InvalidBasicLensValuePreservesAllLensAndDirtyState(string property, string invalidJson)
        {
            if (CameraHelpers.HasCinemachine)
                Assert.Ignore("The public basic Camera fallback requires Cinemachine to be absent.");
            _camera.fieldOfView = 60;
            _camera.nearClipPlane = 0.3f;
            _camera.farClipPlane = 1000;
            _camera.orthographicSize = 5;
            _assetRoot = "Assets/__McpCameraContract_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(_assetRoot));
            _assetRootGuid = AssetDatabase.CreateFolder("Assets", _assetRoot.Substring("Assets/".Length));
            Assert.IsNotEmpty(_assetRootGuid);
            Assert.IsTrue(EditorSceneManager.SaveScene(_ownedScene, _assetRoot + "/" + _ownedScene.name + ".unity"));
            Assert.IsFalse(_ownedScene.isDirty, "The saved owned scene must establish a clean dirty-state oracle.");
            int cameraDirty = EditorUtility.GetDirtyCount(_camera);
            int objectDirty = EditorUtility.GetDirtyCount(_cameraGo);
            bool sceneDirty = _ownedScene.isDirty;
            var properties = new JObject
            {
                ["fieldOfView"] = 35,
                ["nearClipPlane"] = 0.2f,
                ["farClipPlane"] = 200,
                ["orthographicSize"] = 9,
            };
            properties[property] = JToken.Parse(invalidJson);
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageCamera\\].*" + property, RegexOptions.Singleline));

            var response = SetBasicLens(properties);

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(property, response.ToString());
            Assert.AreEqual(60, _camera.fieldOfView);
            Assert.AreEqual(0.3f, _camera.nearClipPlane);
            Assert.AreEqual(1000, _camera.farClipPlane);
            Assert.AreEqual(5, _camera.orthographicSize);
            Assert.AreEqual(cameraDirty, EditorUtility.GetDirtyCount(_camera));
            Assert.AreEqual(objectDirty, EditorUtility.GetDirtyCount(_cameraGo));
            Assert.AreEqual(sceneDirty, _ownedScene.isDirty);
        }

        [TestCase("null")]
        [TestCase("{}")]
        [TestCase("{\"fieldOfView\":null,\"nearClipPlane\":null,\"farClipPlane\":null,\"orthographicSize\":null}")]
        public void BasicLensNullAndOmittedValuesPreserveDefaults(string propertiesJson)
        {
            float fieldOfView = _camera.fieldOfView;
            float near = _camera.nearClipPlane;
            float far = _camera.farClipPlane;
            float size = _camera.orthographicSize;

            var response = SetBasicLens(JToken.Parse(propertiesJson));

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(fieldOfView, _camera.fieldOfView);
            Assert.AreEqual(near, _camera.nearClipPlane);
            Assert.AreEqual(far, _camera.farClipPlane);
            Assert.AreEqual(size, _camera.orthographicSize);
        }

        [Test]
        public void BasicLensValidNumbersAndZeroAreApplied()
        {
            var response = SetBasicLens(
                new JObject
                {
                    ["fieldOfView"] = "35",
                    ["nearClipPlane"] = 0,
                    ["farClipPlane"] = 200,
                    ["orthographicSize"] = 9,
                }
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(35, _camera.fieldOfView);
            Assert.AreEqual(0, _camera.nearClipPlane);
            Assert.AreEqual(200, _camera.farClipPlane);
            Assert.AreEqual(9, _camera.orthographicSize);
        }

        [TestCase("{broken")]
        [TestCase("[]")]
        [TestCase("true")]
        [TestCase("7")]
        [TestCase("null")]
        public void InvalidPropertiesDoNotCreateDefaultCamera(string properties)
        {
            var before = UnityEngine.Resources.FindObjectsOfTypeAll<GameObject>();
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageCamera\\].*properties", RegexOptions.Singleline));
            GameObject[] created = null;
            try
            {
                var response = JObject.FromObject(ManageCamera.HandleCommand(new JObject { ["action"] = "create_camera", ["properties"] = properties }));
                created = UnityEngine.Resources.FindObjectsOfTypeAll<GameObject>().Except(before).ToArray();
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("properties", response.ToString());
                Assert.IsEmpty(created);
            }
            finally
            {
                if (created != null)
                    foreach (var go in created)
                        if (go != null)
                            Object.DestroyImmediate(go);
            }
        }

        [Test]
        public void ObjectAndNullPropertiesRetainSupportedForms()
        {
            var properties = new JObject { ["priority"] = 0, ["enabled"] = false };
            Assert.AreSame(properties, CameraHelpers.ExtractProperties(new JObject { ["properties"] = properties }));
            Assert.AreEqual(properties.ToString(), CameraHelpers.ExtractProperties(new JObject { ["properties"] = properties.ToString() }).ToString());
            Assert.IsNull(CameraHelpers.ExtractProperties(new JObject()));
            Assert.IsNull(CameraHelpers.ExtractProperties(new JObject { ["properties"] = JValue.CreateNull() }));
            Assert.Throws<ArgumentException>(() => CameraHelpers.ExtractProperties(new JObject { ["properties"] = new JArray(1, 2) }));
        }

        [Test]
        public void ExplicitNameSearchDoesNotModifyNumericIdCamera()
        {
            var namedGo = new GameObject(_cameraGo.GetInstanceIDCompat().ToString());
            namedGo.transform.SetParent(_rig.transform);
            var namedCamera = namedGo.AddComponent<Camera>();
            _camera.fieldOfView = 60;
            var response = JObject.FromObject(
                CameraConfigure.SetBasicCameraLens(
                    new JObject
                    {
                        ["target"] = namedGo.name,
                        ["searchMethod"] = "by_name",
                        ["properties"] = new JObject { ["fieldOfView"] = 35 },
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(35, namedCamera.fieldOfView);
            Assert.AreEqual(60, _camera.fieldOfView);
            Assert.AreSame(
                namedGo,
                CameraHelpers.FindTargetGameObject(new JObject { ["target"] = _rig.name + "/" + namedGo.name, ["searchMethod"] = "by_path" })
            );
        }

        [Test]
        public void ImplicitIdAndHierarchyPathFindTheCamera()
        {
            Assert.AreSame(_cameraGo, CameraHelpers.FindTargetGameObject(new JObject { ["target"] = _cameraGo.GetInstanceIDCompat().ToString() }));
            Assert.AreSame(
                _cameraGo,
                CameraHelpers.FindTargetGameObject(new JObject { ["target"] = _cameraGo.GetInstanceIDCompat(), ["searchMethod"] = "by_id" })
            );
            Assert.AreSame(_cameraGo, CameraHelpers.FindTargetGameObject(new JObject { ["target"] = _rig.name + "/Cam" }));
        }

        [Test]
        public void FollowAndLookAtReferencesAcceptHierarchyPaths()
        {
            string path = _rig.name + "/Cam";
            _cameraGo.SetActive(false);
            Assert.AreSame(_cameraGo, CameraHelpers.ResolveGameObjectRef(path));
            Assert.AreSame(_cameraGo, CameraHelpers.ResolveGameObjectRef(new JValue(path)));
        }

        [Test]
        public void ComponentWriteFailureIncludesPropertyDiagnostic()
        {
            var setter = typeof(CameraConfigure).GetMethod("SetComponentProperties", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(setter);
            var properties = new JObject { ["NoSuchCameraProperty"] = 3 };

            var error = setter.Invoke(null, new object[] { _camera, properties, Array.Empty<string>() });

            Assert.IsNotNull(error, "A failed ComponentOps write must produce an error.");
            var response = JObject.FromObject(error);
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("NoSuchCameraProperty", response.ToString());
        }

        [Test]
        public void ValidEarlierComponentWriteIsPreservedWhenLaterWriteFails()
        {
            var setter = typeof(CameraConfigure).GetMethod("SetComponentProperties", BindingFlags.NonPublic | BindingFlags.Static);
            var properties = new JObject { ["fieldOfView"] = 45, ["NoSuchCameraProperty"] = 3 };

            var error = setter.Invoke(null, new object[] { _camera, properties, Array.Empty<string>() });

            Assert.IsNotNull(error);
            Assert.AreEqual(45, _camera.fieldOfView);
        }

        [Test]
        public void ValidComponentWritesPreserveZeroAndFalse()
        {
            var setter = typeof(CameraConfigure).GetMethod("SetComponentProperties", BindingFlags.NonPublic | BindingFlags.Static);
            var properties = new JObject { ["depth"] = 0, ["enabled"] = false };
            _camera.depth = 3;
            _camera.enabled = true;

            var error = setter.Invoke(null, new object[] { _camera, properties, Array.Empty<string>() });

            Assert.IsNull(error);
            Assert.AreEqual(0, _camera.depth);
            Assert.IsFalse(_camera.enabled);
        }
    }
}
