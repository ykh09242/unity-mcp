using System;
using System.Globalization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.Serialization
{
    /// <summary>Target-aware JSON scalar conversion shared by runtime and Editor readers.</summary>
    public static class JsonScalarConversion
    {
        public static bool Supports(Type type)
        {
            type = Nullable.GetUnderlyingType(type) ?? type;
            return type.IsEnum || type == typeof(bool) || IsInteger(type) || type == typeof(float) || type == typeof(double) || type == typeof(decimal);
        }

        private static bool IsInteger(Type type) =>
            type == typeof(byte)
            || type == typeof(sbyte)
            || type == typeof(short)
            || type == typeof(ushort)
            || type == typeof(int)
            || type == typeof(uint)
            || type == typeof(long)
            || type == typeof(ulong);

        public static object Read(JToken token, Type targetType)
        {
            Type type = Nullable.GetUnderlyingType(targetType) ?? targetType;
            if (!Supports(type))
                throw new ArgumentException($"Unsupported scalar type '{type.Name}'.");
            if (token == null || token.Type == JTokenType.Null)
                return Nullable.GetUnderlyingType(targetType) != null ? null : Activator.CreateInstance(type);
            string text = token is JValue scalar ? scalar.ToString(CultureInfo.InvariantCulture) : token.ToString();
            try
            {
                if (type.IsEnum)
                {
                    if (token.Type == JTokenType.Integer)
                        return Enum.ToObject(type, Read(token, Enum.GetUnderlyingType(type)));
                    if (token.Type == JTokenType.String)
                    {
                        // Preserve named values, flags and exact integer strings.
                        // Enum.Parse rejects decimal/exponent notation and Boolean strings.
                        try
                        {
                            return Enum.Parse(type, text, ignoreCase: true);
                        }
                        catch (ArgumentException ex)
                        {
                            throw Invalid(token, type, ex);
                        }
                    }
                }
                else if (type == typeof(bool))
                {
                    if (token.Type == JTokenType.Boolean)
                        return token.Value<bool>();
                    if (token.Type == JTokenType.String && bool.TryParse(text, out bool boolean))
                        return boolean;
                }
                else if (IsInteger(type))
                {
                    if (
                        (token.Type == JTokenType.Integer || token.Type == JTokenType.String)
                        && decimal.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out decimal integer)
                    )
                        return Convert.ChangeType(integer, type, CultureInfo.InvariantCulture);
                }
                else if (token.Type == JTokenType.Integer || token.Type == JTokenType.Float || token.Type == JTokenType.String)
                {
                    if (type == typeof(decimal))
                    {
                        if (decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out decimal number))
                            return number;
                    }
                    else
                    {
                        double number;
                        bool parsed =
                            token.Type != JTokenType.String
                                ? TryReadDouble(token, out number)
                                : double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out number);
                        if (parsed && !double.IsNaN(number) && !double.IsInfinity(number))
                        {
                            if (type == typeof(double))
                                return number;
                            float single = (float)number;
                            if (!float.IsNaN(single) && !float.IsInfinity(single))
                                return single;
                        }
                    }
                }
            }
            catch (Exception ex) when (ex is OverflowException || ex is InvalidCastException || ex is FormatException)
            {
                throw Invalid(token, type, ex);
            }
            throw Invalid(token, type);
        }

        private static bool TryReadDouble(JToken token, out double value)
        {
            value = token.Value<double>();
            return true;
        }

        public static float ReadFloat(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
                throw Invalid(token, typeof(float));
            return (float)Read(token, typeof(float));
        }

        private static JsonSerializationException Invalid(JToken token, Type type, Exception inner = null) =>
            new JsonSerializationException(
                $"Invalid scalar at '{token?.Path ?? "value"}': expected {type.Name}, got {token?.Type.ToString() ?? "missing"}.",
                inner
            );
    }
}
