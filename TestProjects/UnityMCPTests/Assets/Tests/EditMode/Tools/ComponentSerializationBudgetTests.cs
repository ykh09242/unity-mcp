using System;
using System.Collections;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ComponentSerializationBudgetTests
    {
        [Test]
        public void SubstantialComponentArrayPreservesEveryValue()
        {
            var go = new GameObject("LargeComponentBudget");
            try
            {
                var component = go.AddComponent<SerializationBudgetComponent>();
                var sequence = new BudgetSequence(120000);
                component.values = sequence;
                var result = (Dictionary<string, object>)GameObjectSerializer.GetComponentData(component, true, true);
                Assert.IsFalse(result.ContainsKey("serializationError"));
                var values = (List<object>)((Dictionary<string, object>)result["properties"])["values"];
                Assert.AreEqual(120000, values.Count);
                Assert.AreEqual(119999, Convert.ToInt32(values[119999]));
                Assert.IsTrue(sequence.Disposed);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void HandledSerializerErrorsCannotTurnAnExceededBudgetIntoSuccess(bool plain)
        {
            var sequence = new BudgetSequence(BoundedResponseJson.MaxNodes + 5);
            var payload = new HandledOverflowValue { Values = sequence };
            var budget = new BoundedResponseJson.Budget();
            Assert.Throws<ResponseLimitException>(() =>
            {
                if (plain)
                    BoundedResponseJson.ToPlain(payload, JsonSerializer.CreateDefault(), budget);
                else
                    BoundedResponseJson.Freeze(payload, JsonSerializer.CreateDefault(), budget);
            });
            Assert.IsTrue(budget.Exceeded);
            Assert.IsTrue(sequence.Disposed);
        }

        [Test]
        public void LastComponentMemberCannotHideAHandledSerializationLimit()
        {
            var go = new GameObject("HandledComponentBudget");
            try
            {
                var component = go.AddComponent<HandledOverflowComponent>();
                component.last = new HandledOverflowValue { Values = new BudgetSequence(BoundedResponseJson.MaxNodes + 5) };
                var data = JObject.FromObject(GameObjectSerializer.GetComponentData(component, true, true));
                Assert.AreEqual("component_serialization_limit", (string)data["serializationError"]);
                Assert.IsNull(data["properties"], "An oversized value must never expose a partial-success payload.");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }

        [Test]
        public void OversizedEnumerableStopsAndDisposesBeforeReadingAllValues()
        {
            var go = new GameObject("ComponentBudget");
            try
            {
                var component = go.AddComponent<SerializationBudgetComponent>();
                var sequence = new BudgetSequence(BoundedResponseJson.MaxNodes * 2);
                component.values = sequence;
                var data = JObject.FromObject(GameObjectSerializer.GetComponentData(component, true, true));
                Assert.AreEqual("component_serialization_limit", (string)data["serializationError"]);
                Assert.LessOrEqual(sequence.Reads, BoundedResponseJson.MaxNodes + 1);
                Assert.IsTrue(sequence.Disposed);
                component.values = new[] { 1, 2, 3 };
                var small = (Dictionary<string, object>)GameObjectSerializer.GetComponentData(component, true, true);
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
                var first = new BudgetSequence(BoundedResponseJson.MaxNodes * 3 / 5);
                var second = new BudgetSequence(BoundedResponseJson.MaxNodes * 3 / 5);
                go.AddComponent<SerializationBudgetComponent>().values = first;
                go.AddComponent<SerializationBudgetComponent>().values = second;
                var result = JObject.FromObject(
                    GameObjectComponentsResource.HandleCommand(new JObject { ["instanceID"] = go.GetInstanceIDCompat(), ["evaluate_getters"] = true })
                );
                Assert.IsTrue((bool)result["success"]);
                Assert.LessOrEqual(first.Reads + second.Reads, BoundedResponseJson.MaxNodes + 1);
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
                var result = JObject.FromObject(GameObjectSerializer.GetComponentData(component, true, true));
                Assert.AreEqual("component_serialization_limit", (string)result["serializationError"]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }
    }

    public sealed class HandledOverflowComponent : MonoBehaviour
    {
        public HandledOverflowValue last;
    }

    public struct HandledOverflowValue
    {
        public IEnumerable<int> Values;

        [Newtonsoft.Json.Serialization.OnError]
        private void HandleError(System.Runtime.Serialization.StreamingContext context, Newtonsoft.Json.Serialization.ErrorContext error)
        {
            error.Handled = true;
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
