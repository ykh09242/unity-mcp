using System;
using System.Reflection;
using MCPForUnity.Editor.Services;
using NUnit.Framework;

namespace MCPForUnity.Editor.Tests.EditMode.Services
{
    public class CompilationEventSnapshotTests
    {
        [TestCase(true)]
        [TestCase(false)]
        public void EventTrackedStateDoesNotReenterNativeCompilationQuery(bool compiling)
        {
            var method = ReadMethod();
            int nativeReads = 0;
            Func<bool> readNative = () =>
            {
                nativeReads++;
                throw new InvalidOperationException("event snapshot must not reenter native compilation enumeration");
            };
            Assert.That((bool)method.Invoke(null, new object[] { compiling, readNative }), Is.EqualTo(compiling));
            Assert.That(nativeReads, Is.Zero);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void OrdinarySnapshotRetainsNativeCompilationQuery(bool nativeState)
        {
            var method = ReadMethod();
            int nativeReads = 0;
            Func<bool> readNative = () =>
            {
                nativeReads++;
                return nativeState;
            };
            Assert.That((bool)method.Invoke(null, new object[] { null, readNative }), Is.EqualTo(nativeState));
            Assert.That(nativeReads, Is.EqualTo(1));
        }

        private static MethodInfo ReadMethod()
        {
            // Inspect the pure nested selector without initializing the outer Editor cache.
            var selector = typeof(EditorStateCache).GetNestedType("CompilationObservation", BindingFlags.NonPublic);
            Assert.That(selector, Is.Not.Null, "compilation-event snapshots still query native compilation state");
            var method = selector.GetMethod("Read", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.That(method, Is.Not.Null);
            return method;
        }
    }
}
