using System;
using System.Diagnostics;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;

internal static class CodecHarness
{
    private static FileStream _trace;
    private static long _wireBytes;
    private static int _frames;
    private static ClientWebSocket _socket;
    private static int _rate;

    private static async Task SendAsync(ArraySegment<byte> value, WebSocketMessageType kind, CancellationToken token)
    {
        _wireBytes += value.Count;
        _frames++;
        if (_socket != null)
        {
            if (_rate > 0) await Task.Delay(TimeSpan.FromSeconds((double)value.Count / (_rate * 1024)), token);
            await _socket.SendAsync(value, kind, true, token);
        }
        if (_trace == null) return;
        byte[] header = { (byte)(kind == WebSocketMessageType.Binary ? 1 : 0),
            (byte)(value.Count >> 24), (byte)(value.Count >> 16), (byte)(value.Count >> 8), (byte)value.Count };
        await _trace.WriteAsync(header, 0, header.Length, token);
        await _trace.WriteAsync(value.Array, value.Offset, value.Count, token);
    }

    public static async Task<int> Main(string[] args)
    {
        byte[] source = File.ReadAllBytes(args[0]);
        string text = Encoding.UTF8.GetString(source);
        string mode = args.Length > 2 ? args[2] : "bytes";
        if (mode.StartsWith("edges"))
            text = new string('a', 16383) + "\ud83d\ude00|\ud800|\udfff|" + new string('한', 500000);
        var times = new double[7];
        var allocations = new long[7];
        long wire = 0;
        int frames = 0;
        for (int iteration = -1; iteration < 7; iteration++)
        {
            _wireBytes = 0;
            _frames = 0;
            GC.Collect();
            long before = GC.GetTotalAllocatedBytes(true);
            var watch = Stopwatch.StartNew();
            await WriteAsync(source, text, mode);
            watch.Stop();
            if (iteration >= 0)
            {
                times[iteration] = watch.Elapsed.TotalMilliseconds;
                allocations[iteration] = GC.GetTotalAllocatedBytes(true) - before;
                wire = _wireBytes;
                frames = _frames;
            }
        }
        double wireTime;
        using (var socket = new ClientWebSocket())
        {
            if (args.Length > 3)
            {
                _socket = socket;
                _rate = int.Parse(args[4]);
                await socket.ConnectAsync(new Uri(args[3]), CancellationToken.None);
            }
            var watch = Stopwatch.StartNew();
            using (_trace = new FileStream(args[1], FileMode.Create, FileAccess.Write))
                await WriteAsync(source, text, mode);
            watch.Stop();
            wireTime = watch.Elapsed.TotalMilliseconds;
            if (_socket != null)
                await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "owned test", CancellationToken.None);
        }
        Console.WriteLine(JsonSerializer.Serialize(new { bytes = source.Length, wire_bytes = wire,
            frames, encode_ms = times, allocated_bytes = allocations, owned_wire_ms = wireTime }));
        return 0;
    }

    private static Task WriteAsync(byte[] source, string text, string mode)
    {
        const string id = "01234567-89ab-cdef-0123-456789abcdef";
        if (mode == "bytes_json" || mode == "edges_bytes")
            return LargeResultWriter.SendAsync(id, Encoding.UTF8.GetBytes(text), true, SendAsync, CancellationToken.None);
        if (mode == "bytes")
            return LargeResultWriter.SendAsync(id, source, true, SendAsync, CancellationToken.None);
#if CURRENT_CODEC
        return LargeResultWriter.SendJsonAsync(id, text, mode != "legacy", SendAsync, CancellationToken.None,
            mode == "gzip_json" || mode == "gzip_negotiated_only", mode == "gzip_json" || mode == "gzip_policy_only");
#else
        return LargeResultWriter.SendAsync(id, Encoding.UTF8.GetBytes(text), mode != "legacy", SendAsync, CancellationToken.None);
#endif
    }
}
