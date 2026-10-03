using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools
{
    // Own benign CLR metadata only. No getter, method, editor, scene or asset mutation.
    // Authored/compiled in this audit; native Mono execution is not claimed.
    [Parallelizable(ParallelScope.None)]
    public class ReflectionMetadataIntegrityTests
    {
        private bool captured;
        private FieldInfo assemblyField;
        private object previousAssemblies;
        private ConcurrentDictionary<Type, string[]> extensions;
        private KeyValuePair<Type, string[]>[] previousExtensions;
        private Dictionary<string, Type> fqn, shortNames;
        private Dictionary<string, Type> previousFqn, previousShortNames;

        [SetUp]
        public void SetUp()
        {
            captured = false;
            if (EditorApplication.isCompiling)
                Assert.Ignore("Do not inspect metadata while Unity compiles.");
            assemblyField = Field(typeof(UnityReflect), "_assemblyTypeCache");
            previousAssemblies = assemblyField.GetValue(null);
            extensions = (ConcurrentDictionary<Type, string[]>)Field(typeof(UnityReflect), "ExtensionMethodCache").GetValue(null);
            previousExtensions = extensions.ToArray();
            fqn = (Dictionary<string, Type>)Field(typeof(UnityTypeResolver), "CacheByFqn").GetValue(null);
            shortNames = (Dictionary<string, Type>)Field(typeof(UnityTypeResolver), "CacheByName").GetValue(null);
            previousFqn = new Dictionary<string, Type>(fqn);
            previousShortNames = new Dictionary<string, Type>(shortNames);
            captured = true;
            var types = new[] { typeof(ReflectionMetadataFixture), typeof(ReflectionMetadataIntList),
                typeof(ReflectionMetadataStringList), typeof(ReflectionMetadataDerived),
                typeof(ReflectionMetadataObjectComparer), typeof(ReflectionMetadataExtensions) };
            assemblyField.SetValue(null, new Dictionary<string, Type[]>
            {
                // This controlled cache key supplies the existing Unity assembly filter.
                // All receiver/extension/member metadata comes from actual owned CLR types.
                ["Unity.ReflectionMetadataFixture, Version=1.0.0.0"] = types
            });
            extensions.Clear();
            fqn.Clear();
            shortNames.Clear();
            foreach (var type in types)
            {
                fqn[type.FullName] = type;
                shortNames[type.Name] = type;
            }
        }

        [TearDown]
        public void TearDown()
        {
            if (!captured) return;
            assemblyField.SetValue(null, previousAssemblies);
            extensions.Clear();
            foreach (var entry in previousExtensions) extensions[entry.Key] = entry.Value;
            fqn.Clear();
            foreach (var entry in previousFqn) fqn[entry.Key] = entry.Value;
            shortNames.Clear();
            foreach (var entry in previousShortNames) shortNames[entry.Key] = entry.Value;
        }

        [Test]
        public void OverloadedIndexersExposeExactIndexTypes()
        {
            var result = Member(typeof(ReflectionMetadataFixture), "Item");
            Assert.IsTrue(result.Value<bool>("success"));
            Assert.AreEqual("property", result["data"].Value<string>("member_type"));
            Assert.AreEqual(2, result["data"].Value<int>("overload_count"));
            var overloads = (JArray)result["data"]["overloads"];
            CollectionAssert.AreEquivalent(new[] { "int", "string" }, overloads.Select(p => p["index_parameters"][0].Value<string>("type")).ToArray());
            CollectionAssert.AreEquivalent(new[] { "int", "string" }, overloads.Select(p => p.Value<string>("property_type")).ToArray());
            Assert.IsTrue(overloads.All(p => p.Value<bool>("can_read") && !p.Value<bool>("can_write")));
        }

        [TestCase("Matrix", "int[,]")]
        [TestCase("Cube", "int[,,]")]
        [TestCase("Jagged", "int[][]")]
        [TestCase("Vector", "int[]")]
        public void MethodArrayReturnPreservesRank(string member, string expected)
        {
            var result = Member(typeof(ReflectionMetadataFixture), member);
            Assert.IsTrue(result.Value<bool>("success"));
            Assert.AreEqual(expected, result["data"]["overloads"][0].Value<string>("return_type"));
        }

        [Test]
        public void MatrixParameterSignaturePreservesRank()
        {
            var result = Member(typeof(ReflectionMetadataFixture), "Consume");
            Assert.IsTrue(result.Value<bool>("success"));
            Assert.AreEqual("int[,,]", result["data"]["overloads"][0]["parameters"][0].Value<string>("type"));
            StringAssert.Contains("int[,,]", result["data"]["overloads"][0].Value<string>("signature"));
        }

        [Test]
        public void OrdinaryPropertyRetainsExistingFields()
        {
            var result = Member(typeof(ReflectionMetadataFixture), "Value");
            Assert.IsTrue(result.Value<bool>("success"));
            Assert.AreEqual("property", result["data"].Value<string>("member_type"));
            Assert.AreEqual("string", result["data"].Value<string>("property_type"));
            Assert.IsTrue(result["data"].Value<bool>("can_read"));
            Assert.IsTrue(result["data"].Value<bool>("can_write"));
            Assert.IsFalse(result["data"].Value<bool>("is_static"));
            Assert.IsNull(result["data"]["overloads"]);
        }

        [TestCase(typeof(ReflectionMetadataStringList), "ClosedInts", false)]
        [TestCase(typeof(ReflectionMetadataIntList), "ClosedInts", true)]
        [TestCase(typeof(ReflectionMetadataStringList), "AnyItems", true)]
        [TestCase(typeof(ReflectionMetadataDerived), "FromBase", true)]
        [TestCase(typeof(ReflectionMetadataStringList), "CovariantObjects", true)]
        [TestCase(typeof(ReflectionMetadataObjectComparer), "ContravariantStrings", true)]
        public void ExtensionLookupMatchesReceiver(Type type, string method, bool expected)
        {
            var result = Member(type, method);
            Assert.IsTrue(result.Value<bool>("success"));
            Assert.AreEqual(expected, result["data"].Value<bool>("found"));
            var typeResult = Invoke("get_type", type);
            Assert.IsTrue(typeResult.Value<bool>("success"));
            Assert.AreEqual(expected, ((JArray)typeResult["data"]["extension_methods"]).Values<string>().Contains(method));
        }

        [Test]
        public void WarmCacheRetainsTheSameMetadata()
        {
            var first = Invoke("get_type", typeof(ReflectionMetadataStringList));
            Assert.IsTrue(first.Value<bool>("success"));
            var names = extensions[typeof(ReflectionMetadataStringList)];
            var second = Invoke("get_type", typeof(ReflectionMetadataStringList));
            Assert.IsTrue(JToken.DeepEquals(first, second));
            Assert.AreSame(names, extensions[typeof(ReflectionMetadataStringList)]);
        }

        private static JObject Member(Type type, string member) => Invoke("get_member", type, member);
        private static JObject Invoke(string action, Type type, string member = null)
        {
            // Public dispatch remains in the controlled complete-source proof. Calling
            // its editor-state preflight here could initialize unowned global services.
            var method = typeof(UnityReflect).GetMethod(action == "get_type" ? "GetTypeInfo" : "GetMemberInfo",
                BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            var parameters = new ToolParams(new JObject
            {
                ["action"] = action, ["class_name"] = type.FullName, ["member_name"] = member
            });
            return JObject.FromObject(method.Invoke(null, new object[] { parameters }));
        }
        private static FieldInfo Field(Type type, string name)
        {
            var field = type.GetField(name, BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(field, name);
            return field;
        }
    }

    public class ReflectionMetadataFixture
    {
        public int this[int index] => throw new InvalidOperationException("Metadata only; do not invoke.");
        public string this[string key] => throw new InvalidOperationException("Metadata only; do not invoke.");
        public string Value { get; set; }
        public int[,] Matrix() => throw new InvalidOperationException("Metadata only; do not invoke.");
        public int[,,] Cube() => throw new InvalidOperationException("Metadata only; do not invoke.");
        public int[][] Jagged() => throw new InvalidOperationException("Metadata only; do not invoke.");
        public int[] Vector() => throw new InvalidOperationException("Metadata only; do not invoke.");
        public void Consume(int[,,] cube) => throw new InvalidOperationException("Metadata only; do not invoke.");
    }
    public class ReflectionMetadataBase<T> { }
    public class ReflectionMetadataDerived : ReflectionMetadataBase<int> { }
    public class ReflectionMetadataIntList : List<int> { }
    public class ReflectionMetadataStringList : List<string> { }
    public class ReflectionMetadataObjectComparer : IComparer<object>
    {
        public int Compare(object x, object y) => throw new InvalidOperationException("Metadata only; do not invoke.");
    }
    public static class ReflectionMetadataExtensions
    {
        public static int ClosedInts(this ICollection<int> value) => 0;
        public static int AnyItems<T>(this ICollection<T> value) => 0;
        public static int FromBase<T>(this ReflectionMetadataBase<T> value) => 0;
        public static int CovariantObjects(this IEnumerable<object> value) => 0;
        public static int ContravariantStrings(this IComparer<string> value) => 0;
    }
}
