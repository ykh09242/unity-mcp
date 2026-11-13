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

        [Test]
        public void FailedPipelineSwapRollsBackOnlyItsOwnUndoGroupAndCanBeRetried()
        {
            if (!CameraHelpers.HasCinemachine)
                Assert.Ignore("Cinemachine is not installed.");
            var oldType = CameraHelpers.ResolveComponentType("CinemachineThirdPersonFollow");
            var newType = CameraHelpers.ResolveComponentType("CinemachinePositionComposer");
            Assert.IsNotNull(oldType);
            Assert.IsNotNull(newType);
            var cm = _cameraGo.AddComponent(CameraHelpers.CinemachineCameraType);
            var old = _cameraGo.AddComponent(oldType);
            var distance = oldType.GetField("CameraDistance");
            Assert.IsNotNull(distance);
            distance.SetValue(old, 7f);
            Undo.IncrementCurrentGroup();
            Undo.RecordObject(_camera, "Unrelated edit");
            _camera.fieldOfView = 42;
            Undo.FlushUndoRecordObjects();

            var swap = typeof(CameraConfigure).GetMethod("SwapPipelineComponent", BindingFlags.NonPublic | BindingFlags.Static);
            var configure = typeof(CameraConfigure).GetMethod("ConfigureComponentAtomically", BindingFlags.NonPublic | BindingFlags.Static);
            Func<Component> acquire = () => (Component)swap.Invoke(null, new object[] { _cameraGo, "Body", newType });
            var setters = new System.Collections.Generic.List<Action<Component>> { _ => throw new InvalidOperationException("test failure") };
            var arguments = new object[] { acquire, setters, null };
            Assert.IsNotNull(configure.Invoke(null, arguments));
            var restored = CameraHelpers.GetPipelineComponent(cm, "Body");
            Assert.IsNotNull(restored);
            Assert.AreEqual(oldType, restored.GetType());
            Assert.AreEqual(7f, distance.GetValue(restored));
            Assert.IsNull(_cameraGo.GetComponent(newType));
            Assert.AreEqual(42, _camera.fieldOfView);
            Undo.PerformUndo();
            Assert.AreNotEqual(42, _camera.fieldOfView, "Rollback must not consume the preceding edit.");

            var response = JObject.FromObject(
                CameraConfigure.SetBody(
                    new JObject
                    {
                        ["target"] = _cameraGo.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["properties"] = new JObject { ["bodyType"] = "CinemachinePositionComposer" },
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(_cameraGo.GetComponent(newType));
            Undo.PerformUndo();
            restored = CameraHelpers.GetPipelineComponent(cm, "Body");
            Assert.IsNotNull(restored);
            Assert.AreEqual(oldType, restored.GetType());
            Assert.AreEqual(7f, distance.GetValue(restored));
        }

        [Test]
        public void FailedAimAdditionRestoresPreviousStage()
        {
            if (!CameraHelpers.HasCinemachine)
                Assert.Ignore("Cinemachine is not installed.");
            var oldType = CameraHelpers.ResolveComponentType("CinemachinePanTilt");
            var newType = CameraHelpers.ResolveComponentType("CinemachineRotationComposer");
            Assert.IsNotNull(oldType);
            Assert.IsNotNull(newType);
            var cm = _cameraGo.AddComponent(CameraHelpers.CinemachineCameraType);
            _cameraGo.AddComponent(oldType);
            var configure = typeof(CameraConfigure).GetMethod("ConfigureComponentAtomically", BindingFlags.NonPublic | BindingFlags.Static);
            Func<Component> acquire = () =>
            {
                Undo.DestroyObjectImmediate(CameraHelpers.GetPipelineComponent(cm, "Aim"));
                throw new InvalidOperationException("test addition failure");
            };
            var arguments = new object[] { acquire, new System.Collections.Generic.List<Action<Component>>(), null };
            Assert.IsNotNull(configure.Invoke(null, arguments));
            Assert.IsNotNull(_cameraGo.GetComponent(oldType));
            Assert.IsNull(_cameraGo.GetComponent(newType));
        }

        [Test]
        public void ExistingExtensionRefusesChangedAndInvalidPropertiesOnRetry()
        {
            if (!CameraHelpers.HasCinemachine)
                Assert.Ignore("Cinemachine is not installed.");
            _cameraGo.AddComponent(CameraHelpers.CinemachineCameraType);
            var type = CameraHelpers.ResolveComponentType("CinemachineImpulseListener");
            Assert.IsNotNull(type);
            var extension = (Behaviour)_cameraGo.AddComponent(type);
            extension.enabled = true;
            foreach (var property in new[] { new JProperty("enabled", false), new JProperty("NoSuchProperty", 1) })
            {
                var properties = new JObject { ["extensionType"] = type.Name };
                properties.Add(property);
                var response = JObject.FromObject(
                    CameraConfigure.AddExtension(
                        new JObject
                        {
                            ["target"] = _cameraGo.GetInstanceIDCompat(),
                            ["searchMethod"] = "by_id",
                            ["properties"] = properties,
                        }
                    )
                );
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("not applied", response.ToString());
                Assert.IsTrue(extension.enabled);
            }
        }

        [Test]
        public void LensWrite_VerifiesExactCameraWithoutActivatingIt()
        {
            var result = JObject.FromObject(
                CameraConfigure.SetBasicCameraLens(
                    new JObject
                    {
                        ["action"] = "set_lens",
                        ["target"] = _cameraGo.GetInstanceIDCompat().ToString(),
                        ["properties"] = new JObject { ["fieldOfView"] = 47 },
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(_camera.GetInstanceIDCompat(), result["data"]["verification"]["observed_values"].Value<int>("camera_instance_id"));
            Assert.AreEqual(47, result["data"]["verification"]["observed_values"].Value<float>("fieldOfView"));
            Assert.IsFalse(_camera.enabled);
        }

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

        private Component CreatePrefabCamera(bool cinemachine, Type pipelineType = null)
        {
            if (cinemachine && !CameraHelpers.HasCinemachine)
                Assert.Ignore("Cinemachine is not installed.");
            if (cinemachine)
            {
                var component = _cameraGo.AddComponent(CameraHelpers.CinemachineCameraType);
                if (component is Behaviour behaviour)
                    behaviour.enabled = false;
                if (pipelineType != null)
                    _cameraGo.AddComponent(pipelineType);
            }
            _assetRoot = "Assets/__McpCameraContract_" + Guid.NewGuid().ToString("N");
            _assetRootGuid = AssetDatabase.CreateFolder("Assets", _assetRoot.Substring("Assets/".Length));
            Assert.IsNotEmpty(_assetRootGuid);
            // Camera Transform changes must be ordinary overrides, not default root overrides.
            var prefabSource = new GameObject("CameraPrefabRoot");
            _cameraGo.transform.SetParent(prefabSource.transform, false);
            var prefab = PrefabUtility.SaveAsPrefabAsset(prefabSource, _assetRoot + "/Camera.prefab");
            Assert.IsNotNull(prefab);
            Object.DestroyImmediate(prefabSource);
            var prefabInstance = (GameObject)PrefabUtility.InstantiatePrefab(prefab, _ownedScene);
            prefabInstance.transform.SetParent(_rig.transform, false);
            _cameraGo = prefabInstance.transform.GetChild(0).gameObject;
            _camera = _cameraGo.GetComponent<Camera>();
            return cinemachine ? _cameraGo.GetComponent(CameraHelpers.CinemachineCameraType) : _camera;
        }

        private JObject PrefabRequest(JObject properties) =>
            new JObject
            {
                ["target"] = _cameraGo.GetInstanceIDCompat(),
                ["searchMethod"] = "by_id",
                ["properties"] = properties,
            };

        private void ReopenPrefabScene()
        {
            string rigName = _rig.name;
            Assert.IsTrue(EditorSceneManager.SaveScene(_ownedScene, _assetRoot + "/" + _ownedScene.name + ".unity"));
            _ownedScene = _sceneFixture.ReopenSavedScene();
            _rig = _ownedScene.GetRootGameObjects().Single(go => go.name == rigName);
            _cameraGo = _rig.GetComponentInChildren<Camera>(true).gameObject;
            _camera = _cameraGo.GetComponent<Camera>();
        }

        [Test]
        public void BasicCameraConfigurationDoesNotDirtyUnrelatedPrefabStage()
        {
            CreatePrefabCamera(false);
            Assert.IsTrue(EditorSceneManager.SaveScene(_ownedScene, _assetRoot + "/" + _ownedScene.name + ".unity"));
            var request = PrefabRequest(new JObject { ["fieldOfView"] = 37 });
            var stage = PrefabStageUtility.OpenPrefab(_assetRoot + "/Camera.prefab");
            Assert.IsNotNull(stage);
            try
            {
                Assert.IsFalse(stage.scene.isDirty);
                Assert.IsFalse(_ownedScene.isDirty);
                var response = JObject.FromObject(CameraConfigure.SetBasicCameraLens(request));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.IsTrue(_ownedScene.isDirty);
                Assert.IsFalse(stage.scene.isDirty, "The actual camera scene owns this mutation.");
            }
            finally
            {
                // Only this fixture's stage may be cleared or closed, including on RED.
                if (PrefabStageUtility.GetCurrentPrefabStage() == stage)
                {
                    stage.ClearDirtiness();
                    StageUtility.GoToMainStage();
                }
            }
        }

        [TestCase("lens")]
        [TestCase("target")]
        [TestCase("priority")]
        public void BasicPrefabConfigurationSurvivesUndoRedoAndSceneReopen(string setting)
        {
            CreatePrefabCamera(false);
            float originalFov = _camera.fieldOfView;
            float originalDepth = _camera.depth;
            Quaternion originalRotation = _cameraGo.transform.localRotation;
            _rig.transform.position = new Vector3(5, 2, 8);
            // The rig is also a persistent scene target, distinct from the camera's position.
            _cameraGo.transform.localPosition = new Vector3(-5, -2, -8);
            var request = PrefabRequest(
                new JObject
                {
                    ["fieldOfView"] = 37,
                    ["priority"] = 17,
                    ["lookAt"] = _rig.GetInstanceIDCompat(),
                }
            );
            Undo.IncrementCurrentGroup();
            object result = setting switch
            {
                "lens" => CameraConfigure.SetBasicCameraLens(request),
                "priority" => CameraConfigure.SetBasicCameraPriority(request),
                _ => CameraConfigure.SetBasicCameraTarget(request),
            };
            Assert.IsTrue(JObject.FromObject(result).Value<bool>("success"));
            var changedObject = setting == "target" ? (Object)_cameraGo.transform : _camera;
            var prefabRoot = PrefabUtility.GetOutermostPrefabInstanceRoot(_cameraGo);
            Assert.IsTrue(PrefabUtility.HasPrefabInstanceAnyOverrides(prefabRoot, false));
            Assert.IsTrue(
                PrefabUtility
                    .GetPropertyModifications(prefabRoot)
                    .Any(mod =>
                        mod.target == PrefabUtility.GetCorrespondingObjectFromSource(changedObject)
                        && (
                            setting == "lens" ? mod.propertyPath == "field of view"
                            : setting == "priority" ? mod.propertyPath == "m_Depth"
                            : mod.propertyPath.StartsWith("m_LocalRotation.")
                        )
                    )
            );
            Quaternion expectedRotation = _cameraGo.transform.localRotation;
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            Undo.PerformUndo();
            Assert.AreEqual(originalFov, _camera.fieldOfView);
            Assert.AreEqual(originalDepth, _camera.depth);
            Assert.Less(Quaternion.Angle(originalRotation, _cameraGo.transform.localRotation), 0.01f);
            Undo.PerformRedo();
            ReopenPrefabScene();
            Assert.AreEqual(setting == "lens" ? 37 : originalFov, _camera.fieldOfView);
            Assert.AreEqual(setting == "priority" ? 17 : originalDepth, _camera.depth);
            Assert.Less(Quaternion.Angle(expectedRotation, _cameraGo.transform.localRotation), 0.01f);
            var sourceCamera = PrefabUtility.GetCorrespondingObjectFromSource(_camera);
            Assert.AreEqual(originalFov, sourceCamera.fieldOfView);
            Assert.AreEqual(originalDepth, sourceCamera.depth);
            Assert.Less(Quaternion.Angle(originalRotation, sourceCamera.transform.localRotation), 0.01f);
        }

        [TestCase("body", "CinemachineThirdPersonFollow", "CameraDistance")]
        [TestCase("aim", "CinemachinePanTilt", "PanAxis.Value")]
        [TestCase("noise", "CinemachineBasicMultiChannelPerlin", "AmplitudeGain")]
        public void ExistingCinemachinePipelinePrefabFieldsSurviveUndoRedoAndSceneReopen(string stage, string typeName, string property)
        {
            if (!CameraHelpers.HasCinemachine)
                Assert.Ignore("Cinemachine is not installed.");
            var type = CameraHelpers.ResolveComponentType(typeName);
            Assert.IsNotNull(type, "The Cinemachine 3 test profile must supply " + typeName);
            CreatePrefabCamera(true, type);
            var component = _cameraGo.GetComponent(type);
            float original;
            using (var serialized = new SerializedObject(component))
            {
                using var field = serialized.FindProperty(property);
                Assert.IsNotNull(field, property);
                original = field.floatValue;
            }
            byte[] source = System.IO.File.ReadAllBytes(_assetRoot + "/Camera.prefab");
            float expected = original + 7;
            var request = PrefabRequest(new JObject { [property] = expected });
            Undo.IncrementCurrentGroup();
            object result = stage switch
            {
                "body" => CameraConfigure.SetBody(request),
                "aim" => CameraConfigure.SetAim(request),
                _ => CameraConfigure.SetNoise(request),
            };
            Assert.IsTrue(JObject.FromObject(result).Value<bool>("success"), JObject.FromObject(result).ToString());
            Assert.AreSame(component, _cameraGo.GetComponent(type), "The existing pipeline component must be reused.");
            Assert.IsTrue(
                PrefabUtility
                    .GetPropertyModifications(PrefabUtility.GetOutermostPrefabInstanceRoot(_cameraGo))
                    .Any(mod => mod.target == PrefabUtility.GetCorrespondingObjectFromSource(component) && mod.propertyPath == property)
            );
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            Undo.PerformUndo();
            using (var serialized = new SerializedObject(component))
                Assert.AreEqual(original, serialized.FindProperty(property).floatValue);
            Undo.PerformRedo();
            using (var serialized = new SerializedObject(component))
                Assert.AreEqual(expected, serialized.FindProperty(property).floatValue);
            ReopenPrefabScene();
            using (var serialized = new SerializedObject(_cameraGo.GetComponent(type)))
                Assert.AreEqual(expected, serialized.FindProperty(property).floatValue);
            CollectionAssert.AreEqual(source, System.IO.File.ReadAllBytes(_assetRoot + "/Camera.prefab"));
        }

        [Test]
        public void CinemachinePrefabTargetSurvivesUndoRedoAndSceneReopen()
        {
            var component = CreatePrefabCamera(true);
            var follow = component.GetType().GetProperty("Follow");
            var lookAt = component.GetType().GetProperty("LookAt");
            Assert.IsNotNull(follow);
            Assert.IsNotNull(lookAt);
            Undo.IncrementCurrentGroup();
            var response = JObject.FromObject(
                CameraConfigure.SetCinemachineTarget(
                    PrefabRequest(new JObject { ["follow"] = _rig.GetInstanceIDCompat(), ["lookAt"] = _rig.GetInstanceIDCompat() })
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(
                PrefabUtility
                    .GetPropertyModifications(PrefabUtility.GetOutermostPrefabInstanceRoot(_cameraGo))
                    .Any(mod => mod.target == PrefabUtility.GetCorrespondingObjectFromSource(component) && mod.objectReference == _rig.transform)
            );
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            Undo.PerformUndo();
            Assert.IsNull(follow.GetValue(component));
            Assert.IsNull(lookAt.GetValue(component));
            Undo.PerformRedo();
            ReopenPrefabScene();
            component = _cameraGo.GetComponent(CameraHelpers.CinemachineCameraType);
            Assert.AreSame(_rig.transform, follow.GetValue(component));
            Assert.AreSame(_rig.transform, lookAt.GetValue(component));
            Assert.IsNull(follow.GetValue(PrefabUtility.GetCorrespondingObjectFromSource(component)));
            Assert.IsNull(lookAt.GetValue(PrefabUtility.GetCorrespondingObjectFromSource(component)));
        }

        [Test]
        public void CinemachinePrefabPrioritySurvivesUndoRedoAndSceneReopen()
        {
            var component = CreatePrefabCamera(true);
            var originalPriority = CameraHelpers.ReadCinemachinePriority(component);
            Undo.IncrementCurrentGroup();
            Assert.IsNull(CameraConfigure.SetPriority(component, 29));
            Assert.IsTrue(
                PrefabUtility
                    .GetPropertyModifications(PrefabUtility.GetOutermostPrefabInstanceRoot(_cameraGo))
                    .Any(mod =>
                        mod.target == PrefabUtility.GetCorrespondingObjectFromSource(component)
                        && mod.propertyPath.IndexOf("Priority", StringComparison.OrdinalIgnoreCase) >= 0
                    )
            );
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            Undo.PerformUndo();
            Assert.AreEqual(originalPriority, CameraHelpers.ReadCinemachinePriority(component));
            Undo.PerformRedo();
            ReopenPrefabScene();
            component = _cameraGo.GetComponent(CameraHelpers.CinemachineCameraType);
            Assert.AreEqual(29, CameraHelpers.ReadCinemachinePriority(component));
            Assert.AreEqual(originalPriority, CameraHelpers.ReadCinemachinePriority(PrefabUtility.GetCorrespondingObjectFromSource(component)));
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
            LogAssert.Expect(LogType.Error, new Regex("\\[PropertyConversion\\] Property value conversion failed\\.", RegexOptions.Singleline));
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
