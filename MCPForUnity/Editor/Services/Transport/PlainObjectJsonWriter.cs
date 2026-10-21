using System;
using System.Collections.Generic;
using System.Numerics;
using Newtonsoft.Json;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>Build plain component values without an intermediate JToken tree. Used behind the bounded writer.</summary>
    internal sealed class PlainObjectJsonWriter : JsonWriter
    {
        private readonly Stack<object> containers = new();
        private string property;
        internal object Result { get; private set; }

        internal PlainObjectJsonWriter() => AutoCompleteOnClose = false;

        public override void Flush() { }

        private void Put(object value)
        {
            if (containers.Count == 0)
                Result = value;
            else if (containers.Peek() is Dictionary<string, object> dictionary)
            {
                dictionary[property] = value;
                property = null;
            }
            else
                ((List<object>)containers.Peek()).Add(value);
        }

        public override void WriteStartObject()
        {
            base.WriteStartObject();
            var value = new Dictionary<string, object>();
            Put(value);
            containers.Push(value);
        }

        public override void WriteStartArray()
        {
            base.WriteStartArray();
            var value = new List<object>();
            Put(value);
            containers.Push(value);
        }

        public override void WriteStartConstructor(string name)
        {
            base.WriteStartConstructor(name);
            // The previous component conversion represented non-value constructors as null.
            Put(null);
            containers.Push(new List<object>());
        }

        protected override void WriteEnd(JsonToken token) => containers.Pop();

        public override void WritePropertyName(string name)
        {
            base.WritePropertyName(name);
            property = name;
        }

        public override void WritePropertyName(string name, bool escape) => WritePropertyName(name);

        public override void WriteNull()
        {
            base.WriteNull();
            Put(null);
        }

        public override void WriteUndefined()
        {
            base.WriteUndefined();
            Put(null);
        }

        public override void WriteComment(string text)
        {
            base.WriteComment(text);
            if (containers.Count == 0 || containers.Peek() is List<object>)
                Put(text);
        }

        public override void WriteRaw(string json) => WriteRawValue(json);

        public override void WriteRawValue(string json)
        {
            base.WriteValue(0);
            Put(json);
        }

        public override void WriteValue(object value)
        {
            if (value is BigInteger)
            {
                base.WriteValue(0);
                Put(value);
            }
            else
                base.WriteValue(value);
        }

        public override void WriteValue(int value)
        {
            base.WriteValue(value);
            Put((long)value);
        }

        public override void WriteValue(uint value)
        {
            base.WriteValue(value);
            Put((long)value);
        }

        public override void WriteValue(long value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(ulong value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(short value)
        {
            base.WriteValue(value);
            Put((long)value);
        }

        public override void WriteValue(ushort value)
        {
            base.WriteValue(value);
            Put((long)value);
        }

        public override void WriteValue(byte value)
        {
            base.WriteValue(value);
            Put((long)value);
        }

        public override void WriteValue(sbyte value)
        {
            base.WriteValue(value);
            Put((long)value);
        }

        public override void WriteValue(float value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(double value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(decimal value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(bool value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(char value)
        {
            base.WriteValue(value);
            Put(value.ToString());
        }

        public override void WriteValue(DateTime value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(DateTimeOffset value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(TimeSpan value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(Guid value)
        {
            base.WriteValue(value);
            Put(value);
        }

        public override void WriteValue(string value)
        {
            if (value == null)
                WriteNull();
            else
            {
                base.WriteValue(value);
                Put(value);
            }
        }

        public override void WriteValue(byte[] value)
        {
            if (value == null)
                WriteNull();
            else
            {
                base.WriteValue(value);
                Put(value);
            }
        }

        public override void WriteValue(Uri value)
        {
            if (value == null)
                WriteNull();
            else
            {
                base.WriteValue(value);
                Put(value);
            }
        }
    }
}
