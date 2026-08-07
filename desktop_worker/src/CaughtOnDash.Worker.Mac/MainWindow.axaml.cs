using System;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Threading;
using CaughtOnDash.Worker.Services;

namespace CaughtOnDash.Worker.Mac
{
    public partial class MainWindow : Window
    {
        private const int MaxLogRows = 100;

        private readonly WorkerSession _session;

        public MainWindow()
        {
            InitializeComponent();

            _session = new WorkerSession();
            _session.StateChanged += OnStateChanged;
            _session.LogAppended += OnLogAppended;

            Render(_session.State);
            _session.Log("Application started");

            if (!_session.IsConfigured)
            {
                _session.Log("Worker config is missing backend URL or API token.", Logger.LogLevel.Error);
                return;
            }

            // Auto-start, matching the Windows host. Faulted tasks are surfaced in
            // the log rather than silently dropped.
            _ = _session.StartAsync().ContinueWith(
                task => _session.Log($"Worker start failed: {task.Exception?.GetBaseException().Message}", Logger.LogLevel.Error),
                System.Threading.Tasks.TaskContinuationOptions.OnlyOnFaulted);
        }

        private async void StartButton_Click(object? sender, RoutedEventArgs e)
        {
            await _session.StartAsync();
        }

        private async void StopButton_Click(object? sender, RoutedEventArgs e)
        {
            await _session.StopAsync();
        }

        private async void CancelJobButton_Click(object? sender, RoutedEventArgs e)
        {
            await _session.CancelCurrentJobAsync();
        }

        private void OnStateChanged(WorkerSessionState state)
        {
            Dispatcher.UIThread.Post(() => Render(state));
        }

        private void Render(WorkerSessionState state)
        {
            StatusText.Text = state.Status;
            StatusText.Foreground = state.Status switch
            {
                "Processing" => Brushes.RoyalBlue,
                "Idle" => Brushes.Gray,
                "Error" => Brushes.Red,
                "Missing config" => Brushes.Red,
                _ => Brushes.Gray,
            };

            BackendUrlDisplay.Text = state.BackendUrl;
            LastHeartbeatText.Text = state.LastHeartbeatDisplay;
            CurrentJobText.Text = state.CurrentJob;
            StageText.Text = state.Stage;
            JobProgressBar.Value = state.Progress;
            ProgressText.Text = state.ProgressDisplay;

            JobDetailsText.Text = state.CurrentJob == "None"
                ? "No active job"
                : $"{state.CurrentJob}\nStage: {state.Stage}\nProgress: {state.ProgressDisplay}";

            // Show the progress card only while a job is actually running.
            var isProcessing = state.Status == "Processing";
            ProcessingPanel.IsVisible = isProcessing;
            IdlePanel.IsVisible = !isProcessing;
            IdleText.Text = state.IsConfigured
                ? state.CanStop ? "Waiting for a job." : "Worker stopped."
                : "Worker is not configured.";

            StartButton.IsEnabled = state.CanStart;
            StopButton.IsEnabled = state.CanStop;
            CancelJobButton.IsEnabled = state.CanCancelJob;
        }

        private void OnLogAppended(WorkerLogEntry entry)
        {
            Dispatcher.UIThread.Post(() =>
            {
                var row = new TextBlock
                {
                    Text = entry.Display,
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = entry.Level switch
                    {
                        Logger.LogLevel.Error => Brushes.Red,
                        Logger.LogLevel.Warning => Brushes.Orange,
                        _ => Brushes.Black,
                    },
                };

                LogListBox.Items.Add(row);

                while (LogListBox.Items.Count > MaxLogRows)
                {
                    LogListBox.Items.RemoveAt(0);
                }

                // Follow the newest entry. Without this the list stays pinned at
                // the top while entries scroll past below the fold, which is
                // useless during a job. The WPF host has always done this; the
                // Avalonia port did not.
                LogListBox.ScrollIntoView(LogListBox.Items.Count - 1);
            });
        }
    }
}
