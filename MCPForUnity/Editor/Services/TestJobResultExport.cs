using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Xml;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    internal static class TestJobResultExport
    {
        private static readonly UTF8Encoding Utf8 = new(false, true);
        private const int ChunkBytes = 65536;
        private const int BlockBytes = 16384;

        // Only the whole digest is persisted. A new store authenticates the complete file before
        // caching its block hashes. Warm reads always authenticate the bytes they will decode.
        internal sealed class BlockIndex
        {
            internal readonly string Identity;
            internal readonly string Sha256;
            internal readonly byte[][] Blocks;
            internal readonly long TotalBytes;

            internal BlockIndex(string identity, string sha256, byte[][] blocks, long totalBytes)
            {
                Identity = identity;
                Sha256 = sha256;
                Blocks = blocks;
                TotalBytes = totalBytes;
            }
        }

        public static JObject Read(TestJobResultStore store, string jobId, JObject descriptor, string format, string cursor)
        {
            if (format != "json" && format != "junit")
                throw new ArgumentException("format must be json or junit.");
            lock (store.Gate)
            {
                JObject manifest = store.Manifest(jobId, descriptor);
                string identity = jobId + ":" + (string)descriptor["generation"] + ":" + format + ":";
                long offset = 0;
                if (
                    cursor != null
                    && (!cursor.StartsWith(identity, StringComparison.Ordinal) || !long.TryParse(cursor.Substring(identity.Length), out offset) || offset < 0)
                )
                    throw new ArgumentException("Invalid or stale export cursor.");
                string path = store.ExportPath(jobId, descriptor, format);
                JObject evidence = descriptor["exports"]?[format] as JObject;
                string indexIdentity = identity + (string)descriptor["sha256"];
                BlockIndex index;
                if (evidence == null)
                {
                    if (File.Exists(store.CheckPath(path)))
                        throw new InvalidDataException("Untrusted cached export.");
                    index = Generate(store, jobId, descriptor, manifest, format, path, indexIdentity);
                    evidence = new JObject
                    {
                        ["sha256"] = index.Sha256,
                        ["total_bytes"] = index.TotalBytes,
                        ["source_sha256"] = descriptor["sha256"],
                    };
                    if (descriptor["exports"] == null)
                        descriptor["exports"] = new JObject();
                    descriptor["exports"][format] = evidence;
                    store.ExportIndexes.Add(evidence, index);
                }
                using var file = new FileStream(store.CheckPath(path), FileMode.Open, FileAccess.Read, FileShare.Read);
                long length = file.Length;
                if ((long)evidence["total_bytes"] != length || offset > length)
                    throw new InvalidDataException("Invalid export size or cursor.");
                if (!store.ExportIndexes.TryGetValue(evidence, out index))
                {
                    index = BuildIndex(store, file, indexIdentity);
                    if (index.Sha256 != (string)evidence["sha256"])
                        throw new InvalidDataException("Cached export integrity mismatch.");
                    store.ExportIndexes.Add(evidence, index);
                }
                if (index.Identity != indexIdentity || index.Sha256 != (string)evidence["sha256"] || index.TotalBytes != length)
                    throw new InvalidDataException("Cached export integrity mismatch.");
                int count = (int)Math.Min(ChunkBytes, length - offset);
                int withinBlock = (int)(offset % BlockBytes);
                // Authenticate every overlapping block, including any UTF-8 suffix trimmed below.
                // A change elsewhere in a warm file is detected when that block is requested.
                long first = offset - withinBlock;
                int alignedBytes = (int)Math.Min(length - first, ((withinBlock + count + BlockBytes - 1) / BlockBytes) * BlockBytes);
                byte[] buffer = new byte[alignedBytes];
                file.Position = first;
                ReadExactly(file, buffer, alignedBytes);
                using (var sha = SHA256.Create())
                {
                    for (int start = 0; start < alignedBytes; start += BlockBytes)
                    {
                        int size = Math.Min(BlockBytes, alignedBytes - start);
                        byte[] actual = sha.ComputeHash(buffer, start, size);
                        store.ExportHashBytes += size;
                        if (!actual.SequenceEqual(index.Blocks[(first + start) / BlockBytes]))
                            throw new InvalidDataException("Cached export integrity mismatch.");
                    }
                }
                string text = null;
                for (int trim = 0; trim <= 3 && count - trim >= 0; trim++)
                {
                    try
                    {
                        text = Utf8.GetString(buffer, withinBlock, count - trim);
                        count -= trim;
                        break;
                    }
                    catch (DecoderFallbackException) { }
                }
                if (text == null || count == 0 && offset < length)
                    throw new InvalidDataException("corrupt");
                bool more = offset + count < length;
                return new JObject
                {
                    ["content"] = text,
                    ["generation"] = descriptor["generation"],
                    ["sha256"] = index.Sha256,
                    ["total_bytes"] = length,
                    ["mime_type"] = format == "json" ? "application/json" : "application/xml",
                    ["has_more"] = more,
                    ["next_cursor"] = more ? identity + (offset + count) : null,
                };
            }
        }

        private static void ReadExactly(Stream stream, byte[] buffer, int count)
        {
            for (int offset = 0; offset < count; )
            {
                int read = stream.Read(buffer, offset, count - offset);
                if (read == 0)
                    throw new EndOfStreamException();
                offset += read;
            }
        }

        private static BlockIndex BuildIndex(TestJobResultStore store, FileStream file, string identity)
        {
            long length = file.Length;
            if (length > TestJobResultStore.JobLimit)
                throw new InvalidDataException("Invalid export size.");
            file.Position = 0;
            var blocks = new byte[(length + BlockBytes - 1) / BlockBytes][];
            byte[] buffer = new byte[BlockBytes];
            using var whole = SHA256.Create();
            using var block = SHA256.Create();
            for (int i = 0; i < blocks.Length; i++)
            {
                int count = (int)Math.Min(BlockBytes, length - (long)i * BlockBytes);
                ReadExactly(file, buffer, count);
                whole.TransformBlock(buffer, 0, count, null, 0);
                blocks[i] = block.ComputeHash(buffer, 0, count);
                store.ExportHashBytes += 2L * count;
            }
            whole.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            return new BlockIndex(identity, string.Concat(whole.Hash.Select(value => value.ToString("x2"))), blocks, length);
        }

        private static BlockIndex Generate(
            TestJobResultStore store,
            string jobId,
            JObject descriptor,
            JObject manifest,
            string format,
            string path,
            string indexIdentity
        )
        {
            // Admit the complete owned generation before creating a new temporary export.
            long remaining = store.RemainingExportBudget(jobId, descriptor);
            string temporary = Path.Combine(Path.GetDirectoryName(path), "." + Guid.NewGuid().ToString("N") + ".tmp");
            store.CheckPath(temporary);
            try
            {
                BlockIndex index;
                using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None))
                using (var bounded = new BoundedWrite(file, remaining, store.ObserveExportTemporaryBytes))
                using (var text = new StreamWriter(bounded, Utf8, 8192))
                {
                    if (format == "json")
                    {
                        using var writer = new JsonTextWriter(text) { CloseOutput = false };
                        writer.WriteStartObject();
                        foreach (string key in new[] { "version", "job_id", "mode", "completed_unix_ms", "summary" })
                        {
                            writer.WritePropertyName(key);
                            manifest[key].WriteTo(writer);
                        }
                        foreach (string key in new[] { "status", "started_unix_ms", "finished_unix_ms", "generation", "truncated_result_count" })
                        {
                            writer.WritePropertyName(key);
                            (descriptor[key] ?? JValue.CreateNull()).WriteTo(writer);
                        }
                        writer.WritePropertyName("completion_kind");
                        writer.WriteValue("completed");
                        writer.WritePropertyName("result_availability");
                        writer.WriteValue("available");
                        writer.WritePropertyName("results");
                        writer.WriteStartArray();
                        foreach (JObject row in store.Rows(jobId, descriptor))
                            row.WriteTo(writer);
                        writer.WriteEndArray();
                        writer.WriteEndObject();
                    }
                    else
                    {
                        using var writer = XmlWriter.Create(text, new XmlWriterSettings { CloseOutput = false, Indent = false });
                        writer.WriteStartDocument();
                        writer.WriteStartElement("testsuite");
                        writer.WriteAttributeString("name", "Unity " + (string)manifest["mode"]);
                        writer.WriteAttributeString(
                            "tests",
                            (
                                (int)manifest["count"]
                                + (
                                    (int)manifest["summary"]["failed"] > (int)manifest["state_counts"]["failed"] + (int)manifest["state_counts"]["error"]
                                        ? 1
                                        : 0
                                )
                            ).ToString(System.Globalization.CultureInfo.InvariantCulture)
                        );
                        JObject states = (JObject)manifest["state_counts"];
                        bool missingFailure = (int)manifest["summary"]["failed"] > (int)states["failed"] + (int)states["error"];
                        writer.WriteAttributeString("failures", ((int)states["failed"]).ToString(System.Globalization.CultureInfo.InvariantCulture));
                        writer.WriteAttributeString(
                            "errors",
                            ((int)states["error"] + (missingFailure ? 1 : 0)).ToString(System.Globalization.CultureInfo.InvariantCulture)
                        );
                        writer.WriteAttributeString("skipped", ((int)states["skipped"]).ToString(System.Globalization.CultureInfo.InvariantCulture));
                        writer.WriteAttributeString("time", Duration((double)manifest["summary"]["durationSeconds"]));
                        writer.WriteStartElement("properties");
                        double duration = (double)manifest["summary"]["durationSeconds"];
                        writer.WriteStartElement("property");
                        writer.WriteAttributeString("name", "xml_duration_sanitized");
                        writer.WriteAttributeString("value", double.IsNaN(duration) || double.IsInfinity(duration) || duration < 0 ? "true" : "false");
                        writer.WriteEndElement();
                        foreach (var property in ((JObject)manifest["summary"]).Properties())
                        {
                            writer.WriteStartElement("property");
                            writer.WriteAttributeString("name", "unity_summary_" + property.Name);
                            writer.WriteAttributeString("value", XmlText(property.Value.ToString()));
                            writer.WriteEndElement();
                        }
                        writer.WriteEndElement();
                        foreach (JObject row in store.Rows(jobId, descriptor))
                            WriteCase(writer, row);
                        if (missingFailure)
                        {
                            writer.WriteStartElement("testcase");
                            writer.WriteAttributeString("name", "unity_run_evidence");
                            writer.WriteStartElement("error");
                            writer.WriteString("Authoritative Unity summary reports failures without corresponding captured leaf failure evidence.");
                            writer.WriteEndElement();
                            writer.WriteEndElement();
                        }
                        writer.WriteEndElement();
                        writer.WriteEndDocument();
                    }
                    text.Flush();
                    index = BuildIndex(store, file, indexIdentity);
                }
                store.ValidateExportBudget(jobId, descriptor, new FileInfo(store.CheckPath(temporary)).Length);
                store.CheckPath(path);
                File.Move(store.CheckPath(temporary), path);
                return index;
            }
            finally
            {
                if (File.Exists(store.CheckPath(temporary)))
                    File.Delete(store.CheckPath(temporary));
            }
        }

        private static string Duration(double value) =>
            double.IsNaN(value) || double.IsInfinity(value) || value < 0 ? "0" : value.ToString("R", System.Globalization.CultureInfo.InvariantCulture);

        private static string XmlText(string value)
        {
            var text = new StringBuilder();
            value ??= "";
            for (int index = 0; index < value.Length; index++)
            {
                char character = value[index];
                if (char.IsHighSurrogate(character) && index + 1 < value.Length && char.IsLowSurrogate(value[index + 1]))
                {
                    text.Append(character);
                    text.Append(value[++index]);
                }
                else if (XmlConvert.IsXmlChar(character))
                    text.Append(character);
            }
            return text.ToString();
        }

        private static void WriteCase(XmlWriter writer, JObject row)
        {
            writer.WriteStartElement("testcase");
            writer.WriteAttributeString("name", XmlText((string)row["fullName"]));
            writer.WriteAttributeString("time", ((double)row["durationSeconds"]).ToString("R", System.Globalization.CultureInfo.InvariantCulture));
            string state = (string)row["state"];
            string normalized = state?.ToLowerInvariant();
            string element =
                normalized == "passed" ? null
                : normalized == "failed" ? "failure"
                : normalized == "skipped" || normalized == "ignored" || normalized == "inconclusive" ? "skipped"
                : "error";
            writer.WriteStartElement("properties");
            writer.WriteStartElement("property");
            writer.WriteAttributeString("name", "original_state");
            writer.WriteAttributeString("value", XmlText(state));
            writer.WriteEndElement();
            int removed = new[] { "message", "stackTrace", "output", "fullName" }.Sum(key =>
                ((string)row[key] ?? "").Length - XmlText((string)row[key]).Length
            );
            writer.WriteStartElement("property");
            writer.WriteAttributeString("name", "xml_sanitized_characters");
            writer.WriteAttributeString("value", removed.ToString(System.Globalization.CultureInfo.InvariantCulture));
            writer.WriteEndElement();
            writer.WriteEndElement();
            if (element != null)
            {
                writer.WriteStartElement(element);
                writer.WriteAttributeString("message", XmlText((string)row["message"]));
                writer.WriteString(XmlText((string)row["message"]) + "\n" + XmlText((string)row["stackTrace"]));
                writer.WriteEndElement();
            }
            writer.WriteElementString("system-out", XmlText((string)row["output"]));
            writer.WriteEndElement();
        }

        private sealed class BoundedWrite : Stream
        {
            private readonly Stream output;
            private long length;
            private readonly long limit;
            private readonly Action<long> observe;

            public BoundedWrite(Stream output, long limit, Action<long> observe)
            {
                this.output = output;
                this.limit = limit;
                this.observe = observe;
            }

            public override void Write(byte[] buffer, int offset, int count)
            {
                if (length + count > limit)
                    throw new InvalidDataException("quota_exceeded");
                output.Write(buffer, offset, count);
                length += count;
                observe(output.Length);
            }

            public override void Flush() => output.Flush();

            public override bool CanRead => false;
            public override bool CanSeek => false;
            public override bool CanWrite => true;
            public override long Length => length;
            public override long Position
            {
                get => length;
                set => throw new NotSupportedException();
            }

            public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();

            public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

            public override void SetLength(long value) => throw new NotSupportedException();
        }
    }
}
