using System;
using System.Net.WebSockets;
using System.Threading;
using System.Threading.Tasks;

internal sealed class SocketProbe : WebSocket
{
    internal int Remaining;
    public override WebSocketCloseStatus? CloseStatus => null;
    public override string CloseStatusDescription => null;
    public override WebSocketState State => WebSocketState.Open;
    public override string SubProtocol => null;

    public override void Abort() { }

    public override void Dispose() { }

    public override Task CloseAsync(WebSocketCloseStatus status, string description, CancellationToken token) => Task.CompletedTask;

    public override Task CloseOutputAsync(WebSocketCloseStatus status, string description, CancellationToken token) => Task.CompletedTask;

    public override Task SendAsync(ArraySegment<byte> buffer, WebSocketMessageType type, bool end, CancellationToken token) =>
        throw new NotSupportedException();

    public override Task<WebSocketReceiveResult> ReceiveAsync(ArraySegment<byte> buffer, CancellationToken token)
    {
        int count = Math.Min(Remaining, buffer.Count);
        for (int i = 0; i < count; i++)
            buffer.Array[buffer.Offset + i] = (byte)'x';
        Remaining -= count;
        return Task.FromResult(new WebSocketReceiveResult(count, WebSocketMessageType.Text, Remaining == 0));
    }
}
