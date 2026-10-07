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

        private object PrepareCameraProperties(JObject properties, out System.Collections.Generic.List<Action<Component>> setters, Type componentType = null)
        {
            var prepare = typeof(CameraConfigure).GetMethod("PrepareComponentProperties", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(prepare);
            var arguments = new object[] { componentType ?? typeof(Camera), properties, Array.Empty<string>(), null };
            var error = prepare.Invoke(null, arguments);
            setters = (System.Collections.Generic.List<Action<Component>>)arguments[3];
            return error;
        }

        private object PrepareCameraPropertiesForTarget(Component component, JObject properties, out System.Collections.Generic.List<Action<Component>> setters)
        {
            var prepare = typeof(CameraConfigure).GetMethod("PrepareComponentPropertiesForTarget", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(prepare);
            var arguments = new object[] { typeof(Camera), component, properties, Array.Empty<string>(), null, _cameraGo };
            var error = prepare.Invoke(null, arguments);
            setters = (System.Collections.Generic.List<Action<Component>>)arguments[4];
            return error;
        }

        [TestCase(false)]
        [TestCase(true)]
        public void NullNestedReferenceRejectsBeforeAnyEarlierCameraWriteOrCreation(bool existingTarget)
        {
            Assert.IsNull(_camera.targetTexture);
            float original = _camera.fieldOfView;
            int dirty = EditorUtility.GetDirtyCount(_camera);
            var components = _cameraGo.GetComponents<Component>();
            var error = PrepareCameraPropertiesForTarget(
                existingTarget ? _camera : null,
                new JObject { ["fieldOfView"] = 45, ["targetTexture.name"] = "Changed" },
                out _
            );
            Assert.IsNotNull(error);
            StringAssert.Contains("targetTexture.name", JObject.FromObject(error).ToString());
            Assert.AreEqual(original, _camera.fieldOfView);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(_camera));
            CollectionAssert.AreEqual(components, _cameraGo.GetComponents<Component>());
        }

        [Test]
        public void EarlierNullReferenceAssignmentRejectsNestedWriteDespiteExistingReference()
        {
            var texture = new RenderTexture(4, 4, 0) { name = "Original" };
            try
            {
                _camera.targetTexture = texture;
                float original = _camera.fieldOfView;
                var error = PrepareCameraPropertiesForTarget(
                    _camera,
                    new JObject
                    {
                        ["fieldOfView"] = 45,
                        ["targetTexture"] = JValue.CreateNull(),
                        ["targetTexture.name"] = "Changed",
                    },
                    out _
                );
                Assert.IsNotNull(error);
                Assert.AreEqual(original, _camera.fieldOfView);
                Assert.AreSame(texture, _camera.targetTexture);
                Assert.AreEqual("Original", texture.name);
            }
            finally
            {
                _camera.targetTexture = null;
                Object.DestroyImmediate(texture);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void EarlierReferenceAssignmentSupportsNestedWriteWithoutPreflightMutation(bool existingTarget)
        {
            var texture = new RenderTexture(4, 4, 0) { name = "Original" };
            try
            {
                var error = PrepareCameraPropertiesForTarget(
                    existingTarget ? _camera : null,
                    new JObject
                    {
                        ["targetTexture"] = new JObject { ["instanceID"] = texture.GetInstanceIDCompat() },
                        ["targetTexture.name"] = "Changed",
                        ["backgroundColor.r"] = 0.25f,
                    },
                    out var setters
                );
                Assert.IsNull(error);
                Assert.IsNull(_camera.targetTexture);
                Assert.AreEqual("Original", texture.name);
                foreach (var setter in setters)
                    setter(_camera);
                Assert.AreSame(texture, _camera.targetTexture);
                Assert.AreEqual("Changed", texture.name);
                Assert.AreEqual(0.25f, _camera.backgroundColor.r);
            }
            finally
            {
                _camera.targetTexture = null;
                Object.DestroyImmediate(texture);
            }
        }

        [Test]
        public void ExistingNestedReferenceSupportsWriteWithoutPreflightMutation()
        {
            var texture = new RenderTexture(4, 4, 0) { name = "Original" };
            try
            {
                _camera.targetTexture = texture;
                var error = PrepareCameraPropertiesForTarget(_camera, new JObject { ["targetTexture.name"] = "Changed" }, out var setters);
                Assert.IsNull(error);
                Assert.AreEqual("Original", texture.name);
                foreach (var setter in setters)
                    setter(_camera);
                Assert.AreEqual("Changed", texture.name);
            }
            finally
            {
                _camera.targetTexture = null;
                Object.DestroyImmediate(texture);
            }
        }

        [Test]
        public void NewComponentPreparationSupportsKnownGameObjectAndTransformOwners()
        {
            string originalName = _cameraGo.name;
            Vector3 originalPosition = _cameraGo.transform.localPosition;
            var error = PrepareCameraPropertiesForTarget(
                null,
                new JObject { ["gameObject.name"] = "Changed", ["transform.localPosition.x"] = 2f },
                out var setters
            );
            Assert.IsNull(error);
            Assert.AreEqual(originalName, _cameraGo.name);
            Assert.AreEqual(originalPosition, _cameraGo.transform.localPosition);
            foreach (var setter in setters)
                setter(_camera);
            Assert.AreEqual("Changed", _cameraGo.name);
            Assert.AreEqual(2f, _cameraGo.transform.localPosition.x);
        }

        [Test]
        public void PreparedComponentPropertiesRejectInvalidLaterKeyBeforeWriting()
        {
            float original = _camera.fieldOfView;
            int dirty = EditorUtility.GetDirtyCount(_camera);
            var error = PrepareCameraProperties(new JObject { ["fieldOfView"] = 45, ["NoSuchCameraProperty"] = 3 }, out _);
            Assert.IsNotNull(error);
            StringAssert.Contains("NoSuchCameraProperty", JObject.FromObject(error).ToString());
            Assert.AreEqual(original, _camera.fieldOfView);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(_camera));
        }

        [Test]
        public void PreparedComponentPropertiesRejectInvalidLaterScalarBeforeWriting()
        {
            Assert.IsNotNull(typeof(CameraConfigure).GetMethod("PrepareComponentProperties", BindingFlags.NonPublic | BindingFlags.Static));
            float original = _camera.fieldOfView;
            int dirty = EditorUtility.GetDirtyCount(_camera);
            LogAssert.Expect(LogType.Error, new Regex("Error converting token to System.Boolean", RegexOptions.Singleline));
            var error = PrepareCameraProperties(new JObject { ["fieldOfView"] = 45, ["enabled"] = "not-a-boolean" }, out _);
            Assert.IsNotNull(error);
            StringAssert.Contains("enabled", JObject.FromObject(error).ToString());
            Assert.AreEqual(original, _camera.fieldOfView);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(_camera));
        }

        [Test]
        public void PreparedComponentPropertiesConvertAllValuesWithoutWriting()
        {
            float original = _camera.fieldOfView;
            int dirty = EditorUtility.GetDirtyCount(_camera);
            var error = PrepareCameraProperties(
                new JObject
                {
                    ["field_of_view"] = "45",
                    ["depth"] = 0,
                    ["enabled"] = false,
                },
                out var setters
            );
            Assert.IsNull(error);
            Assert.AreEqual(original, _camera.fieldOfView);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(_camera));
            foreach (var setter in setters)
                setter(_camera);
            Assert.AreEqual(45, _camera.fieldOfView);
            Assert.AreEqual(0, _camera.depth);
            Assert.IsFalse(_camera.enabled);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PreparedComponentPropertiesResolveTypedObjectReferencesBeforeWriting(bool bareId)
        {
            var texture = new RenderTexture(4, 4, 0);
            try
            {
                JToken reference = bareId ? new JValue(texture.GetInstanceIDCompat()) : new JObject { ["instanceID"] = texture.GetInstanceIDCompat() };
                var error = PrepareCameraProperties(new JObject { ["targetTexture"] = reference }, out var setters);
                Assert.IsNull(error);
                Assert.IsNull(_camera.targetTexture);
                foreach (var setter in setters)
                    setter(_camera);
                Assert.AreSame(texture, _camera.targetTexture);
            }
            finally
            {
                _camera.targetTexture = null;
                Object.DestroyImmediate(texture);
            }
        }

        [Test]
        public void PreparedComponentReferencesResolveNamesAndComponentFiltersBeforeWriting()
        {
            var reference = new GameObject("CameraPreparedReference_" + Guid.NewGuid().ToString("N"));
            reference.transform.SetParent(_rig.transform);
            var body = reference.AddComponent<Rigidbody>();
            body.isKinematic = true;
            var joint = _cameraGo.AddComponent<FixedJoint>();
            var error = PrepareCameraProperties(
                new JObject
                {
                    ["connectedBody"] = new JObject { ["name"] = reference.name, ["component"] = "Rigidbody" },
                },
                out var setters,
                typeof(FixedJoint)
            );
            Assert.IsNull(error);
            Assert.IsNull(joint.connectedBody);
            foreach (var setter in setters)
                setter(joint);
            Assert.AreSame(body, joint.connectedBody);
        }

        [Test]
        public void PreparedComponentReferencesRejectWrongFilterBeforeEarlierWrite()
        {
            var joint = _cameraGo.AddComponent<FixedJoint>();
            float original = joint.breakForce;
            var error = PrepareCameraProperties(
                new JObject
                {
                    ["breakForce"] = 12,
                    ["connectedBody"] = new JObject { ["instanceID"] = _cameraGo.GetInstanceIDCompat(), ["component"] = "Camera" },
                },
                out _,
                typeof(FixedJoint)
            );
            Assert.IsNotNull(error);
            Assert.AreEqual(original, joint.breakForce);
            Assert.IsNull(joint.connectedBody);
        }

        [Test]
        public void PreparedComponentPropertiesRejectWrongReferenceTypeWithoutWriting()
        {
            float original = _camera.fieldOfView;
            var error = PrepareCameraProperties(
                new JObject
                {
                    ["fieldOfView"] = 45,
                    ["targetTexture"] = new JObject { ["instanceID"] = _cameraGo.GetInstanceIDCompat() },
                },
                out _
            );
            Assert.IsNotNull(error);
            Assert.AreEqual(original, _camera.fieldOfView);
            Assert.IsNull(_camera.targetTexture);
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
