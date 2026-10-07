using System;
using System.Collections.Generic;
using System.Numerics;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class ComponentSerializationIntegrityTests
    {
        private static object ConvertToken(JToken token)
        {
            var method = typeof(GameObjectSerializer).GetMethod("ConvertJTokenToPlainObject", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            return method.Invoke(null, new object[] { token });
        }

        [Test]
        public void LargeUnsignedIntegerRemainsExact()
        {
            var value = ConvertToken(new JValue(ulong.MaxValue));
            Assert.AreEqual(ulong.MaxValue, value);
            Assert.AreEqual("18446744073709551615", JsonConvert.SerializeObject(value));
        }

        [Test]
        public void BigIntegerRemainsExact()
        {
            var expected = BigInteger.Parse("184467440737095516160000");
            var value = ConvertToken(new JValue(expected));
            Assert.AreEqual(expected, value);
            Assert.AreEqual("184467440737095516160000", JsonConvert.SerializeObject(value));
        }

        [Test]
        public void DecimalRemainsExact()
        {
            const decimal expected = 1234567890.1234567890123456789m;
            var value = ConvertToken(new JValue(expected));
            Assert.AreEqual(expected, value);
            Assert.AreEqual("1234567890.1234567890123456789", JsonConvert.SerializeObject(value));
        }

        [Test]
        public void NestedNumbersDoNotDiscardOtherValues()
        {
            var token = new JObject { ["values"] = new JArray(false, ulong.MaxValue, new JValue(1234567890.1234567890123456789m), null), ["zero"] = 0 };
            var result = ConvertToken(token);
            Assert.IsInstanceOf<Dictionary<string, object>>(result);
            Assert.AreEqual(token.ToString(Formatting.None), JsonConvert.SerializeObject(result));
        }

        [TestCase(-9223372036854775808L)]
        [TestCase(0L)]
        [TestCase(9223372036854775807L)]
        public void SignedIntegerRemainsExact(long expected)
        {
            Assert.AreEqual(expected, ConvertToken(new JValue(expected)));
        }

        [Test]
        public void SingleUsesOriginalNumericRepresentation()
        {
            var value = ConvertToken(JToken.FromObject(0.1f));
            Assert.AreEqual("0.1", JsonConvert.SerializeObject(value));
        }

        [Test]
        public void DoubleRemainsUnchanged()
        {
            Assert.AreEqual(-1.25d, ConvertToken(new JValue(-1.25d)));
        }

        [Test]
        public void FalseRemainsFalse()
        {
            Assert.AreEqual(false, ConvertToken(new JValue(false)));
        }

        [Test]
        public void NullRemainsNull()
        {
            Assert.IsNull(ConvertToken(JValue.CreateNull()));
        }

        [Test]
        public void EmptyArrayRemainsEmptyList()
        {
            Assert.IsEmpty((List<object>)ConvertToken(new JArray()));
        }
    }
}
