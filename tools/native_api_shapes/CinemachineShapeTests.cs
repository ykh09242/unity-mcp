using System;
using System.Linq;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools.Capabilities;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    public class CinemachineShapeTests
    {
        private readonly PrefabTestSceneFixture fixture = new();
        private Scene scene;

        [OneTimeSetUp]
        public void Prepare() => fixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void Restore() => fixture.RestoreRunnerBootstrap();

        [SetUp]
        public void OpenScene() => scene = fixture.Create("CameraShape_", Guid.NewGuid().ToString("N"));

        [TearDown]
        public void CloseScene() => fixture.Close();

        [Test]
        public void LegacyProvider() =>
            Installed("2.10.7", "Cinemachine.CinemachineVirtualCamera, Cinemachine", "CinemachineTransposer", "CinemachineComposer", false);

        [Test]
        public void ModernProvider() =>
            Installed("3.1.7", "Unity.Cinemachine.CinemachineCamera, Unity.Cinemachine", "CinemachineFollow", "CinemachineRotationComposer", true);

        [Test]
        public void AbsentProvider()
        {
            Provider(null);
            var created = Data(Cameras("create_camera", new JObject { ["name"] = "Basic" }));
            Assert.IsFalse(created.Value<bool>("cinemachine"));
            var basic = scene.GetRootGameObjects().Single(go => go.name == "Basic");
            Data(Cameras("set_lens", new JObject { ["fieldOfView"] = 44 }, basic));
            Assert.AreEqual(44, basic.GetComponent<Camera>().fieldOfView);
            fixture.ClearDirtiness();
            var before = Snapshot();
            var inventory = Inventory(false);
            Assert.AreEqual(0, ((JArray)inventory["cinemachineCameras"]).Count);
            Assert.IsFalse(inventory["unityCameras"].Single(camera => camera.Value<string>("name") == "Basic").Value<bool>("hasBrain"));
            Reject(Cinema("ensure_brain"), "requires the Cinemachine package");
            Reject(Cinema("set_body", new JObject { ["bodyType"] = "CinemachineFollow" }, basic), "requires the Cinemachine package");
            AssertUnchanged(before);
        }

        private void Installed(string version, string cameraIdentity, string body, string aim, bool decollider)
        {
            Provider(version);
            var cameraType = Type.GetType(cameraIdentity, true);
            var assembly = cameraType.Assembly;
            Assert.AreEqual("com.unity.cinemachine", UnityEditor.PackageManager.PackageInfo.FindForAssembly(assembly).name);
            var empty = new GameObject("Unconfigured camera");
            var emptyCamera = (Behaviour)empty.AddComponent(cameraType);
            emptyCamera.enabled = false;
            // CM2's GetComponentOwner may silently rebuild a missing hidden pipeline during a read.
            var pipeline = assembly.GetType(cameraType.Namespace + ".CinemachinePipeline");
            if (pipeline != null)
            {
                foreach (var child in empty.GetComponentsInChildren(pipeline, true))
                    UnityEngine.Object.DestroyImmediate(child.gameObject);
            }
            var output = new GameObject("Output", typeof(Camera));
            var brainType = assembly.GetType(cameraType.Namespace + ".CinemachineBrain", true);
            fixture.ClearDirtiness();
            var before = Snapshot();
            var unconfigured = Inventory(true);
            Assert.AreEqual(JTokenType.Null, unconfigured["brain"].Type);
            Assert.IsNull((string)Row(unconfigured, empty)["body"]);
            Reject(Cinema("get_brain_status"), "No CinemachineBrain");
            AssertUnchanged(before);

            Data(Cinema("ensure_brain", new JObject { ["camera"] = output.name }));
            var brain = output.GetComponent(brainType);
            Assert.IsNotNull(brain);
            Data(
                Cameras(
                    "create_camera",
                    new JObject
                    {
                        ["name"] = "Tracking",
                        ["priority"] = 28,
                        ["preset"] = "follow",
                    }
                )
            );
            var tracking = scene.GetRootGameObjects().Single(go => go.name == "Tracking");
            Assert.IsNotNull(tracking.GetComponent(cameraType));
            Assert.AreEqual(1, tracking.GetComponentsInChildren(assembly.GetType(cameraType.Namespace + "." + body, true), true).Length);
            Assert.AreEqual(1, tracking.GetComponentsInChildren(assembly.GetType(cameraType.Namespace + "." + aim, true), true).Length);
            var extension = new JObject { ["extensionType"] = "CinemachineDecollider" };
            if (decollider)
            {
                Data(Cinema("add_extension", extension, tracking));
                Assert.IsNotNull(tracking.GetComponent(assembly.GetType(cameraType.Namespace + ".CinemachineDecollider", true)));
            }
            Data(Cinema("set_blend", new JObject { ["style"] = "EaseInOut", ["duration"] = 0.7f }));
            var forced = Data(Cinema("force_camera", target: tracking));
            Assert.AreEqual(forced.Value<int>("overrideId"), Data(Cinema("release_override")).Value<int>("releasedOverrideId"));

            fixture.ClearDirtiness();
            before = Snapshot();
            var configured = Inventory(true);
            var row = Row(configured, tracking);
            Assert.AreEqual(cameraType.Name, row.Value<string>("cameraType"));
            Assert.AreEqual(body, row.Value<string>("body"));
            Assert.AreEqual(aim, row.Value<string>("aim"));
            Assert.AreEqual(28, row.Value<int>("priority"));
            Assert.AreEqual(output.name, configured["brain"].Value<string>("gameObject"));
            Assert.AreEqual(output.name, Data(Cinema("get_brain_status")).Value<string>("gameObject"));
            Reject(Cinema("set_body", new JObject { ["bodyType"] = "CinemachineHardLookAt" }, tracking), "resolve body component");
            if (!decollider)
                Reject(Cinema("add_extension", extension, tracking), "Extension type 'CinemachineDecollider' not found");
            AssertUnchanged(before);
        }

        private static void Provider(string version)
        {
            var installed = version != null;
            var ping = Data(Cinema("ping"));
            Assert.AreEqual(installed, ping.Value<bool>("cinemachine"));
            Assert.AreEqual(version, (string)ping["version"]);
            var feature = FeatureReadiness.Capture()["cinemachine"];
            Assert.AreEqual(installed, feature.Value<bool?>("supported"));
            Assert.AreEqual(version, (string)feature["package_version"]);
            Assert.AreEqual(installed ? "preflight_required" : "missing_package", feature.Value<string>("reason"));
        }

        private static JObject Inventory(bool installed)
        {
            var inventory = Data(JObject.FromObject(CamerasResource.HandleCommand(new JObject())));
            Assert.AreEqual(installed, inventory.Value<bool>("cinemachineInstalled"));
            Assert.IsTrue(JToken.DeepEquals(inventory, Data(Cameras("list_cameras"))));
            return inventory;
        }

        private static JToken Row(JObject inventory, GameObject target) =>
            inventory["cinemachineCameras"].Single(row => row.Value<int>("instanceID") == target.GetInstanceIDCompat());

        private JArray Snapshot()
        {
            var objects = scene.GetRootGameObjects().SelectMany(go => go.GetComponentsInChildren<Transform>(true));
            return new JArray(
                objects
                    .OrderBy(t => t.gameObject.GetInstanceIDCompat())
                    .Select(t => new JObject
                    {
                        ["object"] = t.gameObject.GetInstanceIDCompat(),
                        ["parent"] = t.parent == null ? 0 : t.parent.gameObject.GetInstanceIDCompat(),
                        ["dirty"] = EditorUtility.GetDirtyCount(t.gameObject),
                        ["components"] = new JArray(
                            t.GetComponents<Component>()
                                .Select(component => new JObject
                                {
                                    ["id"] = component.GetInstanceIDCompat(),
                                    ["type"] = component.GetType().FullName,
                                    ["dirty"] = EditorUtility.GetDirtyCount(component),
                                    ["serialized"] = EditorJsonUtility.ToJson(component),
                                })
                        ),
                    })
            );
        }

        private void AssertUnchanged(JArray before)
        {
            Assert.AreEqual(before.ToString(), Snapshot().ToString(), "Inventory or rejected operation changed scene objects or components.");
            Assert.IsFalse(scene.isDirty, "Inventory or rejected operation marked the scene dirty.");
        }

        private static JObject Request(string action, JObject properties, GameObject target) =>
            new JObject
            {
                ["action"] = action,
                ["properties"] = properties,
                ["target"] = target == null ? (int?)null : target.GetInstanceIDCompat(),
            };

        private static JObject Cinema(string action, JObject properties = null, GameObject target = null) =>
            JObject.FromObject(ManageCinemachine.HandleCommand(Request(action, properties, target)));

        private static JObject Cameras(string action, JObject properties = null, GameObject target = null) =>
            JObject.FromObject(ManageCameras.HandleCommand(Request(action, properties, target)));

        private static JObject Data(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        private static void Reject(JObject response, string reason)
        {
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(reason, response.ToString());
        }
    }
}
