using System;
using System.Windows;
using System.Windows.Media;
using CaughtOnDash.Worker.Models;
using CaughtOnDash.Worker.Services;

namespace CaughtOnDash.Worker.ViewModels
{
    public class MainViewModel
    {
        private readonly MainWindow _mainWindow;
        private readonly AppConfigService _configService;
        private readonly WorkerApiClient _apiClient;
        private readonly LocalVideoStorageService _storageService;
        private readonly PlaceholderAnalyzer _analyzer;
        private WorkerLoopService? _workerLoopService;
        private WorkerConfig _config;

        public MainViewModel(MainWindow mainWindow)
        {
            _mainWindow = mainWindow;
            _configService = new AppConfigService();
            _apiClient = new WorkerApiClient();
            _storageService = new LocalVideoStorageService();
            _analyzer = new PlaceholderAnalyzer();
            _config = _configService.LoadConfig();

            InitializeUI();
        }

        private void InitializeUI()
        {
            if (_config.IsConfigured)
            {
                _apiClient.Initialize(_config.BackendUrl, _config.ApiToken);
                _mainWindow.BackendUrlDisplay.Text = _config.BackendUrl;
                AddLog($"Loaded backend config: {_config.BackendUrl}");
                _mainWindow.StatusText.Text = "Stopped";
                _mainWindow.StatusText.Foreground = Brushes.Gray;
                _mainWindow.StartButton.IsEnabled = true;
                _mainWindow.StopButton.IsEnabled = false;
            }
            else
            {
                _mainWindow.BackendUrlDisplay.Text = "Missing config";
                _mainWindow.StatusText.Text = "Missing config";
                _mainWindow.StatusText.Foreground = Brushes.Red;
                _mainWindow.StartButton.IsEnabled = false;
                _mainWindow.StopButton.IsEnabled = false;
                AddLog("Worker config is missing backend URL or API token.", Logger.LogLevel.Error);
            }

            AddLog("Application started");
        }

        public async System.Threading.Tasks.Task StartAutomaticallyAsync()
        {
            if (!_config.IsConfigured)
            {
                AddLog("Cannot auto-connect worker because config is incomplete.", Logger.LogLevel.Error);
                _mainWindow.StatusText.Text = "Missing config";
                _mainWindow.StatusText.Foreground = Brushes.Red;
                return;
            }

            _apiClient.Initialize(_config.BackendUrl, _config.ApiToken);
            _mainWindow.BackendUrlDisplay.Text = _config.BackendUrl;

            AddLog($"Auto-connected to backend: {_config.BackendUrl}");
            _mainWindow.StatusText.Text = "Stopped";
            _mainWindow.StatusText.Foreground = Brushes.Gray;
            _mainWindow.StartButton.IsEnabled = true;
            _mainWindow.StopButton.IsEnabled = false;

            await StartWorker();
        }

        public async System.Threading.Tasks.Task StartWorker()
        {
            if (!_config.IsConfigured)
            {
                AddLog("Worker not configured. Check appsettings.json.", Logger.LogLevel.Error);
                return;
            }

            if (_workerLoopService != null && _workerLoopService.IsRunning)
            {
                AddLog("Worker is already running.");
                return;
            }

            try
            {
                _workerLoopService = new WorkerLoopService(_config, _apiClient, _analyzer, _storageService);
                _workerLoopService.OnStatusUpdate += HandleWorkerStatusUpdate;

                _mainWindow.StartButton.IsEnabled = false;
                _mainWindow.StopButton.IsEnabled = true;

                AddLog("Starting worker...");
                await _workerLoopService.StartAsync();
            }
            catch (Exception ex)
            {
                AddLog($"Failed to start worker: {ex.Message}", Logger.LogLevel.Error);
                _mainWindow.StartButton.IsEnabled = true;
                _mainWindow.StopButton.IsEnabled = false;
            }
        }

