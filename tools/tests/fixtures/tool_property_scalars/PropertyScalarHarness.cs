using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

public sealed class ScalarDto<T>
{
    public T Scalar;
    public T[] Values;
    public Dictionary<string, T> Map;
}

internal static class PropertyScalarHarness
{
    private static int cases,
        failures;

    private static void Check(string name, Action action)
    {
        cases++;
        try
        {
            action();
        }
        catch (Exception error)
        {
            failures++;
            Console.WriteLine("FAIL " + name + ": " + error);
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new Exception(message);
    }

    private static void Reject(Action action)
    {
        try
        {
            action();
        }
        catch (JsonException)
        {
            return;
        }
        catch (ArgumentException)
        {
            return;
        }
        throw new Exception("Invalid value was accepted");
    }

    private static object Convert(string json, Type type) => PropertyConversion.ConvertToType(JToken.Parse(json), type);

    private static int Main()
    {
        Console.WriteLine(
            "ACTUAL_PRODUCTION: PropertyConversion, UnityJsonSerializer, JsonScalarConversion, UnityTypeConverters, ParamCoercion, VectorParsing, TextureOps, RendererHelpers"
        );
        Console.WriteLine("RUNTIME: " + typeof(Vector3).Assembly.FullName);
        Type[] numeric =
        {
            typeof(byte),
            typeof(sbyte),
            typeof(short),
            typeof(ushort),
            typeof(int),
            typeof(uint),
            typeof(long),
            typeof(ulong),
            typeof(float),
            typeof(double),
            typeof(decimal),
        };
        foreach (Type type in numeric)
        {
            foreach (string json in new[] { "true", "false", "\"true\"", "\"false\"", "{}", "[]" })
            {
                string input = json;
                Check(type.Name + " scalar rejects " + input, () => Reject(() => Convert(input, type)));
                Check(type.Name + " array rejects " + input, () => Reject(() => Convert("[" + input + "]", type.MakeArrayType())));
                Check(type.Name + " list rejects " + input, () => Reject(() => Convert("[" + input + "]", typeof(List<>).MakeGenericType(type))));
                Check(
                    type.Name + " dictionary rejects " + input,
                    () => Reject(() => Convert("{\"bad\":" + input + "}", typeof(Dictionary<,>).MakeGenericType(typeof(string), type)))
                );
                foreach (string member in new[] { "Scalar", "Values", "Map" })
                {
                    string nested =
                        member == "Values" ? "[" + input + "]"
                        : member == "Map" ? "{\"bad\":" + input + "}"
                        : input;
                    Check(
                        type.Name + " DTO " + member + " rejects " + input,
                        () => Reject(() => Convert("{\"" + member + "\":" + nested + "}", typeof(ScalarDto<>).MakeGenericType(type)))
                    );
                }
            }
            Check(type.Name + " valid zero", () => Require(System.Convert.ToDecimal(Convert("0", type)) == 0m, "Zero changed"));
            Check(type.Name + " valid string", () => Require(System.Convert.ToDecimal(Convert("\"2\"", type)) == 2m, "String changed"));
            Check(type.Name + " nullable null", () => Require(Convert("null", typeof(Nullable<>).MakeGenericType(type)) == null, "Null changed"));
        }
        foreach (Type type in new[] { typeof(byte), typeof(sbyte), typeof(short), typeof(ushort), typeof(int), typeof(uint), typeof(long), typeof(ulong) })
        foreach (string json in new[] { "1.0", "1.5", "\"1.0\"", "\"1e0\"" })
        {
            Check(type.Name + " fractional token " + json, () => Reject(() => Convert(json, type)));
            Check(type.Name + " fractional array " + json, () => Reject(() => Convert("[" + json + "]", type.MakeArrayType())));
        }
        foreach (string json in new[] { "0", "1", "0.0", "\"0\"", "\"1\"", "{}", "[]" })
        {
            Check("Boolean rejects " + json, () => Reject(() => Convert(json, typeof(bool))));
            Check("nested Boolean rejects " + json, () => Reject(() => Convert("{\"Values\":[" + json + "]}", typeof(ScalarDto<bool>))));
        }
        Check("canonical Boolean strings", () => Require((bool)Convert("\"true\"", typeof(bool)) && !(bool)Convert("false", typeof(bool)), "Boolean changed"));
        foreach (string json in new[] { "true", "false", "1.0", "1.5", "\"true\"", "\"false\"", "\"1.0\"", "\"1e0\"" })
        {
            Check("enum rejects " + json, () => Reject(() => Convert(json, typeof(DayOfWeek))));
            Check("nullable enum rejects " + json, () => Reject(() => Convert(json, typeof(DayOfWeek?))));
            Check("enum array rejects " + json, () => Reject(() => Convert("[" + json + "]", typeof(DayOfWeek[]))));
            Check("enum nested DTO rejects " + json, () => Reject(() => Convert("{\"Values\":[" + json + "]}", typeof(ScalarDto<DayOfWeek>))));
        }
        Check(
            "enum names and integer controls",
            () =>
                Require(
                    (DayOfWeek)Convert("\"Monday\"", typeof(DayOfWeek)) == DayOfWeek.Monday
                        && (DayOfWeek)Convert("1", typeof(DayOfWeek)) == DayOfWeek.Monday
                        && (DayOfWeek)Convert("\"1\"", typeof(DayOfWeek)) == DayOfWeek.Monday,
                    "Enum changed"
                )
        );
        Check("nullable enum null control", () => Require(Convert("null", typeof(DayOfWeek?)) == null, "Nullable enum changed"));
        foreach (Type type in new[] { typeof(bool), typeof(int), typeof(float), typeof(DayOfWeek), typeof(byte) })
        {
            Check(type.Name + " DTO required null rejected", () => Reject(() => Convert("{\"Scalar\":null}", typeof(ScalarDto<>).MakeGenericType(type))));
            Check(type.Name + " array required null rejected", () => Reject(() => Convert("[null]", type.MakeArrayType())));
            Check(
                type.Name + " optional top-level null preserved",
                () => Require(Convert("null", type).Equals(Activator.CreateInstance(type)), "Top-level default changed")
            );
        }
        Check(
            "valid nested DTO",
            () =>
            {
                var value = (ScalarDto<int>)Convert("{\"Scalar\":3,\"Values\":[4,\"5\"],\"Map\":{\"x\":6}}", typeof(ScalarDto<int>));
                Require(value.Scalar == 3 && value.Values[1] == 5 && value.Map["x"] == 6, "DTO changed");
            }
        );
        foreach (Type type in new[] { typeof(float), typeof(double), typeof(decimal) })
        foreach (string json in new[] { "\"NaN\"", "\"Infinity\"", "\"-Infinity\"", "\"1e999\"" })
            Check(type.Name + " finite " + json, () => Reject(() => Convert(json, type)));
        var shapes = new Dictionary<Type, string>
        {
            [typeof(Vector2)] = "{\"x\":BAD,\"y\":2}",
            [typeof(Vector3)] = "{\"x\":BAD,\"y\":2,\"z\":3}",
            [typeof(Vector4)] = "{\"x\":BAD,\"y\":2,\"z\":3,\"w\":4}",
            [typeof(Quaternion)] = "{\"x\":BAD,\"y\":2,\"z\":3,\"w\":4}",
            [typeof(Color)] = "{\"r\":BAD,\"g\":2,\"b\":3,\"a\":1}",
            [typeof(Rect)] = "{\"x\":BAD,\"y\":2,\"width\":3,\"height\":4}",
            [typeof(Matrix4x4)] = "{\"m00\":BAD}",
            [typeof(Bounds)] = "{\"center\":{\"x\":BAD,\"y\":0,\"z\":0},\"size\":{\"x\":1,\"y\":1,\"z\":1}}",
        };
        foreach (var shape in shapes)
        {
            foreach (string bad in new[] { "true", "false", "\"NaN\"", "\"Infinity\"", "\"1e999\"" })
                Check(shape.Key.Name + " rejects " + bad, () => Reject(() => Convert(shape.Value.Replace("BAD", bad), shape.Key)));
            Check(shape.Key.Name + " numeric control", () => Require(Convert(shape.Value.Replace("BAD", "1"), shape.Key) != null, "Valid struct rejected"));
        }
        foreach (Type type in new[] { typeof(Vector2), typeof(Vector3), typeof(Vector4), typeof(Quaternion) })
        {
            string components =
                type == typeof(Vector2) ? "1,2"
                : type == typeof(Vector3) ? "1,2,3"
                : "1,2,3,4";
            Type nullable = typeof(Nullable<>).MakeGenericType(type);
            Check(type.Name + " nullable array normalization", () => Require(Convert("[" + components + "]", nullable) != null, "Nullable shorthand rejected"));
            Check(
                type.Name + " nullable collection normalization",
                () =>
                {
                    var values = (Array)Convert("[[" + components + "],null]", nullable.MakeArrayType());
                    Require(values.GetValue(0) != null && values.GetValue(1) == null, "Nullable collection changed");
                }
            );
            Check(type.Name + " nullable collection rejects Boolean", () => Reject(() => Convert("[[true," + components + "]]", nullable.MakeArrayType())));
        }
        Check("VectorParsing rejects Boolean", () => Reject(() => VectorParsing.ParseVector3(JArray.Parse("[true,2,3]"))));
        Check("VectorParsing default rejects explicit invalid", () => Reject(() => VectorParsing.ParseVector3OrDefault(JArray.Parse("[true,2,3]"))));
        Check(
            "VectorParsing default accepts absent",
            () => Require(VectorParsing.ParseVector3OrDefault(null, new Vector3(4, 5, 6)).x == 4f, "Missing default changed")
        );
        Check("Bounds parser rejects invalid center", () => Reject(() => VectorParsing.ParseBounds(JToken.Parse("{\"center\":[true,0,0],\"size\":[1,1,1]}"))));
        Check("TextureOps rejects Boolean", () => Reject(() => TextureOps.ParseColor32(JArray.Parse("[true,2,3]"))));
        Check("TextureOps rejects fractional component", () => Reject(() => TextureOps.ParseColor32(JArray.Parse("[1.5,2,3]"))));
        Check(
            "TextureOps integer clamp control",
            () =>
            {
                var color = TextureOps.ParseColor32(JArray.Parse("[999,-2,3]"));
                Require(color.r == 255 && color.g == 0 && color.b == 3 && color.a == 255, "Clamping changed");
            }
        );
        Check(
            "read-only scalar converter preserves output",
            () =>
                Require(
                    JToken
                        .FromObject(
                            new
                            {
                                Flag = true,
                                Count = 3,
                                Value = 1.5f,
                            },
                            UnityJsonSerializer.Instance
                        )
                        .ToString(Formatting.None) == "{\"Flag\":true,\"Count\":3,\"Value\":1.5}",
                    "Scalar output changed"
                )
        );
        Check(
            "byte array base64 control",
            () =>
                Require(
                    PropertyConversion.ConvertTo<byte[]>(new JValue("AQID"))[2] == 3
                        && JToken.FromObject(new byte[] { 1, 2, 3 }, UnityJsonSerializer.Instance).ToString(Formatting.None) == "\"AQID\"",
                    "Byte array encoding changed"
                )
        );
        Check(
            "input tokens unchanged",
            () =>
            {
                JArray input = JArray.Parse("[[1,2,3],null]");
                string before = input.ToString();
                PropertyConversion.ConvertTo<Vector3?[]>(input);
                Require(before == input.ToString(), "Caller tokens mutated");
            }
        );
        Check(
            "curve explicit stepped tangent control",
            () =>
                Require(
                    float.IsPositiveInfinity(VectorParsing.ReadCurveTangent(new JValue(float.PositiveInfinity)))
                        && float.IsNegativeInfinity(VectorParsing.ReadCurveTangent(new JValue("-Infinity"))),
                    "Stepped tangent changed"
                )
        );
        foreach (JToken tangent in new JToken[] { new JValue(true), new JValue(float.NaN), new JValue("NaN"), new JValue("1e999") })
            Check("curve tangent rejects " + tangent, () => Reject(() => VectorParsing.ReadCurveTangent(tangent)));
        foreach (
            string key in new[]
            {
                "receiveShadows",
                "sortingOrder",
                "sortingLayerID",
                "renderingLayerMask",
                "shadowCastingMode",
                "lightProbeUsage",
                "reflectionProbeUsage",
                "motionVectorGenerationMode",
            }
        )
            Check(
                "renderer prepared property rejects " + key,
                () =>
                {
                    var parameters = new JObject { [key] = key == "receiveShadows" ? new JValue(1) : new JValue(true) };
                    Reject(() => RendererHelpers.PrepareCommonRendererProperties(null, parameters, new List<string>()));
                }
            );
        foreach (string key in new[] { "width", "startWidth", "endWidth", "widthMultiplier" })
            Check(
                "renderer widths reject Boolean " + key,
                () =>
                {
                    int writes = 0;
                    var changes = new List<string>();
                    Reject(() =>
                        RendererHelpers.ApplyWidthProperties(
                            new JObject { [key] = true },
                            changes,
                            value => writes++,
                            value => writes++,
                            value => writes++,
                            value => writes++,
                            (value, fallback) => null
                        )
                    );
                    Require(writes == 0 && changes.Count == 0, "Invalid scalar triggered a setter");
                }
            );
        foreach (string key in new[] { "loop", "useWorldSpace", "numCornerVertices", "numCapVertices", "alignment", "textureMode", "generateLightingData" })
            Check(
                "renderer line properties reject " + key,
                () =>
                {
                    int writes = 0;
                    var changes = new List<string>();
                    JToken invalid = key == "loop" || key == "useWorldSpace" || key == "generateLightingData" ? new JValue(1) : new JValue(true);
                    Reject(() =>
                        RendererHelpers.ApplyLineTrailProperties(
                            new JObject { [key] = invalid },
                            changes,
                            value => writes++,
                            value => writes++,
                            value => writes++,
                            value => writes++,
                            value => writes++,
                            value => writes++,
                            value => writes++
                        )
                    );
                    Require(writes == 0 && changes.Count == 0, "Invalid scalar triggered a setter");
                }
            );
        Check(
            "renderer valid width callbacks",
            () =>
            {
                float start = 0f,
                    end = 0f;
                RendererHelpers.ApplyWidthProperties(
                    new JObject { ["width"] = 2.5f },
                    new List<string>(),
                    value => start = value,
                    value => end = value,
                    value => { },
                    value => { },
                    (value, fallback) => null
                );
                Require(start == 2.5f && end == 2.5f, "Width control changed");
            }
        );
        Console.WriteLine("RESULT: " + (cases - failures) + "/" + cases + " passed; failures=" + failures);
        return failures == 0 ? 0 : 1;
    }
}
