using System;
using System.Collections;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ComponentSerializationBudgetTests
    {
        [Test]
        public void OversizedEnumerableStopsAndDisposesBeforeReadingAllValues()
        {
            var go = new GameObject("ComponentBudget");
            try
            {
                var component = go.AddComponent<SerializationBudgetComponent>();
                var sequence = new BudgetSequence(200000);
                component.values = sequence;
                var data = JObject.FromObject(GameObjectSerializer.GetComponentData(component));
                Assert.AreEqual("component_serialization_limit", (string)data["serializationError"]);
                Assert.LessOrEqual(sequence.Reads, 100001);
                Assert.IsTrue(sequence.Disposed);
                component.values = new[] { 1, 2, 3 };
                var small = (Dictionary<string, object>)GameObjectSerializer.GetComponentData(component);
                var properties = (Dictionary<string, object>)small["properties"];
                CollectionAssert.AreEqual(new[] { 1, 2, 3 }, (List<object>)properties["values"]);
                Assert.IsFalse(small.ContainsKey("serializationError"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }

        [Test]
        public void ComponentPageSharesOneEnumerationBudget()
        {
            var go = new GameObject("ComponentPageBudget");
            try
            {
                var first = new BudgetSequence(60000);
                var second = new BudgetSequence(60000);
                go.AddComponent<SerializationBudgetComponent>().values = first;
                go.AddComponent<SerializationBudgetComponent>().values = second;
                var result = JObject.FromObject(GameObjectComponentsResource.HandleCommand(new JObject { ["instanceID"] = go.GetInstanceIDCompat() }));
                Assert.IsTrue((bool)result["success"]);
                Assert.LessOrEqual(first.Reads + second.Reads, 100001);
                Assert.IsTrue(first.Disposed && second.Disposed);
                var components = (JArray)result["data"]["components"];
                Assert.AreEqual("component_serialization_limit", (string)components[2]["serializationError"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }

        [Test]
        public void DeepComponentValueReturnsExplicitLimitInsteadOfPartialSuccess()
        {
            var go = new GameObject("ComponentDepthBudget");
            try
            {
                var component = go.AddComponent<SerializationBudgetComponent>();
                var root = new JArray();
                var current = root;
                for (int i = 0; i < 80; i++)
                {
                    var child = new JArray();
                    current.Add(child);
                    current = child;
                }
                component.nested = root;
                var result = JObject.FromObject(GameObjectSerializer.GetComponentData(component));
                Assert.AreEqual("component_serialization_limit", (string)result["serializationError"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }
    }

    public sealed class SerializationBudgetComponent : MonoBehaviour
    {
        public IEnumerable<int> values;
        public object nested;
    }

    public sealed class BudgetSequence : IEnumerable<int>
    {
        private readonly int count;
        public int Reads;
        public bool Disposed;

        public BudgetSequence(int count) => this.count = count;

        public IEnumerator<int> GetEnumerator()
        {
            try
            {
                for (int i = 0; i < count; i++)
                {
                    Reads++;
                    yield return i;
                }
            }
            finally
            {
                Disposed = true;
            }
        }

        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }
}
