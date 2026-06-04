using System;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using CaughtOnDash.Worker.Models;
using Newtonsoft.Json;

namespace CaughtOnDash.Worker.Services
{
    public class WorkerApiClient
    {
        private readonly HttpClient _httpClient;
        private string _backendUrl = "";
        private string _apiToken = "";

        public WorkerApiClient()
        {
            _httpClient = new HttpClient();
        }

        public void Initialize(string backendUrl, string apiToken)
        {
            _backendUrl = backendUrl.TrimEnd('/');
            _apiToken = apiToken;
            Logger.Log($"API Client initialized with backend: {_backendUrl}");
        }

        private string GetAuthorizationHeader()
        {
            return $"Bearer {_apiToken}";
        }

        private async Task<T?> SendRequest<T>(string method, string endpoint, object? body = null, CancellationToken cancellationToken = default)
        {
            try
            {
                var url = $"{_backendUrl}{endpoint}";
                var request = new HttpRequestMessage(new HttpMethod(method), url);
                request.Headers.Add("Authorization", GetAuthorizationHeader());

                if (body != null)
                {
                    var json = JsonConvert.SerializeObject(body);
                    request.Content = new StringContent(json, Encoding.UTF8, "application/json");
                }

                var response = await _httpClient.SendAsync(request, cancellationToken);

                if (!response.IsSuccessStatusCode)
                {
                    var errorContent = await response.Content.ReadAsStringAsync(cancellationToken);
                    Logger.Log($"API Error ({response.StatusCode}): {errorContent}", Logger.LogLevel.Error);
                    return default;
                }

                var content = await response.Content.ReadAsStringAsync(cancellationToken);
                var result = JsonConvert.DeserializeObject<T>(content);
                return result;
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to send request to {endpoint}: {ex.Message}", Logger.LogLevel.Error);
                return default;
            }
        }

        public async Task<bool> GetWorkerStatus(CancellationToken cancellationToken = default)
        {
            var result = await SendRequest<WorkerStatus>("GET", "/api/videos/worker/status/", cancellationToken: cancellationToken);
            return result != null;
        }

        public async Task SendHeartbeat(string workerId, string workerName, string status, string? currentJobId = null, string stage = "", int progress = 0, CancellationToken cancellationToken = default)
        {
            var heartbeat = new
            {
                worker_id = workerId,
                worker_name = workerName,
                status = status,
                current_job_id = currentJobId,
                stage = stage,
                progress = progress
            };

            await SendRequest<dynamic>("POST", "/api/videos/worker/heartbeat/", heartbeat, cancellationToken);
        }

        public async Task<JobDto?> GetNextPendingJob(CancellationToken cancellationToken = default)
        {
            try
            {
                var result = await SendRequest<dynamic>("GET", "/api/videos/worker/jobs/next/", cancellationToken: cancellationToken);
                if (result == null)
                    return null;

                var jobData = result["job"];
                if (jobData == null)
                    return null;

                var job = JsonConvert.DeserializeObject<JobDto>(jobData.ToString());
                return job;
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to get next pending job: {ex.Message}", Logger.LogLevel.Error);
                return null;
            }
        }

        public async Task<bool> ClaimJob(Guid jobId, string workerId, string workerName, CancellationToken cancellationToken = default)
        {
            var claimRequest = new
            {
                worker_id = workerId,
                worker_name = workerName
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/claim/", claimRequest, cancellationToken);
            if (result?["success"] == true)
            {
                Logger.Log($"Successfully claimed job {jobId}");
                return true;
            }

            Logger.Log($"Failed to claim job {jobId}: {result?["error"]}", Logger.LogLevel.Error);
            return false;
        }

        public async Task<bool> UpdateJobProgress(Guid jobId, string workerId, string stage, int progress, CancellationToken cancellationToken = default)
        {
            var updateRequest = new
            {
                worker_id = workerId,
                stage = stage,
                progress = progress
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/progress/", updateRequest, cancellationToken);
            return result?["success"] == true;
        }

        public async Task<bool> CompleteJob(Guid jobId, string workerId, AnalysisResult analysisResult, CancellationToken cancellationToken = default)
        {
            var completeRequest = new
            {
                worker_id = workerId,
                summary = analysisResult.Summary,
                tags = analysisResult.Tags,
                events = analysisResult.Events,
                metadata = analysisResult.Metadata
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/complete/", completeRequest, cancellationToken);
            if (result?["success"] == true)
            {
                Logger.Log($"Successfully completed job {jobId}");
                return true;
            }

            Logger.Log($"Failed to complete job {jobId}", Logger.LogLevel.Error);
            return false;
        }

        public async Task<bool> FailJob(Guid jobId, string workerId, string error, string stage = "", CancellationToken cancellationToken = default)
        {
            var failRequest = new
            {
                worker_id = workerId,
                error = error,
                stage = stage
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/fail/", failRequest, cancellationToken);
            if (result?["success"] == true)
            {
                Logger.Log($"Marked job {jobId} as failed: {error}");
                return true;
            }

            Logger.Log($"Failed to mark job {jobId} as failed", Logger.LogLevel.Error);
            return false;
        }

        public async Task<bool> CancelJob(Guid jobId, string workerId, string reason = "", CancellationToken cancellationToken = default)
        {
            var cancelRequest = new
            {
                worker_id = workerId,
                reason = reason
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/cancel/", cancelRequest, cancellationToken);
            if (result?["success"] == true)
            {
                Logger.Log($"Successfully cancelled job {jobId}");
                return true;
            }

            Logger.Log($"Failed to cancel job {jobId}", Logger.LogLevel.Error);
            return false;
        }
    }
}
