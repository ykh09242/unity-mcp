using System.Globalization;
using System.Threading;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnityTests.Editor.Helpers
{
    /// <summary>
    /// Numeric parameters that arrive as JSON strings must parse the same way
    /// on every machine, whatever the current culture is.
    /// </summary>
    public class ToolParamsCultureTests
    {
        private CultureInfo _saved;

        [SetUp]
        public void UseDecimalCommaCulture()
        {
            _saved = Thread.CurrentThread.CurrentCulture;
            // tr-TR uses ',' as the decimal separator and '.' for thousands.
            Thread.CurrentThread.CurrentCulture = new CultureInfo("tr-TR");
        }

        [TearDown]
        public void RestoreCulture()
        {
            Thread.CurrentThread.CurrentCulture = _saved;
        }

        [Test]
        public void GetFloat_StringWithDecimalPoint_IsNotReadAsThousands()
        {
            var p = new ToolParams(new JObject { ["target_size"] = "1.5" });
            Assert.AreEqual(1.5f, p.GetFloat("target_size"));
        }

        [Test]
        public void GetFloat_StringBelowOne_KeepsItsValue()
        {
            var p = new ToolParams(new JObject { ["spacing"] = "0.25" });
            Assert.AreEqual(0.25f, p.GetFloat("spacing"));
        }

        [Test]
        public void GetFloat_JsonNumber_Unchanged()
        {
            var p = new ToolParams(new JObject { ["duration"] = 2.5 });
            Assert.AreEqual(2.5f, p.GetFloat("duration"));
        }

        [Test]
        public void GetInt_StringWithThousandsSeparator_IsRejectedNotGuessed()
        {
            var p = new ToolParams(new JObject { ["count"] = "1.000" });
            Assert.IsNull(p.GetInt("count"));
        }

        [Test]
        public void GetInt_JsonIntegerOutOfRange_ReturnsDefault()
        {
            var p = new ToolParams(new JObject { ["count"] = 2147483648L });
            Assert.AreEqual(7, p.GetInt("count", 7));
        }

        [Test]
        public void GetInt_PlainString_Unchanged()
        {
            var p = new ToolParams(new JObject { ["count"] = "42" });
            Assert.AreEqual(42, p.GetInt("count"));
        }

        [TestCase("-42", -42)]
        [TestCase("-2147483648", int.MinValue)]
        [TestCase("2147483647", int.MaxValue)]
        [TestCase("-42.0", -42)]
        [TestCase("0", 0)]
        public void GetInt_JsonNumber_WithCustomNegativeSign_PreservesValue(string json, int expected)
        {
            var culture = (CultureInfo)CultureInfo.InvariantCulture.Clone();
            culture.NumberFormat.NegativeSign = "~";
            Thread.CurrentThread.CurrentCulture = culture;
            var p = new ToolParams(new JObject { ["page_size"] = JToken.Parse(json) });

            Assert.AreEqual(expected, p.GetInt("pageSize", 7));
        }

        [TestCase("-2147483649")]
        [TestCase("2147483648")]
        [TestCase("18446744073709551616")]
        [TestCase("-42.5")]
        public void GetInt_UnrepresentableJsonNumber_PreservesDefault(string json)
        {
            var p = new ToolParams(new JObject { ["count"] = JToken.Parse(json) });
            Assert.AreEqual(7, p.GetInt("count", 7));
            Assert.IsNull(p.GetInt("count"));
        }

        [TestCase("-42", -42)]
        [TestCase("~42", 7)]
        [TestCase("1.000", 7)]
        [TestCase("4.2e1", 7)]
        public void GetInt_Strings_UseProtocolNotationNotCustomCulture(string value, int expected)
        {
            var culture = (CultureInfo)CultureInfo.InvariantCulture.Clone();
            culture.NumberFormat.NegativeSign = "~";
            Thread.CurrentThread.CurrentCulture = culture;
            var p = new ToolParams(new JObject { ["count"] = value });

            Assert.AreEqual(expected, p.GetInt("count", 7));
        }

        [Test]
        public void GetInt_InvalidExactValue_DoesNotFallBackToAlias()
        {
            foreach (var token in new JToken[] { JValue.CreateNull(), new JValue(""),
                new JValue("invalid"), new JValue(true), new JObject(), new JArray(42) })
            {
                var p = new ToolParams(new JObject { ["page_size"] = token, ["pageSize"] = 42 });
                Assert.AreEqual(7, p.GetInt("page_size", 7), token.Type.ToString());
                Assert.AreEqual(42, p.GetInt("pageSize"));
            }
        }
    }
}
