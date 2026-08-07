using System;
using System.Threading.Tasks;
using CaughtOnDash.Worker.Models;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// UI-agnostic worker state and lifecycle.
    ///
    /// This holds everything a host needs to render -- status, current job,
    /// stage, progress, last heartbeat -- and raises events when it changes.
    /// It deliberately knows nothing about WPF, Avalonia or the console, so the
    /// Windows and macOS hosts share one implementation.
    /// </summary>
    public class WorkerSession
    {
        private readonly AppConfigService _configService;
        private readonly WorkerApiClient _apiClient;
        private readonly LocalVideoStorageService _storageService;
        private readonly IAnalyzer _analyzer;
        private readonly WorkerConfig _config;

        private WorkerLoopService? _loop;

        public event Action<WorkerSessionState>? StateChanged;
        public event Action<WorkerLogEntry>? LogAppended;

        public WorkerSession(IAnalyzer? analyzer = null)
        {
            _configService = new AppConfigService();
            _apiClient = new WorkerApiClient();
            _storageService = new LocalVideoStorageService();
            _config = _configService.LoadConfig();
            _analyzer = analyzer ?? CreateAnalyzer(_config);

            State = new WorkerSessionState
            {
                BackendUrl = _config.IsConfigured ? _config.BackendUrl : "Not configured",
                Status = _config.IsConfigured ? "Stopped" : "Missing config",
                IsConfigured = _config.IsConfigured,
                CanStart = _config.IsConfigured,
                CanStop = false,
                CanCancelJob = false,
            };

            if (_config.IsConfigured)
            {
                _apiClient.Initialize(_config.BackendUrl, _config.ApiToken);
            }
        }

        /// <summary>
        /// Pick the analyzer named in config, defaulting to the placeholder.
        /// </summary>
        /// <remarks>
        /// An unrecognised name falls back rather than throwing: a typo should
        /// leave a working worker with a loud log line, not a dead one.
        /// </remarks>
        private static IAnalyzer CreateAnalyzer(WorkerConfig config)
        {
            var name = (config.Analyzer ?? "").Trim().ToLowerInvariant();

            switch (name)
            {
                case "python":
                    Logger.Log($"Using the Python analyzer ({config.PythonExecutable})");
                    return new PythonAnalyzer(config);

                case "placeholder":
                case "":
                    Logger.Log("Using the placeholder analyzer -- results are not real");
                    return new PlaceholderAnalyzer();

                default:
                    Logger.Log(
                        $"Unknown analyzer '{config.Analyzer}'; falling back to the placeholder.",
                        Logger.LogLevel.Warning);
                    return new PlaceholderAnalyzer();
            }
        }

        public WorkerSessionState State { get; private set; }

        public bool IsConfigured => _config.IsConfigured;

        public bool IsRunning => _loop?.IsRunning ?? false;

        public void Log(string message, Logger.LogLevel level = Logger.LogLevel.Info)
        {
            Logger.Log(message, level);
            LogAppended?.Invoke(new WorkerLogEntry
            {
                Timestamp = DateTime.Now,
                Message = message,
                Level = level,
            });
        }

        public async Task StartAsync()
        {
            if (!_config.IsConfigured)
            {
                Log("Worker not configured. Check appsettings.json.", Logger.LogLevel.Error);
                return;
            }

            if (IsRunning)
            {
                Log("Worker is already running.");
                return;
            }

            try
            {
                _loop = new WorkerLoopService(_config, _apiClient, _analyzer, _storageService);
                _loop.OnStatusUpdate += HandleLoopEvent;

                Mutate(state =>
                {
                    state.CanStart = false;
                    state.CanStop = true;
                });

                Log($"Connected to backend: {_config.BackendUrl}");
                Log("Starting worker...");

                await _loop.StartAsync();
            }
            catch (Exception ex)
            {
                Log($"Failed to start worker: {ex.Message}", Logger.LogLevel.Error);
                Mutate(state =>
                {
                    state.Status = "Error";
                    state.CanStart = true;
                    state.CanStop = false;
                });
            }
        }

        public async Task StopAsync()
        {
            try
            {
                Log("Stopping worker...");

                if (_loop != null)
                {
                    await _loop.StopAsync();
                    _loop.OnStatusUpdate -= HandleLoopEvent;
                    _loop = null;
                }

                Mutate(state =>
                {
                    state.Status = "Stopped";
                    state.Stage = "Idle";
                    state.CurrentJob = "None";
                    state.Progress = 0;
                    state.CanStart = true;
                    state.CanStop = false;
                    state.CanCancelJob = false;
                });

                Log("Worker stopped");
            }
            catch (Exception ex)
            {
                Log($"Error stopping worker: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        public async Task CancelCurrentJobAsync()
        {
            try
            {
                if (_loop != null)
                {
                    Log("Cancelling current job...");
                    await _loop.CancelCurrentJobAsync();
                }
            }
            catch (Exception ex)
            {
                Log($"Error cancelling job: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        private void HandleLoopEvent(WorkerLoopEvent evt)
        {
            Mutate(state =>
            {
                switch (evt.Status)
                {
                    case "idle":
                        state.Status = "Idle";
                        state.Stage = "Idle";
                        state.CurrentJob = "None";
                        // Clear the bar too. Without this it keeps the last
                        // job's value, so an idle worker sat at whatever
                        // percentage the previous job stopped reporting at.
                        state.Progress = 0;
                        state.CanCancelJob = false;
                        break;

                    case "processing":
                        state.Status = "Processing";
                        if (evt.JobTitle != null)
                        {
                            state.CurrentJob = evt.JobTitle;
                        }
                        if (evt.Stage != null)
                        {
                            state.Stage = evt.Stage;
                        }
                        state.Progress = evt.Progress;
                        state.CanCancelJob = true;
                        break;

                    case "error":
                        state.Status = "Error";
                        break;

                    case "stopped":
                        state.Status = "Stopped";
                        state.Progress = 0;
                        state.CanCancelJob = false;
                        break;
                }

                state.LastHeartbeat = DateTime.Now;
            });

            if (!string.IsNullOrWhiteSpace(evt.Message))
            {
                Log(evt.Message);
            }
        }

        private void Mutate(Action<WorkerSessionState> mutation)
        {
            var next = State.Clone();
            mutation(next);
            State = next;
            StateChanged?.Invoke(next);
        }
    }

    public class WorkerSessionState
    {
        public string BackendUrl { get; set; } = "Not configured";
        public string Status { get; set; } = "Stopped";
        public string Stage { get; set; } = "Idle";
        public string CurrentJob { get; set; } = "None";
        public int Progress { get; set; }
        public DateTime? LastHeartbeat { get; set; }
        public bool IsConfigured { get; set; }
        public bool CanStart { get; set; }
        public bool CanStop { get; set; }
        public bool CanCancelJob { get; set; }

        public string LastHeartbeatDisplay =>
            LastHeartbeat.HasValue ? LastHeartbeat.Value.ToString("HH:mm:ss") : "Never";

        public string ProgressDisplay => $"{Progress}%";

        public WorkerSessionState Clone() => (WorkerSessionState)MemberwiseClone();
    }

    public class WorkerLogEntry
    {
        public DateTime Timestamp { get; set; }
        public string Message { get; set; } = "";
        public Logger.LogLevel Level { get; set; }

        public string Display => $"[{Timestamp:HH:mm:ss}] {Message}";
    }
}
