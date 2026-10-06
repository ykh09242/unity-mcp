using System;
using System.Globalization;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

internal static class ToolScalarHarness
{
    private static int cases, failures;
    private static int Main(string[] args)
    {
        Console.WriteLine("RUNTIME: " + typeof(object).Assembly.FullName + "; JSON=" + typeof(JToken).Assembly.FullName);
        foreach (var token in new JToken[] { new JValue(0), new JValue(1), new JValue(0.5), new JValue("0"), new JValue("1"), new JValue("yes"), new JValue("off"), new JValue("bad"), new JObject(), new JArray(true) }) {
            Check("bool rejects " + Describe(token), () => Reject(() => ParamCoercion.CoerceBool(token, true)));
            Check("bool nullable rejects " + Describe(token), () => Reject(() => ParamCoercion.CoerceBoolNullable(token)));
            Check("ToolParams bool rejects " + Describe(token), () => Reject(() => new ToolParams(new JObject { ["delete_children"] = token }).GetBool("deleteChildren", true)));
        }
        foreach (var token in new JToken[] { new JValue(true), new JValue(false), new JValue(0.5), new JValue(-0.5), new JValue("1.5"), new JValue("bad"), new JValue(2147483648L), new JObject(), new JArray(1) }) {
            Check("int rejects " + Describe(token), () => Reject(() => ParamCoercion.CoerceInt(token, 7)));
            Check("int nullable rejects " + Describe(token), () => Reject(() => ParamCoercion.CoerceIntNullable(token)));
            Check("ToolParams int rejects " + Describe(token), () => Reject(() => new ToolParams(new JObject { ["page_size"] = token }).GetInt("pageSize", 7)));
        }
        foreach (var token in new JToken[] { new JValue(true), new JValue(false), new JValue("bad"), new JValue(double.NaN), new JValue(double.PositiveInfinity), new JObject(), new JArray(1) }) {
            Check("float rejects " + Describe(token), () => Reject(() => ParamCoercion.CoerceFloat(token, 7f)));
            Check("float nullable rejects " + Describe(token), () => Reject(() => ParamCoercion.CoerceFloatNullable(token)));
            Check("ToolParams float rejects " + Describe(token), () => Reject(() => new ToolParams(new JObject { ["scale"] = token }).GetFloat("scale", 7f)));
        }
        Check("missing and null optional fields keep defaults", () => {
            foreach (var token in new JToken[] { null, JValue.CreateNull() }) {
                Require(ParamCoercion.CoerceBool(token, true) && ParamCoercion.CoerceBoolNullable(token) == null, "Boolean default changed");
                Require(ParamCoercion.CoerceInt(token, 7) == 7 && ParamCoercion.CoerceIntNullable(token) == null, "Integer default changed");
                Require(ParamCoercion.CoerceFloat(token, 7f) == 7f && ParamCoercion.CoerceFloatNullable(token) == null, "Float default changed");
            }
        });
        Check("canonical booleans stay booleans", () => {
            Require(ParamCoercion.CoerceBool(new JValue(true), false), "true changed");
            Require(!ParamCoercion.CoerceBool(new JValue("FALSE"), true), "false string changed");
        });
        Check("integer and numeric string preserve precision", () => {
            Require(ParamCoercion.CoerceInt(new JValue(42), 0) == 42, "Integer changed");
            Require(ParamCoercion.CoerceLong(new JValue("9223372036854775807"), 0) == long.MaxValue, "Long string changed");
            Require(ParamCoercion.CoerceLong(new JValue(long.MinValue), 0) == long.MinValue, "Long token changed");
        });
        Check("invariant numeric strings preserve aliases", () => {
            var saved = CultureInfo.CurrentCulture;
            try {
                CultureInfo.CurrentCulture = new CultureInfo("tr-TR");
                var p = new ToolParams(new JObject { ["step_size"] = "0.25", ["page_size"] = "42" });
                Require(p.GetFloat("stepSize") == 0.25f && p.GetInt("pageSize") == 42, "Invariant parsing/alias failed");
            } finally { CultureInfo.CurrentCulture = saved; }
        });
        Check("invalid exact value cannot fall back to alias or default", () => {
            var p = new ToolParams(new JObject { ["page_size"] = true, ["pageSize"] = 42 });
            Reject(() => p.GetInt("page_size", 7));
            Require(p.GetInt("pageSize") == 42, "Exact valid alias changed");
        });
        Check("numeric dry-run checks reject NaN Infinity and float overflow", () => {
            foreach (var token in new[] { new JValue(double.NaN), new JValue(double.PositiveInfinity), new JValue(1e100) }) {
                Require(!ParamCoercion.IsNumericToken(token), "Nonfinite/overflow numeric token accepted");
                Require(!ParamCoercion.ValidateNumericField(new JObject { ["time"] = token }, "time", out _), "Invalid numeric field accepted");
            }
        });
        Check("curve tangent Infinity stays valid and NaN is rejected", () => {
            Require(ParamCoercion.ValidateNumericField(new JObject { ["inTangent"] = double.PositiveInfinity }, "inTangent", out _), "Stepped tangent rejected");
            Require(!ParamCoercion.ValidateNumericField(new JObject { ["inTangent"] = double.NaN }, "inTangent", out _), "NaN tangent accepted");
        });
        if (args.Length == 0 || args[0] != "--skip-nunit") RunNUnitFixtures();
        Console.WriteLine("RESULT: " + (cases - failures) + "/" + cases + " passed; failures=" + failures);
        return failures == 0 ? 0 : 1;
    }
    private static void RunNUnitFixtures() {
        foreach (var type in Assembly.GetExecutingAssembly().GetTypes().Where(t => t.Namespace == "MCPForUnityTests.Editor.Helpers" && t.Name.Contains("Tests"))) {
            var fixture = Activator.CreateInstance(type);
            var setups = type.GetMethods().Where(m => m.GetCustomAttributes(typeof(SetUpAttribute), true).Any()).ToArray();
            var teardowns = type.GetMethods().Where(m => m.GetCustomAttributes(typeof(TearDownAttribute), true).Any()).ToArray();
            foreach (var method in type.GetMethods()) {
                var attributes = method.GetCustomAttributes(typeof(TestCaseAttribute), true).Cast<TestCaseAttribute>().ToArray();
                var arguments = attributes.Length > 0 ? attributes.Select(a => a.Arguments).ToArray()
                    : method.GetCustomAttributes(typeof(TestAttribute), true).Any() ? new[] { new object[0] } : new object[0][];
                foreach (var values in arguments) Check(type.Name + "." + method.Name + "(" + string.Join(",", values.Select(v => v?.ToString())) + ")", () => {
                    try { foreach (var setup in setups) setup.Invoke(fixture, null); method.Invoke(fixture, values); }
                    finally { foreach (var teardown in teardowns) teardown.Invoke(fixture, null); }
                });
            }
        }
    }
    private static string Describe(JToken value) => value.Type + " " + value.ToString(Newtonsoft.Json.Formatting.None);
    private static void Require(bool value, string text) { if (!value) throw new Exception(text); }
    private static void Reject(Action action) {
        try { action(); } catch (ArgumentException) { return; }
        throw new Exception("Explicit invalid scalar was accepted or silently defaulted");
    }
    private static void Check(string name, Action test) {
        cases++;
        try { test(); Console.WriteLine("PASS: " + name); }
        catch (Exception error) {
            failures++;
            while (error.InnerException != null) error = error.InnerException;
            Console.WriteLine("FAIL: " + name + " -- " + error.Message.Replace("\r", "").Replace("\n", " "));
        }
    }
}
