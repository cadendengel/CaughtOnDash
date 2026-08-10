using System.Windows;
using CaughtOnDash.Worker.ViewModels;

namespace CaughtOnDash.Worker
{
    /// <summary>
    /// Interaction logic for MainWindow.xaml
    /// </summary>
    public partial class MainWindow : Window
    {
        private MainViewModel? _viewModel;

        public MainWindow()
        {
            InitializeComponent();
            _viewModel = new MainViewModel(this);
            this.DataContext = _viewModel;

            _ = _viewModel.StartAutomaticallyAsync();
        }

        private async void StartButton_Click(object sender, RoutedEventArgs e)
        {
            if (_viewModel != null)
            {
                await _viewModel.StartWorker();
            }
        }

        private void StopButton_Click(object sender, RoutedEventArgs e)
        {
            _viewModel?.StopWorker();
        }

        private void CancelJobButton_Click(object sender, RoutedEventArgs e)
        {
            _viewModel?.CancelCurrentJob();
        }

        // ---- queue table ----

        private void RefreshQueueButton_Click(object sender, RoutedEventArgs e)
            => _viewModel?.RefreshQueues();

        private async void RequeueOutdatedButton_Click(object sender, RoutedEventArgs e)
        {
            if (_viewModel is null) return;
            RequeueOutdatedButton.IsEnabled = false;
            try
            {
                await _viewModel.RequeueOutdatedAsync();
            }
            finally
            {
                RequeueOutdatedButton.IsEnabled = true;
            }
        }

        private void ReviewTab_Click(object sender, RoutedEventArgs e)
            => _viewModel?.ShowReviewQueue();

        private void QueuedTab_Click(object sender, RoutedEventArgs e)
            => _viewModel?.ShowRunQueue();

        private void SelectAll_Click(object sender, RoutedEventArgs e)
            => _viewModel?.SelectAll();

        private void ClearSelection_Click(object sender, RoutedEventArgs e)
            => _viewModel?.ClearSelection();

        private void Preview_Click(object sender, RoutedEventArgs e)
            => _viewModel?.PreviewSelected();

        private void MoveUp_Click(object sender, RoutedEventArgs e)
            => _viewModel?.MoveSelectedUp();

        private void MoveDown_Click(object sender, RoutedEventArgs e)
            => _viewModel?.MoveSelectedDown();

        private void Reject_Click(object sender, RoutedEventArgs e)
            => _viewModel?.RejectSelected();

        private void StartBatch_Click(object sender, RoutedEventArgs e)
            => _viewModel?.StartBatch();
    }
}
