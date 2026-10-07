using System;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Resources
{
    public sealed class ResourceReadCounter : MonoBehaviour
    {
        [NonSerialized]
        public int GetterCalls;
        public DateTimeOffset Timestamp { get; set; }
        public DateTimeOffset TimestampField;
        public int ProbeValue
        {
            get
            {
                GetterCalls++;
                return 7;
            }
        }
    }

    public class GameObjectResourceReadTests
    {
        private GameObject _owned;
        private ResourceReadCounter _first;
        private ResourceReadCounter _second;
        private Scene _ownedScene;
        private Scene _previousScene;
        private Object[] _previousSelection;
        private bool _captured;

        [SetUp]
        public void SetUp()
        {
            _captured = false;
            _owned = null;
            _ownedScene = default;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned prefab stage is open.");
            _previousScene = SceneManager.GetActiveScene();
            _previousSelection = Selection.objects;
            _captured = true;
            try
            {
                _ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                SceneManager.SetActiveScene(_ownedScene);
                _owned = new GameObject("ResourceRead_" + Guid.NewGuid().ToString("N"));
                SceneManager.MoveGameObjectToScene(_owned, _ownedScene);
                _first = _owned.AddComponent<ResourceReadCounter>();
                _second = _owned.AddComponent<ResourceReadCounter>();
            }
            catch
            {
                CleanupOwnedScene();
                throw;
            }
        }

        [TearDown]
        public void TearDown()
        {
            CleanupOwnedScene();
        }

        private void CleanupOwnedScene()
        {
            if (!_captured)
                return;
            try
            {
                if (_owned != null)
                    Object.DestroyImmediate(_owned);
            }
            finally
            {
                try
                {
                    if (_ownedScene.IsValid() && _ownedScene.isLoaded)
                        EditorSceneManager.CloseScene(_ownedScene, true);
                }
                finally
                {
                    try
                    {
                        if (_previousScene.IsValid() && _previousScene.isLoaded)
                            SceneManager.SetActiveScene(_previousScene);
                    }
                    finally
                    {
                        Selection.objects = _previousSelection;
                        _captured = false;
                        _owned = null;
                    }
                }
            }
        }

        private JObject Parameters()
        {
            return new JObject { ["instanceID"] = _owned.GetInstanceIDCompat() };
        }

        private static JObject Json(object response)
        {
            return JObject.FromObject(response);
        }

        [Test]
        public void Metadata_UsesVectorObjectsAndScalarHierarchyReferences()
        {
            _owned.SetActive(false);
            _owned.transform.position = new Vector3(0, -1, 2);
            var child = new GameObject("OwnedChild_" + Guid.NewGuid().ToString("N"));
            SceneManager.MoveGameObjectToScene(child, _ownedScene);
            child.transform.SetParent(_owned.transform, false);
            var response = Json(GameObjectResource.HandleCommand(Parameters()));
            Assert.IsTrue(response.Value<bool>("success"));
            var data = (JObject)response["data"];
            Assert.AreEqual(_owned.GetInstanceIDCompat(), data.Value<int>("instanceID"));
            Assert.IsFalse(data.Value<bool>("active"));
            Assert.AreEqual(JTokenType.Null, data["parent"].Type);
            Assert.AreEqual(child.GetInstanceIDCompat(), data["children"][0].Value<int>());
            Assert.AreEqual(-1f, data.SelectToken("transform.position.y").Value<float>());
            Assert.IsInstanceOf<JObject>(data.SelectToken("transform.position"));
            Assert.AreEqual(0, _first.GetterCalls + _second.GetterCalls);
        }

        [TestCase("instanceID")]
        [TestCase("instance_id")]
        [TestCase("id")]
        public void OwnedIdAliases_AreAcceptedAcrossAllThreeHandlers(string key)
        {
            var request = new JObject
            {
                [key] = _owned.GetInstanceIDCompat(),
                ["componentName"] = "Transform",
                ["includeProperties"] = false,
            };
            Assert.IsTrue(Json(GameObjectResource.HandleCommand(request)).Value<bool>("success"));
            Assert.IsTrue(Json(GameObjectComponentsResource.HandleCommand(request)).Value<bool>("success"));
            Assert.IsTrue(Json(GameObjectComponentResource.HandleCommand(request)).Value<bool>("success"));
        }

        [Test]
        public void NullPrimaryId_PreservesAliasPrecedenceAndRequiredIdFailure()
        {
            var request = Parameters();
            request["id"] = request["instanceID"].DeepClone();
            request["instanceID"] = JValue.CreateNull();
            request["componentName"] = "Transform";
            Assert.IsFalse(Json(GameObjectResource.HandleCommand(request)).Value<bool>("success"));
            Assert.IsFalse(Json(GameObjectComponentsResource.HandleCommand(request)).Value<bool>("success"));
            Assert.IsFalse(Json(GameObjectComponentResource.HandleCommand(request)).Value<bool>("success"));
            Assert.AreEqual(0, _first.GetterCalls + _second.GetterCalls);
        }

        [Test]
        public void MetadataOnlyPage_ClampsZeroAndSkipsComponentGetters()
        {
            var request = Parameters();
            request["page_size"] = 0;
            request["cursor"] = -1;
            request["include_properties"] = false;
            var response = Json(GameObjectComponentsResource.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"));
            var data = (JObject)response["data"];
            Assert.AreEqual(1, data.Value<int>("pageSize"));
            Assert.AreEqual(0, data.Value<int>("cursor"));
            Assert.AreEqual(3, data.Value<int>("totalCount"));
            Assert.AreEqual(1, data.Value<int>("nextCursor"));
            Assert.AreEqual(_owned.transform.GetInstanceIDCompat(), data.SelectToken("components[0].instanceID").Value<int>());
            Assert.AreEqual("UnityEngine.Transform", data.SelectToken("components[0].typeName").Value<string>());
            Assert.IsNull(data.SelectToken("components[0].properties"));
            Assert.AreEqual(0, _first.GetterCalls + _second.GetterCalls);
        }

        [Test]
        public void PropertyPage_ReadsOnlyTheSelectedOwnedComponent()
        {
            var request = Parameters();
            request["pageSize"] = 1;
            request["cursor"] = 1;
            var response = Json(GameObjectComponentsResource.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(_first.GetInstanceIDCompat(), response.SelectToken("data.components[0].instanceID").Value<int>());
            Assert.AreEqual(7, response.SelectToken("data.components[0].properties.ProbeValue").Value<int>());
            Assert.AreEqual(1, _first.GetterCalls);
            Assert.AreEqual(0, _second.GetterCalls);
        }

        [Test]
        public void TerminalCursor_ReturnsEmptyWithoutReadingAnyComponentProperties()
        {
            var request = Parameters();
            request["cursor"] = int.MaxValue;
            request["pageSize"] = int.MaxValue;
            var response = Json(GameObjectComponentsResource.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"));
            var data = (JObject)response["data"];
            Assert.AreEqual(3, data.Value<int>("cursor"));
            Assert.AreEqual(100, data.Value<int>("pageSize"));
            Assert.AreEqual(0, ((JArray)data["components"]).Count);
            Assert.AreEqual(JTokenType.Null, data["nextCursor"].Type);
            Assert.IsFalse(data.Value<bool>("hasMore"));
            Assert.AreEqual(0, _first.GetterCalls + _second.GetterCalls);
        }

        [Test]
        public void ComponentFullName_IgnoresCaseAndRetainsFirstMatch()
        {
            var request = Parameters();
            request["component_name"] = typeof(ResourceReadCounter).FullName.ToUpperInvariant();
            var response = Json(GameObjectComponentResource.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(_first.GetInstanceIDCompat(), response.SelectToken("data.component.instanceID").Value<int>());
            Assert.AreEqual(1, _first.GetterCalls);
            Assert.AreEqual(0, _second.GetterCalls);
        }

        [Test]
        public void ComponentDateMembers_PreserveWireOffsetsAndReadFreshValues()
        {
            var request = Parameters();
            request["componentName"] = typeof(ResourceReadCounter).FullName;
            var values = new[]
            {
                new DateTimeOffset(2026, 10, 4, 1, 2, 3, TimeSpan.FromHours(9)).AddTicks(1234567),
                new DateTimeOffset(2026, 10, 5, 4, 5, 6, TimeSpan.FromHours(-7)).AddTicks(7654321),
            };

            foreach (var expected in values)
            {
                _first.Timestamp = expected;
                _first.TimestampField = expected;
                var wire = JsonConvert.SerializeObject(GameObjectComponentResource.HandleCommand(request));
                var response = JsonConvert.DeserializeObject<JObject>(
                    wire,
                    new JsonSerializerSettings { DateParseHandling = DateParseHandling.DateTimeOffset }
                );
                Assert.IsTrue(response.Value<bool>("success"));
                var properties = response.SelectToken("data.component.properties");
                foreach (var member in new[] { "Timestamp", "TimestampField" })
                    Assert.IsTrue(properties[member].ToObject<DateTimeOffset>().EqualsExact(expected), member);
            }
            Assert.AreEqual(2, _first.GetterCalls);
            Assert.AreEqual(0, _second.GetterCalls);
        }
    }
}
