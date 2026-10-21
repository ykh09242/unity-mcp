using System;
using System.Globalization;
using System.IO;
using System.Numerics;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.Transport
{
    internal sealed class ResponseLimitException : JsonSerializationException
    {
        internal ResponseLimitException()
            : base("Command result exceeds the transport size or complexity limit") { }
    }

    /// <summary>Admit response graphs before freezing, and stop JSON writes before oversize copies.</summary>
    internal static class BoundedResponseJson
    {
        internal const int MaxDepth = 64;
        internal const int MaxNodes = 1000000;

        internal static JToken Freeze(object payload, JsonSerializer serializer = null, Budget budget = null)
        {
            budget ??= new Budget();
            budget.Check();
            if (payload is JToken token)
            {
                using var reader = token.CreateReader();
                reader.MaxDepth = null; // Validate below returns the bounded transport error.
                Validate(reader, budget);
                return token;
            }
            using var writer = new TokenWriter(budget: budget);
            (serializer ?? JsonSerializer.CreateDefault()).Serialize(writer, payload);
            budget.Check(); // User OnError callbacks cannot turn a limit refusal into partial success.
            return writer.Token ?? JValue.CreateNull();
        }

        internal static object ToPlain(object payload, JsonSerializer serializer, Budget budget)
        {
            using var output = new PlainObjectJsonWriter();
            using var writer = new TokenWriter(output, budget);
            serializer.Serialize(writer, payload);
            budget.Check();
            return output.Result;
        }

        internal static JToken Parse(string json)
        {
            CheckText(json);
            using var input = new JsonTextReader(new StringReader(json)) { DateParseHandling = DateParseHandling.None, MaxDepth = MaxDepth };
            using var output = new TokenWriter();
            output.WriteToken(input);
            while (input.Read()) { }
            return output.Token ?? throw new JsonReaderException("Invalid response payload");
        }

        internal static string Serialize(object payload)
        {
            using var output = new LimitedTextWriter();
            using var writer = new DepthLimitedTextWriter(output) { AutoCompleteOnClose = false };
            JsonSerializer.CreateDefault().Serialize(writer, payload);
            writer.Flush();
            return output.Finish();
        }

        private static void CheckText(string value)
        {
            if (value != null && (value.Length > LargeResultWriter.MaxResultBytes || Encoding.UTF8.GetByteCount(value) > LargeResultWriter.MaxResultBytes))
                throw new ResponseLimitException();
        }

        private static void Validate(JsonReader reader, Budget budget = null, int depthOffset = 0)
        {
            budget = budget ?? new Budget();
            while (reader.Read())
            {
                if (reader.Depth + depthOffset > MaxDepth)
                    budget.Fail();
                switch (reader.TokenType)
                {
                    case JsonToken.EndObject:
                    case JsonToken.EndArray:
                    case JsonToken.EndConstructor:
                    case JsonToken.None:
                        continue;
                }
                if (reader.TokenType == JsonToken.Raw)
                    ValidateRaw(Convert.ToString(reader.Value, CultureInfo.InvariantCulture), budget, reader.Depth + depthOffset);
                else
                    budget.Value(reader.Value);
            }
        }

        private static void ValidateRaw(string json, Budget budget, int depthOffset)
        {
            budget = budget ?? new Budget();
            try
            {
                CheckText(json);
            }
            catch (ResponseLimitException)
            {
                budget.Fail();
            }
            using var reader = new JsonTextReader(new StringReader(json ?? "null")) { MaxDepth = null, DateParseHandling = DateParseHandling.None };
            Validate(reader, budget, depthOffset);
        }

        internal sealed class Budget
        {
            private int nodes;
            private long scalarBytes;
            internal bool Exceeded { get; private set; }

            internal void Check()
            {
                if (Exceeded)
                    throw new ResponseLimitException();
            }

            internal void Fail()
            {
                Exceeded = true;
                throw new ResponseLimitException();
            }

            internal void Node()
            {
                if (Exceeded || ++nodes > MaxNodes)
                    Fail();
            }

            internal void Value(object value)
            {
                Node();
                if (value is string text)
                {
                    if (text.Length > LargeResultWriter.MaxResultBytes)
                        Fail();
                    scalarBytes += Encoding.UTF8.GetByteCount(text);
                }
                else if (value is byte[] bytes)
                    scalarBytes += ((long)bytes.Length + 2) / 3 * 4;
                if (scalarBytes > LargeResultWriter.MaxResultBytes)
                    Fail();
            }
        }

        // Compose a JTokenWriter instead of inheriting its JTokenReader DeepClone fast path:
        // converters that call WriteToken must pass every node through admission too.
        private sealed class TokenWriter : JsonWriter
        {
            private readonly JsonWriter target;
            private readonly Budget budget;
            internal JToken Token => ((JTokenWriter)target).Token;

            internal TokenWriter(JsonWriter output = null, Budget budget = null)
            {
                target = output ?? new JTokenWriter { AutoCompleteOnClose = false };
                this.budget = budget ?? new Budget();
                this.budget.Check();
                AutoCompleteOnClose = false;
            }

            public override void Flush() => target.Flush();

            public override void Close()
            {
                target.Close();
                base.Close();
            }

            private void Enter()
            {
                if (Top >= MaxDepth)
                    budget.Fail();
                budget.Value(null);
            }

            public override void WriteStartObject()
            {
                Enter();
                base.WriteStartObject();
                target.WriteStartObject();
            }

            public override void WriteStartArray()
            {
                Enter();
                base.WriteStartArray();
                target.WriteStartArray();
            }

            public override void WriteStartConstructor(string name)
            {
                Enter();
                budget.Value(name);
                base.WriteStartConstructor(name);
                target.WriteStartConstructor(name);
            }

            protected override void WriteEnd(JsonToken token) => target.WriteEnd();

            public override void WritePropertyName(string name)
            {
                budget.Value(name);
                base.WritePropertyName(name);
                target.WritePropertyName(name);
            }

            public override void WritePropertyName(string name, bool escape) => WritePropertyName(name);

            public override void WriteNull()
            {
                budget.Value(null);
                base.WriteNull();
                target.WriteNull();
            }

            public override void WriteUndefined()
            {
                budget.Value(null);
                base.WriteUndefined();
                target.WriteUndefined();
            }

            public override void WriteComment(string text)
            {
                budget.Value(text);
                base.WriteComment(text);
                target.WriteComment(text);
            }

            public override void WriteRaw(string json) => WriteRawValue(json);

            public override void WriteRawValue(string json)
            {
                ValidateRaw(json, budget, Top);
                base.WriteValue(0); // Update this writer's value state; only target owns the token.
                target.WriteRawValue(json);
            }

            public override void WriteValue(object value)
            {
                if (value is BigInteger integer)
                {
                    budget.Value(integer.ToString(CultureInfo.InvariantCulture));
                    base.WriteValue(0);
                    target.WriteValue(value);
                }
                else
                    base.WriteValue(value);
            }

            public override void WriteValue(string value)
            {
                if (value == null)
                {
                    WriteNull();
                    return;
                }
                budget.Value(value);
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(int value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(uint value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(long value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(ulong value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(float value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(double value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(bool value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(short value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(ushort value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(char value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(byte value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(sbyte value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(decimal value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(DateTime value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(DateTimeOffset value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(byte[] value)
            {
                if (value == null)
                {
                    WriteNull();
                    return;
                }
                budget.Value(value);
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(TimeSpan value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(Guid value)
            {
                budget.Node();
                base.WriteValue(value);
                target.WriteValue(value);
            }

            public override void WriteValue(Uri value)
            {
                if (value == null)
                {
                    WriteNull();
                    return;
                }
                budget.Value(value.OriginalString);
                base.WriteValue(value);
                target.WriteValue(value);
            }
        }

        private sealed class DepthLimitedTextWriter : JsonTextWriter
        {
            public override void WriteRawValue(string json)
            {
                ValidateRaw(json, null, Top);
                base.WriteRawValue(json);
            }

            internal DepthLimitedTextWriter(TextWriter output)
                : base(output) { }

            private void Enter()
            {
                if (Top >= MaxDepth)
                    throw new ResponseLimitException();
            }

            public override void WriteStartObject()
            {
                Enter();
                base.WriteStartObject();
            }

            public override void WriteStartArray()
            {
                Enter();
                base.WriteStartArray();
            }

            public override void WriteStartConstructor(string name)
            {
                Enter();
                base.WriteStartConstructor(name);
            }
        }

        private sealed class LimitedTextWriter : TextWriter
        {
            private readonly StringBuilder text = new();
            private long bytes;
            private bool highSurrogate;
            public override Encoding Encoding => Encoding.UTF8;

            private void Count(char value)
            {
                if (highSurrogate)
                {
                    highSurrogate = false;
                    if (char.IsLowSurrogate(value))
                    {
                        Add(4);
                        return;
                    }
                    Add(3);
                }
                if (char.IsHighSurrogate(value))
                    highSurrogate = true;
                else
                    Add(
                        value < 0x80 ? 1
                        : value < 0x800 ? 2
                        : 3
                    );
            }

            private void Add(int count)
            {
                bytes += count;
                if (bytes > LargeResultWriter.MaxResultBytes)
                    throw new ResponseLimitException();
            }

            public override void Write(char value)
            {
                Count(value);
                text.Append(value);
            }

            public override void Write(string value)
            {
                if (value == null)
                    return;
                if (value.Length > 0)
                    CountChunk(Encoding.UTF8.GetByteCount(value), value[0], value[value.Length - 1]);
                text.Append(value);
            }

            public override void Write(char[] buffer, int index, int count)
            {
                if (count > 0)
                    CountChunk(Encoding.UTF8.GetByteCount(buffer, index, count), buffer[index], buffer[index + count - 1]);
                text.Append(buffer, index, count);
            }

            private void CountChunk(int count, char first, char last)
            {
                // UTF8 counts isolated surrogates as three-byte replacements. Carry a
                // trailing high surrogate across writer calls so a split pair counts four.
                if (highSurrogate)
                    count += char.IsLowSurrogate(first) ? 1 : 3;
                highSurrogate = char.IsHighSurrogate(last);
                if (highSurrogate)
                    count -= 3;
                Add(count);
            }

            internal string Finish()
            {
                if (highSurrogate)
                {
                    highSurrogate = false;
                    Add(3);
                }
                return text.ToString();
            }
        }
    }
}
