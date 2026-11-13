using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Resources.Tests;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using NUnit.Framework.Interfaces;
using UnityEditor;
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
        public async Task EmptySuite_IsNotListedAsATest()
        {
            Assert.IsEmpty(await Collect(Suite("EmptyRoot")));
        }

        [Test]
        public async Task EmptyAssembly_IsNotListedAlongsideActualTests()
        {
            var root = Suite("EditMode", Suite("Empty.dll"), Suite("Tests.dll", Leaf("Passes", "Fixture.Passes")));
            var tests = await Collect(root);
            Assert.AreEqual(1, tests.Count);
            Assert.AreEqual("Fixture.Passes", tests[0]["full_name"]);
            Assert.AreEqual("EditMode/Tests.dll/Passes", tests[0]["path"]);
            Assert.AreEqual("EditMode", tests[0]["mode"]);
        }

        [Test]
        public async Task SuiteWithNullChildren_IsNotListedAsATest()
        {
            var suite = Suite("EmptyRoot");
            suite.Children = null;
            Assert.IsEmpty(await Collect(suite));
        }

        [Test]
        public async Task LeafWithoutFullName_UsesItsName()
        {
            var tests = await Collect(Leaf("Passes", null));
            Assert.AreEqual(1, tests.Count);
            Assert.AreEqual("Passes", tests[0]["full_name"]);
            Assert.AreEqual("Passes", tests[0]["path"]);
        }

        [Test]
        public async Task DuplicateSameModeTest_RemainsDeduplicated()
        {
            var leaf = Leaf("Passes", "Fixture.Passes");
            Assert.AreEqual(1, (await Collect(Suite("EditMode", leaf, leaf))).Count);
        }

        private static async Task<IReadOnlyList<Dictionary<string, string>>> Collect(ITestAdaptor node)
        {
            using var service = new TestRunnerService((_, complete) => complete(node), TimeSpan.FromMinutes(1));
            return await service.GetTestsAsync(TestMode.EditMode);
        }

        [Test]
        public async Task LargeFilteredPage_YieldsAndReleasesLockForFreshNextPage()
        {
            const int leaves = 4096;
            int calls = 0,
                visited = 0,
                tick = 0,
                lastTick = -1,
                slices = 0,
                maxBatch = 0,
                batch = 0;
            long firstVisit = 0,
                lastVisit = 0,
                activeTicks = 0,
                maxSlice = 0;
            var lockInterval = new Stopwatch();
            void FinishSlice()
            {
                long span = lastVisit - firstVisit;
                activeTicks += span;
                maxSlice = Math.Max(maxSlice, span);
                maxBatch = Math.Max(maxBatch, batch);
            }
            IEnumerable<ITestAdaptor> Cases()
            {
                for (int i = 0; i < leaves; i++)
                {
                    long now = Stopwatch.GetTimestamp();
                    if (tick != lastTick)
                    {
                        FinishSlice();
                        firstVisit = now;
                        batch = 0;
                        lastTick = tick;
                        slices++;
                    }
                    batch++;
                    visited++;
                    lastVisit = now;
                    yield return Leaf("Case" + i, (i % 2 == 0 ? "Matches." : "Other.") + i);
                }
                FinishSlice();
            }
            var root = Suite("OldAssembly");
            root.Children = Cases();
            using var service = new TestRunnerService(
                (_, complete) =>
                {
                    if (++calls == 1)
                    {
                        lockInterval.Start();
                        complete(root);
                    }
                    else
                    {
                        lockInterval.Stop();
                        complete(Suite("NewAssembly", Leaf("First", "Matches.A"), Leaf("Second", "Matches.B")));
                    }
                },
                TimeSpan.FromMinutes(1)
            );
            var field = typeof(MCPServiceLocator).GetField("_testRunnerService", BindingFlags.Static | BindingFlags.NonPublic);
            object original = field.GetValue(null);
            void Tick() => tick++;
            EditorApplication.update += Tick;
            try
            {
                field.SetValue(null, service);
                var parameters = new JObject
                {
                    ["mode"] = "EditMode",
                    ["filter"] = "mAtChEs",
                    ["page_size"] = 1,
                };
                var first = GetTests.HandleCommand(parameters);
                Assert.IsFalse(first.IsCompleted, "A large inventory must yield before the first page completes.");
                Assert.LessOrEqual(visited, 256);
                parameters["cursor"] = 1;
                var second = GetTestsForMode.HandleCommand(parameters);
                Assert.AreEqual(1, calls, "The competing page must wait for the active retrieval.");
                var initial = JObject.FromObject(await first)["data"];
                var refreshed = JObject.FromObject(await second)["data"];
                Assert.AreEqual(leaves, visited);
                Assert.Greater(slices, 1, "Editor updates must interleave with the traversal.");
                Assert.AreEqual(leaves / 2, initial.Value<int>("totalCount"));
                Assert.AreEqual(1, initial.Value<int>("nextCursor"));
                Assert.AreEqual("OldAssembly/Case0", initial["items"][0].Value<string>("path"));
                Assert.AreEqual(2, refreshed.Value<int>("totalCount"));
                Assert.IsFalse(refreshed.Value<bool>("hasMore"));
                Assert.AreEqual("NewAssembly/Second", refreshed["items"][0].Value<string>("path"));
                Assert.AreEqual(2, calls, "Dynamic NUnit cases can change even without reloading the assembly.");
                TestContext.WriteLine(
                    $"discovery synthetic nodes={leaves + 1} updates_with_visits={slices} max_leaves_per_update={maxBatch} "
                        + $"editor_visit_spans_ms={1000.0 * activeTicks / Stopwatch.Frequency:F2} longest_visit_span_ms={1000.0 * maxSlice / Stopwatch.Frequency:F2} "
                        + $"first_retrieval_to_waiter_admission_ms={lockInterval.Elapsed.TotalMilliseconds:F2}"
                );
            }
            finally
            {
                EditorApplication.update -= Tick;
                field.SetValue(null, original);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task InterruptedTraversalDisposesEnumeratorsAndSkipsUnvisitedLeaves(bool reload)
        {
            int visits = 0,
                closes = 0;
            IEnumerable<ITestAdaptor> Children()
            {
                try
                {
                    for (int i = 0; i < 4096; i++)
                    {
                        visits++;
                        yield return Leaf("Case" + visits, null);
                    }
                }
                finally
                {
                    closes++;
                }
            }
            var root = Suite("Cases");
            root.Children = Children();
            using var service = new TestRunnerService((_, complete) => complete(root), TimeSpan.FromMinutes(1));
            var pending = service.GetTestsAsync(TestMode.EditMode);
            Assert.IsFalse(pending.IsCompleted);
            var queued = service.GetTestsAsync(TestMode.PlayMode);
            int before = visits;
            if (reload)
                typeof(TestRunnerService).GetMethod("ReleaseApi", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(service, null);
            else
                service.Dispose();
            foreach (var task in new[] { pending, queued })
            {
                try
                {
                    await task;
                    Assert.Fail("Invalidation must cancel active and waiting discovery.");
                }
                catch (OperationCanceledException) { }
            }
            Assert.AreEqual(before, visits);
            // An enumerator which yielded a case has entered its try/finally.
            Assert.AreEqual(before == 0 ? 0 : 1, closes);
        }

        [Test]
        public async Task ExpiredLargeTraversalReleasesEnumeratorAndAdmitsQueuedDiscovery()
        {
            int visits = 0,
                closes = 0,
                calls = 0;
            IEnumerable<ITestAdaptor> Cases()
            {
                try
                {
                    for (int i = 0; i < 4096; i++)
                    {
                        visits++;
                        yield return Leaf("Case" + i, null);
                    }
                }
                finally
                {
                    closes++;
                }
            }
            var root = Suite("Cases");
            root.Children = Cases();
            System.Threading.CancellationTokenSource observation = null;
            using var service = new TestRunnerService(
                (_, complete) => complete(++calls == 1 ? root : Leaf("Retry", null)),
                TimeSpan.FromMinutes(1),
                budget => observation ??= budget
            );
            var pending = service.GetTestsAsync(TestMode.EditMode);
            Assert.IsFalse(pending.IsCompleted);
            var retry = service.GetTestsAsync(TestMode.EditMode);
            Assert.IsFalse(retry.IsCompleted);
            int before = visits;
            observation.Cancel();
            try
            {
                await pending;
                Assert.Fail("A yielded traversal must honor its observation deadline.");
            }
            catch (TimeoutException) { }
            Assert.AreEqual(before, visits);
            Assert.AreEqual(before == 0 ? 0 : 1, closes);
            Assert.AreEqual("Retry", (await retry)[0]["full_name"]);
        }

        [Test]
        public async Task DeepHierarchyPreservesPathAcrossSlicesAndUnwindsBeforeSibling()
        {
            const int depth = 2048;
            ITestAdaptor child = Leaf("Bottom", "A.Bottom");
            for (int i = 0; i < depth; i++)
                child = Suite("N", child);
            using var service = new TestRunnerService((_, complete) => complete(Suite("Root", child, Leaf("Other", "A.Other"))), TimeSpan.FromMinutes(1));
            var result = await service.GetTestsAsync(TestMode.EditMode);
            Assert.AreEqual("Root/" + string.Concat(Enumerable.Repeat("N/", depth)) + "Bottom", result[0]["path"]);
            Assert.AreEqual("Root/Other", result[1]["path"]);
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
            public bool HasChildren => IsSuite && Children != null;
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
