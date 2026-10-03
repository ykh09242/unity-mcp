using System.Collections;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Resources
{
    public class GameObjectSerializationReadOnlyTests
    {
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
