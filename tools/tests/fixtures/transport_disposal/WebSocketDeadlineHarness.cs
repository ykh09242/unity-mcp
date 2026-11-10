using System;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using NUnit.Framework;

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    // Only data-only dependencies are substituted. The request, admission marker,
    // deadline methods and complete queue owner are compiled from production source.
    public class WebSocketTransportClient
    {
        private static readonly TimeSpan DefaultCommandTimeout = TimeSpan.FromSeconds(300);

        private class Command { }

        private class CommandPhaseTiming { }

        private class CommandProgressBuffer
        {
            public void Close() { }
        }

        // PRODUCTION_MEMBERS
    }
}

internal static class WebSocketDeadlineHarness
{
    public static int Main()
    {
        int failures = 0;
        Type type = typeof(MCPForUnityTests.Editor.Services.CooperativeCancellationTests);
        foreach (MethodInfo method in type.GetMethods())
        {
            var cases = method.GetCustomAttributes<TestCaseAttribute>().ToArray();
            if (method.GetCustomAttribute<TestAttribute>() == null && cases.Length == 0)
                continue;
            object[][] arguments = cases.Length == 0 ? new[] { Array.Empty<object>() } : cases.Select(item => item.Arguments).ToArray();
            foreach (object[] args in arguments)
            {
                string label = method.Name + "(" + string.Join(",", args) + ")";
                try
                {
                    object result = method.Invoke(Activator.CreateInstance(type), args);
                    if (result is Task task)
                        task.GetAwaiter().GetResult();
                    Console.WriteLine("PASS " + label);
                }
                catch (Exception ex)
                {
                    failures++;
                    Console.Error.WriteLine("FAIL " + label + ": " + ex.GetBaseException());
                }
            }
        }
        if (failures == 0)
            Console.WriteLine("PASS WebSocket deadline ownership");
        return failures == 0 ? 0 : 1;
    }
}
