using System;
using System.Net.WebSockets;
using System.Threading;
using System.Threading.Tasks;

// Only socket/Editor ownership is substituted. The send, disposal and admission
// methods (including their fields) are copied verbatim from production by the test.
internal sealed class SendDisposalHarness
{
    private ClientWebSocket _socket = new ClientWebSocket();
    private CancellationTokenSource _connectionCts = new CancellationTokenSource();
    private CancellationTokenSource _lifecycleCts;
    private Admission _commandAdmission;
    private Task _forcedCommandSettlement = Task.CompletedTask;
    private Task _stopSettlement = Task.CompletedTask;
    private readonly TaskCompletionSource<bool> _cleanup = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    private int _forceStops;

    // PRODUCTION_MEMBERS

    private sealed class Admission
    {
        public CancellationToken Token;
    }

    private static class McpLog
    {
        public static void Warn(string message) => Console.Error.WriteLine(message);
    }

    private void ForceStop()
    {
        _forceStops++;
        _socket.Dispose();
        _socket = null;
        _connectionCts = null;
        _forcedCommandSettlement = _cleanup.Task;
    }

    private static async Task Main()
    {
        for (int iteration = 0; iteration < 25; iteration++)
        {
            var client = new SendDisposalHarness();
            using var canceled = new CancellationTokenSource();
            var owner = client._connectionCts.Token;
            await client._sendLock.WaitAsync();
            var queued = client.SendOwnedFrameAsync(new ArraySegment<byte>(new byte[1]), WebSocketMessageType.Text, owner, CancellationToken.None);
            var canceling = client.SendOwnedFrameAsync(new ArraySegment<byte>(new byte[1]), WebSocketMessageType.Text, owner, canceled.Token);
            if (iteration == 0)
            {
                client.Dispose();
                client.Dispose();
            }
            else
                await Task.WhenAll(Task.Run(client.Dispose), Task.Run(client.Dispose));
            if (client._forceStops != 1 || client._cleanup.Task.IsCompleted)
                throw new Exception("Disposal must retire exactly once without waiting for cleanup.");
            await ExpectCancellation(client.SendOwnedFrameAsync(new ArraySegment<byte>(new byte[1]), WebSocketMessageType.Text, owner, CancellationToken.None));
            canceled.Cancel();
            await ExpectCancellation(canceling);
            client._sendLock.Release();
            await ExpectCancellation(queued);
            if (!client._sendLock.Wait(0))
                throw new Exception("Send waiter failed to release the shared semaphore.");
            client._sendLock.Release();
            client._cleanup.SetResult(true);
            var deadline = DateTime.UtcNow.AddSeconds(5);
            bool disposed = false;
            while (DateTime.UtcNow < deadline && !disposed)
            {
                try
                {
                    if (client._sendLock.Wait(0))
                        client._sendLock.Release();
                }
                catch (ObjectDisposedException)
                {
                    disposed = true;
                }
                await Task.Delay(1);
            }
            if (!disposed)
                throw new Exception("Settled disposal must release the shared send resource.");
        }
        Console.WriteLine("PASS queued send disposal (25 cancellation/disposal races)");
    }

    private static async Task ExpectCancellation(Task task)
    {
        try
        {
            await task.WaitAsync(TimeSpan.FromSeconds(5));
        }
        catch (OperationCanceledException)
        {
            return;
        }
        throw new Exception("Expected the retired send to cancel.");
    }
}
