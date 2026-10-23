using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services;
using NUnit.Framework;
using NUnit.Framework.Interfaces;
using UnityEditor.TestTools.TestRunner.Api;
using TestRunState = UnityEditor.TestTools.TestRunner.Api.RunState;

namespace MCPForUnityTests.Editor.Services
{
    public class TestRunnerServiceDiscoveryTests
    {
        [Test]
        public async System.Threading.Tasks.Task QueuedDiscoveryExpiresWithoutConsumingTheActiveRetrieval()
        {
            Action<ITestAdaptor> callback = null;
            int calls = 0;
            using var service = new TestRunnerService(
                (_, complete) =>
                {
                    calls++;
                    callback = complete;
                },
                TimeSpan.FromSeconds(300)
            );
            var active = service.GetTestsAsync(TestMode.EditMode);
            try
            {
                try
                {
                    await service.GetTestsAsync(TestMode.PlayMode, TimeSpan.FromMilliseconds(1));
                    Assert.Fail("A queued discovery must honor its own observation budget.");
                }
                catch (TimeoutException) { }
                Assert.AreEqual(1, calls);
                Assert.IsFalse(active.IsCompleted);
            }
            finally
            {
                callback?.Invoke(null);
                await active;
            }
        }

        [Test]
        public async System.Threading.Tasks.Task NativeResourceForwardsExplicitDiscoveryBudget()
        {
            var field = typeof(MCPServiceLocator).GetField("_testRunnerService", BindingFlags.Static | BindingFlags.NonPublic);
            object previous = field.GetValue(null);
            var recorder = new BudgetRecorder();
            try
            {
                field.SetValue(null, recorder);
                var result = await MCPForUnity.Editor.Resources.Tests.GetTests.HandleCommand(new Newtonsoft.Json.Linq.JObject { ["timeout_seconds"] = 1800 });
                Assert.IsTrue(Newtonsoft.Json.Linq.JObject.FromObject(result).Value<bool>("success"));
                Assert.AreEqual(1800, recorder.Budget.TotalSeconds);
                Assert.AreEqual(1, recorder.Calls);
            }
            finally
            {
                field.SetValue(null, previous);
            }
        }

        private sealed class BudgetRecorder : ITestRunnerService, IBudgetedTestDiscovery
        {
            internal TimeSpan Budget;
            internal int Calls;

            public System.Threading.Tasks.Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode) =>
                throw new InvalidOperationException("The explicit budget was lost.");

            public System.Threading.Tasks.Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode, TimeSpan budget)
            {
                Calls++;
                Budget = budget;
                return System.Threading.Tasks.Task.FromResult<IReadOnlyList<Dictionary<string, string>>>(Array.Empty<Dictionary<string, string>>());
            }

            public System.Threading.Tasks.Task<TestRunResult> RunTestsAsync(TestMode mode, TestFilterOptions filterOptions = null) =>
                throw new NotSupportedException();
        }

        [TestCase(null, 300)]
        [TestCase("1800", 1800)]
        [TestCase("NaN", 300)]
        public void DiscoveryBudgetCanAccommodateLargeTestAssemblies(string value, double expected)
        {
            const string name = "UNITY_MCP_TEST_DISCOVERY_TIMEOUT";
            var previous = Environment.GetEnvironmentVariable(name);
            try
            {
                Environment.SetEnvironmentVariable(name, value);
                using var service = new TestRunnerService();
                var budget = (TimeSpan)
                    typeof(TestRunnerService).GetField("_retrievalTimeout", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(service);
                Assert.AreEqual(expected, budget.TotalSeconds);
            }
            finally
            {
                Environment.SetEnvironmentVariable(name, previous);
            }
        }

        [Test]
        public void EmptySuite_IsNotListedAsATest()
        {
            Assert.IsEmpty(Collect(Suite("EmptyRoot")));
        }

        [Test]
        public void EmptyAssembly_IsNotListedAlongsideActualTests()
        {
            var root = Suite("EditMode", Suite("Empty.dll"), Suite("Tests.dll", Leaf("Passes", "Fixture.Passes")));
            var tests = Collect(root);
            Assert.AreEqual(1, tests.Count);
            Assert.AreEqual("Fixture.Passes", tests[0]["full_name"]);
            Assert.AreEqual("EditMode/Tests.dll/Passes", tests[0]["path"]);
            Assert.AreEqual("EditMode", tests[0]["mode"]);
        }

        [Test]
        public void SuiteWithNullChildren_IsNotListedAsATest()
        {
            var suite = Suite("EmptyRoot");
            suite.Children = null;
            Assert.IsEmpty(Collect(suite));
        }

        [Test]
        public void LeafWithoutFullName_UsesItsName()
        {
            var tests = Collect(Leaf("Passes", null));
            Assert.AreEqual(1, tests.Count);
            Assert.AreEqual("Passes", tests[0]["full_name"]);
            Assert.AreEqual("Passes", tests[0]["path"]);
        }

        [Test]
        public void DuplicateSameModeTest_RemainsDeduplicated()
        {
            var leaf = Leaf("Passes", "Fixture.Passes");
            Assert.AreEqual(1, Collect(Suite("EditMode", leaf, leaf)).Count);
        }

        private static List<Dictionary<string, string>> Collect(ITestAdaptor node)
        {
            var method = typeof(TestRunnerService).GetMethod("CollectFromNode", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            var tests = new List<Dictionary<string, string>>();
            method.Invoke(null, new object[] { node, TestMode.EditMode, tests, new HashSet<string>(StringComparer.Ordinal), new List<string>() });
            return tests;
        }

        private static TestNode Suite(string name, params ITestAdaptor[] children) =>
            new TestNode
            {
                Name = name,
                FullName = name,
                IsSuite = true,
                Children = children,
            };

        private static TestNode Leaf(string name, string fullName) => new TestNode { Name = name, FullName = fullName };

        private class TestNode : ITestAdaptor
        {
            public string Id => UniqueName;
            public string Name { get; set; }
            public string FullName { get; set; }
            public int TestCaseCount => IsSuite ? Children?.Sum(child => child.TestCaseCount) ?? 0 : 1;
            public bool HasChildren => Children?.Any() ?? false;
            public bool IsSuite { get; set; }
            public IEnumerable<ITestAdaptor> Children { get; set; } = Array.Empty<ITestAdaptor>();
            public ITestAdaptor Parent => null;
            public int TestCaseTimeout => 0;
            public ITypeInfo TypeInfo => null;
            public IMethodInfo Method => null;
            public object[] Arguments => Array.Empty<object>();
            public string[] Categories => Array.Empty<string>();
            public bool IsTestAssembly => IsSuite && Name.EndsWith(".dll", StringComparison.Ordinal);
            public TestRunState RunState => TestRunState.Runnable;
            public string Description => null;
            public string SkipReason => null;
            public string ParentId => null;
            public string ParentFullName => null;
            public string UniqueName => FullName ?? Name;
            public string ParentUniqueName => null;
            public int ChildIndex => 0;
            public TestMode TestMode => TestMode.EditMode;
        }
    }
}
