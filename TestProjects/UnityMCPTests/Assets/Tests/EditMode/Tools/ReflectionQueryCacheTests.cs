using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using MCPForUnity.Editor.Tools;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ReflectionQueryCacheTests
    {
        private object previousAssemblies;
        private object previousGeneration;
        private bool previousDynamic;
        private KeyValuePair<Type, string[]>[] previousReceivers;
        private KeyValuePair<Type, MethodInfo[]>[] previousMetadata;
        private ConcurrentDictionary<Type, string[]> Receivers => (ConcurrentDictionary<Type, string[]>)Field("ExtensionMethodCache").GetValue(null);
        private Dictionary<Type, MethodInfo[]> Metadata => (Dictionary<Type, MethodInfo[]>)Field("ExtensionMethodMetadataCache").GetValue(null);

        [SetUp]
        public void SetUp()
        {
            previousAssemblies = Field("_assemblyTypeCache").GetValue(null);
            previousGeneration = Field("_extensionCacheGeneration").GetValue(null);
            previousDynamic = (bool)Field("_hasDynamicExtensionProviders").GetValue(null);
            previousReceivers = Receivers.ToArray();
            previousMetadata = Metadata.ToArray();
            Invoke("InvalidateCache");
        }

        [TearDown]
        public void TearDown()
        {
            Invoke("InvalidateCache");
            Field("_assemblyTypeCache").SetValue(null, previousAssemblies);
            Field("_extensionCacheGeneration").SetValue(null, previousGeneration);
            Field("_hasDynamicExtensionProviders").SetValue(null, previousDynamic);
            foreach (var entry in previousReceivers)
                Receivers.TryAdd(entry.Key, entry.Value);
            foreach (var entry in previousMetadata)
                Metadata.Add(entry.Key, entry.Value);
        }

        [Test]
        public void RepeatedSuccessfulAndMissingMemberQueriesReuseProviderMetadata()
        {
            var provider = new CountingExtensionType();
            Seed(provider);
            var expected = Members("Provided");
            Assert.AreEqual(1, expected.Length);
            provider.Calls = 0;
            for (int i = 0; i < 100; i++)
            {
                CollectionAssert.AreEqual(expected, Members("Provided"));
                Assert.IsEmpty(Members("Missing"));
            }
            Assert.AreEqual(0, provider.Calls);
        }

        [Test]
        public void DifferentCallerTypesCannotRetainAnUnboundedReceiverCache()
        {
            Seed(new CountingExtensionType());
            foreach (var seed in new[] { typeof(int), typeof(string), typeof(bool), typeof(byte), typeof(short), typeof(long), typeof(float), typeof(double) })
            {
                var receiver = seed;
                for (int i = 0; i < 64; i++)
                {
                    receiver = receiver.MakeArrayType();
                    CollectionAssert.AreEqual(new[] { "Provided" }, (string[])Invoke("FindExtensionMethods", receiver));
                }
            }
            int capacity = (int)Field("MaxExtensionReceiverCacheEntries").GetRawConstantValue();
            Assert.That(Receivers.Count, Is.InRange(1, capacity));
        }

        [Test]
        public void InvalidationClearsBothCachesAndRebuildsProviderMetadata()
        {
            var provider = new CountingExtensionType();
            Seed(provider);
            Invoke("FindExtensionMethods", typeof(string));
            Assert.IsNotEmpty(Receivers);
            Assert.IsNotEmpty(Metadata);
            Invoke("InvalidateCache");
            Assert.IsEmpty(Receivers);
            Assert.IsEmpty(Metadata);
            Seed(provider);
            provider.Calls = 0;
            Members("Provided");
            Assert.AreEqual(1, provider.Calls);
        }

        [Test]
        public void ReplacingTypeCacheGenerationCannotReuseOldReceiverResults()
        {
            Seed(new CountingExtensionType());
            Invoke("FindExtensionMethods", typeof(string));
            var replacement = new CountingExtensionType { IncludeLate = true };
            Seed(replacement);
            CollectionAssert.AreEquivalent(new[] { "Provided", "LaterProvided" }, (string[])Invoke("FindExtensionMethods", typeof(string)));
            Assert.AreEqual(1, replacement.Calls);
            Assert.AreEqual(1, Metadata.Count);
        }

        [Test]
        public void DynamicProvidersBypassBothCachesAndExposeChangedMetadata()
        {
            // A delegator models a dynamic provider acquiring methods without a new assembly load.
            var assembly = AssemblyBuilder.DefineDynamicAssembly(
                new AssemblyName("Unity.ReflectionQueryAudit" + Guid.NewGuid().ToString("N")),
                AssemblyBuilderAccess.Run
            );
            var provider = new CountingExtensionType(assembly);
            Seed(provider);
            CollectionAssert.AreEqual(new[] { "Provided" }, (string[])Invoke("FindExtensionMethods", typeof(string)));
            provider.IncludeLate = true;
            CollectionAssert.AreEquivalent(new[] { "Provided", "LaterProvided" }, (string[])Invoke("FindExtensionMethods", typeof(string)));
            Assert.AreEqual(1, Members("LaterProvided").Length);
            Assert.AreEqual(3, provider.Calls);
            Assert.IsEmpty(Receivers);
            Assert.IsEmpty(Metadata);
        }

        private static MethodInfo[] Members(string name) => (MethodInfo[])Invoke("FindExtensionMethodInfos", typeof(string), name);

        private static FieldInfo Field(string name) => typeof(UnityReflect).GetField(name, BindingFlags.NonPublic | BindingFlags.Static);

        private static object Invoke(string name, params object[] arguments) =>
            typeof(UnityReflect).GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, arguments);

        private static void Seed(Type provider) =>
            Field("_assemblyTypeCache").SetValue(null, new Dictionary<string, Type[]> { { "Unity.ReflectionQueryAudit", new[] { provider } } });

        private class CountingExtensionType : TypeDelegator
        {
            public int Calls;
            public bool IncludeLate;
            private readonly Assembly assembly;

            public CountingExtensionType(Assembly assembly = null)
                : base(typeof(ReflectionQueryFixtureExtensions))
            {
                this.assembly = assembly;
            }

            public override Assembly Assembly => assembly ?? base.Assembly;

            public override MethodInfo[] GetMethods(BindingFlags flags)
            {
                Calls++;
                return base.GetMethods(flags).Where(method => IncludeLate || method.Name != "LaterProvided").ToArray();
            }
        }
    }

    public static class ReflectionQueryFixtureExtensions
    {
        public static int Provided(this object receiver) => 1;

        public static int LaterProvided(this object receiver) => 2;
    }
}
