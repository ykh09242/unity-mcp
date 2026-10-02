using System;
using System.Collections.Generic;
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

        private sealed class FakeTestRunner : ITestRunnerService
        {
            public Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode)
            {
                var tests = new List<Dictionary<string, string>>();
                for (int i = 0; i < 450; i++)
                    tests.Add(new Dictionary<string, string> { ["name"] = i == 0 ? "FirstTest" : "Test" + i, ["index"] = i.ToString() });
                return Task.FromResult<IReadOnlyList<Dictionary<string, string>>>(tests);
            }

            public Task<TestRunResult> RunTestsAsync(TestMode mode, TestFilterOptions filterOptions = null)
                => throw new NotSupportedException();
        }
    }
}
