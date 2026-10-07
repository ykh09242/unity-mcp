using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
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
        [SerializeField] private Light privateLight = null;
        public Light PrivateLight => privateLight;
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
            if (type == null) Assert.Ignore($"Optional UI component '{typeName}' is not installed.");
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
            Assert.IsFalse(ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.lightReference),
                Reference(owner.transform, objectForm), out string error));
            Assert.IsNotEmpty(error);
            Assert.AreSame(light, probe.lightReference);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetProperty_GameObjectWithoutRequiredComponent_FailsWithoutChangingReference(bool objectForm)
        {
            probe.lightReference = light;
            Assert.IsFalse(ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.lightReference),
                Reference(owner, objectForm), out string error));
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
            Assert.IsTrue(ComponentOps.SetProperty(probe, nameof(ObjectReferenceProbe.lightReference),
                JValue.CreateNull(), out string error), error);
            Assert.IsNull(probe.lightReference);
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
