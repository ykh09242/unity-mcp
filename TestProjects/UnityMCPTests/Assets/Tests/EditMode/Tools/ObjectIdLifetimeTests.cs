using System;
using System.Collections;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class ObjectIdLifetimeTests
    {
        [UnityTest]
        public IEnumerator DeadIdsExpireWithoutAnotherLookup_WhileLiveHandlesRemain()
        {
            Type type = typeof(UnityObjectIdCompat).Assembly.GetType("MCPForUnity.Runtime.Helpers.EditorObjectIdCache");
            Assert.IsNotNull(type, "The Editor reverse cache needs a lifetime owner.");
            var live = new GameObject("ObjectIdLifetimeLive");
            var dead = new GameObject("ObjectIdLifetimeDead");
            double now = 0;
            Func<ulong, UnityEngine.Object> resolve = id => id == 1 ? live : dead;
            object cache = Activator.CreateInstance(
                type,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                null,
                new object[] { resolve, (Func<double>)(() => now) },
                null
            );
            try
            {
                type.GetMethod("Remember").Invoke(cache, new object[] { 1, 1UL });
                type.GetMethod("Remember").Invoke(cache, new object[] { 2, 2UL });
                UnityEngine.Object.DestroyImmediate(dead);
                now = 11;
                yield return null;
                yield return null;
                Assert.AreEqual(1, type.GetProperty("Count").GetValue(cache));
                Assert.AreSame(live, type.GetMethod("Resolve").Invoke(cache, new object[] { 1 }));
                Assert.IsNull(type.GetMethod("Resolve").Invoke(cache, new object[] { 2 }));
                type.GetMethod("Clear").Invoke(cache, null);
                Assert.AreEqual(0, type.GetProperty("Count").GetValue(cache));
                Assert.IsFalse((bool)type.GetProperty("IsScheduled").GetValue(cache));
            }
            finally
            {
                type.GetMethod("Dispose").Invoke(cache, null);
                UnityEngine.Object.DestroyImmediate(live);
                if (dead != null)
                    UnityEngine.Object.DestroyImmediate(dead);
            }
        }
    }
}
