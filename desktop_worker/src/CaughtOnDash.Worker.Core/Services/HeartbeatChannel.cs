using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// Sends heartbeats over a persistent WebSocket instead of a POST per beat.
    ///
    /// The connection itself is the liveness signal: the backend knows this
    /// worker died when the socket closes, rather than waiting out the stale
    /// window. That is the whole reason this exists.
    ///
    /// It is strictly an optimisation. Every method reports failure rather than
    /// throwing, and the caller falls back to the HTTP heartbeat -- so a worker
    /// behind a proxy that blocks WebSockets, or pointed at a backend still
    /// served by WSGI, degrades to slower liveness instead of appearing dead.
    /// </summary>
    public class HeartbeatChannel : IDisposable
    {
        private readonly string _socketUrl;
        private readonly string _apiToken;

        private ClientWebSocket? _socket;
        private bool _unauthorized;

        // Reconnect backoff, so a backend that will never accept the socket is
        // not hammered once every heartbeat.
        private DateTime _nextAttempt = DateTime.MinValue;
        private int _failures;

        public HeartbeatChannel(string backendUrl, string apiToken)
        {
            _socketUrl = ToWebSocketUrl(backendUrl);
            _apiToken = apiToken;
        }

        public bool IsConnected => _socket?.State == WebSocketState.Open;

        /// <summary>
        /// http(s) base URL to the ws(s) heartbeat endpoint. Public so a test
        /// can pin the scheme swap -- getting wss wrong against production
        /// fails in a way that looks like a firewall problem.
        /// </summary>
        public static string ToWebSocketUrl(string backendUrl)
        {
            var trimmed = (backendUrl ?? "").TrimEnd('/');

            if (trimmed.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                return "wss://" + trimmed.Substring(8) + "/ws/worker/";
            if (trimmed.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
                return "ws://" + trimmed.Substring(7) + "/ws/worker/";

            return trimmed + "/ws/worker/";
        }

        /// <summary>
        /// Sends a heartbeat. False means "use HTTP this time" -- never an error.
        /// </summary>
        public async Task<bool> TrySendAsync(
            string workerId, string status, string? jobId, string stage, int progress,
            CancellationToken cancellationToken)
        {
            // A rejected token will be rejected again. Stop trying and let the
            // HTTP path report the failure, where it is already logged clearly.
            if (_unauthorized)
                return false;

            if (!await EnsureConnectedAsync(cancellationToken))
                return false;

            var message = JsonConvert.SerializeObject(new
            {
                type = "heartbeat",
                worker_id = workerId,
                status,
                job_id = jobId,
                stage,
                progress
            });

            try
            {
                var bytes = Encoding.UTF8.GetBytes(message);
                await _socket!.SendAsync(
                    new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, cancellationToken);

                // Wait for the ack rather than trusting the write. A socket can
                // be open while nothing is reading it, and a heartbeat that is
                // silently going nowhere is exactly the failure that once hid
                // the backend rejecting every worker POST.
                return await ReceiveAckAsync(cancellationToken);
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception ex)
            {
                Logger.Log($"Heartbeat socket send failed, falling back to HTTP: {ex.Message}",
                    Logger.LogLevel.Warning);
                Drop();
                return false;
            }
        }

        private async Task<bool> ReceiveAckAsync(CancellationToken cancellationToken)
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(5));

            var buffer = new byte[512];
            try
            {
                var result = await _socket!.ReceiveAsync(new ArraySegment<byte>(buffer), timeout.Token);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    Drop();
                    return false;
                }
                return true;
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                // Open socket, no answer. Treat as dead and let HTTP take over.
                Logger.Log("Heartbeat socket did not acknowledge, falling back to HTTP",
                    Logger.LogLevel.Warning);
                Drop();
                return false;
            }
        }

        private async Task<bool> EnsureConnectedAsync(CancellationToken cancellationToken)
        {
            if (IsConnected)
                return true;

            Drop();

            if (DateTime.UtcNow < _nextAttempt)
                return false;

            var socket = new ClientWebSocket();
            // The worker is a native client, so the token goes in a header
            // rather than the query string and stays out of access logs.
            socket.Options.SetRequestHeader("Authorization", $"Bearer {_apiToken}");

            try
            {
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                timeout.CancelAfter(TimeSpan.FromSeconds(10));

                await socket.ConnectAsync(new Uri(_socketUrl), timeout.Token);

                _socket = socket;
                _failures = 0;
                Logger.Log($"Heartbeat socket connected to {_socketUrl}");
                return true;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                socket.Dispose();
                throw;
            }
            catch (Exception ex)
            {
                socket.Dispose();
                BackOff();

                if (IsUnauthorized(ex))
                {
                    _unauthorized = true;
                    Logger.Log("Heartbeat socket rejected the worker token; staying on HTTP",
                        Logger.LogLevel.Error);
                }
                else if (_failures == 1)
                {
                    // Log the first failure only. A backend without WebSocket
                    // support is a permanent condition, not news every 10s.
                    Logger.Log($"Heartbeat socket unavailable, using HTTP: {ex.Message}",
                        Logger.LogLevel.Warning);
                }

                return false;
            }
        }

        private static bool IsUnauthorized(Exception ex)
        {
            // 4401 is the consumer's application-level close code; a proxy in
            // front may turn the rejection into a plain 401 during the upgrade.
            var text = ex.Message ?? "";
            return text.Contains("401") || text.Contains("4401") || text.Contains("403");
        }

        private void BackOff()
        {
            _failures++;
            var seconds = Math.Min(300, 10 * Math.Pow(2, Math.Min(_failures - 1, 5)));
            _nextAttempt = DateTime.UtcNow.AddSeconds(seconds);
        }

        private void Drop()
        {
            if (_socket == null)
                return;

            try { _socket.Abort(); } catch { /* already gone */ }
            try { _socket.Dispose(); } catch { /* already gone */ }
            _socket = null;
        }

        /// <summary>
        /// Closes cleanly so the backend marks this worker offline immediately
        /// rather than inferring it from silence.
        /// </summary>
        public async Task CloseAsync()
        {
            if (_socket == null)
                return;

            try
            {
                if (_socket.State == WebSocketState.Open)
                {
                    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                    await _socket.CloseAsync(
                        WebSocketCloseStatus.NormalClosure, "worker stopping", timeout.Token);
                }
            }
            catch
            {
                // Shutting down. A failed goodbye is not worth reporting -- the
                // stale window covers it.
            }
            finally
            {
                Drop();
            }
        }

        public void Dispose() => Drop();
    }
}