        public async void StopWorker()
        {
            try
            {
                AddLog("Stopping worker...");
                if (_workerLoopService != null)
                {
                    await _workerLoopService.StopAsync();
                }

                _mainWindow.StartButton.IsEnabled = true;
                _mainWindow.StopButton.IsEnabled = false;
                _mainWindow.StatusText.Text = "Stopped";
                _mainWindow.StatusText.Foreground = Brushes.Red;
                _mainWindow.CancelJobButton.IsEnabled = false;

                AddLog("Worker stopped");
            }
            catch (Exception ex)
            {
                AddLog($"Error stopping worker: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        public async void CancelCurrentJob()
        {
            try
            {
                if (_workerLoopService != null)
                {
                    AddLog("Cancelling current job...");
                    await _workerLoopService.CancelCurrentJobAsync();
                }
            }
            catch (Exception ex)
            {
                AddLog($"Error cancelling job: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        private void HandleWorkerStatusUpdate(WorkerLoopEvent evt)
        {
            _mainWindow.Dispatcher.Invoke(() =>
            {
                switch (evt.Status)
                {
                    case "idle":
                        _mainWindow.StatusText.Text = "Idle";
                        _mainWindow.StatusText.Foreground = Brushes.Gray;
                        _mainWindow.StageText.Text = "Idle";
                        _mainWindow.CurrentJobText.Text = "None";
                        _mainWindow.CancelJobButton.IsEnabled = false;
                        break;

                    case "processing":
                        _mainWindow.StatusText.Text = "Processing";
                        _mainWindow.StatusText.Foreground = Brushes.Blue;
                        if (evt.JobTitle != null)
                        {
                            _mainWindow.CurrentJobText.Text = evt.JobTitle;
                        }
                        if (evt.Stage != null)
                        {
                            _mainWindow.StageText.Text = evt.Stage;
                        }
                        _mainWindow.ProgressBar.Value = evt.Progress;
                        _mainWindow.ProgressText.Text = $"{evt.Progress}%";
                        _mainWindow.CancelJobButton.IsEnabled = true;
                        break;

                    case "error":
                        _mainWindow.StatusText.Text = "Error";
                        _mainWindow.StatusText.Foreground = Brushes.Red;
                        break;

                    case "stopped":
                        _mainWindow.StatusText.Text = "Stopped";
                        _mainWindow.StatusText.Foreground = Brushes.Red;
                        _mainWindow.ProgressBar.Value = 0;
                        _mainWindow.ProgressText.Text = "0%";
                        break;
                }

                // Show the progress card only while a job is actually running.
                // A bar sitting at 0% reads as stuck; nothing there reads as
                // nothing running.
                var isProcessing = evt.Status == "processing";
                _mainWindow.ProcessingPanel.Visibility = isProcessing ? Visibility.Visible : Visibility.Collapsed;
                _mainWindow.IdlePanel.Visibility = isProcessing ? Visibility.Collapsed : Visibility.Visible;
                if (!isProcessing)
                {
                    _mainWindow.IdleText.Text = evt.Status == "stopped" ? "Worker stopped." : "Waiting for a job.";
                }

                _mainWindow.LastHeartbeatText.Text = DateTime.Now.ToString("HH:mm:ss");
                AddLog(evt.Message);
            });
        }

        private void AddLog(string message, Logger.LogLevel level = Logger.LogLevel.Info)
        {
            Logger.Log(message, level);

            _mainWindow.Dispatcher.Invoke(() =>
            {
                var item = new System.Windows.Controls.ListBoxItem
                {
                    Content = new System.Windows.Controls.TextBlock
                    {
                        Text = $"[{DateTime.Now:HH:mm:ss}] {message}",
                        TextWrapping = System.Windows.TextWrapping.Wrap,
                        Foreground = level switch
                        {
                            Logger.LogLevel.Error => Brushes.Red,
                            Logger.LogLevel.Warning => Brushes.Orange,
                            _ => Brushes.Black
                        }
                    }
                };

                _mainWindow.LogListBox.Items.Add(item);
                _mainWindow.LogListBox.ScrollIntoView(item);

                // Keep only last 100 logs
                while (_mainWindow.LogListBox.Items.Count > 100)
                {
                    _mainWindow.LogListBox.Items.RemoveAt(0);
                }
            });
        }
    }
}
