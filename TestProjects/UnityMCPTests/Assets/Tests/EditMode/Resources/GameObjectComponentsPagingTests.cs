using System.Collections.Generic;
using System.Linq;
using System.Numerics;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Resources
{
    public class GameObjectComponentsPagingTests
    {
        private GameObject _target;

        [SetUp]
        public void SetUp()
        {
            _target = new GameObject("ComponentPagingTest");
            _target.AddComponent<BoxCollider>();
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(_target);
        }

        [Test]
        public void NegativeCursor_ReturnsFirstPageWithAdvancingCursor()
        {
            var response = ReadPage(-10);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(0, response["data"]["cursor"].Value<int>());
            Assert.AreEqual(1, response["data"]["components"].Count());
            Assert.AreEqual(1, response["data"]["nextCursor"].Value<int>());
        }

        [Test]
        public void CursorBeyondEnd_ReturnsEmptyFinalPage()
        {
            var response = ReadPage(int.MaxValue);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(2, response["data"]["cursor"].Value<int>());
            Assert.IsEmpty(response["data"]["components"]);
            Assert.AreEqual(JTokenType.Null, response["data"]["nextCursor"].Type);
        }

        private static IEnumerable<JToken> LargeWholeNumbers()
        {
            yield return new JValue(2147483648L);
            yield return new JValue(long.MaxValue);
            yield return new JValue(ulong.MaxValue);
            yield return new JValue(BigInteger.Parse("999999999999999999999999999999"));
            yield return new JValue("2147483648");
        }

        [TestCaseSource(nameof(LargeWholeNumbers))]
        public void LargeCursor_ReturnsEmptyFinalPage(JToken cursor)
        {
            var response = ReadPage(cursor);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(2, response["data"]["cursor"].Value<int>());
            Assert.IsEmpty(response["data"]["components"]);
            Assert.AreEqual(JTokenType.Null, response["data"]["nextCursor"].Type);
            Assert.IsFalse(response["data"]["hasMore"].Value<bool>());
        }

        [TestCaseSource(nameof(LargeWholeNumbers))]
        public void LargePageSize_UsesMaximum(JToken pageSize)
        {
            var response = ReadPage(0, pageSize);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(100, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(2, response["data"]["components"].Count());
        }

        [TestCase("-2147483649")]
        [TestCase("-99999999999999999999999")]
        public void LargeNegativePaging_UsesFirstPageAndMinimumSize(string wholeNumber)
        {
            foreach (var token in new[] { JToken.Parse(wholeNumber), new JValue(wholeNumber) })
            {
                var response = ReadPage(token, token);
                Assert.AreEqual(0, response["data"]["cursor"].Value<int>());
                Assert.AreEqual(1, response["data"]["pageSize"].Value<int>());
                Assert.AreEqual(1, response["data"]["nextCursor"].Value<int>());
            }
        }

        [TestCase("null", 0, 25)]
        [TestCase("\"bad\"", 0, 25)]
        [TestCase("{}", 0, 25)]
        [TestCase("[]", 0, 25)]
        [TestCase("true", 0, 25)]
        [TestCase("1.9", 1, 1)]
        [TestCase("\"1.9\"", 1, 1)]
        public void AdjacentPagingCoercion_PreservesDefaultsAndFractions(string json, int cursor, int pageSize)
        {
            var token = JToken.Parse(json);
            var response = ReadPage(token, token);
            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(cursor, response["data"]["cursor"].Value<int>());
            Assert.AreEqual(pageSize, response["data"]["pageSize"].Value<int>());
        }

        [Test]
        public void CamelCasePageSize_TakesPrecedenceOverSnakeCase()
        {
            var response = JObject.FromObject(
                GameObjectComponentsResource.HandleCommand(
                    new JObject
                    {
                        ["instanceID"] = _target.GetInstanceIDCompat(),
                        ["pageSize"] = 1,
                        ["page_size"] = new JValue(2147483648L),
                        ["includeProperties"] = false,
                    }
                )
            );
            Assert.AreEqual(1, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(1, response["data"]["components"].Count());
        }

        private JObject ReadPage(JToken cursor, JToken pageSize = null) =>
            JObject.FromObject(
                GameObjectComponentsResource.HandleCommand(
                    new JObject
                    {
                        ["instanceID"] = _target.GetInstanceIDCompat(),
                        ["cursor"] = cursor,
                        ["pageSize"] = pageSize ?? new JValue(1),
                        ["includeProperties"] = false,
                    }
                )
            );
    }
}
