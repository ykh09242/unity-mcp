using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Physics;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class PaginationLimitsTests
    {
        [TestCase(0, false)]
        [TestCase(1, true)]
        [TestCase(1000, true)]
        [TestCase(1001, false)]
        [TestCase(int.MaxValue, false)]
        public void AuthoritativePageBounds(int size, bool allowed)
        {
            Assert.AreEqual(allowed, PaginationBounds.TryRead(new JValue(size), 50, 1, PaginationBounds.MaxPageSize, "page_size", out _, out _));
        }

        [TestCase("search")]
        [TestCase("list")]
        [TestCase("validate")]
        public void DirectHandlersRejectOversizedPageBeforeDiscovery(string action)
        {
            var args = new JObject
            {
                ["action"] = action,
                ["pageSize"] = 1001,
                ["path"] = "Assets",
            };
            object response =
                action == "search" ? ManageAsset.HandleCommand(args)
                : action == "list" ? ManageUI.HandleCommand(args)
                : ManagePhysics.HandleCommand(args);
            Assert.IsFalse(JObject.FromObject(response).Value<bool>("success"));
        }

        [TestCase(0, false)]
        [TestCase(1, true)]
        [TestCase(32, true)]
        [TestCase(33, false)]
        [TestCase(int.MaxValue, false)]
        public void PreviewSearchHasLowerAuthoritativePageBound(int size, bool allowed)
        {
            Assert.AreEqual(allowed, PaginationBounds.TryRead(new JValue(size), 32, 1, PaginationBounds.MaxPreviewPageSize, "page_size", out _, out _));
        }

        [Test]
        public void PageOffsetDoesNotOverflow()
        {
            Assert.AreEqual(((long)int.MaxValue - 1) * 1000, PaginationBounds.StartIndex(int.MaxValue, 1000));
        }

        [Test]
        public void PreviewBudgetsRejectOversizedImageAndBoundAggregateBase64()
        {
            var budget = new PaginationBounds.PreviewBudget();
            Assert.IsFalse(budget.TryReserve(0));
            Assert.IsFalse(budget.TryReserve(PaginationBounds.MaxPreviewPngBytes + 1));
            int accepted = 0;
            while (budget.TryReserve(PaginationBounds.MaxPreviewPngBytes))
                accepted++;
            Assert.AreEqual(11, accepted);
            Assert.LessOrEqual(budget.Base64Bytes, PaginationBounds.MaxPreviewBase64Bytes);
        }

        [TestCase(true)]
        [TestCase("NaN")]
        [TestCase("1e500")]
        [TestCase("1.5")]
        [TestCase("2147483648")]
        public void SuppliedInvalidPageIsNotReplacedByDefault(object value)
        {
            Assert.IsFalse(PaginationBounds.TryRead(JToken.FromObject(value), 50, 1, PaginationBounds.MaxPageSize, "page_size", out _, out _));
        }
    }
}
