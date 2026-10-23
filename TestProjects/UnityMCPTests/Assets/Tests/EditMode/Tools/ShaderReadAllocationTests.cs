using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ShaderReadAllocationTests
    {
        [TestCase(false)]
        [TestCase(true)]
        public void MeasureLargeSourceRead(bool unicode)
        {
            string directory = "Assets/__McpShaderReadMeasure_" + Guid.NewGuid().ToString("N");
            string full = Path.Combine(Application.dataPath, directory.Substring(7));
            Directory.CreateDirectory(full);
            string path = Path.Combine(full, "Measured.shader");
            try
            {
                string block = unicode ? string.Concat(Enumerable.Repeat("// 한글 😀\n", 4096)) : new string('x', 65536);
                byte[] bytes = Encoding.UTF8.GetBytes(block);
                using (var stream = File.Create(path))
                    while (stream.Length < 16 * 1024 * 1024)
                        stream.Write(bytes, 0, bytes.Length);
                var request = new JObject
                {
                    ["action"] = "read",
                    ["name"] = "Measured",
                    ["path"] = directory,
                    ["readEncoding"] = "text",
                };
                var counterMethod = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", Type.EmptyTypes);
                Func<long> counter = counterMethod == null ? null : (Func<long>)Delegate.CreateDelegate(typeof(Func<long>), counterMethod);
                long calibration = counter?.Invoke() ?? 0;
                byte[] calibrationBuffer = new byte[65536];
                bool available = counter != null && counter() - calibration >= calibrationBuffer.Length;
                GC.KeepAlive(calibrationBuffer);
                Assert.That(ManageShader.HandleCommand(request), Is.TypeOf<SuccessResponse>());
                var times = new double[5];
                var allocated = new long[5];
                long payloadBytes = 0;
                for (int i = 0; i < times.Length; i++)
                {
                    long before = available ? counter() : 0;
                    var watch = Stopwatch.StartNew();
                    var response = ManageShader.HandleCommand(request) as SuccessResponse;
                    watch.Stop();
                    times[i] = watch.Elapsed.TotalMilliseconds;
                    allocated[i] = available ? counter() - before : -1;
                    Assert.IsNotNull(response);
                    var data = response.Data;
                    string text = data.GetType().GetProperty("contents")?.GetValue(data) as string;
                    string encoded = data.GetType().GetProperty("encodedContents")?.GetValue(data) as string;
                    payloadBytes = 2L * ((text?.Length ?? 0) + (encoded?.Length ?? 0));
                    Assert.That(text, Is.Not.Empty);
                    GC.KeepAlive(response);
                }
                Array.Sort(times);
                Array.Sort(allocated);
                long traversedChars = 0;
                int pages = 0;
                var traversal = Stopwatch.StartNew();
                while (true)
                {
                    var response = ManageShader.HandleCommand(request) as SuccessResponse;
                    Assert.IsNotNull(response);
                    object data = response.Data;
                    traversedChars += ((string)data.GetType().GetProperty("contents").GetValue(data)).Length;
                    pages++;
                    object next = data.GetType().GetProperty("nextOffset")?.GetValue(data);
                    if (next == null)
                        break;
                    Assert.That(pages, Is.LessThan(10000));
                    request["readOffset"] = (long)next;
                }
                traversal.Stop();
                Assert.That(traversedChars, Is.EqualTo(new FileInfo(path).Length / bytes.Length * block.Length));
                request["readOffset"] = 0;
                request["readLimit"] = 1048576;
                long bulkChars = 0;
                int bulkPages = 0;
                var bulk = Stopwatch.StartNew();
                while (true)
                {
                    var response = ManageShader.HandleCommand(request) as SuccessResponse;
                    Assert.IsNotNull(response);
                    object data = response.Data;
                    bulkChars += ((string)data.GetType().GetProperty("contents").GetValue(data)).Length;
                    bulkPages++;
                    object next = data.GetType().GetProperty("nextOffset")?.GetValue(data);
                    if (next == null)
                        break;
                    Assert.That(bulkPages, Is.LessThan(1000));
                    request["readOffset"] = (long)next;
                }
                bulk.Stop();
                Assert.That(bulkChars, Is.EqualTo(traversedChars));
                TestContext.WriteLine(
                    "SHADER_READ_MEASURE "
                        + new JObject
                        {
                            ["unicode"] = unicode,
                            ["file_bytes"] = new FileInfo(path).Length,
                            ["median_ms"] = times[2],
                            ["median_thread_allocated_bytes"] = allocated[2],
                            ["allocation_counter_calibrated"] = available,
                            ["returned_string_utf16_bytes"] = payloadBytes,
                            ["runs"] = times.Length,
                            ["full_traversal_ms"] = traversal.Elapsed.TotalMilliseconds,
                            ["full_traversal_pages"] = pages,
                            ["full_traversal_characters"] = traversedChars,
                            ["bulk_traversal_ms"] = bulk.Elapsed.TotalMilliseconds,
                            ["bulk_traversal_pages"] = bulkPages,
                        }.ToString(Newtonsoft.Json.Formatting.None)
                );
            }
            finally
            {
                Assert.That(Path.GetFullPath(full), Does.StartWith(Path.GetFullPath(Application.dataPath) + Path.DirectorySeparatorChar));
                File.Delete(path);
                Directory.Delete(full);
            }
        }
    }
}
