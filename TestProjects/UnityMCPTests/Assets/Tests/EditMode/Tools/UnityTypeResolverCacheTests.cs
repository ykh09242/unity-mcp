using System;
using System.Collections.Generic;
using System.Reflection;
using System.Reflection.Emit;
using MCPForUnity.Editor.Helpers;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class UnityTypeResolverCacheTests
    {
        private Dictionary<string, Type> fullNames,
            shortNames;
        private Dictionary<(string name, Type constraint), Type> constrained;
        private Dictionary<string, Type> previousFullNames,
            previousShortNames;
        private Dictionary<(string name, Type constraint), Type> previousConstrained;

        [SetUp]
        public void SetUp()
        {
            RefreshGeneration();
            fullNames = ReadField<Dictionary<string, Type>>("CacheByFqn");
            shortNames = ReadField<Dictionary<string, Type>>("CacheByName");
            constrained = ReadField<Dictionary<(string name, Type constraint), Type>>("CacheByConstraint");
            previousFullNames = new(fullNames);
            previousShortNames = new(shortNames);
            previousConstrained = new(constrained);
            fullNames.Clear();
            shortNames.Clear();
            constrained.Clear();
        }

        [TearDown]
        public void TearDown()
        {
            RefreshGeneration();
            Restore(fullNames, previousFullNames);
            Restore(shortNames, previousShortNames);
            Restore(constrained, previousConstrained);
        }

        [Test]
        public void RepeatedConstrainedShortNameRetainsItsOwnCachedResolution()
        {
            string name = nameof(ResolverCacheA.ResolverCacheCollision);
            Assert.IsTrue(UnityTypeResolver.TryResolve(name, out Type first, out string error, typeof(ResolverCacheBaseA)), error);
            Assert.AreSame(typeof(ResolverCacheA.ResolverCacheCollision), first);
            Assert.AreSame(first, constrained[(name, typeof(ResolverCacheBaseA))]);
            Assert.IsTrue(UnityTypeResolver.TryResolve(name, out Type second, out error, typeof(ResolverCacheBaseB)), error);
            Assert.AreSame(typeof(ResolverCacheB.ResolverCacheCollision), second);
            Assert.AreSame(second, constrained[(name, typeof(ResolverCacheBaseB))]);
            Assert.IsTrue(UnityTypeResolver.TryResolve(name, out Type again, out error, typeof(ResolverCacheBaseA)), error);
            Assert.AreSame(first, again);
            Assert.IsFalse(UnityTypeResolver.TryResolve(name, out _, out error));
            StringAssert.Contains("Ambiguous", error);
            Assert.IsFalse(shortNames.ContainsKey(name));
        }

        [Test]
        public void ConstrainedCacheStaysBoundedUnderPressure()
        {
            int limit = ReadField<int>("MaxConstrainedCacheEntries");
            for (int i = 0; i < limit; i++)
                constrained[("__OwnedCachePressure" + i, typeof(object))] = typeof(string);
            string name = nameof(ResolverCacheA.ResolverCacheCollision);
            Assert.IsTrue(UnityTypeResolver.TryResolve(name, out Type resolved, out string error, typeof(ResolverCacheBaseA)), error);
            Assert.AreSame(typeof(ResolverCacheA.ResolverCacheCollision), resolved);
            Assert.LessOrEqual(constrained.Count, limit);
            Assert.AreSame(resolved, constrained[(name, typeof(ResolverCacheBaseA))]);
        }

        [Test]
        public void NewAssemblyInvalidatesAFormerlyUniqueConstrainedName()
        {
            string name = "OwnedReloadCache_" + Guid.NewGuid().ToString("N");
            var firstModule = AssemblyBuilder
                .DefineDynamicAssembly(new AssemblyName(name + "First"), AssemblyBuilderAccess.Run)
                .DefineDynamicModule("OwnedModule");
            Type first = firstModule.DefineType("First." + name, TypeAttributes.Public, typeof(ResolverCacheBaseA)).CreateType();
            Assert.IsTrue(UnityTypeResolver.TryResolve(name, out Type resolved, out string error, typeof(ResolverCacheBaseA)), error);
            Assert.AreSame(first, resolved);
            // Seed a positive entry to exercise assembly invalidation independently of the dynamic-type bypass.
            constrained[(name, typeof(ResolverCacheBaseA))] = first;
            var secondModule = AssemblyBuilder
                .DefineDynamicAssembly(new AssemblyName(name + "Second"), AssemblyBuilderAccess.Run)
                .DefineDynamicModule("OwnedModule");
            secondModule.DefineType("Second." + name, TypeAttributes.Public, typeof(ResolverCacheBaseA)).CreateType();
            Assert.IsFalse(UnityTypeResolver.TryResolve(name, out resolved, out error, typeof(ResolverCacheBaseA)));
            Assert.IsNull(resolved);
            StringAssert.Contains("Ambiguous", error);
            Assert.IsFalse(constrained.ContainsKey((name, typeof(ResolverCacheBaseA))));
        }

        [Test]
        public void TypesAddedToAnExistingDynamicAssemblyRevalidateStaticShortNameHits()
        {
            string name = "OwnedLateDefinition_" + Guid.NewGuid().ToString("N");
            var module = AssemblyBuilder.DefineDynamicAssembly(new AssemblyName(name), AssemblyBuilderAccess.Run).DefineDynamicModule("OwnedModule");
            RefreshGeneration();
            // Seed an immutable positive result without adding a permanent collision to a compiled fixture's name.
            constrained[(name, typeof(ResolverCacheBaseA))] = typeof(ResolverCacheA.ResolverCacheCollision);
            shortNames[name] = typeof(ResolverCacheA.ResolverCacheCollision);
            Type late = module.DefineType("Late." + name, TypeAttributes.Public, typeof(ResolverCacheBaseA)).CreateType();
            Assert.IsTrue(UnityTypeResolver.TryResolve(name, out Type resolved, out string error, typeof(ResolverCacheBaseA)), error);
            Assert.AreSame(late, resolved, "A stale immutable hit cannot hide newly defined mutable metadata.");
            Assert.IsTrue(UnityTypeResolver.TryResolve(name, out resolved, out error), error);
            Assert.AreSame(late, resolved);
        }

        [Test]
        public void DynamicAssemblyShortNamesAreNotCachedWhileTypesCanStillBeAdded()
        {
            string name = "OwnedDynamicCache_" + Guid.NewGuid().ToString("N");
            var module = AssemblyBuilder.DefineDynamicAssembly(new AssemblyName(name), AssemblyBuilderAccess.Run).DefineDynamicModule("OwnedModule");
            Type first = module.DefineType("First." + name, TypeAttributes.Public, typeof(ResolverCacheBaseA)).CreateType();
            Assert.IsTrue(UnityTypeResolver.TryResolve(name, out Type resolved, out string error, typeof(ResolverCacheBaseA)), error);
            Assert.AreSame(first, resolved);
            Assert.IsFalse(constrained.ContainsKey((name, typeof(ResolverCacheBaseA))));
            Assert.IsTrue(UnityTypeResolver.TryResolve(name, out resolved, out error), error);
            Assert.IsFalse(shortNames.ContainsKey(name));
            module.DefineType("Second." + name, TypeAttributes.Public, typeof(ResolverCacheBaseA)).CreateType();
            Assert.IsFalse(UnityTypeResolver.TryResolve(name, out _, out error, typeof(ResolverCacheBaseA)));
            StringAssert.Contains("Ambiguous", error);
            Assert.IsFalse(UnityTypeResolver.TryResolve(name, out _, out error));
            StringAssert.Contains("Ambiguous", error);
        }

        private static T ReadField<T>(string name) => (T)typeof(UnityTypeResolver).GetField(name, BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);

        private static void RefreshGeneration() =>
            typeof(UnityTypeResolver).GetMethod("RefreshCacheGeneration", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);

        private static void Restore<TKey>(Dictionary<TKey, Type> cache, Dictionary<TKey, Type> previous)
        {
            cache.Clear();
            foreach (var entry in previous)
                cache[entry.Key] = entry.Value;
        }
    }

    public class ResolverCacheBaseA { }

    public class ResolverCacheBaseB { }
}

namespace MCPForUnityTests.EditMode.Tools.ResolverCacheA
{
    public class ResolverCacheCollision : MCPForUnityTests.EditMode.Tools.ResolverCacheBaseA { }
}

namespace MCPForUnityTests.EditMode.Tools.ResolverCacheB
{
    public class ResolverCacheCollision : MCPForUnityTests.EditMode.Tools.ResolverCacheBaseB { }
}
