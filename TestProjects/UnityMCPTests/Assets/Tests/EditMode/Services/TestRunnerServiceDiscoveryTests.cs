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
