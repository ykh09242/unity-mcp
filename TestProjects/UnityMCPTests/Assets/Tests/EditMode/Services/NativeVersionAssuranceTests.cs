using System.Collections;
using MCPForUnity.Editor.Helpers;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>Required native-version cohorts skip outside their installed API; skipped evidence cannot pass.</summary>
    public class NativeVersionAssuranceTests
    {
        public sealed class ReferenceHolder : ScriptableObject
        {
            public Object reference;
        }

        [Test]
        public void Unity66EntityId_ReadWritePreservesNativeObjectIdentity()
        {
#if UNITY_6000_6_OR_NEWER
            var first = new GameObject("NativeEntityIdFirst");
            var second = new GameObject("NativeEntityIdSecond");
            var holder = ScriptableObject.CreateInstance<ReferenceHolder>();
            try
            {
                using var serialized = new SerializedObject(holder);
                var property = serialized.FindProperty("reference");
                property.objectReferenceValue = first;
                serialized.ApplyModifiedPropertiesWithoutUndo();
                serialized.Update();
                Assert.AreEqual(first.GetEntityId(), property.objectReferenceEntityIdValue);
                Assert.AreSame(first, UnityEngine.Resources.EntityIdToObject(property.objectReferenceEntityIdValue));
                property.objectReferenceEntityIdValue = second.GetEntityId();
                serialized.ApplyModifiedPropertiesWithoutUndo();
                serialized.Update();
                Assert.AreSame(second, holder.reference);
                Assert.AreEqual(second.GetEntityId(), property.objectReferenceEntityIdValue);
                Assert.AreNotEqual(EntityId.ToULong(first.GetEntityId()), EntityId.ToULong(second.GetEntityId()));
            }
            finally
            {
                Object.DestroyImmediate(holder);
                Object.DestroyImmediate(second);
                Object.DestroyImmediate(first);
            }
            LogAssert.NoUnexpectedReceived();
#else
            Assert.Ignore("Requires an actual Unity 6.6+ Editor; reference compilation is insufficient.");
#endif
        }

        [UnityTest]
        public IEnumerator Unity65PanelRenderer_ConfiguresAndReadsActualNativeRoot()
        {
#if UNITY_6000_5_OR_NEWER
            var type = UIComponentAdapter.PanelRendererType;
            Assert.IsNotNull(type, "Unity 6.5+ must expose its installed PanelRenderer.");
            Assert.AreNotEqual(typeof(NativeVersionAssuranceTests).Assembly, type.Assembly, "A project lookalike cannot satisfy native evidence.");
            var owner = new GameObject("NativePanelRenderer");
            var source = ScriptableObject.CreateInstance<VisualTreeAsset>();
            var settings = ScriptableObject.CreateInstance<PanelSettings>();
            try
            {
                var adapter = UIComponentAdapter.Attach(owner, UIComponentAdapter.ResolveType(owner, "panel_renderer", true));
                adapter.Configure(source, settings, 7);
                yield return null;
                yield return null;
                Assert.AreEqual("panel_renderer", adapter.Kind);
                Assert.AreSame(source, adapter.SourceAsset);
                Assert.AreSame(settings, adapter.PanelSettings);
                var root = adapter.Root;
                Assert.IsNotNull(root, "Read the real native root through the production callback contract.");
                root.Add(new Label("Native renderer marker") { name = "native-marker" });
                Assert.AreSame(root, adapter.Root);
                Assert.AreEqual("Native renderer marker", adapter.Root.Q<Label>("native-marker").text);
            }
            finally
            {
                Object.DestroyImmediate(owner);
                Object.DestroyImmediate(settings);
                Object.DestroyImmediate(source);
            }
            LogAssert.NoUnexpectedReceived();
#else
            Assert.Ignore("Requires an actual Unity 6.5+ Editor with graphics; reference compilation is insufficient.");
            yield break;
#endif
        }
    }
}
