using System;
using System.Globalization;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Finite, explicit observation budgets for expensive editor work.</summary>
    internal static class OperationTimeouts
    {
        internal const double MaximumSeconds = 86400;

        internal static double EnvironmentSeconds(string name, double fallback, double maximum = MaximumSeconds)
        {
            string raw = Environment.GetEnvironmentVariable(name);
            if (
                !double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out double seconds)
                || double.IsNaN(seconds)
                || double.IsInfinity(seconds)
                || seconds <= 0
            )
                return fallback;
            return Math.Min(seconds, maximum);
        }

        internal static double ReadSeconds(JObject parameters, double fallback)
        {
            double? snake = Read(parameters?["timeout_seconds"], "timeout_seconds");
            double? camel = Read(parameters?["timeoutSeconds"], "timeoutSeconds");
            return snake ?? camel ?? fallback;
        }

        private static double? Read(JToken token, string name)
        {
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (token.Type == JTokenType.Integer || token.Type == JTokenType.Float)
            {
                double seconds;
                try
                {
                    seconds = token.Value<double>();
                }
                catch (Exception error) when (error is OverflowException || error is FormatException || error is InvalidCastException)
                {
                    throw new ArgumentException($"Invalid {name}: expected finite positive seconds up to 86400.", name, error);
                }
                if (!double.IsNaN(seconds) && !double.IsInfinity(seconds) && seconds > 0 && seconds <= MaximumSeconds)
                    return seconds;
            }
            throw new ArgumentException($"Invalid {name}: expected finite positive seconds up to 86400.", name);
        }
    }
}
