using System;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using MCPForUnity.Editor.Tools;

namespace MCPForUnityTests.Editor.Tools
{
    public class ConsoleCursorContractTests
    {
        [Test]
        public void CursorAtIntMax_DoesNotOverflowAndValidPageStillReads()
        {
            string marker = "ConsoleCursorContract_" + Guid.NewGuid().ToString("N");
            Debug.Log(marker);
            var parameters = new JObject
            {
                ["action"] = "get", ["types"] = new JArray("all"), ["format"] = "json",
                ["filterText"] = marker, ["pageSize"] = 2, ["cursor"] = int.MaxValue
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
