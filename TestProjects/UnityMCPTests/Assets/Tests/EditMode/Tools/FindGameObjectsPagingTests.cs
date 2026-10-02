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
    }
}
