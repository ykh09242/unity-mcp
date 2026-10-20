using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class WebSocketMessageReaderTests
    {
        private sealed class Socket : WebSocket
        {
            internal int Remaining;
            internal int Fragment = 8192;
            internal Action OnReceive;
            internal WebSocketMessageType Type = WebSocketMessageType.Text;
            public override WebSocketCloseStatus? CloseStatus => null;
            public override string CloseStatusDescription => "closed";
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
                token.ThrowIfCancellationRequested();
                int count = Math.Min(Remaining, Math.Min(Fragment, buffer.Count));
                for (int i = 0; i < count; i++)
                    buffer.Array[buffer.Offset + i] = (byte)'x';
                Remaining -= count;
                OnReceive?.Invoke();
                return Task.FromResult(new WebSocketReceiveResult(count, Type, Remaining == 0));
            }
        }

        [Test]
        public void OversizedFragmentedMessageIsRejectedBeforeCompletion()
        {
            using var reader = new WebSocketMessageReader();
            using var socket = new Socket { Remaining = 9 * 1024 * 1024 };
            Assert.ThrowsAsync<InvalidOperationException>(async () => await reader.ReadAsync(socket, CancellationToken.None));
            Assert.Greater(socket.Remaining, 0);
        }

        [Test]
        public async Task CancellationNeverReturnsPartialText()
        {
            using var reader = new WebSocketMessageReader();
            using var cancellation = new CancellationTokenSource();
            using var socket = new Socket
            {
                Remaining = 100,
                Fragment = 10,
                OnReceive = cancellation.Cancel,
            };
            try
            {
                await reader.ReadAsync(socket, cancellation.Token);
                Assert.Fail("Cancellation must not return an incomplete JSON message.");
            }
            catch (OperationCanceledException error)
            {
                Assert.AreEqual(cancellation.Token, error.CancellationToken);
            }
        }

        [Test]
        public async Task RepeatedMessagesHaveNoPreviousMessageBytes()
        {
            using var reader = new WebSocketMessageReader();
            using var socket = new Socket { Remaining = 100000 };
            Assert.AreEqual(new string('x', 100000), await reader.ReadAsync(socket, CancellationToken.None));
            socket.Remaining = 3;
            Assert.AreEqual("xxx", await reader.ReadAsync(socket, CancellationToken.None));
            socket.Remaining = 0;
            Assert.AreEqual("", await reader.ReadAsync(socket, CancellationToken.None));
        }

        [Test]
        public void BinaryMessagesCannotReachTheJsonParser()
        {
            using var reader = new WebSocketMessageReader();
            using var socket = new Socket { Remaining = 10, Type = WebSocketMessageType.Binary };
            Assert.ThrowsAsync<InvalidOperationException>(async () => await reader.ReadAsync(socket, CancellationToken.None));
        }
    }
}
