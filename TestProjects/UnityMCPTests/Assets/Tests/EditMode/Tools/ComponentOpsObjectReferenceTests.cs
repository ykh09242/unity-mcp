using System.Collections.Generic;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnity.Runtime.Serialization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class ObjectReferenceProbe : MonoBehaviour
    {
        public Light lightReference;
        public Behaviour behaviour;
        public GameObject target;
        public int count;
        public Light[] lightReferences;
        public ObjectReferenceData data;

        [SerializeField]
        private Light privateLight = null;
        public Light PrivateLight => privateLight;
    }

    [System.Serializable]
    public class ObjectReferenceData
    {
        public Light first;
        public List<Light> references;
    }

    public class ComponentOpsObjectReferenceTests
    {
        private GameObject owner;
        private GameObject source;
        private ObjectReferenceProbe probe;
        private Light light;

        [SetUp]
        public void SetUp()
        {
            owner = new GameObject("ObjectReferenceOwner");
            source = new GameObject("ObjectReferenceSource");
            probe = owner.AddComponent<ObjectReferenceProbe>();
            light = source.AddComponent<Light>();
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(owner);
            Object.DestroyImmediate(source);
        }

        private static JToken Reference(Object value, bool objectForm)
        {
            int id = value.GetInstanceIDCompat();
            return objectForm ? new JObject { ["instanceID"] = id } : new JValue(id);
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void SetProperty_ComponentReference_IntegerAndObjectFormsResolveSameComponent(bool objectForm, bool gameObjectId)
        {
            JToken value = Reference(gameObjectId ? (Object)source : light, objectForm);
            Assert.IsTrue(ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.lightReference), value, out string error), error);
            Assert.AreSame(light, probe.lightReference);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetProperty_BaseTypeReference_ResolvesDerivedComponent(bool objectForm)
        {
            Assert.IsTrue(ComponentOps.SetProperty(probe, "behaviour", Reference(source, objectForm), out string error), error);
            Assert.AreSame(light, probe.behaviour);
        }

        [TestCase("UnityEngine.UI.Button", false, false)]
        [TestCase("UnityEngine.UI.Button", true, false)]
        [TestCase("UnityEngine.UI.Button", false, true)]
        [TestCase("UnityEngine.UI.Button", true, true)]
        [TestCase("TMPro.TextMeshProUGUI", false, false)]
        [TestCase("TMPro.TextMeshProUGUI", true, false)]
        [TestCase("TMPro.TextMeshProUGUI", false, true)]
        [TestCase("TMPro.TextMeshProUGUI", true, true)]
        public void SetProperty_UiComponentReference_IntegerAndObjectFormsRoundTrip(string typeName, bool objectForm, bool gameObjectId)
        {
            var type = UnityTypeResolver.ResolveComponent(typeName);
            if (type == null)
                Assert.Ignore($"Optional UI component '{typeName}' is not installed.");
            var ui = new GameObject("UiReferenceSource", typeof(RectTransform));
            try
            {
                var component = ui.AddComponent(type);
                JToken value = Reference(gameObjectId ? (Object)ui : component, objectForm);
                Assert.IsTrue(ComponentOps.SetProperty(probe, "behaviour", value, out string error), error);
                Assert.AreSame(component, probe.behaviour);
            }
            finally
            {
                Object.DestroyImmediate(ui);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetProperty_PrivateSerializedReference_ResolvesGameObjectComponent(bool objectForm)
        {
            Assert.IsTrue(ComponentOps.SetProperty(probe, "privateLight", Reference(source, objectForm), out string error), error);
            Assert.AreSame(light, probe.PrivateLight);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetProperty_GameObjectReference_PreservesGameObject(bool objectForm)
        {
            Assert.IsTrue(ComponentOps.SetProperty(probe, "target", Reference(source, objectForm), out string error), error);
            Assert.AreSame(source, probe.target);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetProperty_NativeObjectProperty_ResolvesIntegerAndObjectForms(bool objectForm)
        {
            var renderer = owner.AddComponent<SpriteRenderer>();
            var texture = new Texture2D(4, 4);
            var sprite = Sprite.Create(texture, new Rect(0, 0, 4, 4), Vector2.zero);
            try
            {
                Assert.IsTrue(ComponentOps.SetProperty(renderer, "sprite", Reference(sprite, objectForm), out string error), error);
                Assert.AreSame(sprite, renderer.sprite);
            }
            finally
            {
                Object.DestroyImmediate(sprite);
                Object.DestroyImmediate(texture);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetProperty_WrongComponentType_FailsWithoutChangingReference(bool objectForm)
        {
            probe.lightReference = light;
            Assert.IsFalse(
                ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.lightReference), Reference(owner.transform, objectForm), out string error)
            );
            Assert.IsNotEmpty(error);
            Assert.AreSame(light, probe.lightReference);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetProperty_GameObjectWithoutRequiredComponent_FailsWithoutChangingReference(bool objectForm)
        {
            probe.lightReference = light;
            Assert.IsFalse(ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.lightReference), Reference(owner, objectForm), out string error));
            Assert.IsNotEmpty(error);
            Assert.AreSame(light, probe.lightReference);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetProperty_DestroyedReference_FailsWithoutChangingReference(bool objectForm)
        {
            probe.lightReference = light;
            var destroyed = new GameObject("DestroyedReference");
            JToken value = Reference(destroyed, objectForm);
            Object.DestroyImmediate(destroyed);
            Assert.IsFalse(ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.lightReference), value, out string error));
            Assert.IsNotEmpty(error);
            Assert.AreSame(light, probe.lightReference);
        }

        [Test]
        public void SetProperty_NullReference_ClearsExistingReference()
        {
            probe.lightReference = light;
            Assert.IsTrue(ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.lightReference), JValue.CreateNull(), out string error), error);
            Assert.IsNull(probe.lightReference);
        }

        [Test]
        public void SetProperty_MaterialArray_ResolvesReferencesAndExplicitNull()
        {
            var renderer = owner.AddComponent<MeshRenderer>();
            var material = new Material(Shader.Find("Hidden/InternalErrorShader"));
            try
            {
                Assert.IsTrue(
                    ComponentOps.SetProperty(renderer, "sharedMaterials", new JArray(Reference(material, true), JValue.CreateNull()), out string error),
                    error
                );
                CollectionAssert.AreEqual(new Material[] { material, null }, renderer.sharedMaterials);
            }
            finally
            {
                Object.DestroyImmediate(material);
            }
        }

        [TestCase("incompatible")]
        [TestCase("unresolved")]
        [TestCase("boolean")]
        [TestCase("integer")]
        [TestCase("object")]
        [TestCase("array")]
        [TestCase("boolean-id")]
        [TestCase("fractional-id")]
        public void SetProperty_MaterialArray_InvalidLateReferencePreservesEntireArray(string invalidKind)
        {
            var renderer = owner.AddComponent<MeshRenderer>();
            var first = new Material(Shader.Find("Hidden/InternalErrorShader"));
            var second = new Material(Shader.Find("Hidden/InternalErrorShader"));
            var destroyed = new GameObject("DestroyedNestedReference");
            JToken unresolved = Reference(destroyed, true);
            Object.DestroyImmediate(destroyed);
            try
            {
                var original = new[] { first, second };
                renderer.sharedMaterials = original;
                JToken invalid = invalidKind switch
                {
                    "incompatible" => Reference(source.transform, true),
                    "unresolved" => unresolved,
                    "boolean" => new JValue(true),
                    "integer" => Reference(second, false),
                    "object" => new JObject { ["unexpected"] = true },
                    "array" => new JArray(),
                    "boolean-id" => new JObject { ["instanceID"] = true },
                    "fractional-id" => new JObject { ["instanceID"] = 1.5 },
                    _ => throw new System.ArgumentOutOfRangeException(nameof(invalidKind)),
                };

                bool success = ComponentOps.SetProperty(renderer, "sharedMaterials", new JArray(Reference(second, true), invalid), out string error);

                CollectionAssert.AreEqual(original, renderer.sharedMaterials, "Invalid input must preserve every existing material reference.");
                Assert.IsFalse(success);
                Assert.IsNotEmpty(error);
                LogAssert.NoUnexpectedReceived();
            }
            finally
            {
                Object.DestroyImmediate(first);
                Object.DestroyImmediate(second);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetPropertyCommand_InvalidMaterialArrayPreservesReferences(bool bulkProperties)
        {
            var renderer = owner.AddComponent<MeshRenderer>();
            var material = new Material(Shader.Find("Hidden/InternalErrorShader"));
            try
            {
                renderer.sharedMaterials = new[] { material };
                var value = new JArray(JValue.CreateNull(), Reference(source.transform, true));
                var parameters = new JObject
                {
                    ["action"] = "set_property",
                    ["target"] = owner.GetInstanceIDCompat(),
                    ["componentType"] = "MeshRenderer",
                };
                if (bulkProperties)
                    parameters["properties"] = new JObject { ["sharedMaterials"] = value };
                else
                {
                    parameters["property"] = "sharedMaterials";
                    parameters["value"] = value;
                }
                LogAssert.Expect(LogType.Warning, new Regex("\\[ManageComponents\\].*expected 'Material'"));

                var response = JObject.FromObject(ManageComponents.HandleCommand(parameters));

                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                CollectionAssert.AreEqual(new[] { material }, renderer.sharedMaterials);
                StringAssert.Contains("expected 'Material'", response["data"]["errors"].ToString());
                LogAssert.NoUnexpectedReceived();
            }
            finally
            {
                Object.DestroyImmediate(material);
            }
        }

        [Test]
        public void SetProperty_NullReferenceArray_ClearsExistingArray()
        {
            probe.lightReferences = new[] { light };
            Assert.IsTrue(ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.lightReferences), JValue.CreateNull(), out string error), error);
            Assert.IsNull(probe.lightReferences);
        }

        [Test]
        public void SetProperty_NestedReferenceData_ResolvesListAndExplicitNull()
        {
            var value = new JObject { ["first"] = Reference(light, true), ["references"] = new JArray(Reference(source, true), JValue.CreateNull()) };

            Assert.IsTrue(ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.data), value, out string error), error);
            Assert.AreSame(light, probe.data.first);
            CollectionAssert.AreEqual(new Light[] { light, null }, probe.data.references);
        }

        [TestCase("incompatible")]
        [TestCase("unresolved")]
        [TestCase("boolean")]
        public void SetProperty_NestedReferenceData_InvalidLateReferencePreservesExistingData(string invalidKind)
        {
            var original = new ObjectReferenceData
            {
                first = light,
                references = new List<Light> { light, light },
            };
            probe.data = original;
            var destroyed = new GameObject("DestroyedNestedDataReference");
            JToken unresolved = Reference(destroyed, true);
            Object.DestroyImmediate(destroyed);
            JToken invalid = invalidKind switch
            {
                "incompatible" => Reference(source.transform, true),
                "unresolved" => unresolved,
                "boolean" => new JValue(true),
                _ => throw new System.ArgumentOutOfRangeException(nameof(invalidKind)),
            };
            var value = new JObject { ["first"] = JValue.CreateNull(), ["references"] = new JArray(Reference(light, true), invalid) };

            bool success = ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.data), value, out string error);

            Assert.AreSame(original, probe.data, "Invalid nested input must preserve the existing DTO.");
            Assert.AreSame(light, probe.data.first);
            CollectionAssert.AreEqual(new[] { light, light }, probe.data.references);
            Assert.IsFalse(success);
            Assert.IsNotEmpty(error);
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void DefaultObjectConverter_UnsupportedReferenceRetainsTolerantReadBehavior()
        {
            var serializer = new JsonSerializer();
            serializer.Converters.Add(new UnityEngineObjectConverter());
            LogAssert.Expect(LogType.Warning, new Regex("Unexpected token type 'Boolean' when deserializing Light"));

            Assert.IsNull(new JValue(true).ToObject<Light>(serializer));
        }

        [Test]
        public void SetProperty_IntegerScalar_RemainsScalarAndInvalidBooleanStillFails()
        {
            Assert.IsTrue(ComponentOps.SetProperty(probe, "count", new JValue(-42), out string error), error);
            Assert.AreEqual(-42, probe.count);
            LogAssert.Expect(LogType.Error, new Regex("Error converting token to System.Int32"));
            Assert.IsFalse(ComponentOps.SetProperty(probe, "count", new JValue(true), out error));
            Assert.AreEqual(-42, probe.count);
        }
    }
}
