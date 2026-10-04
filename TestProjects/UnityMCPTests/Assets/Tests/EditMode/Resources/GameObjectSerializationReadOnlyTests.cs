using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Resources
{
    public class GameObjectSerializationReadOnlyTests
    {
        [TestCase(0)]
        [TestCase(9)]
        [TestCase(-7)]
        public void PlainDateValues_PreserveOffsetAndFractionalTicks(int offsetHours)
        {
            var expected = new DateTimeOffset(2026, 10, 4, 1, 2, 3, TimeSpan.FromHours(offsetHours)).AddTicks(1234567);
            var actual = ConvertPlainValue(JToken.FromObject(expected));

            Assert.IsInstanceOf<DateTimeOffset>(actual);
            Assert.IsTrue(((DateTimeOffset)actual).EqualsExact(expected));
            Assert.AreEqual(JsonConvert.SerializeObject(expected), JsonConvert.SerializeObject(actual));
        }

        [TestCase(0)]
        [TestCase(9)]
        [TestCase(-7)]
        public void NestedDateValues_PreserveOffsets(int offsetHours)
        {
            var expected = new DateTimeOffset(2026, 10, 4, 1, 2, 3, TimeSpan.FromHours(offsetHours));
            var converted = (Dictionary<string, object>)ConvertPlainValue(JToken.FromObject(new { dates = new[] { expected } }));
            var actual = ((List<object>)converted["dates"])[0];

            Assert.IsInstanceOf<DateTimeOffset>(actual);
            Assert.IsTrue(((DateTimeOffset)actual).EqualsExact(expected));
        }

        [TestCase(DateTimeKind.Unspecified)]
        [TestCase(DateTimeKind.Utc)]
        [TestCase(DateTimeKind.Local)]
        public void PlainDateTimeValues_PreserveKind(DateTimeKind kind)
        {
            var expected = new DateTime(2026, 10, 4, 1, 2, 3, kind).AddTicks(1234567);
            var actual = ConvertPlainValue(JToken.FromObject(expected));

            Assert.IsInstanceOf<DateTime>(actual);
            Assert.AreEqual(expected, actual);
            Assert.AreEqual(kind, ((DateTime)actual).Kind);
        }

        [Test]
        public void PlainNullDateValue_RemainsNull()
            => Assert.IsNull(ConvertPlainValue(JValue.CreateNull()));

        [TestCase(0)]
        [TestCase(9)]
        [TestCase(-7)]
        public void ComponentMemberDateValues_PreserveWireOffset(int offsetHours)
        {
            var expected = new DateTimeOffset(2026, 10, 4, 1, 2, 3, TimeSpan.FromHours(offsetHours)).AddTicks(1234567);
            var properties = new Dictionary<string, object>();
            var add = typeof(GameObjectSerializer).GetMethod("AddSerializableValue", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(add);
            add.Invoke(null, new object[] { properties, "timestamp", typeof(DateTimeOffset), expected });

            Assert.IsInstanceOf<DateTimeOffset>(properties["timestamp"]);
            Assert.IsTrue(((DateTimeOffset)properties["timestamp"]).EqualsExact(expected));
            var wire = JsonConvert.SerializeObject(properties);
            var roundTrip = JsonConvert.DeserializeObject<Dictionary<string, DateTimeOffset>>(wire);
            Assert.IsTrue(roundTrip["timestamp"].EqualsExact(expected));
        }

        private static object ConvertPlainValue(JToken value)
        {
            var convert = typeof(GameObjectSerializer).GetMethod("ConvertJTokenToPlainObject", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(convert);
            return convert.Invoke(null, new object[] { value });
        }

        [Test]
        public void EditModeComponentReads_PreserveAssignedMaterialAndMeshReferences()
        {
            Assert.IsFalse(Application.isPlaying);
            AssertReadDoesNotInstantiate();
        }

        [UnityTest]
        public IEnumerator PlayModeComponentReads_PreserveAssignedMaterialAndMeshReferences()
        {
            yield return new EnterPlayMode();
            Assert.IsTrue(Application.isPlaying);
            AssertReadDoesNotInstantiate();
        }

        [UnityTearDown]
        public IEnumerator ExitPlayModeAfterTest()
        {
            if (Application.isPlaying)
                yield return new ExitPlayMode();
        }

        private static void AssertReadDoesNotInstantiate()
        {
            var go = new GameObject("ReadOnlySerialization_" + System.Guid.NewGuid().ToString("N"));
            Material first = null;
            Material second = null;
            Mesh mesh = null;
            try
            {
                Shader shader = Shader.Find("Standard") ?? Shader.Find("Universal Render Pipeline/Lit");
                Assert.IsNotNull(shader);
                first = new Material(shader) { name = "ExistingRuntimeMaterial" };
                second = new Material(shader) { name = "SecondRuntimeMaterial" };
                mesh = new Mesh { name = "ExistingRuntimeMesh" };
                var renderer = go.AddComponent<MeshRenderer>();
                renderer.sharedMaterials = new[] { first, second };
                var filter = go.AddComponent<MeshFilter>();
                filter.sharedMesh = mesh;

                var rendererData = JObject.FromObject(GameObjectSerializer.GetComponentData(renderer));
                var meshData = JObject.FromObject(GameObjectSerializer.GetComponentData(filter));

                CollectionAssert.AreEqual(new[] { first, second }, renderer.sharedMaterials);
                Assert.AreSame(mesh, filter.sharedMesh);
                Assert.AreEqual(first.GetInstanceIDCompat(), rendererData["properties"]["material"]["instanceID"].Value<int>());
                Assert.AreEqual(second.GetInstanceIDCompat(), rendererData["properties"]["materials"][1]["instanceID"].Value<int>());
                Assert.AreEqual(mesh.GetInstanceIDCompat(), meshData["properties"]["mesh"]["instanceID"].Value<int>());

                renderer.sharedMaterials = new Material[] { null };
                filter.sharedMesh = null;
                rendererData = JObject.FromObject(GameObjectSerializer.GetComponentData(renderer));
                meshData = JObject.FromObject(GameObjectSerializer.GetComponentData(filter));
                Assert.AreEqual(JTokenType.Null, rendererData["properties"]["material"].Type);
                Assert.AreEqual(JTokenType.Null, meshData["properties"]["mesh"].Type);
            }
            finally
            {
                // Also clean up any unexpected copies if the regression fails on an older implementation.
                var renderer = go.GetComponent<MeshRenderer>();
                if (renderer != null)
                    foreach (Material material in renderer.sharedMaterials)
                        if (material != null && material != first && material != second)
                            Object.DestroyImmediate(material);
                var filter = go.GetComponent<MeshFilter>();
                if (filter != null && filter.sharedMesh != null && filter.sharedMesh != mesh)
                    Object.DestroyImmediate(filter.sharedMesh);
                Object.DestroyImmediate(go);
                if (first != null) Object.DestroyImmediate(first);
                if (second != null) Object.DestroyImmediate(second);
                if (mesh != null) Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void GameObjectSummary_SkipsMissingComponentSlots()
        {
            var go = new GameObject("MissingSlotSerialization_" + System.Guid.NewGuid().ToString("N"));
            try
            {
                using (var serialized = new SerializedObject(go))
                {
                    SerializedProperty components = serialized.FindProperty("m_Component");
                    int index = components.arraySize;
                    components.InsertArrayElementAtIndex(index);
                    components.GetArrayElementAtIndex(index).FindPropertyRelative("component").objectReferenceValue = null;
                    serialized.ApplyModifiedPropertiesWithoutUndo();
                }
                Assert.IsTrue(go.GetComponents<Component>().Any(component => component == null), "Fixture must contain a missing component slot.");

                var summary = JObject.FromObject(GameObjectSerializer.GetGameObjectData(go));
                CollectionAssert.AreEqual(new[] { typeof(Transform).FullName }, summary["componentNames"].Values<string>());
            }
            finally
            {
                Object.DestroyImmediate(go);
            }
        }
    }
}
