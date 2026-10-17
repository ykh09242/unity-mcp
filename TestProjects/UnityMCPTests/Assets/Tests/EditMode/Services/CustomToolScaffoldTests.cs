using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.Compilation;
using UnityEngine.TestTools;

namespace MCPForUnity.Editor.Tests.EditMode.Services
{
    [Parallelizable(ParallelScope.None)]
    public class CustomToolScaffoldTests
    {
        // No generated attributed source enters Assets or startup TypeCache discovery.
        [UnityTest]
        [Explicit("Requires the reviewed external DEV19 generated-source packet and sole-QA selection.")]
        public IEnumerator GeneratedSourcesCompileDiscoverAndExecuteThroughOwnedRegistry()
        {
            string packet = Environment.GetEnvironmentVariable("UNITY_MCP_DEV19_PACKET");
            Assert.IsNotEmpty(packet, "Sole QA must supply the reviewed immutable external-source packet.");
            packet = Path.GetFullPath(packet);
            string assets = Path.GetFullPath(UnityEngine.Application.dataPath);
            Assert.IsFalse(
                string.Equals(packet, assets, StringComparison.OrdinalIgnoreCase)
                    || packet.StartsWith(assets + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            );
            var manifest = JObject.Parse(File.ReadAllText(Path.Combine(packet, "inputs.json")));
            Assert.AreEqual("unity-mcp-dev19@1", (string)manifest["format"]);
            var inputs = (JArray)manifest["files"];
            Assert.AreEqual(4, inputs.Count);
            var sources = new List<string>();
            foreach (JObject input in inputs)
            {
                string relative = (string)input["path"];
                Assert.AreEqual(Path.GetFileName(relative), relative);
                string path = Path.Combine(packet, relative);
                byte[] bytes = File.ReadAllBytes(path);
                Assert.LessOrEqual(bytes.Length, 32768);
                using (var algorithm = SHA256.Create())
                    Assert.AreEqual((string)input["sha256"], BitConverter.ToString(algorithm.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant());
                sources.Add(path);
            }
            string owned = Path.Combine(packet, "compile-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(owned);
            string assemblyPath = Path.Combine(owned, "Dev19Generated.dll");
#pragma warning disable CS0618 // Retain the compilation API available across supported Unity versions.
            var builder = new AssemblyBuilder(assemblyPath, sources.ToArray())
            {
                flags = AssemblyBuilderFlags.EditorAssembly,
                additionalReferences = new[] { typeof(McpForUnityToolAttribute).Assembly.Location, typeof(JObject).Assembly.Location },
            };
#pragma warning restore CS0618
            bool finished = false;
            CompilerMessage[] diagnostics = null;
            builder.buildFinished += (_, messages) =>
            {
                diagnostics = messages;
                finished = true;
            };
            Assert.IsTrue(builder.Build(), "Owned generated-source build was not admitted.");
            var clock = System.Diagnostics.Stopwatch.StartNew();
            while (!finished && clock.Elapsed.TotalSeconds < 60)
                yield return null;
            Assert.IsTrue(finished, "Compiler did not finish within the fixture budget; preserve its packet for QA cleanup.");
            Assert.IsNotNull(diagnostics);
            Assert.IsEmpty(diagnostics.Where(message => message.type == CompilerMessageType.Error).Select(message => message.message).ToArray());
            var assembly = global::System.Reflection.Assembly.Load(File.ReadAllBytes(assemblyPath));
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            var register = typeof(CommandRegistry).GetMethod("RegisterCommandType", BindingFlags.NonPublic | BindingFlags.Static);
            var commandMethod = typeof(CommandRegistry).GetMethod("GetCommandMethod", BindingFlags.NonPublic | BindingFlags.Static);
            var extract = typeof(ToolDiscoveryService).GetMethod("ExtractToolMetadata", BindingFlags.NonPublic | BindingFlags.Instance);
            var outbound = typeof(WebSocketTransportClient).GetMethod("BuildToolsRegistration", BindingFlags.NonPublic | BindingFlags.Static);
            foreach (JObject input in inputs)
            {
                string name = (string)input["tool_name"];
                Assert.IsFalse(handlers.Contains(name), "Fixture must not replace an existing command.");
                var type = assembly.GetType((string)input["type"], true);
                var attribute = type.GetCustomAttribute<McpForUnityToolAttribute>();
                Assert.IsNotNull(commandMethod.Invoke(null, new object[] { type }));
                var metadata = (ToolMetadata)extract.Invoke(new ToolDiscoveryService(), new object[] { type, attribute });
                Assert.IsNotNull(metadata);
                Assert.IsTrue(string.IsNullOrEmpty(metadata.SchemaError), metadata.SchemaError);
                Assert.IsNotNull(metadata.InputSchema);
                Assert.IsNotNull(metadata.OutputDataSchema);
                Assert.AreEqual(1, metadata.SchemaVersion);
                Assert.AreEqual(name, metadata.Name);
                Assert.IsTrue((bool)register.Invoke(null, new object[] { type, false }));
                object ownedHandler = handlers[name];
                try
                {
                    var arguments = new JObject { ["text"] = "owned echo" };
                    var task = CommandRegistry.InvokeCommandAsync(name, arguments, CancellationToken.None);
                    while (!task.IsCompleted && clock.Elapsed.TotalSeconds < 120)
                        yield return null;
                    Assert.IsTrue(task.IsCompleted, "Owned invocation exceeded the fixture budget.");
                    Assert.IsFalse(task.IsFaulted, task.Exception?.ToString());
                    var response = JObject.FromObject(task.GetAwaiter().GetResult());
                    bool polling = (string)input["template"] == "async-polling";
                    JObject start = null;
                    if (polling)
                    {
                        start = response;
                        Assert.AreEqual("pending", (string)response["_mcp_status"]);
                        string job = (string)response["data"]["job_id"];
                        Assert.IsNotEmpty(job);
                        var status = new JObject
                        {
                            ["text"] = "owned echo",
                            ["action"] = "status",
                            ["job_id"] = job,
                        };
                        var busy = CommandRegistry.InvokeCommandAsync(name, arguments);
                        Assert.IsFalse((bool)JObject.FromObject(busy.GetAwaiter().GetResult())["success"]);
                        var stale = CommandRegistry.InvokeCommandAsync(
                            name,
                            new JObject
                            {
                                ["text"] = "owned echo",
                                ["action"] = "status",
                                ["job_id"] = "stale",
                            }
                        );
                        Assert.IsFalse((bool)JObject.FromObject(stale.GetAwaiter().GetResult())["success"]);
                        using (var cancellation = new CancellationTokenSource())
                        {
                            var canceled = CommandRegistry.InvokeCommandAsync(name, status, cancellation.Token);
                            cancellation.Cancel();
                            while (!canceled.IsCompleted && clock.Elapsed.TotalSeconds < 120)
                                yield return null;
                            Assert.IsTrue(canceled.IsCompleted, "Owned canceled invocation did not settle.");
                            Assert.IsTrue(canceled.IsCanceled, canceled.Exception?.ToString());
                        }
                        var completing = CommandRegistry.InvokeCommandAsync(name, status);
                        var concurrent = CommandRegistry.InvokeCommandAsync(name, status);
                        Assert.AreEqual("pending", (string)JObject.FromObject(concurrent.GetAwaiter().GetResult())["_mcp_status"]);
                        while (!completing.IsCompleted && clock.Elapsed.TotalSeconds < 120)
                            yield return null;
                        Assert.IsTrue(completing.IsCompleted, "Owned status invocation did not settle.");
                        response = JObject.FromObject(completing.GetAwaiter().GetResult());
                        var repeated = CommandRegistry.InvokeCommandAsync(name, status);
                        Assert.IsTrue(JToken.DeepEquals(response, JObject.FromObject(repeated.GetAwaiter().GetResult())));
                    }
                    Assert.IsTrue((bool)response["success"]);
                    Assert.AreEqual("owned echo", (string)response["data"]["text"]);
                    Assert.AreEqual(1, (int)response["data"]["count"]);
                    var registration = (JObject)outbound.Invoke(null, new object[] { new List<ToolMetadata> { metadata } });
                    TestContext.WriteLine(
                        "DEV19_SCAFFOLD_REPLAY "
                            + new JObject
                            {
                                ["descriptor"] = registration["tools"][0],
                                ["arguments"] = arguments,
                                ["start"] = start,
                                ["response"] = response,
                            }.ToString(Formatting.None)
                    );
                    // JSON Schema integer includes integral floating JSON numbers.
                    var fractional = CommandRegistry.InvokeCommandAsync(name, new JObject { ["text"] = "owned echo", ["count"] = 1.5 });
                    Assert.IsFalse((bool)JObject.FromObject(fractional.GetAwaiter().GetResult())["success"]);
                    var integralArguments = new JObject
                    {
                        ["text"] = "owned echo",
                        ["count"] = 1.0,
                        ["unknown_owned"] = new JObject { ["keep"] = true },
                    };
                    var originalArguments = integralArguments.DeepClone();
                    var integral = CommandRegistry.InvokeCommandAsync(name, integralArguments);
                    Assert.IsTrue((bool)JObject.FromObject(integral.GetAwaiter().GetResult())["success"]);
                    Assert.IsTrue(JToken.DeepEquals(originalArguments, integralArguments), "normalization must preserve caller arguments");
                    Assert.AreEqual(JTokenType.Float, integralArguments["count"].Type);
                }
                finally
                {
                    if (ReferenceEquals(handlers[name], ownedHandler))
                        handlers.Remove(name);
                }
            }
            // Exact external packet and compiled bytes remain immutable evidence; sole QA owns cleanup.
        }
    }
}
