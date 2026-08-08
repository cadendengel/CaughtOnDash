using CaughtOnDash.Worker.Services;
using Xunit;

namespace CaughtOnDash.Worker.Core.Tests
{
    /// <summary>
    /// The scheme swap is the part worth pinning. A heartbeat socket built with
    /// the wrong scheme fails at connect time and looks exactly like a firewall
    /// blocking WebSockets, so the worker would quietly sit on HTTP forever
    /// with nothing obviously broken.
    /// </summary>
    public class HeartbeatChannelTests
    {
        [Theory]
        [InlineData("https://api.example.com", "wss://api.example.com/ws/worker/")]
        [InlineData("http://127.0.0.1:8000", "ws://127.0.0.1:8000/ws/worker/")]
        [InlineData("https://api.example.com/", "wss://api.example.com/ws/worker/")]
        [InlineData("HTTPS://Api.Example.com", "wss://Api.Example.com/ws/worker/")]
        public void MapsHttpSchemesToWebSocketSchemes(string backend, string expected)
        {
            Assert.Equal(expected, HeartbeatChannel.ToWebSocketUrl(backend));
        }

        [Fact]
        public void LeavesAnAlreadySchemelessUrlAlone()
        {
            // Misconfiguration rather than a supported form; it must not crash
            // the heartbeat loop, only fail to connect.
            Assert.Equal("api.example.com/ws/worker/", HeartbeatChannel.ToWebSocketUrl("api.example.com"));
        }

        [Fact]
        public void AnEmptyBackendDoesNotThrow()
        {
            Assert.Equal("/ws/worker/", HeartbeatChannel.ToWebSocketUrl(""));
        }
    }
}
