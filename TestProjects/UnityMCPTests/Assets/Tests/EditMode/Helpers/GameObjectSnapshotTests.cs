using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Helpers
{
    public class GameObjectSnapshotTests
    {
        [Test]
        public void NullGameObjectRetainsNullResponse()
        {
            Assert.IsNull(GameObjectSerializer.GetGameObjectData(null));
        }

        [Test]
        public void SnapshotPreservesWorldLocalVectorsAndResponseShape()
        {
            var parent = new GameObject("Snapshot Parent");
            var target = new GameObject("Snapshot Target");
            try
            {
                parent.transform.position = new Vector3(10, 20, 30);
                parent.transform.rotation = Quaternion.Euler(15, 25, 35);
                parent.transform.localScale = new Vector3(2, 3, 4);
                var transform = target.transform;
                transform.SetParent(parent.transform, false);
                transform.localPosition = new Vector3(1, 2, 3);
                transform.localRotation = Quaternion.Euler(40, 50, 60);
                transform.localScale = new Vector3(5, 6, 7);
                target.AddComponent<BoxCollider>();
                var snapshot = JObject.FromObject(GameObjectSerializer.GetGameObjectData(target));
                Assert.AreEqual("Snapshot Target", snapshot.Value<string>("name"));
                Assert.AreEqual(target.GetInstanceIDCompat(), snapshot.Value<int>("instanceID"));
                Assert.AreEqual(parent.GetInstanceIDCompat(), snapshot.Value<int>("parentInstanceID"));
                Assert.AreEqual(target.scene.path, snapshot.Value<string>("scenePath"));
                Assert.AreEqual(target.tag, snapshot.Value<string>("tag"));
                Assert.AreEqual(target.layer, snapshot.Value<int>("layer"));
                Assert.AreEqual(target.activeSelf, snapshot.Value<bool>("activeSelf"));
                Assert.AreEqual(target.activeInHierarchy, snapshot.Value<bool>("activeInHierarchy"));
                Assert.AreEqual(target.isStatic, snapshot.Value<bool>("isStatic"));
                CollectionAssert.AreEquivalent(new[] { typeof(Transform).FullName, typeof(BoxCollider).FullName }, snapshot["componentNames"].Values<string>());
                var vectors = snapshot["transform"];
                AssertVector(vectors["position"], transform.position);
                AssertVector(vectors["localPosition"], transform.localPosition);
                AssertVector(vectors["rotation"], transform.rotation.eulerAngles);
                AssertVector(vectors["localRotation"], transform.localRotation.eulerAngles);
                AssertVector(vectors["scale"], transform.localScale);
                AssertVector(vectors["forward"], transform.forward);
                AssertVector(vectors["up"], transform.up);
                AssertVector(vectors["right"], transform.right);
            }
            finally
            {
                Object.DestroyImmediate(target);
                Object.DestroyImmediate(parent);
            }
        }

        [Test]
        public void NextSerializationSeesChangesWithoutMutatingEarlierSnapshot()
        {
            var target = new GameObject("Before");
            var parent = new GameObject("Parent");
            try
            {
                target.transform.position = new Vector3(1, 2, 3);
                var before = JObject.FromObject(GameObjectSerializer.GetGameObjectData(target));
                target.name = "After";
                target.transform.SetParent(parent.transform, false);
                target.transform.localPosition = new Vector3(4, 5, 6);
                target.transform.rotation = Quaternion.Euler(10, 20, 30);
                target.transform.localScale = new Vector3(7, 8, 9);
                var after = JObject.FromObject(GameObjectSerializer.GetGameObjectData(target));
                Assert.AreEqual("Before", before.Value<string>("name"));
                Assert.AreEqual(0, before.Value<int>("parentInstanceID"));
                AssertVector(before["transform"]["position"], new Vector3(1, 2, 3));
                Assert.AreEqual("After", after.Value<string>("name"));
                Assert.AreEqual(parent.GetInstanceIDCompat(), after.Value<int>("parentInstanceID"));
                AssertVector(after["transform"]["position"], target.transform.position);
                AssertVector(after["transform"]["localPosition"], target.transform.localPosition);
                AssertVector(after["transform"]["rotation"], target.transform.rotation.eulerAngles);
                AssertVector(after["transform"]["scale"], target.transform.localScale);
            }
            finally
            {
                Object.DestroyImmediate(target);
                Object.DestroyImmediate(parent);
            }
        }

        private static void AssertVector(JToken actual, Vector3 expected)
        {
            Assert.That(actual.Value<float>("x"), Is.EqualTo(expected.x).Within(0.00001f));
            Assert.That(actual.Value<float>("y"), Is.EqualTo(expected.y).Within(0.00001f));
            Assert.That(actual.Value<float>("z"), Is.EqualTo(expected.z).Within(0.00001f));
        }
    }
}
