using System;
using System.Collections;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class DefaultMaterialLifetimeTests
    {
        private static Type Pipeline => typeof(ManageMaterial).Assembly.GetType("MCPForUnity.Editor.Helpers.RenderPipelineUtility");
        private static readonly BindingFlags Static = BindingFlags.Static | BindingFlags.NonPublic;
        private const string Receipt = "MCPForUnity.Tests.MaterialLifetime.Reload";

        private static Material DefaultMaterial() => (Material)Pipeline.GetMethod("GetOrCreateDefaultSceneMaterial", Static).Invoke(null, null);

        [Test]
        public void LosingManagedLookupDoesNotAllocateAnotherNativeDefault()
        {
            Material first = DefaultMaterial();
            // Reproduce the former static dictionary disappearing at a domain reload.
            var dictionary = Pipeline.GetField("s_DefaultSceneMaterials", Static)?.GetValue(null) as IDictionary;
            dictionary?.Clear();
            Material second = DefaultMaterial();
            Assert.AreSame(first, second, "Native defaults need an owner that survives managed cache loss.");
        }

        [Test]
        public void VertexColorDefaultsAreSharedAcrossMeshes()
        {
            var method = Pipeline.GetMethod("GetOrCreateVertexColorMaterial", Static);
            Assert.IsNotNull(method);
            Shader shader = Shader.Find("Sprites/Default");
            Material first = (Material)method.Invoke(null, new object[] { shader });
            for (int index = 0; index < 20; index++)
                Assert.AreSame(first, method.Invoke(null, new object[] { shader }));
            Assert.AreSame(shader, first.shader);
        }

        [Test]
        public void DefaultCleanupDestroysOwnedMaterialsButPreservesBorrowedResources()
        {
            Type type = typeof(ManageMaterial).Assembly.GetType("MCPForUnity.Editor.Helpers.DefaultMaterialStore", true);
            object store = type.GetProperty("instance", BindingFlags.Public | BindingFlags.Static | BindingFlags.FlattenHierarchy).GetValue(null);
            var instance = BindingFlags.Instance | BindingFlags.NonPublic;
            Material owned = DefaultMaterial();
            Material borrowed = AssetDatabase.GetBuiltinExtraResource<Material>("Default-Line.mat");
            Assert.IsNotNull(borrowed);
            type.GetMethod("Store", instance).Invoke(store, new object[] { "test-borrowed", borrowed, false });
            type.GetMethod("DestroyOwnedMaterials", instance).Invoke(store, null);
            Assert.IsTrue(owned == null);
            Assert.IsTrue(borrowed != null);
            Assert.IsNotNull(borrowed.shader);
        }

        [UnityTest, Explicit("Controller-owned isolated project domain-reload verification only.")]
        public IEnumerator ActualDomainReloadPreservesDefaultAndInstanceOwnership()
        {
            try
            {
                Assert.IsEmpty(SessionState.GetString(Receipt, ""));
                var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
                go.name = "McpMaterialReload_" + Guid.NewGuid().ToString("N");
                Material shared = DefaultMaterial();
                go.GetComponent<Renderer>().sharedMaterial = shared;
                SetColor(go, Color.red);
                SessionState.SetString(
                    Receipt,
                    new JObject
                    {
                        ["name"] = go.name,
                        ["default_id"] = shared.GetInstanceIDCompat(),
                        ["instance_id"] = go.GetComponent<Renderer>().sharedMaterial.GetInstanceIDCompat(),
                    }.ToString()
                );
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                var receipt = JObject.Parse(SessionState.GetString(Receipt, ""));
                var restored = GameObject.Find(receipt.Value<string>("name"));
                Assert.IsNotNull(restored);
                Assert.AreEqual(receipt.Value<int>("default_id"), DefaultMaterial().GetInstanceIDCompat());
                Assert.AreEqual(receipt.Value<int>("instance_id"), restored.GetComponent<Renderer>().sharedMaterial.GetInstanceIDCompat());
                SetColor(restored, Color.green);
                Assert.AreEqual(receipt.Value<int>("instance_id"), restored.GetComponent<Renderer>().sharedMaterial.GetInstanceIDCompat());
            }
            finally
            {
                string saved = SessionState.GetString(Receipt, "");
                SessionState.EraseString(Receipt);
                if (!string.IsNullOrEmpty(saved))
                {
                    var go = GameObject.Find(JObject.Parse(saved).Value<string>("name"));
                    if (go != null)
                    {
                        Material material = go.GetComponent<Renderer>().sharedMaterial;
                        Undo.ClearAll();
                        UnityEngine.Object.DestroyImmediate(go);
                        if (material != null && material != DefaultMaterial() && !EditorUtility.IsPersistent(material))
                            UnityEngine.Object.DestroyImmediate(material);
                    }
                }
            }
        }

        private static void SetColor(GameObject go, Color color)
        {
            var result = JObject.FromObject(
                ManageMaterial.HandleCommand(
                    new JObject
                    {
                        ["action"] = "set_renderer_color",
                        ["target"] = go.GetInstanceIDCompat().ToString(),
                        ["mode"] = "instance",
                        ["color"] = new JArray(color.r, color.g, color.b, color.a),
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }
    }
}
