using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class FindGameObjectsPagingTests
    {
        private readonly List<GameObject> _objects = new List<GameObject>();
        private PrefabTestSceneFixture projectionFixture;

        private void CreateProjectionScene()
        {
            projectionFixture = new PrefabTestSceneFixture();
            projectionFixture.PrepareRunnerBootstrap();
            projectionFixture.Create("Projection_", Guid.NewGuid().ToString("N"));
        }

        [TestCase("by_name")]
        [TestCase("by_tag")]
        [TestCase("by_layer")]
        [TestCase("by_component")]
        [TestCase("by_path")]
        [TestCase("by_id")]
        public void ExplicitLegacyPreservesOmittedScopeResponse(string method)
        {
            var go = new GameObject("Legacy_" + Guid.NewGuid().ToString("N"));
            _objects.Add(go);
            string term =
                method == "by_id" ? go.GetInstanceIDCompat().ToString()
                : method == "by_tag" ? "Untagged"
                : method == "by_layer" ? "0"
                : method == "by_component" ? "Transform"
                : go.name;
            var request = new JObject
            {
                ["search_method"] = method,
                ["search_term"] = term,
                ["page_size"] = "2",
                ["cursor"] = "0",
            };
            var omitted = JObject.FromObject(FindGameObjects.HandleCommand(request));
            request["scene_scope"] = "legacy";
            var explicitScope = JObject.FromObject(FindGameObjects.HandleCommand(request));
            Assert.IsTrue(JToken.DeepEquals(omitted, explicitScope));
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var obj in _objects)
                UnityEngine.Object.DestroyImmediate(obj);
            _objects.Clear();
            projectionFixture?.Close();
            projectionFixture?.RestoreRunnerBootstrap();
            projectionFixture = null;
        }

        [Test]
        public void ProjectionPreservesDefaultIdsAndInactiveDuplicateIdentity()
        {
            CreateProjectionScene();
            string name = "Projection_" + Guid.NewGuid().ToString("N");
            for (int i = 0; i < 3; i++)
            {
                var go = new GameObject(name);
                _objects.Add(go);
                go.SetActive(i != 1);
            }
            var request = new JObject
            {
                ["searchTerm"] = name,
                ["searchMethod"] = "by_name",
                ["includeInactive"] = true,
            };
            var baseline = JObject.FromObject(FindGameObjects.HandleCommand(request));
            Assert.IsNull(baseline["data"]["projection"]);
            request["projection"] = new JArray("name", "active", "transform");
            var projected = JObject.FromObject(FindGameObjects.HandleCommand(request));
            Assert.IsTrue((bool)projected["success"], projected.ToString());
            Assert.IsTrue(JToken.DeepEquals(baseline["data"]["instanceIDs"], projected["data"]["instanceIDs"]));
            Assert.AreEqual(3, ((JArray)projected["data"]["projection"]).Count);
            Assert.IsFalse(
                (bool)((JArray)projected["data"]["projection"]).First(row => (int)row["instanceID"] == _objects[1].GetInstanceIDCompat())["activeSelf"]
            );
        }

        [Test]
        public void ProjectionCapDoesNotTruncateSearchIds()
        {
            CreateProjectionScene();
            string name = "ProjectionCap_" + Guid.NewGuid().ToString("N");
            for (int i = 0; i < 70; i++)
                _objects.Add(new GameObject(name));
            var response = JObject.FromObject(
                FindGameObjects.HandleCommand(
                    new JObject
                    {
                        ["searchTerm"] = name,
                        ["searchMethod"] = "by_name",
                        ["page_size"] = 100,
                        ["projection"] = new JArray("name"),
                    }
                )
            );
            Assert.AreEqual(70, ((JArray)response["data"]["instanceIDs"]).Count);
            Assert.AreEqual(64, ((JArray)response["data"]["projection"]).Count);
            Assert.IsFalse((bool)response["data"]["projection_verification"]["complete"]);
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
                [numberKey] = 2,
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
            [Values(false, true)] bool stringToken
        )
        {
            string name = "Paging_" + Guid.NewGuid().ToString("N");
            _objects.Add(new GameObject(name));
            var parameters = new JObject
            {
                ["searchTerm"] = name,
                ["pageSize"] = 1,
                [offsetKey] = stringToken ? new JValue(offset) : JToken.Parse(offset),
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
            [Values(false, true)] bool stringToken
        )
        {
            string name = "Paging_" + Guid.NewGuid().ToString("N");
            _objects.Add(new GameObject(name));
            var parameters = new JObject { ["searchTerm"] = name, [sizeKey] = stringToken ? new JValue(size) : JToken.Parse(size) };

            var response = JObject.FromObject(FindGameObjects.HandleCommand(parameters));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(500, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(1, ((JArray)response["data"]["instanceIDs"]).Count);
            Assert.AreEqual(size, parameters[sizeKey].ToString());
        }

        [TestCase("null", "null", 50, 0, 1)]
        [TestCase("-1", "-1", 50, 0, 1)]
        [TestCase("-1000000000000000000000000000000", "-1000000000000000000000000000000", 50, 0, 1)]
        [TestCase("\"2\"", "\"1\"", 2, 1, 0)]
        public void DefaultAndIntegerPagination_PreservesCoercion(string size, string cursor, int expectedSize, int expectedCursor, int expectedCount)
        {
            string name = "Paging_" + Guid.NewGuid().ToString("N");
            _objects.Add(new GameObject(name));
            var parameters = new JObject
            {
                ["searchTerm"] = name,
                ["pageSize"] = JToken.Parse(size),
                ["cursor"] = JToken.Parse(cursor),
            };

            var response = JObject.FromObject(FindGameObjects.HandleCommand(parameters));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(expectedSize, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(expectedCursor, response["data"]["cursor"].Value<int>());
            Assert.AreEqual(expectedCount, ((JArray)response["data"]["instanceIDs"]).Count);
        }

        [Test]
        public void MalformedPaginationReturnsValidationError(
            [Values("page_size", "pageSize", "page_number", "pageNumber", "cursor")] string key,
            [Values("\"garbage\"", "1.9", "\"2.9\"", "true", "[]", "{}")] string value
        )
        {
            AssertValidationError(key, value);
        }

        [Test]
        public void MalformedSearchFlagReturnsValidationError(
            [Values("includeInactive", "include_inactive", "searchInactive", "search_inactive")] string key,
            [Values("\"not-bool\"", "[]", "{}")] string value
        )
        {
            AssertValidationError(key, value);
        }

        private static void AssertValidationError(string key, string value)
        {
            var parameters = new JObject { ["searchTerm"] = "Paging_" + Guid.NewGuid().ToString("N"), [key] = JToken.Parse(value) };
            JToken original = parameters.DeepClone();

            var response = JObject.FromObject(FindGameObjects.HandleCommand(parameters));

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(key, response.Value<string>("error"));
            Assert.IsTrue(JToken.DeepEquals(original, parameters));
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void ValidSearchFlagsPreserveInactiveFiltering(
            [Values("includeInactive", "include_inactive", "searchInactive", "search_inactive")] string key,
            [Values(false, true)] bool includeInactive
        )
        {
            string name = "Paging_" + Guid.NewGuid().ToString("N");
            var inactive = new GameObject(name);
            _objects.Add(inactive);
            inactive.SetActive(false);

            var response = JObject.FromObject(FindGameObjects.HandleCommand(new JObject { ["searchTerm"] = name, [key] = includeInactive }));

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(includeInactive ? 1 : 0, response["data"]["totalCount"].Value<int>());
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
                ["page_number"] = JToken.Parse("1000000000000000000000000000000"),
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
                ["cursor"] = JToken.Parse("1000000000000000000000000000000"),
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
