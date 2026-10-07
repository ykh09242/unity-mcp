using System;
using MCPForUnity.Runtime.Serialization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>
    /// Utility class for coercing JSON parameter values to strongly-typed values.
    /// Keeps Boolean and numeric domains separate and rejects explicit invalid scalar values.
    /// </summary>
    public static class ParamCoercion
    {
        /// <summary>
        /// Reads a scalar without boolean/numeric coercion, fractional integer conversion,
        /// nonfinite numbers or silent defaults for explicitly invalid values.
        /// Missing/null optional values return default(T); canonical scalar strings are accepted.
        /// </summary>
        public static T ReadScalar<T>(this JToken token)
        {
            if (IsMissing(token))
                return default;
            try
            {
                return (T)JsonScalarConversion.Read(token, typeof(T));
            }
            catch (JsonSerializationException error)
            {
                var target = Nullable.GetUnderlyingType(typeof(T)) ?? typeof(T);
                string name = string.IsNullOrEmpty(token.Path) ? "value" : token.Path;
                throw new ArgumentException($"Invalid parameter '{name}': expected {target.Name}, got {token.Type}.", name, error);
            }
        }

        /// <summary>Reads a keyed optional scalar with the same strict rules.</summary>
        public static T ReadScalar<T>(this JToken token, object key)
        {
            return token == null ? default : token[key].ReadScalar<T>();
        }

        private static bool IsMissing(JToken token) => token == null || token.Type == JTokenType.Null;

        /// <summary>Reads an optional integer; only missing/null uses the default.</summary>
        public static int CoerceInt(JToken token, int defaultValue) => IsMissing(token) ? defaultValue : token.ReadScalar<int>();

        /// <summary>Reads an optional long integer; only missing/null uses the default.</summary>
        public static long CoerceLong(JToken token, long defaultValue) => IsMissing(token) ? defaultValue : token.ReadScalar<long>();

        /// <summary>Reads a nullable integer; explicit invalid values are rejected.</summary>
        public static int? CoerceIntNullable(JToken token) => token.ReadScalar<int?>();

        /// <summary>Reads an optional Boolean/canonical Boolean string; numeric flags are rejected.</summary>
        public static bool CoerceBool(JToken token, bool defaultValue) => IsMissing(token) ? defaultValue : token.ReadScalar<bool>();

        /// <summary>Reads a nullable Boolean; explicit invalid values are rejected.</summary>
        public static bool? CoerceBoolNullable(JToken token) => token.ReadScalar<bool?>();

        /// <summary>Reads an optional finite float; only missing/null uses the default.</summary>
        public static float CoerceFloat(JToken token, float defaultValue) => IsMissing(token) ? defaultValue : token.ReadScalar<float>();

        /// <summary>Reads a nullable finite float; explicit invalid values are rejected.</summary>
        public static float? CoerceFloatNullable(JToken token) => token.ReadScalar<float?>();

        /// <summary>Reads a nullable curve tangent, allowing explicit signed infinity for stepped segments.</summary>
        public static float? ReadCurveTangent(this JToken token)
        {
            return TryReadInfiniteCurveTangent(token, out float tangent) ? tangent : token.ReadScalar<float?>();
        }

        private static bool TryReadInfiniteCurveTangent(JToken token, out float tangent)
        {
            tangent = 0f;
            if (token is not JValue value)
                return false;
            switch (value.Value)
            {
                case float single when float.IsInfinity(single):
                    tangent = single;
                    return true;
                case double number when double.IsInfinity(number):
                    tangent = (float)number;
                    return true;
                case string text when text.Trim() == "Infinity" || text.Trim() == "+Infinity":
                    tangent = float.PositiveInfinity;
                    return true;
                case string negative when negative.Trim() == "-Infinity":
                    tangent = float.NegativeInfinity;
                    return true;
                default:
                    return false;
            }
        }

        /// <summary>
        /// Coerces a JToken to a string value, with null handling.
        /// </summary>
        /// <param name="token">The JSON token to coerce</param>
        /// <param name="defaultValue">Default value if null or empty</param>
        /// <returns>The string value or default</returns>
        public static string CoerceString(JToken token, string defaultValue = null)
        {
            if (token == null || token.Type == JTokenType.Null)
                return defaultValue;

            var s = token.ToString();
            return string.IsNullOrEmpty(s) ? defaultValue : s;
        }

        /// <summary>
        /// Reads an optional enum through the strict scalar parser, preserving named flags.
        /// </summary>
        /// <typeparam name="T">The enum type</typeparam>
        /// <param name="token">The JSON token to coerce</param>
        /// <param name="defaultValue">Default value only for missing/null input</param>
        /// <returns>The enum value; explicit invalid values throw an argument error</returns>
        public static T CoerceEnum<T>(JToken token, T defaultValue)
            where T : struct, Enum
        {
            return IsMissing(token) ? defaultValue : token.ReadScalar<T>();
        }

        /// <summary>
        /// Checks if a JToken represents a finite float-compatible numeric value.
        /// Useful for validating JSON values before parsing.
        /// </summary>
        /// <param name="token">The JSON token to check</param>
        /// <returns>True for finite, representable numeric tokens; false otherwise</returns>
        public static bool IsNumericToken(JToken token)
        {
            if (token == null || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float))
                return false;
            try
            {
                token.ReadScalar<float>();
                return true;
            }
            catch (ArgumentException)
            {
                return false;
            }
        }

        /// <summary>
        /// Validates that an optional field in a JObject is numeric if present.
        /// Used for dry-run validation of complex type formats.
        /// </summary>
        /// <param name="obj">The JSON object containing the field</param>
        /// <param name="fieldName">The name of the field to validate</param>
        /// <param name="error">Output error message if validation fails</param>
        /// <returns>True if the field is absent, null, or numeric; false if present but non-numeric</returns>
        public static bool ValidateNumericField(JObject obj, string fieldName, out string error)
        {
            error = null;
            var token = obj[fieldName];
            if (token == null || token.Type == JTokenType.Null)
            {
                return true; // Field not present, valid (will use default)
            }
            // Unity uses explicit infinite tangents to encode stepped curve segments.
            // Restrict this exception to slope/tangent fields; all other numeric fields are finite.
            bool curveTangent = fieldName == "inTangent" || fieldName == "outTangent" || fieldName == "inSlope" || fieldName == "outSlope";
            bool explicitInfiniteTangent = curveTangent && TryReadInfiniteCurveTangent(token, out _);
            if (!explicitInfiniteTangent && !IsNumericToken(token))
            {
                error = $"must be a finite representable number, got {token.Type}";
                return false;
            }
            return true;
        }

        /// <summary>
        /// Validates that an optional field in a JObject is an integer if present.
        /// Used for dry-run validation of complex type formats.
        /// </summary>
        /// <param name="obj">The JSON object containing the field</param>
        /// <param name="fieldName">The name of the field to validate</param>
        /// <param name="error">Output error message if validation fails</param>
        /// <returns>True if the field is absent, null, or integer; false if present but non-integer</returns>
        public static bool ValidateIntegerField(JObject obj, string fieldName, out string error)
        {
            error = null;
            var token = obj[fieldName];
            if (token == null || token.Type == JTokenType.Null)
            {
                return true; // Field not present, valid
            }
            if (token.Type != JTokenType.Integer)
            {
                error = $"must be an integer, got {token.Type}";
                return false;
            }
            return true;
        }

        /// <summary>
        /// Normalizes a property name by removing separators and converting to camelCase.
        /// Handles common naming variations from LLMs and humans.
        /// Examples:
        ///   "Use Gravity" → "useGravity"
        ///   "is_kinematic" → "isKinematic"
        ///   "max-angular-velocity" → "maxAngularVelocity"
        ///   "Angular Drag" → "angularDrag"
        /// </summary>
        /// <param name="input">The property name to normalize</param>
        /// <returns>The normalized camelCase property name</returns>
        public static string NormalizePropertyName(string input)
        {
            if (string.IsNullOrEmpty(input))
                return input;

            // Split on common separators: space, underscore, dash
            var parts = input.Split(new[] { ' ', '_', '-' }, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 0)
                return input;

            // First word is lowercase, subsequent words are Title case (camelCase)
            var sb = new System.Text.StringBuilder();
            for (int i = 0; i < parts.Length; i++)
            {
                string part = parts[i];
                if (i == 0)
                {
                    // First word: all lowercase
                    sb.Append(part.ToLowerInvariant());
                }
                else
                {
                    // Subsequent words: capitalize first letter, lowercase rest
                    sb.Append(char.ToUpperInvariant(part[0]));
                    if (part.Length > 1)
                        sb.Append(part.Substring(1).ToLowerInvariant());
                }
            }
            return sb.ToString();
        }
    }
}
