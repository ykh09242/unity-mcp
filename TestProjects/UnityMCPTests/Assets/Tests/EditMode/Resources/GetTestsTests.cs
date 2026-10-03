using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Resources.Tests;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.TestTools.TestRunner.Api;

namespace MCPForUnityTests.Editor.Resources
{
    public class GetTestsTests
    {
        private object _previousService;
        private FieldInfo _serviceField;

        [SetUp]
        public void SetUp()
        {
            _serviceField = typeof(MCPServiceLocator).GetField("_testRunnerService", BindingFlags.Static | BindingFlags.NonPublic);
            _previousService = _serviceField.GetValue(null);
            MCPServiceLocator.Register<ITestRunnerService>(new FakeTestRunner());
        }

        [TearDown]
        public void TearDown()
        {
            _serviceField.SetValue(null, _previousService);
        }

        [Test]
        public async Task OmittedParameters_ReturnDefaultTestPage()
        {
            var response = JObject.FromObject(await GetTests.HandleCommand(null));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual("FirstTest", response["data"]["items"][0]["name"].Value<string>());
            Assert.AreEqual(50, response["data"]["pageSize"].Value<int>());
        }

        [Test]
        public async Task ClampedPageSize_UsesClampedSizeForPageOffsetWithoutMutatingInput()
        {
            var parameters = new JObject { ["page_size"] = 300, ["page_number"] = 2 };

            var response = JObject.FromObject(await GetTests.HandleCommand(parameters));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(200, response["data"]["items"][0]["index"].Value<int>());
            Assert.AreEqual(200, response["data"]["pageSize"].Value<int>());
            Assert.AreEqual(300, parameters.Value<int>("page_size"));
        }

        [TestCase(false, null)]
        [TestCase(false, "")]
        [TestCase(true, null)]
        [TestCase(true, "")]
        public async Task UnfilteredPage_ReadsOnlyRequestedItems(bool forMode, string filter)
        {
            var tests = new CountingTestList(CreateTests());
            MCPServiceLocator.Register<ITestRunnerService>(new FakeTestRunner(tests));
            var parameters = new JObject
            {
                ["mode"] = "EditMode", ["cursor"] = 200, ["page_size"] = 25, ["filter"] = filter
            };

            var response = JObject.FromObject(await ReadPage(forMode, parameters));

            Assert.AreEqual(450, response["data"]["totalCount"].Value<int>());
            Assert.AreEqual(25, ((JArray)response["data"]["items"]).Count);
            Assert.AreEqual(200, response["data"]["items"][0]["index"].Value<int>());
            Assert.AreEqual(225, response["data"]["nextCursor"].Value<int>());
            Assert.AreEqual(0, tests.Copies, "An unfiltered page should not copy the full result set.");
            Assert.AreEqual(25, tests.Reads, "Only the requested page should be read.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task FilteredPage_PreservesCaseInsensitiveFullNameMatches(bool forMode)
        {
            var parameters = new JObject
            {
                ["mode"] = "EditMode", ["filter"] = "suite.test42", ["cursor"] = 1, ["page_size"] = 3
            };

            var response = JObject.FromObject(await ReadPage(forMode, parameters));

            Assert.AreEqual(11, response["data"]["totalCount"].Value<int>());
            CollectionAssert.AreEqual(new[] { "420", "421", "422" },
                response["data"]["items"].Select(item => item["index"].Value<string>()).ToArray());
            Assert.AreEqual(4, response["data"]["nextCursor"].Value<int>());
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task ReadOnlyListWithoutMutableInterface_RemainsPageable(bool forMode)
        {
            MCPServiceLocator.Register<ITestRunnerService>(new FakeTestRunner(new ReadOnlyTests(CreateTests())));
            var parameters = new JObject { ["mode"] = "EditMode", ["cursor"] = 449, ["page_size"] = 25 };

            var response = JObject.FromObject(await ReadPage(forMode, parameters));

            Assert.AreEqual(450, response["data"]["totalCount"].Value<int>());
            Assert.AreEqual(1, ((JArray)response["data"]["items"]).Count);
            Assert.AreEqual("449", response["data"]["items"][0]["index"].Value<string>());
            Assert.IsFalse(response["data"]["hasMore"].Value<bool>());
        }

        private static Task<object> ReadPage(bool forMode, JObject parameters)
            => forMode ? GetTestsForMode.HandleCommand(parameters) : GetTests.HandleCommand(parameters);

        private static List<Dictionary<string, string>> CreateTests()
        {
            var tests = new List<Dictionary<string, string>>();
            for (int i = 0; i < 450; i++)
                tests.Add(new Dictionary<string, string>
                {
                    ["name"] = i == 0 ? "FirstTest" : "Test" + i,
                    ["full_name"] = "Suite.Test" + i,
                    ["index"] = i.ToString()
                });
            return tests;
        }

        private sealed class ReadOnlyTests : IReadOnlyList<Dictionary<string, string>>
        {
            private readonly List<Dictionary<string, string>> _items;
            public ReadOnlyTests(List<Dictionary<string, string>> items) => _items = items;
            public int Count => _items.Count;
            public Dictionary<string, string> this[int index] => _items[index];
            public IEnumerator<Dictionary<string, string>> GetEnumerator() => _items.GetEnumerator();
            IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
        }

        private sealed class CountingTestList : IList<Dictionary<string, string>>, IReadOnlyList<Dictionary<string, string>>
        {
            private readonly List<Dictionary<string, string>> _items;
            public CountingTestList(List<Dictionary<string, string>> items) => _items = items;
            public int Copies { get; private set; }
            public int Reads { get; private set; }
            public int Count => _items.Count;
            public bool IsReadOnly => true;
            public Dictionary<string, string> this[int index]
            {
                get { Reads++; return _items[index]; }
                set => throw new NotSupportedException();
            }
            public void CopyTo(Dictionary<string, string>[] array, int arrayIndex)
            {
                Copies++;
                Reads += _items.Count;
                _items.CopyTo(array, arrayIndex);
            }
            public IEnumerator<Dictionary<string, string>> GetEnumerator()
            {
                for (int i = 0; i < Count; i++) yield return this[i];
            }
            IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
            public int IndexOf(Dictionary<string, string> item) => _items.IndexOf(item);
            public bool Contains(Dictionary<string, string> item) => _items.Contains(item);
            public void Add(Dictionary<string, string> item) => throw new NotSupportedException();
            public void Clear() => throw new NotSupportedException();
            public void Insert(int index, Dictionary<string, string> item) => throw new NotSupportedException();
            public bool Remove(Dictionary<string, string> item) => throw new NotSupportedException();
            public void RemoveAt(int index) => throw new NotSupportedException();
        }

        private sealed class FakeTestRunner : ITestRunnerService
        {
            private readonly IReadOnlyList<Dictionary<string, string>> _tests;
            public FakeTestRunner(IReadOnlyList<Dictionary<string, string>> tests = null) => _tests = tests ?? CreateTests();

            public Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode)
                => Task.FromResult(_tests);

            public Task<TestRunResult> RunTestsAsync(TestMode mode, TestFilterOptions filterOptions = null)
                => throw new NotSupportedException();
        }
    }
}
