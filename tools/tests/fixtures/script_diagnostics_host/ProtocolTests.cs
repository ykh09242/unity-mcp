using System.Text;
using NUnit.Framework;
using UnityMcp.ScriptDiagnostics;

namespace ScriptDiagnosticsHost.Tests;

public class ProtocolTests
{
    [TestCase(-1)]
    [TestCase(0)]
    [TestCase(300_000_000)]
    public async Task InvalidLengthIsRejectedBeforeAllocation(int length)
    {
        using var stream = new MemoryStream(BitConverter.GetBytes(length));
        await Assert.ThrowsAsync<InvalidDataException>(async () => await WireProtocol.ReadRequestAsync(stream));
    }

    [Test]
    public async Task PrematureEndIsNotAnEmptyRequest()
    {
        using var stream = new MemoryStream([100, 0, 0, 0, 123]);
        await Assert.ThrowsAsync<EndOfStreamException>(async () => await WireProtocol.ReadRequestAsync(stream));
    }

    [Test]
    public async Task ProtocolMismatchIsExplicit()
    {
        byte[] payload = Encoding.UTF8.GetBytes("{\"Protocol\":2}");
        using var stream = new MemoryStream([.. BitConverter.GetBytes(payload.Length), .. payload]);
        await Assert.ThrowsAsync<InvalidDataException>(async () => await WireProtocol.ReadRequestAsync(stream));
    }
}
