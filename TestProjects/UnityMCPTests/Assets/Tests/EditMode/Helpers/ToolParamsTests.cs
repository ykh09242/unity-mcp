using NUnit.Framework;
using Newtonsoft.Json.Linq;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnityTests.Editor.Helpers
{
    /// <summary>
    /// Tests for the ToolParams parameter validation wrapper.
    /// </summary>
    public class ToolParamsTests
    {
        #region Constructor Tests

        [Test]
        public void ToolParams_Constructor_ThrowsOnNullParams()
        {
            Assert.Throws<System.ArgumentNullException>(() => new ToolParams(null));
        }

        [Test]
        public void ToolParams_Constructor_AcceptsEmptyJObject()
        {
            Assert.DoesNotThrow(() => new ToolParams(new JObject()));
        }

        #endregion

        #region GetRequired Tests

        [Test]
        public void GetRequired_ExistingParameter_ReturnsSuccess()
        {
            var json = new JObject { ["action"] = "create" };
            var p = new ToolParams(json);

            var result = p.GetRequired("action");

            Assert.IsTrue(result.IsSuccess);
            Assert.AreEqual("create", result.Value);
            Assert.IsNull(result.ErrorMessage);
        }

        [Test]
        public void GetRequired_MissingParameter_ReturnsError()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            var result = p.GetRequired("action");

            Assert.IsFalse(result.IsSuccess);
            Assert.IsNull(result.Value);
            Assert.That(result.ErrorMessage, Does.Contain("'action' parameter is required"));
        }

        [Test]
        public void GetRequired_EmptyStringParameter_ReturnsError()
        {
            var json = new JObject { ["action"] = "" };
            var p = new ToolParams(json);

            var result = p.GetRequired("action");

            Assert.IsFalse(result.IsSuccess);
            Assert.That(result.ErrorMessage, Does.Contain("'action' parameter is required"));
        }

        [Test]
        public void GetRequired_CustomErrorMessage_ReturnsCustomError()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            var result = p.GetRequired("action", "Custom error message");

            Assert.IsFalse(result.IsSuccess);
            Assert.AreEqual("Custom error message", result.ErrorMessage);
        }

        #endregion

        #region Get Tests

        [Test]
        public void Get_ExistingParameter_ReturnsValue()
        {
            var json = new JObject { ["name"] = "TestObject" };
            var p = new ToolParams(json);

            var value = p.Get("name");

            Assert.AreEqual("TestObject", value);
        }

        [Test]
        public void Get_MissingParameter_ReturnsNull()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            var value = p.Get("name");

            Assert.IsNull(value);
        }

        [Test]
        public void Get_MissingParameterWithDefault_ReturnsDefault()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            var value = p.Get("name", "DefaultName");

            Assert.AreEqual("DefaultName", value);
        }

        #endregion

        #region Snake/Camel Case Fallback Tests

        [Test]
        public void Get_SnakeCaseParameter_FindsWithCamelCaseKey()
        {
            var json = new JObject { ["search_method"] = "by_name" };
            var p = new ToolParams(json);

            // Asking for camelCase should find snake_case
            var value = p.Get("searchMethod");

            Assert.AreEqual("by_name", value);
        }

        [Test]
        public void Get_CamelCaseParameter_FindsWithSnakeCaseKey()
        {
            var json = new JObject { ["searchMethod"] = "by_name" };
            var p = new ToolParams(json);

            // Asking for snake_case should find camelCase
            var value = p.Get("search_method");

            Assert.AreEqual("by_name", value);
        }

        [Test]
        public void Get_ExactMatchTakesPrecedence()
        {
            // If both snake_case and camelCase exist, exact match wins
            var json = new JObject
            {
                ["search_method"] = "snake",
                ["searchMethod"] = "camel"
            };
            var p = new ToolParams(json);

            Assert.AreEqual("snake", p.Get("search_method"));
            Assert.AreEqual("camel", p.Get("searchMethod"));
        }

        #endregion

        #region GetInt Tests

        [Test]
        public void GetInt_ValidInteger_ReturnsValue()
        {
            var json = new JObject { ["count"] = "10" };
            var p = new ToolParams(json);

            var value = p.GetInt("count");

            Assert.AreEqual(10, value);
        }

        [Test]
        public void GetInt_MissingParameter_ReturnsNull()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            var value = p.GetInt("count");

            Assert.IsNull(value);
        }

        [Test]
        public void GetInt_MissingParameterWithDefault_ReturnsDefault()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            var value = p.GetInt("count", 5);

            Assert.AreEqual(5, value);
        }

        [Test]
        public void GetInt_InvalidInteger_ReturnsDefault()
        {
            var json = new JObject { ["count"] = "not_a_number" };
            var p = new ToolParams(json);

            var value = p.GetInt("count", 5);

            Assert.AreEqual(5, value);
        }

        #endregion

        #region GetFloat Tests

        [TestCase("en-US", false)]
        [TestCase("de-DE", false)]
        [TestCase("fr-FR", false)]
        [TestCase("en-US", true)]
        [TestCase("de-DE", true)]
        [TestCase("fr-FR", true)]
        public void GetFloat_ProtocolDecimal_IsIndependentOfEditorCulture(string culture, bool numericToken)
        {
            var originalCulture = System.Globalization.CultureInfo.CurrentCulture;
            try
            {
                System.Globalization.CultureInfo.CurrentCulture = new System.Globalization.CultureInfo(culture);
                var json = numericToken
                    ? JObject.Parse("{\"step_size\":2.5}")
                    : JObject.Parse("{\"step_size\":\"2.5\"}");

                Assert.AreEqual(2.5f, new ToolParams(json).GetFloat("stepSize", 7f));
            }
            finally
            {
                System.Globalization.CultureInfo.CurrentCulture = originalCulture;
            }
        }

        [Test]
        public void GetFloat_InvalidValues_PreserveDefaultAndExactAliasPrecedence()
        {
            foreach (var token in new JToken[] { JValue.CreateNull(), new JValue(""),
                new JValue("invalid"), new JValue(true), new JObject(), new JArray(2.5) })
            {
                var p = new ToolParams(new JObject { ["step_size"] = token, ["stepSize"] = 2.5 });
                Assert.AreEqual(7f, p.GetFloat("step_size", 7f), token.Type.ToString());
                Assert.IsNull(p.GetFloat("step_size"), token.Type.ToString());
                Assert.AreEqual(2.5f, p.GetFloat("stepSize"));
            }
        }

        [Test]
        public void GetFloat_ValidFloat_ReturnsValue()
        {
            var json = new JObject { ["scale"] = "2.5" };
            var p = new ToolParams(json);

            var value = p.GetFloat("scale");

            Assert.AreEqual(2.5f, value);
        }

        [Test]
        public void GetFloat_MissingParameter_ReturnsNull()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            var value = p.GetFloat("scale");

            Assert.IsNull(value);
        }

        [Test]
        public void GetFloat_MissingParameterWithDefault_ReturnsDefault()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            var value = p.GetFloat("scale", 1.0f);

            Assert.AreEqual(1.0f, value);
        }

        #endregion

        #region ParamCoercion Integer Range Tests

        [TestCase("2147483648")]
        [TestCase("-2147483649")]
        [TestCase("1e100")]
        [TestCase("-1e100")]
        [TestCase("NaN")]
        [TestCase("Infinity")]
        [TestCase("-Infinity")]
        public void CoerceInt_OutOfRangeOrNonfiniteString_PreservesDefault(string value)
        {
            Assert.AreEqual(17, ParamCoercion.CoerceInt(new JValue(value), 17));
        }

        [TestCase("2147483648")]
        [TestCase("-2147483649")]
        [TestCase("1e100")]
        [TestCase("-1e100")]
        [TestCase("NaN")]
        [TestCase("Infinity")]
        [TestCase("-Infinity")]
        public void CoerceIntNullable_OutOfRangeOrNonfiniteString_ReturnsNull(string value)
        {
            Assert.IsNull(ParamCoercion.CoerceIntNullable(new JValue(value)));
        }

        [TestCase("9223372036854775808")]
        [TestCase("-9223372036854775809")]
        [TestCase("9.223372036854775808e18")]
        [TestCase("-9.223372036854775809e18")]
        [TestCase("1e100")]
        [TestCase("-1e100")]
        [TestCase("NaN")]
        [TestCase("Infinity")]
        [TestCase("-Infinity")]
        public void CoerceLong_OutOfRangeOrNonfiniteString_PreservesDefault(string value)
        {
            Assert.AreEqual(17L, ParamCoercion.CoerceLong(new JValue(value), 17L));
        }

        [TestCase(1e100)]
        [TestCase(-1e100)]
        [TestCase(double.NaN)]
        [TestCase(double.PositiveInfinity)]
        [TestCase(double.NegativeInfinity)]
        public void IntegerCoercion_OutOfRangeOrNonfiniteFloatToken_PreservesDefault(double value)
        {
            var token = new JValue(value);
            Assert.AreEqual(17, ParamCoercion.CoerceInt(token, 17));
            Assert.IsNull(ParamCoercion.CoerceIntNullable(token));
            Assert.AreEqual(17L, ParamCoercion.CoerceLong(token, 17L));
        }

        [TestCase("1.9", 1)]
        [TestCase("-1.9", -1)]
        [TestCase("1e2", 100)]
        [TestCase("2147483647", int.MaxValue)]
        [TestCase("-2147483648", int.MinValue)]
        [TestCase("2147483647.9", int.MaxValue)]
        [TestCase("-2147483648.9", int.MinValue)]
        public void IntegerCoercion_RepresentableStrings_PreserveTruncationAndBounds(string value, int expected)
        {
            var token = new JValue(value);
            Assert.AreEqual(expected, ParamCoercion.CoerceInt(token, 17));
            Assert.AreEqual(expected, ParamCoercion.CoerceIntNullable(token));
            Assert.AreEqual((long)expected, ParamCoercion.CoerceLong(token, 17L));
        }

        [Test]
        public void IntegerCoercion_IntegerTokensAndLongBounds_PreserveDefaultsAndPrecision()
        {
            var oversizedInt = JToken.Parse("2147483648");
            Assert.AreEqual(17, ParamCoercion.CoerceInt(oversizedInt, 17));
            Assert.IsNull(ParamCoercion.CoerceIntNullable(oversizedInt));
            Assert.AreEqual(2147483648L, ParamCoercion.CoerceLong(oversizedInt, 17L));
            var oversizedLong = JToken.Parse("9223372036854775808");
            Assert.AreEqual(17L, ParamCoercion.CoerceLong(oversizedLong, 17L));
            Assert.AreEqual(long.MaxValue, ParamCoercion.CoerceLong(new JValue("9223372036854775807"), 17L));
            Assert.AreEqual(long.MinValue, ParamCoercion.CoerceLong(new JValue("-9223372036854775808"), 17L));
        }

        [TestCase("1.9", 1L)]
        [TestCase("-1.9", -1L)]
        [TestCase("1e-100", 0L)]
        [TestCase("9.223372036854775807e18", long.MaxValue)]
        [TestCase("-9.223372036854775808e18", long.MinValue)]
        [TestCase("9223372036854775807.9", long.MaxValue)]
        [TestCase("-9223372036854775808.9", long.MinValue)]
        public void CoerceLong_RepresentableFractionsAndScientificBounds_PreserveTruncation(string value, long expected)
        {
            Assert.AreEqual(expected, ParamCoercion.CoerceLong(new JValue(value), 17L));
        }

        [Test]
        public void BoolCoercion_ExistingScalarAndDefaultPolicy_IsPreserved()
        {
            foreach (var token in new JToken[] { new JValue(true), new JValue("true"), new JValue("yes"),
                new JValue("on"), new JValue(1) })
            {
                Assert.IsTrue(ParamCoercion.CoerceBool(token, false));
                Assert.AreEqual(true, ParamCoercion.CoerceBoolNullable(token));
            }
            foreach (var token in new JToken[] { new JValue(false), new JValue("false"), new JValue("no"),
                new JValue("off"), new JValue(0) })
            {
                Assert.IsFalse(ParamCoercion.CoerceBool(token, true));
                Assert.AreEqual(false, ParamCoercion.CoerceBoolNullable(token));
            }
            foreach (var token in new JToken[] { null, JValue.CreateNull(), new JValue("invalid"),
                new JValue(2), new JObject(), new JArray(true) })
            {
                Assert.IsFalse(ParamCoercion.CoerceBool(token, false));
                Assert.IsTrue(ParamCoercion.CoerceBool(token, true));
                Assert.IsNull(ParamCoercion.CoerceBoolNullable(token));
            }
        }

        [Test]
        public void FloatCoercion_ExistingNonfinitePolicy_IsPreserved()
        {
            var originalCulture = System.Globalization.CultureInfo.CurrentCulture;
            try
            {
                System.Globalization.CultureInfo.CurrentCulture = System.Globalization.CultureInfo.InvariantCulture;
                foreach (var token in new JToken[] { new JValue(double.NaN), new JValue("NaN") })
                {
                    Assert.IsTrue(float.IsNaN(ParamCoercion.CoerceFloat(token, 7f)));
                    Assert.IsTrue(float.IsNaN(ParamCoercion.CoerceFloatNullable(token).Value));
                    Assert.IsTrue(float.IsNaN(new ToolParams(new JObject { ["value"] = token }).GetFloat("value", 7f).Value));
                }
                foreach (var token in new JToken[] { new JValue(double.PositiveInfinity), new JValue("Infinity") })
                {
                    Assert.AreEqual(float.PositiveInfinity, ParamCoercion.CoerceFloat(token, 7f));
                    Assert.AreEqual(float.PositiveInfinity, ParamCoercion.CoerceFloatNullable(token));
                    Assert.AreEqual(float.PositiveInfinity, new ToolParams(new JObject { ["value"] = token }).GetFloat("value", 7f));
                }
            }
            finally
            {
                System.Globalization.CultureInfo.CurrentCulture = originalCulture;
            }
        }

        [Test]
        public void IntegerCoercion_InvalidContainersAndNull_PreserveDefaults()
        {
            foreach (var token in new JToken[] { null, JValue.CreateNull(), new JValue(""),
                new JValue("invalid"), new JValue(true), new JObject(), new JArray(1) })
            {
                Assert.AreEqual(17, ParamCoercion.CoerceInt(token, 17));
                Assert.IsNull(ParamCoercion.CoerceIntNullable(token));
                Assert.AreEqual(17L, ParamCoercion.CoerceLong(token, 17L));
            }
        }

        #endregion

        #region GetBool Tests

        [Test]
        public void GetBool_TrueBoolean_ReturnsTrue()
        {
            var json = new JObject { ["enabled"] = true };
            var p = new ToolParams(json);

            var value = p.GetBool("enabled");

            Assert.IsTrue(value);
        }

        [Test]
        public void GetBool_FalseBoolean_ReturnsFalse()
        {
            var json = new JObject { ["enabled"] = false };
            var p = new ToolParams(json);

            var value = p.GetBool("enabled");

            Assert.IsFalse(value);
        }

        [Test]
        public void GetBool_MissingParameter_ReturnsDefault()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            var value = p.GetBool("enabled", true);

            Assert.IsTrue(value);
        }

        [Test]
        public void GetBool_StringTrue_ReturnsTrue()
        {
            var json = new JObject { ["enabled"] = "true" };
            var p = new ToolParams(json);

            var value = p.GetBool("enabled");

            Assert.IsTrue(value);
        }

        [Test]
        public void GetBool_SnakeCaseParameter_FindsWithCamelCaseKey()
        {
            var json = new JObject { ["include_inactive"] = true };
            var p = new ToolParams(json);

            // Asking for camelCase should find snake_case
            var value = p.GetBool("includeInactive");

            Assert.IsTrue(value);
        }

        [Test]
        public void GetBool_CamelCaseParameter_FindsWithSnakeCaseKey()
        {
            var json = new JObject { ["includeInactive"] = true };
            var p = new ToolParams(json);

            // Asking for snake_case should find camelCase
            var value = p.GetBool("include_inactive");

            Assert.IsTrue(value);
        }

        #endregion

        #region Has Tests

        [Test]
        public void Has_ExistingParameter_ReturnsTrue()
        {
            var json = new JObject { ["key"] = "value" };
            var p = new ToolParams(json);

            Assert.IsTrue(p.Has("key"));
        }

        [Test]
        public void Has_MissingParameter_ReturnsFalse()
        {
            var json = new JObject();
            var p = new ToolParams(json);

            Assert.IsFalse(p.Has("key"));
        }

        [Test]
        public void Has_SnakeCaseParameter_FindsWithCamelCaseKey()
        {
            var json = new JObject { ["search_term"] = "Player" };
            var p = new ToolParams(json);

            // Asking for camelCase should find snake_case
            Assert.IsTrue(p.Has("searchTerm"));
        }

        [Test]
        public void Has_CamelCaseParameter_FindsWithSnakeCaseKey()
        {
            var json = new JObject { ["searchTerm"] = "Player" };
            var p = new ToolParams(json);

            // Asking for snake_case should find camelCase
            Assert.IsTrue(p.Has("search_term"));
        }

        #endregion

        #region GetRaw Tests

        [Test]
        public void GetRaw_ComplexObject_ReturnsJToken()
        {
            var json = new JObject { ["data"] = new JObject { ["nested"] = "value" } };
            var p = new ToolParams(json);

            var raw = p.GetRaw("data");

            Assert.IsNotNull(raw);
            Assert.IsInstanceOf<JObject>(raw);
            Assert.AreEqual("value", raw["nested"]?.ToString());
        }

        [Test]
        public void GetRaw_Array_ReturnsJToken()
        {
            var json = new JObject { ["items"] = new JArray { "a", "b", "c" } };
            var p = new ToolParams(json);

            var raw = p.GetRaw("items");

            Assert.IsNotNull(raw);
            Assert.IsInstanceOf<JArray>(raw);
            Assert.AreEqual(3, ((JArray)raw).Count);
        }

        [Test]
        public void GetRaw_SnakeCaseParameter_FindsWithCamelCaseKey()
        {
            var json = new JObject { ["component_properties"] = new JObject { ["mass"] = 1.5 } };
            var p = new ToolParams(json);

            // Asking for camelCase should find snake_case
            var raw = p.GetRaw("componentProperties");

            Assert.IsNotNull(raw);
            Assert.IsInstanceOf<JObject>(raw);
            Assert.AreEqual(1.5, raw["mass"]?.Value<double>());
        }

        [Test]
        public void GetRaw_CamelCaseParameter_FindsWithSnakeCaseKey()
        {
            var json = new JObject { ["componentProperties"] = new JObject { ["mass"] = 1.5 } };
            var p = new ToolParams(json);

            // Asking for snake_case should find camelCase
            var raw = p.GetRaw("component_properties");

            Assert.IsNotNull(raw);
            Assert.IsInstanceOf<JObject>(raw);
            Assert.AreEqual(1.5, raw["mass"]?.Value<double>());
        }

        #endregion

        #region Result<T> Tests

        [Test]
        public void Result_Success_IsSuccessTrue()
        {
            var result = Result<string>.Success("value");

            Assert.IsTrue(result.IsSuccess);
            Assert.AreEqual("value", result.Value);
            Assert.IsNull(result.ErrorMessage);
        }

        [Test]
        public void Result_Error_IsSuccessFalse()
        {
            var result = Result<string>.Error("error message");

            Assert.IsFalse(result.IsSuccess);
            Assert.IsNull(result.Value);
            Assert.AreEqual("error message", result.ErrorMessage);
        }

        [Test]
        public void Result_GetOrError_Success_ReturnsNull()
        {
            var result = Result<string>.Success("value");

            var error = result.GetOrError(out var value);

            Assert.IsNull(error);
            Assert.AreEqual("value", value);
        }

        [Test]
        public void Result_GetOrError_Error_ReturnsErrorResponse()
        {
            var result = Result<string>.Error("error message");

            var error = result.GetOrError(out var value);

            Assert.IsNotNull(error);
            Assert.IsInstanceOf<ErrorResponse>(error);
            Assert.IsNull(value);

            var errorResponse = error as ErrorResponse;
            Assert.AreEqual("error message", errorResponse.error);
        }

        #endregion
    }
}
