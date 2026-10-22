using System;
using System.IO;
using System.Net.WebSockets;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;

// Runs only synthetic file bytes; no Unity Editor or external sockets are used.
internal static class LargeResultHarness
{
    private static readonly SemaphoreSlim SendLock = new SemaphoreSlim(1, 1);
    private static readonly TaskCompletionSource<bool> FirstChunk = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    private static readonly TaskCompletionSource<bool> ControlQueued = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    private static readonly TaskCompletionSource<bool> SecondChunk = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    private static int _binaryCount;
    private static bool _holdControlCaller;
    private static bool _interleave;
    private static bool _cancel;
    private static CancellationTokenSource _cancellation;
    private static ClientWebSocket _socket;

    private static async Task SendFrameAsync(ArraySegment<byte> bytes, WebSocketMessageType kind, CancellationToken token)
    {
        await SendLock.WaitAsync(token);
        try
        {
            byte[] copy = new byte[bytes.Count];
            Buffer.BlockCopy(bytes.Array, bytes.Offset, copy, 0, bytes.Count);
            if (_socket == null)
                Console.WriteLine((kind == WebSocketMessageType.Binary ? "B" : "T") + "|" + Convert.ToBase64String(copy));
            else
                await _socket.SendAsync(new ArraySegment<byte>(copy), kind, true, token);
            if (kind == WebSocketMessageType.Binary && Interlocked.Increment(ref _binaryCount) == 1)
            {
                if (_interleave)
                {
                    FirstChunk.SetResult(true);
                    await ControlQueued.Task;
                }
                if (_cancel)
                    _cancellation.Cancel();
            }
            else if (kind == WebSocketMessageType.Binary && _binaryCount == 2)
            {
                SecondChunk.TrySetResult(true);
            }
        }
        finally
        {
            SendLock.Release();
        }
    }

    private static async Task SendControlAsync()
    {
        await FirstChunk.Task;
        byte[] pong = System.Text.Encoding.UTF8.GetBytes("{\"type\":\"pong\"}");
        // Start the send synchronously so WaitAsync is queued behind the first chunk
        // before that chunk is allowed to release the send lock.
        Task pendingSend = SendFrameAsync(new ArraySegment<byte>(pong), WebSocketMessageType.Text, CancellationToken.None);
        ControlQueued.SetResult(true);
        // The queued send must progress even while its notifying caller is suspended.
        if (_holdControlCaller)
            await SecondChunk.Task;
        await pendingSend;
    }

    public static async Task<int> Main(string[] args)
    {
        byte[] bytes = File.ReadAllBytes(args[0]);
        string mode = args[1];
        _holdControlCaller = mode == "interleave-held-caller";
        _interleave = mode == "interleave" || _holdControlCaller;
        _cancel = mode == "cancel";
        using (_cancellation = new CancellationTokenSource())
        using (var socket = new ClientWebSocket())
        {
            if (args.Length > 3)
            {
                _socket = socket;
                await socket.ConnectAsync(new Uri(args[3]), _cancellation.Token);
            }
            Task control = _interleave ? SendControlAsync() : Task.CompletedTask;
            try
            {
                await LargeResultWriter.SendAsync(args[2], bytes, mode != "legacy", SendFrameAsync, _cancellation.Token);
                await control;
                if (_socket != null)
                    await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "synthetic transfer complete", _cancellation.Token);
            }
            catch (OperationCanceledException)
            {
                Console.WriteLine("CANCELLED");
            }
        }
        return 0;
    }
}
