using System;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class DynamicCompilationLifetimeTests
    {
        [Test]
        public void SixtyFiveDistinctSnippets_DoNotEvictTheFirstLoadedAssembly()
        {
            string nonce = Guid.NewGuid().ToString("N");
            string first = "return 740001; // " + nonce;
            Execute(first);
            // The first compiler invocation can load its own file-backed dependencies.
            Execute(first);
            for (int index = 1; index < 65; index++)
                Execute("return " + (740001 + index) + "; // " + nonce);
            int loaded = LoadedSnippets();
            Execute(first);
            Assert.AreEqual(loaded, LoadedSnippets(), "Already loaded assemblies must remain reusable beyond the former 64-entry LRU.");
        }

        [Test]
        public void LifetimeBudget_RefusesNewLoadsButStillReusesExistingSources()
        {
            Type cacheType = typeof(UnityObjectIdCompat).Assembly.GetType("MCPForUnity.Runtime.Helpers.DynamicCompilationCache");
            Assert.IsNotNull(cacheType, "Compilation needs an admission budget for non-unloadable assemblies.");
            object cache = Activator.CreateInstance(cacheType, new object[] { 2 });
            MethodInfo compile = cacheType.GetMethod("TryGetOrCompile");
            int calls = 0;
            Func<Assembly> factory = () =>
            {
                calls++;
                return typeof(DynamicCompilationLifetimeTests).Assembly;
            };
            Assert.IsTrue(Compile(cache, compile, "first", factory));
            Assert.IsTrue(Compile(cache, compile, "second", factory));
            Assert.IsFalse(Compile(cache, compile, "third", factory));
            Assert.IsTrue(Compile(cache, compile, "first", factory));
            Assert.AreEqual(2, calls);
        }

        [Test]
        public void FailedCompilation_DoesNotConsumeAdmission()
        {
            Type cacheType = typeof(UnityObjectIdCompat).Assembly.GetType("MCPForUnity.Runtime.Helpers.DynamicCompilationCache");
            Assert.IsNotNull(cacheType);
            object cache = Activator.CreateInstance(cacheType, new object[] { 1 });
            MethodInfo compile = cacheType.GetMethod("TryGetOrCompile");
            Assert.IsTrue(Compile(cache, compile, "bad", () => null));
            Assert.IsTrue(Compile(cache, compile, "good", () => typeof(DynamicCompilationLifetimeTests).Assembly));
            Assert.IsFalse(Compile(cache, compile, "different", () => typeof(DynamicCompilationLifetimeTests).Assembly));
        }

        [Test]
        public void ReentrantCompiler_CountsBothLoadsAgainstTheLifetimeBudget()
        {
            var cache = new DynamicCompilationCache(2);
            Assert.IsTrue(
                cache.TryGetOrCompile(
                    "same",
                    () =>
                    {
                        Assert.IsTrue(cache.TryGetOrCompile("same", () => typeof(DynamicCompilationLifetimeTests).Assembly, out _));
                        return typeof(DynamicCompilationLifetimeTests).Assembly;
                    },
                    out _
                )
            );
            Assert.AreEqual(2, cache.Count);
            Assert.IsFalse(cache.TryGetOrCompile("another", () => typeof(DynamicCompilationLifetimeTests).Assembly, out _));
        }

        private static bool Compile(object cache, MethodInfo method, string key, Func<Assembly> factory)
        {
            object[] arguments = { key, factory, null };
            return (bool)method.Invoke(cache, arguments);
        }

        private static void Execute(string source)
        {
            var response = JObject.FromObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "execute", ["code"] = source }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
        }

        private static int LoadedSnippets() => AppDomain.CurrentDomain.GetAssemblies().Count(assembly => assembly.GetType("MCPDynamicCode", false) != null);
    }
}
