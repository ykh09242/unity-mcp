using System;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using BigInteger = System.Numerics.BigInteger;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>One finite numeric predicate. Existing equality retains its separate semantics.</summary>
    public sealed class PlayScenarioNumericCondition
    {
        [JsonProperty("operator")]
        public string Operator;

        [JsonProperty("value", NullValueHandling = NullValueHandling.Ignore)]
        public JToken Value;

        [JsonProperty("min", NullValueHandling = NullValueHandling.Ignore)]
        public JToken Min;

        [JsonProperty("max", NullValueHandling = NullValueHandling.Ignore)]
        public JToken Max;

        [JsonProperty("absolute_tolerance", NullValueHandling = NullValueHandling.Ignore)]
        public JToken AbsoluteTolerance;

        [JsonProperty("relative_tolerance", NullValueHandling = NullValueHandling.Ignore)]
        public JToken RelativeTolerance;

        internal static PlayScenarioNumericCondition Parse(JToken token)
        {
            if (!(token is JObject value))
                throw new ArgumentException("Numeric comparison must be an object.");
            PlayScenarioDefinition.Fields(value, "operator", "value", "min", "max", "absolute_tolerance", "relative_tolerance");
            if (value["operator"]?.Type != JTokenType.String)
                throw new ArgumentException("Numeric comparison requires an exact operator string.");
            var result = new PlayScenarioNumericCondition
            {
                Operator = (string)value["operator"],
                Value = value["value"]?.DeepClone(),
                Min = value["min"]?.DeepClone(),
                Max = value["max"]?.DeepClone(),
                AbsoluteTolerance = value["absolute_tolerance"]?.DeepClone(),
                RelativeTolerance = value["relative_tolerance"]?.DeepClone(),
            };
            result.Compile();
            return result;
        }

        internal Predicate Compile() => new Predicate(this);

        internal string Description() => JObject.FromObject(this).ToString(Formatting.None);

        internal sealed class Predicate
        {
            private readonly string operation;
            private readonly Dyadic target;
            private readonly Dyadic minimum;
            private readonly Dyadic maximum;
            private readonly Dyadic absolute;
            private readonly Dyadic relative;

            internal Predicate(PlayScenarioNumericCondition source)
            {
                operation = source.Operator;
                bool tolerance = source.AbsoluteTolerance != null || source.RelativeTolerance != null;
                switch (operation)
                {
                    case "gte":
                    case "lte":
                        if (source.Min != null || source.Max != null || tolerance)
                            throw new ArgumentException("gte/lte accept only operator and value.");
                        target = Numeric(source.Value);
                        break;
                    case "range":
                        if (source.Value != null || tolerance)
                            throw new ArgumentException("range accepts only operator, min and max.");
                        minimum = Numeric(source.Min);
                        maximum = Numeric(source.Max);
                        if (minimum.CompareTo(maximum) > 0)
                            throw new ArgumentException("Numeric range min must not exceed max.");
                        break;
                    case "approximately":
                        if (source.Min != null || source.Max != null || !tolerance)
                            throw new ArgumentException("approximately requires value and at least one tolerance, without range bounds.");
                        target = Numeric(source.Value);
                        absolute = source.AbsoluteTolerance == null ? Dyadic.Zero : Numeric(source.AbsoluteTolerance);
                        relative = source.RelativeTolerance == null ? Dyadic.Zero : Numeric(source.RelativeTolerance);
                        if (absolute.Sign < 0 || relative.Sign < 0)
                            throw new ArgumentException("Numeric tolerances must be finite and nonnegative.");
                        break;
                    default:
                        throw new ArgumentException("Unsupported numeric comparison operator.");
                }
            }

            private static Dyadic Numeric(JToken value)
            {
                if (value == null || (value.Type != JTokenType.Integer && value.Type != JTokenType.Float) || !PlayScenarioDefinition.Scalar(value))
                    throw new ArgumentException("Numeric operands must be signed 64-bit integers or finite numbers.");
                return Dyadic.FromScalar(PlayScenarioStateValue.FromJson(value));
            }

            internal bool Matches(PlayScenarioStateValue value)
            {
                if (!value.IsValid || (value.Kind != PlayScenarioStateValueKind.Integer && value.Kind != PlayScenarioStateValueKind.Number))
                    return false;
                Dyadic actual = Dyadic.FromScalar(value);
                switch (operation)
                {
                    case "gte":
                        return actual.CompareTo(target) >= 0;
                    case "lte":
                        return actual.CompareTo(target) <= 0;
                    case "range":
                        return actual.CompareTo(minimum) >= 0 && actual.CompareTo(maximum) <= 0;
                    case "approximately":
                        Dyadic scaled = relative.Multiply(target.Abs());
                        Dyadic allowed = absolute.CompareTo(scaled) >= 0 ? absolute : scaled;
                        return actual.Subtract(target).Abs().CompareTo(allowed) <= 0;
                    default:
                        throw new InvalidOperationException("Invalid compiled numeric comparison.");
                }
            }
        }

        /// <summary>
        /// Exact significand * 2^exponent arithmetic. Inputs are only int64/finite binary64;
        /// even relative products/alignment stay below 4196 magnitude bits and 4090 shift bits.
        /// No unbounded JSON integer/exponent or floating-point intermediate enters this type.
        /// </summary>
        private readonly struct Dyadic
        {
            private const int MaximumBits = 4196;
            private const int MaximumShift = 4090;
            private readonly BigInteger coefficient;
            private readonly int exponent;
            internal static Dyadic Zero => new Dyadic(BigInteger.Zero, 0);
            internal int Sign => coefficient.Sign;

            private Dyadic(BigInteger coefficient, int exponent)
            {
                if (exponent < -2148 || exponent > 1942 || Bits(coefficient) > MaximumBits)
                    throw new InvalidOperationException("Numeric assertion arithmetic exceeded its fixed input bounds.");
                this.coefficient = coefficient;
                this.exponent = coefficient.IsZero ? 0 : exponent;
            }

            internal static Dyadic FromScalar(PlayScenarioStateValue value)
            {
                if (value.Kind == PlayScenarioStateValueKind.Integer)
                    return new Dyadic(new BigInteger(value.IntegerValue), 0);
                if (value.Kind != PlayScenarioStateValueKind.Number || !value.IsValid)
                    throw new ArgumentException("A finite numeric scalar is required.");
                ulong bits = unchecked((ulong)BitConverter.DoubleToInt64Bits(value.NumberValue));
                int encodedExponent = (int)((bits >> 52) & 2047);
                ulong significand = bits & 0x000fffffffffffffUL;
                if (encodedExponent != 0)
                    significand |= 1UL << 52;
                BigInteger integer = new BigInteger(significand);
                if ((bits >> 63) != 0)
                    integer = -integer;
                return new Dyadic(integer, encodedExponent == 0 ? -1074 : encodedExponent - 1023 - 52);
            }

            internal Dyadic Abs() => new Dyadic(BigInteger.Abs(coefficient), exponent);

            internal Dyadic Multiply(Dyadic other)
            {
                if (Bits(coefficient) + Bits(other.coefficient) > MaximumBits)
                    throw new InvalidOperationException("Numeric product exceeds its fixed bit bound.");
                int power = checked(exponent + other.exponent);
                if (power < -2148 || power > 1942)
                    throw new InvalidOperationException("Numeric product exceeds its fixed exponent bound.");
                return new Dyadic(coefficient * other.coefficient, power);
            }

            internal Dyadic Subtract(Dyadic other)
            {
                int power = Math.Min(exponent, other.exponent);
                BigInteger first = Shift(coefficient, exponent - power);
                BigInteger second = Shift(other.coefficient, other.exponent - power);
                if (Math.Max(Bits(first), Bits(second)) + 1 > MaximumBits)
                    throw new InvalidOperationException("Numeric difference exceeds its fixed bit bound.");
                return new Dyadic(first - second, power);
            }

            internal int CompareTo(Dyadic other)
            {
                int power = Math.Min(exponent, other.exponent);
                return Shift(coefficient, exponent - power).CompareTo(Shift(other.coefficient, other.exponent - power));
            }

            private static BigInteger Shift(BigInteger value, int count)
            {
                if (count < 0 || count > MaximumShift || Bits(value) + count > MaximumBits)
                    throw new InvalidOperationException("Numeric alignment exceeds its fixed bit/shift bound.");
                return value << count;
            }

            private static int Bits(BigInteger value)
            {
                byte[] bytes = BigInteger.Abs(value).ToByteArray();
                int last = bytes.Length - 1;
                while (last > 0 && bytes[last] == 0)
                    last--;
                int result = last * 8;
                for (byte top = bytes[last]; top != 0; top >>= 1)
                    result++;
                return result;
            }
        }
    }
}
