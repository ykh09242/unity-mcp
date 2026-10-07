using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Graphics;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    // Pure managed reflection checks: no Unity objects, assets, scenes or editor state.
    public class VolumeQueryMetadataTests
    {
        [Test]
        public void SharedProfileSummaryReadsOverridesOncePerCache()
        {
            var parameter = new Parameter { State = true };
            var profile = ProfileWith(parameter);
            var cache = NewCache();
            var first = Summarize(profile, cache);
            var second = Summarize(profile, cache);
            Assert.AreEqual(1, parameter.Reads);
            Assert.AreSame(first, second);
            Assert.AreEqual("intensity", JArray.FromObject(first)[0]["overridden_params"][0].Value<string>());
        }

        [Test]
        public void DistinctEqualProfilesKeepSeparateSummaries()
        {
            var firstParameter = new Parameter { State = true };
            var secondParameter = new Parameter { State = false };
            var first = ProfileWith(firstParameter);
            var second = ProfileWith(secondParameter);
            Assert.IsTrue(first.Equals(second), "The fixture deliberately has value equality.");
            var cache = NewCache();
            Assert.AreEqual(1, JArray.FromObject(Summarize(first, cache))[0]["overridden_params"].Count());
            Assert.AreEqual(0, JArray.FromObject(Summarize(second, cache))[0]["overridden_params"].Count());
            Assert.AreEqual(1, firstParameter.Reads);
            Assert.AreEqual(1, secondParameter.Reads);
            Assert.AreEqual(2, cache.Count);
        }

        [Test]
        public void FreshRequestAndUncachedSummarySeeChangedOverrides()
        {
            var parameter = new Parameter { State = true };
            var profile = ProfileWith(parameter);
            Assert.AreEqual(1, JArray.FromObject(Summarize(profile, NewCache()))[0]["overridden_params"].Count());
            parameter.State = false;
            Assert.AreEqual(0, JArray.FromObject(Summarize(profile, NewCache()))[0]["overridden_params"].Count());
            parameter.State = true;
            Assert.AreEqual(1, JArray.FromObject(Summarize(profile, null))[0]["overridden_params"].Count());
            Assert.AreEqual(3, parameter.Reads);
        }

        [Test]
        public void NullEffectsAreSkippedAndInactiveEffectsKeepTheirOverridesAndOrder()
        {
            var profile = new Profile
            {
                components = new ArrayList
                {
                    null,
                    new Effect
                    {
                        active = false,
                        intensity = new Parameter { State = true },
                    },
                    new OtherEffect
                    {
                        active = true,
                        threshold = new Parameter { State = false },
                    },
                },
            };
            var effects = JArray.FromObject(Summarize(profile, NewCache()));
            Assert.AreEqual(2, effects.Count);
            Assert.AreEqual("Effect", effects[0].Value<string>("type"));
            Assert.IsFalse(effects[0].Value<bool>("active"));
            Assert.AreEqual("intensity", effects[0]["overridden_params"][0].Value<string>());
            Assert.AreEqual("OtherEffect", effects[1].Value<string>("type"));
            Assert.IsTrue(effects[1].Value<bool>("active"));
            Assert.AreEqual(0, effects[1]["overridden_params"].Count());
        }

        [Test]
        public void NullMissingAndEmptyComponentsRemainEmptyArrays()
        {
            var cache = NewCache();
            Assert.AreEqual(0, Summarize(null, cache).Count);
            Assert.AreEqual(0, cache.Count, "Null profiles cannot be dictionary keys.");
            Assert.AreEqual(0, Summarize(new Profile { components = null }, cache).Count);
            Assert.AreEqual(0, Summarize(new Profile(), cache).Count);
            Assert.AreEqual(0, Summarize(new object(), cache).Count);
        }

        private static Profile ProfileWith(Parameter parameter) => new Profile { components = new ArrayList { new Effect { intensity = parameter } } };

        private static Dictionary<object, List<object>> NewCache()
        {
            var comparerType = typeof(VolumeOps).GetNestedType("ProfileReferenceComparer", BindingFlags.NonPublic);
            Assert.IsNotNull(comparerType);
            var comparer = (IEqualityComparer<object>)comparerType.GetField("Instance", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            return new Dictionary<object, List<object>>(comparer);
        }

        private static List<object> Summarize(object profile, Dictionary<object, List<object>> cache)
        {
            var method = typeof(VolumeOps).GetMethod("BuildProfileEffects", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            return (List<object>)method.Invoke(null, new object[] { profile, cache });
        }

        public sealed class Profile
        {
            public IList components = new ArrayList();

            public override bool Equals(object obj) => obj is Profile;

            public override int GetHashCode() => 0;
        }

        public sealed class Effect
        {
            public bool active = true;
            public Parameter intensity;
        }

        public sealed class OtherEffect
        {
            public bool active = true;
            public Parameter threshold;
        }

        public sealed class Parameter
        {
            public bool State;
            public int Reads;
            public bool overrideState
            {
                get
                {
                    Reads++;
                    return State;
                }
            }
        }
    }
}
