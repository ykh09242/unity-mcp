using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ComponentSuggestionCacheTests
    {
        private Dictionary<string, List<string>> cache;
        private Dictionary<string, List<string>> previous;

        [SetUp]
        public void SetUp()
        {
            cache = (Dictionary<string, List<string>>)ReadField("PropertySuggestionCache");
            previous = new Dictionary<string, List<string>>(cache);
            cache.Clear();
        }

        [TearDown]
        public void TearDown()
        {
            cache.Clear();
            foreach (var entry in previous)
                cache[entry.Key] = entry.Value;
        }

        [Test]
        public void UniqueTyposCannotGrowTheSuggestionCacheWithoutLimit()
        {
            int limit = (int)ReadField("MaxSuggestionCacheEntries");
            var properties = new List<string> { "color", "enabled" };
            for (int i = 0; i < limit * 3; i++)
            {
                ComponentResolver.GetFuzzyPropertySuggestions("ownedMissing" + i, properties);
                Assert.LessOrEqual(cache.Count, limit);
            }
        }

        [Test]
        public void OversizedKeysKeepSuggestionsAvailableWithoutRetention()
        {
            int limit = (int)ReadField("MaxSuggestionCacheKeyCharacters");
            var properties = new List<string> { "color" };
            var result = ComponentResolver.GetFuzzyPropertySuggestions(new string('_', limit + 1), properties);
            CollectionAssert.AreEqual(properties, result);
            Assert.AreEqual(0, cache.Count);
        }

        [Test]
        public void CallersCannotMutateCachedSuggestions()
        {
            var properties = new List<string> { "color", "enabled" };
            var first = ComponentResolver.GetFuzzyPropertySuggestions("colur", properties);
            CollectionAssert.AreEqual(new[] { "color" }, first);
            first.Clear();
            first.Add("ownedPoison");
            var second = ComponentResolver.GetFuzzyPropertySuggestions("colur", properties);
            CollectionAssert.AreEqual(new[] { "color" }, second);
            second.Clear();
            var third = ComponentResolver.GetFuzzyPropertySuggestions("colur", properties);
            CollectionAssert.AreEqual(new[] { "color" }, third);
        }

        private static object ReadField(string name) => typeof(ComponentResolver).GetField(name, BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
    }
}
