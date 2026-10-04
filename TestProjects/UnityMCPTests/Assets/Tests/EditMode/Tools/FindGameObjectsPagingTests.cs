using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class FindGameObjectsPagingTests
    {
        private readonly List<GameObject> _objects = new List<GameObject>();

        [TearDown]
        public void TearDown()
        {
            foreach (var obj in _objects)
                UnityEngine.Object.DestroyImmediate(obj);
            _objects.Clear();
        }

        [TestCase("page_size", "page_number")]
        [TestCase("pageSize", "pageNumber")]
        public void OversizedPage_UsesClampedSizeForPageNumberOffset(string sizeKey, string numberKey)
        {
            string name = "Paging_" + Guid.NewGuid().ToString("N");
            for (int i = 0; i < 501; i++)
                _objects.Add(new GameObject(name));
            var parameters = new JObject
            {
                ["searchTerm"] = name,
                ["searchMethod"] = "by_name",
                [sizeKey] = 1000,
                [numberKey] = 2
            };

            var response = JObject.FromObject(FindGameObjects.HandleCommand(parameters));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(500, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(500, response["data"]["cursor"].Value<int>());
            Assert.AreEqual(501, response["data"]["totalCount"].Value<int>());
            Assert.AreEqual(1, ((JArray)response["data"]["instanceIDs"]).Count);
            Assert.AreEqual(1000, parameters.Value<int>(sizeKey));
        }

        [Test]
        public void LargeOffset_ReturnsEmptyFinalPage(
            [Values("cursor", "page_number", "pageNumber")] string offsetKey,
            [Values("2147483646", "2147483647", "2147483648", "9223372036854775808", "1000000000000000000000000000000")] string offset,
            [Values(false, true)] bool stringToken)
        {
            string name = "Paging_" + Guid.NewGuid().ToString("N");
            _objects.Add(new GameObject(name));
            var parameters = new JObject
            {
                ["searchTerm"] = name,
                ["pageSize"] = 1,
                [offsetKey] = stringToken ? new JValue(offset) : JToken.Parse(offset)
            };

            var response = JObject.FromObject(FindGameObjects.HandleCommand(parameters));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(1, response["data"]["cursor"].Value<int>());
            Assert.AreEqual(1, response["data"]["totalCount"].Value<int>());
            Assert.IsEmpty((JArray)response["data"]["instanceIDs"]);
            Assert.IsFalse(response["data"]["hasMore"].Value<bool>());
            Assert.AreEqual(JTokenType.Null, response["data"]["nextCursor"].Type);
            Assert.AreEqual(offset, parameters[offsetKey].ToString());
        }

        [Test]
        public void LargePageSize_UsesMaximumPageSize(
            [Values("page_size", "pageSize")] string sizeKey,
            [Values("2147483646", "2147483647", "2147483648", "9223372036854775808", "1000000000000000000000000000000")] string size,
            [Values(false, true)] bool stringToken)
        {
            string name = "Paging_" + Guid.NewGuid().ToString("N");
            _objects.Add(new GameObject(name));
            var parameters = new JObject
            {
                ["searchTerm"] = name,
                [sizeKey] = stringToken ? new JValue(size) : JToken.Parse(size)
            };

            var response = JObject.FromObject(FindGameObjects.HandleCommand(parameters));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(500, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(1, ((JArray)response["data"]["instanceIDs"]).Count);
            Assert.AreEqual(size, parameters[sizeKey].ToString());
        }

        [TestCase("null", "null", 50, 0, 1)]
        [TestCase("\"garbage\"", "\"garbage\"", 50, 0, 1)]
        [TestCase("-1", "-1", 50, 0, 1)]
        [TestCase("-1000000000000000000000000000000", "-1000000000000000000000000000000", 50, 0, 1)]
        [TestCase("1.9", "0.9", 1, 0, 1)]
        [TestCase("\"2.9\"", "\"1.9\"", 2, 1, 0)]
        public void DefaultAndFractionalPagination_PreservesCoercion(string size, string cursor, int expectedSize, int expectedCursor, int expectedCount)
        {
            string name = "Paging_" + Guid.NewGuid().ToString("N");
            _objects.Add(new GameObject(name));
            var parameters = new JObject
            {
                ["searchTerm"] = name,
                ["pageSize"] = JToken.Parse(size),
                ["cursor"] = JToken.Parse(cursor)
            };

            var response = JObject.FromObject(FindGameObjects.HandleCommand(parameters));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(expectedSize, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(expectedCursor, response["data"]["cursor"].Value<int>());
            Assert.AreEqual(expectedCount, ((JArray)response["data"]["instanceIDs"]).Count);
        }

        [Test]
        public void ExplicitCursor_TakesPrecedenceOverLargePageNumber()
        {
            string name = "Paging_" + Guid.NewGuid().ToString("N");
            _objects.Add(new GameObject(name));
            var parameters = new JObject
            {
                ["searchTerm"] = name,
                ["pageSize"] = 1,
                ["cursor"] = 0,
                ["page_number"] = JToken.Parse("1000000000000000000000000000000")
            };

            var response = JObject.FromObject(FindGameObjects.HandleCommand(parameters));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(0, response["data"]["cursor"].Value<int>());
            Assert.AreEqual(1, ((JArray)response["data"]["instanceIDs"]).Count);
        }

        [Test]
        public void LargeCursor_WithNoMatchesReturnsEmptyFinalPage()
        {
            var parameters = new JObject
            {
                ["searchTerm"] = "Paging_" + Guid.NewGuid().ToString("N"),
                ["cursor"] = JToken.Parse("1000000000000000000000000000000")
            };

            var response = JObject.FromObject(FindGameObjects.HandleCommand(parameters));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(0, response["data"]["totalCount"].Value<int>());
            Assert.AreEqual(0, response["data"]["cursor"].Value<int>());
            Assert.IsEmpty((JArray)response["data"]["instanceIDs"]);
            Assert.IsFalse(response["data"]["hasMore"].Value<bool>());
        }
    }
}
