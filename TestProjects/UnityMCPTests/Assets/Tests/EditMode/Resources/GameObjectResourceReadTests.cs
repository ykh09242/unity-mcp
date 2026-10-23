using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
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
            _previousSelection = Selection.objects;
            _captured = true;
            try
            {
                _owned = new GameObject("ResourceRead_" + Guid.NewGuid().ToString("N"));
                _ownedScene = _owned.scene;
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
                Selection.objects = _previousSelection;
                _captured = false;
                _owned = null;
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

        private GameObject AddPagingChild(string name = "Duplicate")
        {
            var child = new GameObject(name);
            SceneManager.MoveGameObjectToScene(child, _ownedScene);
            child.transform.SetParent(_owned.transform, false);
            return child;
        }

        private JObject ReadChildrenPage(int pageSize = 50, string cursor = null)
        {
            var request = Parameters();
            request["children_page_size"] = pageSize;
            request["children_cursor"] = cursor;
            return Json(GameObjectResource.HandleCommand(request));
        }

        [Test]
        public void ChildrenPaging_WideStableSourceHasExactOrderedScalarIds()
        {
            var expected = new List<int>();
            for (int i = 0; i < 613; i++)
                expected.Add(AddPagingChild().GetInstanceIDCompat());
            var seen = new List<int>();
            string cursor = null;
            int pages = 0;
            do
            {
                var response = ReadChildrenPage(41, cursor);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var data = (JObject)response["data"];
                var children = (JArray)data["children"];
                Assert.LessOrEqual(children.Count, 41);
                Assert.AreEqual(613, data.Value<int>("childrenTotal"));
                Assert.AreEqual("live_indices", data.Value<string>("childrenConsistency"));
                Assert.IsNotEmpty(data.Value<string>("childrenRestartAdvice"));
                seen.AddRange(children.Values<int>());
                cursor = data.Value<string>("childrenNextCursor");
                Assert.AreEqual(cursor != null, data.Value<bool>("childrenTruncated"));
                Assert.Less(++pages, 30);
            } while (cursor != null);
            CollectionAssert.AreEqual(expected, seen);
            Assert.AreEqual(seen.Count, seen.Distinct().Count());
            Assert.AreEqual(0, _first.GetterCalls + _second.GetterCalls);
        }

        [TestCase("reorder")]
        [TestCase("reparent")]
        [TestCase("replace")]
        [TestCase("rename")]
        public void ChildrenPaging_ImmediateObservedMutationRejectsCursor(string mutation)
        {
            for (int i = 0; i < 6; i++)
                AddPagingChild("Child" + i);
            string cursor = ReadChildrenPage(2)["data"].Value<string>("childrenNextCursor");
            Assert.IsNotEmpty(cursor);
            Transform first = _owned.transform.GetChild(0);
            if (mutation == "reorder")
                first.SetSiblingIndex(1);
            else if (mutation == "rename")
                first.name = "RenamedImmediately";
            else if (mutation == "replace")
            {
                Object.DestroyImmediate(first.gameObject);
                AddPagingChild("Replacement").transform.SetSiblingIndex(0);
            }
            else
            {
                first.SetParent(_owned.transform.GetChild(1), false);
                AddPagingChild("Replacement");
            }
            var response = ReadChildrenPage(2, cursor);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("stale_cursor", response.ToString());
        }

        [Test]
        public void ChildrenPaging_CursorIsBoundToTargetAndOptions()
        {
            for (int i = 0; i < 6; i++)
                AddPagingChild();
            string cursor = ReadChildrenPage(2)["data"].Value<string>("childrenNextCursor");
            Assert.IsNotEmpty(cursor);
            Assert.IsFalse(ReadChildrenPage(3, cursor).Value<bool>("success"));
            var other = AddPagingChild("OtherTarget");
            var request = new JObject
            {
                ["instanceID"] = other.GetInstanceIDCompat(),
                ["children_page_size"] = 2,
                ["children_cursor"] = cursor,
            };
            Assert.IsFalse(Json(GameObjectResource.HandleCommand(request)).Value<bool>("success"));
        }

        [TestCase("children_page_size", "0")]
        [TestCase("children_page_size", "501")]
        [TestCase("children_page_size", "1.5")]
        [TestCase("children_page_size", "true")]
        [TestCase("children_cursor", "123")]
        public void ChildrenPaging_InvalidOptionsAreRejected(string key, string json)
        {
            var request = Parameters();
            request[key] = JToken.Parse(json);
            Assert.IsFalse(Json(GameObjectResource.HandleCommand(request)).Value<bool>("success"));
            Assert.AreEqual(0, _first.GetterCalls + _second.GetterCalls);
        }

        [Test]
        public void ChildrenPaging_ComponentMetadataIsBoundedWithoutCallingGetters()
        {
            for (int i = 0; i < 80; i++)
                _owned.AddComponent<ResourceReadCounter>();
            for (int i = 0; i < 70; i++)
                AddPagingChild();
            var response = Json(GameObjectResource.HandleCommand(Parameters()));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var data = (JObject)response["data"];
            Assert.AreEqual(50, ((JArray)data["children"]).Count);
            Assert.AreEqual(64, ((JArray)data["componentTypes"]).Count);
            Assert.AreEqual(83, data.Value<int>("componentCount"));
            Assert.IsTrue(data.Value<bool>("componentTypesTruncated"));
            Assert.IsTrue(data.Value<bool>("metadataTruncated"));
            Assert.LessOrEqual(data.Value<int>("workUsed"), data.Value<int>("workLimit"));
            Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(response.ToString(Formatting.None)), 262144);
            Assert.AreEqual(0, _owned.GetComponents<ResourceReadCounter>().Sum(component => component.GetterCalls));
        }

        [Test]
        public void ChildrenPaging_ExpiredCursorIsRejectedAndCacheIsBounded()
        {
            for (int i = 0; i < 4; i++)
                AddPagingChild();
            var helper = typeof(GameObjectResource).Assembly.GetType("MCPForUnity.Editor.Helpers.HierarchyPaging", true);
            var states = (System.Collections.IDictionary)
                helper.GetField("States", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic).GetValue(null);
            string cursor = ReadChildrenPage(2)["data"].Value<string>("childrenNextCursor");
            var state = states[cursor];
            state
                .GetType()
                .GetField("Expires", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                .SetValue(state, EditorApplication.timeSinceStartup - 1);
            Assert.IsFalse(ReadChildrenPage(2, cursor).Value<bool>("success"));
            for (int i = 0; i < 40; i++)
                Assert.IsNotEmpty(ReadChildrenPage(2)["data"].Value<string>("childrenNextCursor"));
            Assert.LessOrEqual(states.Count, 32);
        }

        [Test]
        public void ChildrenPaging_BoundedPathDoesNotSplitUnicodeSurrogatePair()
        {
            _owned.name = new string('x', 510);
            var child = AddPagingChild("\U0001F600");
            var response = Json(GameObjectResource.HandleCommand(new JObject { ["instanceID"] = child.GetInstanceIDCompat() }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            string path = response["data"].Value<string>("path");
            Assert.LessOrEqual(path.Length, 512);
            Assert.IsTrue(response["data"].Value<bool>("metadataTruncated"));
            for (int i = 0; i < path.Length; i++)
                if (char.IsHighSurrogate(path[i]))
                {
                    Assert.Less(i + 1, path.Length);
                    Assert.IsTrue(char.IsLowSurrogate(path[++i]));
                }
                else
                    Assert.IsFalse(char.IsLowSurrogate(path[i]));
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
            request["evaluate_getters"] = true;
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
        public void StrictComponentReadRejectsAmbiguityBeforeCallingGetters()
        {
            var request = Parameters();
            request["componentName"] = typeof(ResourceReadCounter).FullName;
            request["strict_selection"] = true;
            var response = Json(GameObjectComponentResource.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("ambiguous_component", response.Value<string>("code"));
            Assert.AreEqual(0, _first.GetterCalls + _second.GetterCalls);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SelectedComponentReadCallsOnlySelectedGetters(bool byId)
        {
            var request = Parameters();
            request["evaluate_getters"] = true;
            request["componentName"] = typeof(ResourceReadCounter).FullName;
            request[byId ? "component_instance_id" : "component_index"] = byId ? _second.GetInstanceIDCompat() : 1;
            var response = Json(GameObjectComponentResource.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(_second.GetInstanceIDCompat(), response.SelectToken("data.component.instanceID").Value<int>());
            Assert.AreEqual(0, _first.GetterCalls);
            Assert.AreEqual(1, _second.GetterCalls);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void AudioSourceReadOmitsUnsupportedGettersAndPreservesSelectedValues(bool selected)
        {
            var clip = AudioClip.Create("OwnedResourceReadClip", 32, 1, 8000, false);
            try
            {
                var firstAudio = _owned.AddComponent<AudioSource>();
                var secondAudio = _owned.AddComponent<AudioSource>();
                firstAudio.clip = clip;
                secondAudio.clip = clip;
                firstAudio.volume = 0.23f;
                firstAudio.mute = false;
                secondAudio.volume = 0.79f;
                secondAudio.mute = true;
                var expected = selected ? secondAudio : firstAudio;
                var request = Parameters();
                request["componentName"] = typeof(AudioSource).FullName;
                if (selected)
                    request["component_index"] = 1;

                var response = Json(GameObjectComponentResource.HandleCommand(request));
                LogAssert.NoUnexpectedReceived();
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var component = response["data"]["component"];
                Assert.AreEqual(expected.GetInstanceIDCompat(), component.Value<int>("instanceID"));
                Assert.AreEqual(typeof(AudioSource).FullName, component.Value<string>("typeName"));
                var properties = (JObject)component["properties"];
                Assert.AreEqual(expected.volume, properties.Value<float>("volume"), 0.0001f);
                Assert.AreEqual(expected.mute, properties.Value<bool>("mute"));
                Assert.AreEqual(clip.GetInstanceIDCompat(), properties.SelectToken("clip.instanceID").Value<int>());
                foreach (var unsupported in new[] { "minVolume", "maxVolume", "rolloffFactor" })
                    Assert.IsNull(properties.Property(unsupported), unsupported);
                if (selected)
                    Assert.AreEqual(expected.GetInstanceIDCompat(), response.SelectToken("data.selection.componentInstanceID").Value<int>());
                else
                    Assert.IsNull(response.SelectToken("data.selection"));
                Assert.AreEqual(0, _first.GetterCalls + _second.GetterCalls);
            }
            finally
            {
                Object.DestroyImmediate(clip);
            }
        }

        [Test]
        public void ComponentFullName_IgnoresCaseAndRetainsFirstMatch()
        {
            var request = Parameters();
            request["evaluate_getters"] = true;
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
            request["evaluate_getters"] = true;
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
