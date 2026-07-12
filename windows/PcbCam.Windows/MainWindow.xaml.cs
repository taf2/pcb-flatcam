using Microsoft.Win32;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Forms = System.Windows.Forms;

namespace PcbCam.Windows;

public partial class MainWindow : Window
{
    private int _page = 1;
    private string? _zipPath;
    private string? _projectPath;
    private readonly string? _root;
    private CancellationTokenSource? _buildCancellation;

    public MainWindow()
    {
        InitializeComponent();
        _root = ProjectLocator.FindRoot();
    }

    private void BrowseZip_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Microsoft.Win32.OpenFileDialog { Filter = "Gerber ZIP (*.zip)|*.zip", Title = "Choose EasyEDA Gerber export" };
        if (dialog.ShowDialog(this) == true) SelectZip(dialog.FileName);
    }

    private void Window_DragOver(object sender, System.Windows.DragEventArgs e)
    {
        if (_buildCancellation is not null)
        {
            e.Effects = System.Windows.DragDropEffects.None;
            e.Handled = true;
            return;
        }
        e.Effects = GetDroppedZip(e.Data) is null ? System.Windows.DragDropEffects.None : System.Windows.DragDropEffects.Copy;
        e.Handled = true;
    }

    private void Window_Drop(object sender, System.Windows.DragEventArgs e)
    {
        if (_buildCancellation is not null) return;
        var path = GetDroppedZip(e.Data);
        if (path is not null)
        {
            SelectZip(path);
            if (_page != 1) ShowPage(1);
        }
    }

    private static string? GetDroppedZip(System.Windows.IDataObject data)
    {
        if (!data.GetDataPresent(System.Windows.DataFormats.FileDrop)) return null;
        return (data.GetData(System.Windows.DataFormats.FileDrop) as string[])?
            .FirstOrDefault(path => File.Exists(path) && Path.GetExtension(path).Equals(".zip", StringComparison.OrdinalIgnoreCase));
    }

    private void SelectZip(string path)
    {
        _zipPath = Path.GetFullPath(path);
        SelectedZipText.Text = _zipPath;
        SelectedZipText.Foreground = (System.Windows.Media.Brush)FindResource("Ink");
        GerberError.Text = string.Empty;
        ProjectNameBox.Text = ProjectSlug(path);
        CarveraFolderBox.Text = "PCB-CAM/" + ProjectSlug(path);
        if (string.IsNullOrWhiteSpace(OutputFolderBox.Text))
            OutputFolderBox.Text = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
    }

    private static string ProjectSlug(string path)
    {
        var name = Path.GetFileNameWithoutExtension(path);
        name = Regex.Replace(name, "^Gerber_", string.Empty, RegexOptions.IgnoreCase);
        name = Regex.Replace(name, "_PCB_.*$", string.Empty, RegexOptions.IgnoreCase);
        name = Regex.Replace(name, "_20[0-9]{2}-[0-9]{2}-[0-9]{2}$", string.Empty);
        name = Regex.Replace(name, "[^A-Za-z0-9_-]+", "_").Trim('_');
        return string.IsNullOrWhiteSpace(name) ? "pcb-project" : name;
    }

    private void BrowseOutput_Click(object sender, RoutedEventArgs e)
    {
        using var dialog = new Forms.FolderBrowserDialog
        {
            Description = "Choose the folder that will contain your PCB project",
            UseDescriptionForTitle = true,
            SelectedPath = Directory.Exists(OutputFolderBox.Text) ? OutputFolderBox.Text : Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
            ShowNewFolderButton = true,
        };
        if (dialog.ShowDialog() == Forms.DialogResult.OK) OutputFolderBox.Text = dialog.SelectedPath;
    }

    private async void Next_Click(object sender, RoutedEventArgs e)
    {
        if (_page == 1)
        {
            if (_zipPath is null)
            {
                GerberError.Text = "Choose or drop an EasyEDA Gerber ZIP to continue.";
                return;
            }
            ShowPage(2);
            return;
        }

        if (_page == 2)
        {
            if (!ValidateProject()) return;
            ShowPage(3);
            await StartBuildAsync();
            return;
        }

        ResetWizard();
    }

    private bool ValidateProject()
    {
        ProjectError.Text = string.Empty;
        if (string.IsNullOrWhiteSpace(OutputFolderBox.Text) || !Directory.Exists(OutputFolderBox.Text))
        {
            ProjectError.Text = "Choose an existing output folder.";
            return false;
        }
        if (string.IsNullOrWhiteSpace(ProjectNameBox.Text) || ProjectNameBox.Text.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
        {
            ProjectError.Text = "Enter a valid project folder name.";
            return false;
        }

        _projectPath = Path.GetFullPath(Path.Combine(OutputFolderBox.Text, ProjectNameBox.Text.Trim()));
        if (Directory.Exists(_projectPath) && RefreshExistingBox.IsChecked != true)
        {
            ProjectError.Text = "That project folder already exists. Enable refresh or choose another name.";
            return false;
        }
        if (UploadToCarveraBox.IsChecked == true)
        {
            if (string.IsNullOrWhiteSpace(CarveraHostBox.Text))
            {
                ProjectError.Text = "Enter the Carvera IP address.";
                return false;
            }
            if (!Regex.IsMatch(CarveraFolderBox.Text.Trim(), "^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$"))
            {
                ProjectError.Text = "Carvera work folder may contain letters, numbers, dots, underscores, hyphens, and / only.";
                return false;
            }
        }
        return true;
    }

    private async Task StartBuildAsync()
    {
        if (_root is null)
        {
            FinishWithError("The pcb-cam repository could not be found. Keep the app inside the repository, or set PCB_CAM_ROOT.");
            return;
        }
        var failures = WorkflowRunner.Validate(_root);
        if (failures.Count > 0)
        {
            FinishWithError(string.Join(Environment.NewLine, failures));
            return;
        }

        _buildCancellation = new CancellationTokenSource();
        CancelButton.Visibility = Visibility.Visible;
        var progress = new Progress<WorkflowProgress>(update =>
        {
            if (update.Percent >= 0)
            {
                BuildProgress.Value = Math.Max(BuildProgress.Value, update.Percent);
                BuildStage.Text = update.Stage;
            }
            if (update.Line is not null)
            {
                LogBox.AppendText(update.Line + Environment.NewLine);
                LogBox.ScrollToEnd();
            }
        });

        try
        {
            var carveraTransfer = UploadToCarveraBox.IsChecked == true
                ? new CarveraTransferOptions(CarveraHostBox.Text.Trim(), CarveraFolderBox.Text.Trim())
                : null;
            await new WorkflowRunner(_root).RunAsync(_zipPath!, _projectPath!, carveraTransfer, progress, _buildCancellation.Token);
            BuildTitle.Text = "Your PCB project is ready";
            BuildStage.Text = carveraTransfer is null
                ? "FlatCAM project and Carvera machine files were created successfully."
                : "FlatCAM project, machine files, and Carvera work folder were created successfully.";
            BuildProgress.Value = 100;
            NextButton.Content = "Build another";
            NextButton.Visibility = Visibility.Visible;
            OpenFolderButton.Visibility = Visibility.Visible;
        }
        catch (OperationCanceledException)
        {
            FinishWithError("Build cancelled. The partially created project folder was left in place for inspection.");
        }
        catch (WorkflowException ex)
        {
            FinishWithError(ex.Message + Environment.NewLine + "See the build log for details.");
        }
        catch (Exception ex)
        {
            FinishWithError(ex.Message);
        }
        finally
        {
            CancelButton.Visibility = Visibility.Collapsed;
            _buildCancellation?.Dispose();
            _buildCancellation = null;
        }
    }

    private void FinishWithError(string message)
    {
        BuildTitle.Text = "The build could not finish";
        BuildStage.Text = message;
        BuildStage.Foreground = System.Windows.Media.Brushes.Firebrick;
        NextButton.Content = "Start over";
        NextButton.Visibility = Visibility.Visible;
        if (_projectPath is not null && Directory.Exists(_projectPath)) OpenFolderButton.Visibility = Visibility.Visible;
    }

    private void Back_Click(object sender, RoutedEventArgs e) => ShowPage(Math.Max(1, _page - 1));

    private void Cancel_Click(object sender, RoutedEventArgs e) => _buildCancellation?.Cancel();

    private void OpenFolder_Click(object sender, RoutedEventArgs e)
    {
        if (_projectPath is not null && Directory.Exists(_projectPath))
            Process.Start(new ProcessStartInfo("explorer.exe", $"\"{_projectPath!}\"") { UseShellExecute = true });
    }

    private void ProjectField_Changed(object sender, TextChangedEventArgs e)
    {
        if (!IsLoaded) return;
        try { ProjectPathPreview.Text = "Project: " + Path.Combine(OutputFolderBox.Text, ProjectNameBox.Text); }
        catch { ProjectPathPreview.Text = string.Empty; }
        ProjectError.Text = string.Empty;
    }

    private void UploadToCarvera_Changed(object sender, RoutedEventArgs e)
    {
        CarveraOptionsPanel.Visibility = UploadToCarveraBox.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;
    }

    private void ShowPage(int page)
    {
        _page = page;
        GerberPage.Visibility = page == 1 ? Visibility.Visible : Visibility.Collapsed;
        ProjectPage.Visibility = page == 2 ? Visibility.Visible : Visibility.Collapsed;
        BuildPage.Visibility = page == 3 ? Visibility.Visible : Visibility.Collapsed;
        BackButton.Visibility = page == 2 ? Visibility.Visible : Visibility.Collapsed;
        NextButton.Content = page == 2 ? "Build project" : "Next";
        NextButton.Visibility = page == 3 ? Visibility.Collapsed : Visibility.Visible;
        StepOneBadge.Background = BadgeBrush(page >= 1);
        StepTwoBadge.Background = BadgeBrush(page >= 2);
        StepThreeBadge.Background = BadgeBrush(page >= 3);
    }

    private System.Windows.Media.Brush BadgeBrush(bool active) =>
        new System.Windows.Media.SolidColorBrush((System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString(active ? "#087F5B" : "#344057"));

    private void ResetWizard()
    {
        BuildTitle.Text = "Building your PCB project";
        BuildStage.Text = "Checking the toolchain…";
        BuildStage.Foreground = (System.Windows.Media.Brush)FindResource("Muted");
        BuildProgress.Value = 0;
        LogBox.Clear();
        OpenFolderButton.Visibility = Visibility.Collapsed;
        ShowPage(1);
    }

    protected override void OnClosing(CancelEventArgs e)
    {
        _buildCancellation?.Cancel();
        base.OnClosing(e);
    }
}
