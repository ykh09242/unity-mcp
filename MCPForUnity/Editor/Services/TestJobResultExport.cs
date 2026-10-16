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
                if (evidence == null)
                {
                    if (File.Exists(store.CheckPath(path)))
                        throw new InvalidDataException("Untrusted cached export.");
                    Generate(store, jobId, descriptor, manifest, format, path);
                }
                store.CheckPath(path);
                using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                if (file.Length > TestJobResultStore.JobLimit || offset > file.Length)
                    throw new InvalidDataException("Invalid export size or cursor.");
                string hash;
                using (var sha = SHA256.Create())
                    hash = string.Concat(sha.ComputeHash(file).Select(value => value.ToString("x2")));
                if (evidence == null)
                {
                    evidence = new JObject
                    {
                        ["sha256"] = hash,
                        ["total_bytes"] = file.Length,
                        ["source_sha256"] = descriptor["sha256"],
                    };
                    if (descriptor["exports"] == null)
                        descriptor["exports"] = new JObject();
                    descriptor["exports"][format] = evidence;
                }
                else if (
                    (string)evidence["sha256"] != hash
                    || (long)evidence["total_bytes"] != file.Length
                    || (string)evidence["source_sha256"] != (string)descriptor["sha256"]
                )
                    throw new InvalidDataException("Cached export integrity mismatch.");
                file.Position = offset;
                byte[] buffer = new byte[65536];
                int count = 0,
                    read;
                while (count < buffer.Length && (read = file.Read(buffer, count, buffer.Length - count)) > 0)
                    count += read;
                string text = null;
                for (int trim = 0; trim <= 3 && count - trim >= 0; trim++)
                {
                    try
                    {
                        text = Utf8.GetString(buffer, 0, count - trim);
                        count -= trim;
                        break;
                    }
                    catch (DecoderFallbackException) { }
                }
                if (text == null || count == 0 && offset < file.Length)
                    throw new InvalidDataException("corrupt");
                bool more = offset + count < file.Length;
                return new JObject
                {
                    ["content"] = text,
                    ["generation"] = descriptor["generation"],
                    ["sha256"] = hash,
                    ["total_bytes"] = file.Length,
                    ["mime_type"] = format == "json" ? "application/json" : "application/xml",
                    ["has_more"] = more,
                    ["next_cursor"] = more ? identity + (offset + count) : null,
                };
            }
        }

        private static void Generate(TestJobResultStore store, string jobId, JObject descriptor, JObject manifest, string format, string path)
        {
            // Admit the complete owned generation before creating a new temporary export.
            long remaining = store.RemainingExportBudget(jobId, descriptor);
            string temporary = Path.Combine(Path.GetDirectoryName(path), "." + Guid.NewGuid().ToString("N") + ".tmp");
            store.CheckPath(temporary);
            try
            {
                using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
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
                }
                store.ValidateExportBudget(jobId, descriptor, new FileInfo(store.CheckPath(temporary)).Length);
                store.CheckPath(path);
                File.Move(store.CheckPath(temporary), path);
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
