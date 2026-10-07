using System;
using System.Diagnostics;
using System.Reflection;
using MCPForUnity.Editor.Services.Transport.Transports;

// Separate from functional regressions. Run only in the parent's exclusive CPU slot.
// No publisher is installed in stdio's update callback; WebSocket/publisher rows use
// that lane's harness. Delegate/reflection controls below stay identical across pins.
internal static class IdleQueueBenchmarkHarness
{
    private const BindingFlags PrivateStatic = BindingFlags.Static | BindingFlags.NonPublic;
    private static void EmptyControl() { }
    public static int Main(string[] args)
    {
        if (args.Length != 3) throw new ArgumentException("label iterations samples required");
        string label = args[0];
        int iterations = int.Parse(args[1]), samples = int.Parse(args[2]);
        if (iterations < 1 || samples < 1) throw new ArgumentException("positive iteration/sample counts required");
        Type host = typeof(StdioBridgeHost);
        host.GetField("isRunning", PrivateStatic).SetValue(null, true);
        host.GetField("ownedEndpoint", PrivateStatic).SetValue(null, true);
        host.GetField("nextHeartbeatAt", PrivateStatic).SetValue(null, double.MaxValue);
        var pump = host.GetMethod("ProcessCommands", PrivateStatic);
        var control = typeof(IdleQueueBenchmarkHarness).GetMethod("EmptyControl", PrivateStatic);
        var allocatedMethod = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", BindingFlags.Static | BindingFlags.Public);
        if (allocatedMethod == null) throw new NotSupportedException("Mono current-thread allocation counter unavailable");
        var allocated = (Func<long>)Delegate.CreateDelegate(typeof(Func<long>), allocatedMethod);
        var pumpDelegate = (Action)Delegate.CreateDelegate(typeof(Action), pump);
        var controlDelegate = (Action)Delegate.CreateDelegate(typeof(Action), control);
        Action[] rows = { controlDelegate, pumpDelegate, () => control.Invoke(null, null), () => pump.Invoke(null, null) };
        string[] names = { "delegate_control", "stdio_empty_delegate", "reflection_control", "stdio_empty_reflection" };
        for (int row = 0; row < rows.Length; row++)
        {
            Action tick = rows[row];
            for (int warmup = 0; warmup < 10000; warmup++) tick();
            for (int sample = 0; sample < samples; sample++)
            {
                GC.Collect();
                GC.WaitForPendingFinalizers();
                GC.Collect();
                var clock = new Stopwatch();
                long before = allocated();
                clock.Start();
                for (int iteration = 0; iteration < iterations; iteration++) tick();
                clock.Stop();
                long bytes = allocated() - before;
                Console.WriteLine(label + "," + names[row] + "," + sample + "," + iterations + "," + bytes + "," +
                    clock.Elapsed.TotalMilliseconds.ToString("R", System.Globalization.CultureInfo.InvariantCulture));
            }
        }
        return 0;
    }
}
