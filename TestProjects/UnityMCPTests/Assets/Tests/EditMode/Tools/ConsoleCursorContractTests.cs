using System;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ConsoleCursorContractTests
    {
        [TestCase(0)]
        [TestCase(-1)]
        public void Paging_IgnoresNonPositiveCount(int count)
        {
            string marker = "ConsoleCursorContract_" + Guid.NewGuid().ToString("N");
            Debug.Log(marker);
            var response = JObject.FromObject(
                ReadConsole.HandleCommand(
                    new JObject
                    {
                        ["action"] = "get",
                        ["types"] = new JArray("all"),
                        ["format"] = "json",
                        ["filterText"] = marker,
                        ["pageSize"] = 1,
                        ["count"] = count,
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var items = (JArray)response["data"]["items"];
            Assert.AreEqual(1, items.Count);
            StringAssert.Contains(marker, items[0].Value<string>("message"));
        }

        [Test]
        public void CursorAtIntMax_DoesNotOverflowAndValidPageStillReads()
        {
            string marker = "ConsoleCursorContract_" + Guid.NewGuid().ToString("N");
            Debug.Log(marker);
            var parameters = new JObject
            {
                ["action"] = "get",
                ["types"] = new JArray("all"),
                ["format"] = "json",
                ["filterText"] = marker,
                ["pageSize"] = 2,
                ["cursor"] = int.MaxValue,
            };
            var beyondEnd = JObject.FromObject(ReadConsole.HandleCommand(parameters));
            Assert.IsTrue(beyondEnd.Value<bool>("success"), beyondEnd.ToString());
            var data = beyondEnd["data"];
            Assert.IsFalse(data.Value<bool>("truncated"));
            Assert.AreEqual(JTokenType.Null, data["nextCursor"].Type);
            Assert.AreEqual(0, ((JArray)data["items"]).Count);
            Assert.GreaterOrEqual(data.Value<int>("total"), 1);

            parameters["cursor"] = 0;
            var firstPage = JObject.FromObject(ReadConsole.HandleCommand(parameters));
            Assert.IsTrue(firstPage.Value<bool>("success"), firstPage.ToString());
            var items = (JArray)firstPage["data"]["items"];
            Assert.AreEqual(1, items.Count);
            StringAssert.Contains(marker, items[0].Value<string>("message"));
        }
    }
}
