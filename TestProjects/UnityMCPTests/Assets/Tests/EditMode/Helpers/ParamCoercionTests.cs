using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ParamCoercionTests
    {
        [Flags]
        private enum SampleFlags : byte
        {
            None = 0,
            First = 1,
            Second = 2,
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(0.0)]
        [TestCase(1.0)]
        [TestCase("0")]
        [TestCase("1")]
        [TestCase("yes")]
        [TestCase("on")]
        [TestCase("no")]
        [TestCase("off")]
        public void BooleanReader_RejectsNumericAndTruthyAliases(object value)
        {
            var token = JToken.FromObject(value);
            Assert.Throws<ArgumentException>(() => token.ReadScalar<bool>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<bool?>());
        }

        [TestCase(true)]
        [TestCase(false)]
        [TestCase(0.0)]
        [TestCase(1.0)]
        [TestCase(0.5)]
        [TestCase(-0.5)]
        [TestCase("1.0")]
        [TestCase("1e2")]
        public void IntegerReaders_RejectBooleanAndFloatingRepresentations(object value)
        {
            var token = JToken.FromObject(value);
            Assert.Throws<ArgumentException>(() => token.ReadScalar<int>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<long>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<byte>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<sbyte>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<short>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<ushort>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<uint>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<ulong>());
        }

        [Test]
        public void IntegerReaders_PreserveBoundariesAndRejectOverflow()
        {
            Assert.AreEqual(sbyte.MinValue, new JValue(-128).ReadScalar<sbyte>());
            Assert.AreEqual(byte.MaxValue, new JValue(255).ReadScalar<byte>());
            Assert.AreEqual(short.MinValue, new JValue(-32768).ReadScalar<short>());
            Assert.AreEqual(ushort.MaxValue, new JValue(65535).ReadScalar<ushort>());
            Assert.AreEqual(int.MinValue, new JValue("-2147483648").ReadScalar<int>());
            Assert.AreEqual(uint.MaxValue, new JValue("4294967295").ReadScalar<uint>());
            Assert.AreEqual(long.MaxValue, new JValue("9223372036854775807").ReadScalar<long>());
            Assert.AreEqual(ulong.MaxValue, JToken.Parse("18446744073709551615").ReadScalar<ulong>());
            Assert.Throws<ArgumentException>(() => new JValue(128).ReadScalar<sbyte>());
            Assert.Throws<ArgumentException>(() => new JValue(-1).ReadScalar<byte>());
            Assert.Throws<ArgumentException>(() => new JValue(32768).ReadScalar<short>());
            Assert.Throws<ArgumentException>(() => new JValue(-1).ReadScalar<ushort>());
            Assert.Throws<ArgumentException>(() => new JValue(2147483648L).ReadScalar<int>());
            Assert.Throws<ArgumentException>(() => new JValue(-1).ReadScalar<uint>());
            Assert.Throws<ArgumentException>(() => JToken.Parse("9223372036854775808").ReadScalar<long>());
            Assert.Throws<ArgumentException>(() => JToken.Parse("18446744073709551616").ReadScalar<ulong>());
            Assert.Throws<ArgumentException>(() => JToken.Parse("9999999999999999999999999999999999999").ReadScalar<long>());
        }

        [Test]
        public void NullableAndKeyedReaders_PreserveMissingNullZeroAndFalse()
        {
            var value = JObject.Parse("{\"enabled\":false,\"count\":0,\"none\":null}");
            Assert.IsFalse(value.ReadScalar<bool>("enabled"));
            Assert.AreEqual(false, value.ReadScalar<bool?>("enabled"));
            Assert.AreEqual(0, value.ReadScalar<int>("count"));
            Assert.AreEqual(0, value.ReadScalar<int?>("count"));
            Assert.IsNull(value.ReadScalar<bool?>("none"));
            Assert.IsNull(value.ReadScalar<int?>("missing"));
            Assert.AreEqual(0, value.ReadScalar<int>("missing"));
            Assert.IsNull(((JToken)null).ReadScalar<double?>());
        }

        [Test]
        public void InvalidNestedValue_ReportsPathWithoutValue()
        {
            var value = JObject.Parse("{\"settings\":{\"enabled\":1}}");
            var error = Assert.Throws<ArgumentException>(() => value["settings"].ReadScalar<bool>("enabled"));
            StringAssert.Contains("settings.enabled", error.Message);
            StringAssert.Contains("Boolean", error.Message);
        }

        [TestCase(true)]
        [TestCase(false)]
        [TestCase("NaN")]
        [TestCase("Infinity")]
        [TestCase("-Infinity")]
        public void FloatingReaders_RejectBooleanAndNonfiniteValues(object value)
        {
            var token = JToken.FromObject(value);
            Assert.Throws<ArgumentException>(() => token.ReadScalar<float>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<double>());
            Assert.Throws<ArgumentException>(() => token.ReadScalar<decimal>());
        }

        [Test]
        public void FloatingReaders_PreserveExistingNumericPrecisionAndInvariantStrings()
        {
            const double value = 1.2345678901234567;
            Assert.AreEqual(value, new JValue(value).ReadScalar<double>());
            Assert.AreEqual(2.5f, new JValue(2.5f).ReadScalar<float>());
            Assert.AreEqual(0.25, new JValue("0.25").ReadScalar<double>());
            Assert.AreEqual(1.2345678901234567890123456789m, new JValue("1.2345678901234567890123456789").ReadScalar<decimal>());
            Assert.Throws<ArgumentException>(() => new JValue(double.MaxValue).ReadScalar<float>());
            Assert.Throws<ArgumentException>(() => new JValue(double.NaN).ReadScalar<double>());
            Assert.Throws<ArgumentException>(() => new JValue(double.PositiveInfinity).ReadScalar<double>());
        }

        [Test]
        public void CanonicalStrings_AreAcceptedWithoutBooleanNumericMapping()
        {
            Assert.IsTrue(new JValue("TRUE").ReadScalar<bool>());
            Assert.IsFalse(new JValue("false").ReadScalar<bool>());
            Assert.AreEqual(0, new JValue("0").ReadScalar<int>());
            Assert.AreEqual(-42, new JValue("-42").ReadScalar<int>());
            Assert.Throws<ArgumentException>(() => new JValue("true").ReadScalar<int>());
        }

        [TestCase(double.NaN)]
        [TestCase(double.PositiveInfinity)]
        [TestCase(double.NegativeInfinity)]
        [TestCase(1e100)]
        public void NumericDryRunValidation_RejectsNonfiniteAndFloatOverflow(double value)
        {
            var token = new JValue(value);
            Assert.IsFalse(ParamCoercion.IsNumericToken(token));
            Assert.IsFalse(ParamCoercion.ValidateNumericField(new JObject { ["time"] = token }, "time", out var error));
            Assert.IsNotNull(error);
        }

        [Test]
        public void CurveTangentValidation_PreservesExplicitInfinityButRejectsInvalidScalars()
        {
            foreach (var name in new[] { "inTangent", "outTangent", "inSlope", "outSlope" })
            {
                Assert.IsTrue(ParamCoercion.ValidateNumericField(new JObject { [name] = double.PositiveInfinity }, name, out _));
                Assert.IsTrue(ParamCoercion.ValidateNumericField(new JObject { [name] = double.NegativeInfinity }, name, out _));
                Assert.IsTrue(ParamCoercion.ValidateNumericField(new JObject { [name] = "Infinity" }, name, out _));
                Assert.IsTrue(ParamCoercion.ValidateNumericField(new JObject { [name] = "-Infinity" }, name, out _));
                Assert.IsFalse(ParamCoercion.ValidateNumericField(new JObject { [name] = double.NaN }, name, out _));
                Assert.IsFalse(ParamCoercion.ValidateNumericField(new JObject { [name] = 1e100 }, name, out _));
                Assert.IsFalse(ParamCoercion.ValidateNumericField(new JObject { [name] = true }, name, out _));
            }
        }

        [TestCase(float.PositiveInfinity, float.PositiveInfinity)]
        [TestCase(float.NegativeInfinity, float.NegativeInfinity)]
        [TestCase(double.PositiveInfinity, float.PositiveInfinity)]
        [TestCase(double.NegativeInfinity, float.NegativeInfinity)]
        [TestCase("Infinity", float.PositiveInfinity)]
        [TestCase("+Infinity", float.PositiveInfinity)]
        [TestCase("-Infinity", float.NegativeInfinity)]
        [TestCase(" Infinity ", float.PositiveInfinity)]
        [TestCase(" -Infinity ", float.NegativeInfinity)]
        [TestCase(0, 0f)]
        [TestCase("-2.5", -2.5f)]
        public void CurveTangentReader_PreservesExplicitInfinityAndFiniteValues(object input, float expected)
        {
            Assert.AreEqual(expected, JToken.FromObject(input).ReadCurveTangent());
        }

        [TestCase(double.NaN)]
        [TestCase("NaN")]
        [TestCase(true)]
        [TestCase(false)]
        [TestCase(1e100)]
        [TestCase("1e100")]
        [TestCase("infinity")]
        [TestCase("-infinity")]
        public void CurveTangentReader_RejectsInvalidScalars(object input)
        {
            Assert.Throws<ArgumentException>(() => JToken.FromObject(input).ReadCurveTangent());
        }

        [Test]
        public void CurveTangentReader_PreservesNullableDefaults()
        {
            Assert.IsNull(((JToken)null).ReadCurveTangent());
            Assert.IsNull(JValue.CreateNull().ReadCurveTangent());
        }

        [Test]
        public void NumericDryRunValidation_PreservesFiniteZeroAndOptionalNull()
        {
            Assert.IsTrue(ParamCoercion.IsNumericToken(new JValue(0)));
            Assert.IsTrue(ParamCoercion.IsNumericToken(new JValue(0.25)));
            Assert.IsTrue(ParamCoercion.ValidateNumericField(new JObject { ["time"] = JValue.CreateNull() }, "time", out _));
            Assert.IsTrue(ParamCoercion.ValidateNumericField(new JObject(), "time", out _));
            Assert.IsFalse(ParamCoercion.IsNumericToken(new JValue(true)));
        }

        [Test]
        public void EnumReaders_PreserveNamesFlagsAndBoundsWithoutBooleanCoercion()
        {
            Assert.AreEqual(SampleFlags.First, ParamCoercion.CoerceEnum(new JValue("First"), SampleFlags.None));
            Assert.AreEqual(SampleFlags.First | SampleFlags.Second, new JValue("First,Second").ReadScalar<SampleFlags>());
            Assert.AreEqual(SampleFlags.None, new JValue(0).ReadScalar<SampleFlags>());
            Assert.AreEqual(SampleFlags.Second, new JValue("2").ReadScalar<SampleFlags>());
            Assert.AreEqual(SampleFlags.Second, ParamCoercion.CoerceEnum(null, SampleFlags.Second));
            Assert.Throws<ArgumentException>(() => ParamCoercion.CoerceEnum(new JValue(true), SampleFlags.None));
            Assert.Throws<ArgumentException>(() => ParamCoercion.CoerceEnum(new JValue(1.0), SampleFlags.None));
            Assert.Throws<ArgumentException>(() => ParamCoercion.CoerceEnum(new JValue("invalid"), SampleFlags.None));
            Assert.Throws<ArgumentException>(() => new JValue(256).ReadScalar<SampleFlags>());
            Assert.Throws<ArgumentException>(() => new JValue("256").ReadScalar<SampleFlags>());
        }
    }
}
