using System;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Cameras;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class CameraContractTests
    {
        private GameObject _rig;
        private GameObject _cameraGo;
        private Camera _camera;

        [SetUp]
        public void SetUp()
        {
            _rig = new GameObject("CameraContract_" + Guid.NewGuid().ToString("N"));
            _cameraGo = new GameObject("Cam");
            _cameraGo.transform.SetParent(_rig.transform);
            _camera = _cameraGo.AddComponent<Camera>();
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(_rig);
        }

        [TestCase("{broken")]
        [TestCase("[]")]
        [TestCase("true")]
        [TestCase("7")]
        [TestCase("null")]
        public void InvalidPropertiesDoNotCreateDefaultCamera(string properties)
        {
            var before = Resources.FindObjectsOfTypeAll<GameObject>();
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageCamera\\].*properties", RegexOptions.Singleline));
            GameObject[] created = null;
            try
            {
                var response = JObject.FromObject(ManageCamera.HandleCommand(new JObject
                {
                    ["action"] = "create_camera", ["properties"] = properties
                }));
                created = Resources.FindObjectsOfTypeAll<GameObject>().Except(before).ToArray();
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("properties", response.ToString());
                Assert.IsEmpty(created);
            }
            finally
            {
                if (created != null)
                    foreach (var go in created) if (go != null) Object.DestroyImmediate(go);
            }
        }

        [Test]
        public void ObjectAndNullPropertiesRetainSupportedForms()
        {
            var properties = new JObject { ["priority"] = 0, ["enabled"] = false };
            Assert.AreSame(properties, CameraHelpers.ExtractProperties(new JObject { ["properties"] = properties }));
            Assert.AreEqual(properties.ToString(), CameraHelpers.ExtractProperties(
                new JObject { ["properties"] = properties.ToString() }).ToString());
            Assert.IsNull(CameraHelpers.ExtractProperties(new JObject()));
            Assert.IsNull(CameraHelpers.ExtractProperties(new JObject { ["properties"] = JValue.CreateNull() }));
            Assert.Throws<ArgumentException>(() => CameraHelpers.ExtractProperties(
                new JObject { ["properties"] = new JArray(1, 2) }));
        }

        [Test]
        public void ExplicitNameSearchDoesNotModifyNumericIdCamera()
        {
            var namedGo = new GameObject(_cameraGo.GetInstanceIDCompat().ToString());
            namedGo.transform.SetParent(_rig.transform);
            var namedCamera = namedGo.AddComponent<Camera>();
            _camera.fieldOfView = 60;
            var response = JObject.FromObject(CameraConfigure.SetBasicCameraLens(new JObject
            {
                ["target"] = namedGo.name, ["searchMethod"] = "by_name",
                ["properties"] = new JObject { ["fieldOfView"] = 35 }
            }));

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(35, namedCamera.fieldOfView);
            Assert.AreEqual(60, _camera.fieldOfView);
            Assert.AreSame(namedGo, CameraHelpers.FindTargetGameObject(new JObject
            {
                ["target"] = _rig.name + "/" + namedGo.name, ["searchMethod"] = "by_path"
            }));
        }

        [Test]
        public void ImplicitIdAndHierarchyPathFindTheCamera()
        {
            Assert.AreSame(_cameraGo, CameraHelpers.FindTargetGameObject(new JObject
            {
                ["target"] = _cameraGo.GetInstanceIDCompat().ToString()
            }));
            Assert.AreSame(_cameraGo, CameraHelpers.FindTargetGameObject(new JObject
            {
                ["target"] = _cameraGo.GetInstanceIDCompat(), ["searchMethod"] = "by_id"
            }));
            Assert.AreSame(_cameraGo, CameraHelpers.FindTargetGameObject(new JObject
            {
                ["target"] = _rig.name + "/Cam"
            }));
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
