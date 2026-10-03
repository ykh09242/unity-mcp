using System.Linq;
using MCPForUnity.Editor.Resources.Scene;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;

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

        private JObject ReadPage(int cursor)
            => JObject.FromObject(GameObjectComponentsResource.HandleCommand(new JObject
            {
                ["instanceID"] = _target.GetInstanceIDCompat(),
                ["cursor"] = cursor,
                ["pageSize"] = 1,
                ["includeProperties"] = false
            }));
    }
}
