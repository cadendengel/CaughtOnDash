using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using CaughtOnDash.Worker.Models;

namespace CaughtOnDash.Worker.Services
{
    public class WorkerLoopService
    {
        private readonly WorkerApiClient _apiClient;
        private readonly IAnalyzer _analyzer;
        private readonly LocalVideoStorageService _storageService;
        private readonly WorkerConfig _config;

        private CancellationTokenSource? _cancellationTokenSource;
        private bool _isRunning;
        private Guid? _currentJobId;
        private TaskCompletionSource<bool>? _stopTcs;

        public event Action<WorkerLoopEvent>? OnStatusUpdate;

        public WorkerLoopService(WorkerConfig config, WorkerApiClient apiClient, IAnalyzer analyzer, LocalVideoStorageService storageService)
        {
            _config = config;
            _apiClient = apiClient;
            _analyzer = analyzer;
            _storageService = storageService;
            _isRunning = false;
        }

        public bool IsRunning => _isRunning;
        public Guid? CurrentJobId => _currentJobId;

        public async Task StartAsync()
        {
            if (_isRunning)
            {
                Logger.Log("Worker already running");
                return;
            }

            _isRunning = true;
            _cancellationTokenSource = new CancellationTokenSource();
            _stopTcs = new TaskCompletionSource<bool>();

            Logger.Log("Worker started");
            OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "idle", Message = "Worker started" });

            await MainLoop(_cancellationTokenSource.Token);
        }

        public async Task StopAsync()
        {
            if (!_isRunning)
            {
                return;
            }

            Logger.Log("Stopping worker...");
            _cancellationTokenSource?.Cancel();
            
            if (_stopTcs != null)
            {
                await _stopTcs.Task;
            }

            _isRunning = false;
            _currentJobId = null;
            OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "stopped", Message = "Worker stopped" });
            Logger.Log("Worker stopped");
        }

        public async Task CancelCurrentJobAsync()
        {
            if (!_currentJobId.HasValue)
            {
                Logger.Log("No job to cancel", Logger.LogLevel.Warning);
                return;
            }

            Logger.Log($"Cancelling job {_currentJobId}...");
            var reason = "Cancelled by user";
            await _apiClient.CancelJob(_currentJobId.Value, _config.WorkerId, reason, _cancellationTokenSource?.Token ?? CancellationToken.None);
            _currentJobId = null;
        }

        private async Task MainLoop(CancellationToken cancellationToken)
        {
            try
            {
                // Heartbeat timer
                var heartbeatTask = HeartbeatLoop(cancellationToken);
                
                // Job polling loop
                var jobLoopTask = JobProcessingLoop(cancellationToken);

                await Task.WhenAll(heartbeatTask, jobLoopTask);
            }
            catch (OperationCanceledException)
            {
                Logger.Log("Worker main loop cancelled");
            }
            catch (Exception ex)
            {
                Logger.Log($"Worker main loop error: {ex.Message}", Logger.LogLevel.Error);
                OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "error", Message = $"Error: {ex.Message}" });
            }
            finally
            {
                _stopTcs?.TrySetResult(true);
            }
        }

        private async Task HeartbeatLoop(CancellationToken cancellationToken)
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                try
                {
                    var status = _currentJobId.HasValue ? "processing" : "idle";
                    var stage = _currentJobId.HasValue ? "analyzing" : "";
                    var progress = 0; // This would be updated by the analyzer

                    await _apiClient.SendHeartbeat(
                        _config.WorkerId,
                        _config.WorkerName,
                        status,
                        _currentJobId?.ToString(),
                        stage,
                        progress,
                        cancellationToken
                    );

                    Logger.Log($"Heartbeat sent (status: {status})");
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "heartbeat", Message = "Heartbeat sent" });

                    await Task.Delay(10000, cancellationToken); // 10 seconds
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    Logger.Log($"Heartbeat error: {ex.Message}", Logger.LogLevel.Error);
                    await Task.Delay(5000, cancellationToken);
                }
            }
        }

        private async Task JobProcessingLoop(CancellationToken cancellationToken)
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                try
                {
                    if (_currentJobId.HasValue)
                    {
                        // Already processing a job
                        await Task.Delay(1000, cancellationToken);
                        continue;
                    }

                    // Poll for next job
                    var job = await _apiClient.GetNextPendingJob(cancellationToken);
                    if (job == null)
                    {
                        await Task.Delay(15000, cancellationToken); // 15 seconds
                        continue;
                    }

                    Logger.Log($"Found pending job: {job.JobId} - {job.Title}");
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "job_found", Message = $"Found job: {job.Title}" });

                    // Try to claim the job
                    var claimed = await _apiClient.ClaimJob(job.JobId, _config.WorkerId, _config.WorkerName, cancellationToken);
                    if (!claimed)
                    {
                        Logger.Log($"Failed to claim job {job.JobId}");
                        OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "claim_failed", Message = "Failed to claim job" });
                        continue;
                    }

                    _currentJobId = job.JobId;
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "processing", Message = "Processing job...", JobId = job.JobId, JobTitle = job.Title });

                    // Process the job
                    await ProcessJob(job, cancellationToken);

                    _currentJobId = null;
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "idle", Message = "Job completed" });
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    Logger.Log($"Job processing loop error: {ex.Message}", Logger.LogLevel.Error);
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "error", Message = ex.Message });
                    _currentJobId = null;
                    await Task.Delay(5000, cancellationToken);
                }
            }
        }

        private async Task ProcessJob(JobDto job, CancellationToken cancellationToken)
        {
            string? downloadedPath = null;

            try
            {
                var progress = new Progress<(string stage, int progressValue)>(report =>
                {
                    Logger.Log($"[{job.Title}] {report.stage}: {report.progressValue}%");
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent
                    {
                        Status = "processing",
                        Message = $"Processing: {report.stage}",
                        JobId = job.JobId,
                        JobTitle = job.Title,
                        Progress = report.progressValue,
                        Stage = report.stage
                    });
                });

                // For MVP, use placeholder video path since we're doing placeholder analysis
                var placeholderPath = Path.Combine(Path.GetTempPath(), $"video_{job.JobId}.mp4");

                // Simulate analysis
                var result = await _analyzer.AnalyzeAsync(placeholderPath, progress, cancellationToken);

                // Submit results
                var success = await _apiClient.CompleteJob(job.JobId, _config.WorkerId, result, cancellationToken);
                if (!success)
                {
                    await _apiClient.FailJob(job.JobId, _config.WorkerId, "Failed to submit analysis results", "uploading_results", cancellationToken);
                }
            }
            catch (OperationCanceledException)
            {
                Logger.Log($"Job {job.JobId} processing cancelled by user");
                await _apiClient.CancelJob(job.JobId, _config.WorkerId, "User cancelled processing", cancellationToken);
            }
            catch (Exception ex)
            {
                Logger.Log($"Job {job.JobId} processing failed: {ex.Message}", Logger.LogLevel.Error);
                await _apiClient.FailJob(job.JobId, _config.WorkerId, ex.Message, "analyzing", cancellationToken);
            }
            finally
            {
                // Clean up downloaded video
                if (downloadedPath != null && File.Exists(downloadedPath))
                {
                    try
                    {
                        File.Delete(downloadedPath);
                    }
                    catch { }
                }
            }
        }
    }

    public class WorkerLoopEvent
    {
        public string Status { get; set; } = "";
        public string Message { get; set; } = "";
        public Guid? JobId { get; set; }
        public string? JobTitle { get; set; }
        public int Progress { get; set; }
        public string? Stage { get; set; }
    }
}
