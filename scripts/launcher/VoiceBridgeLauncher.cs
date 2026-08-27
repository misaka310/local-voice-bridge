using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Local Voice Bridge Launcher")]
[assembly: System.Reflection.AssemblyProduct("Local Voice Bridge")]
[assembly: System.Reflection.AssemblyDescription("Small Windows launcher for the local tray application and setup")]
[assembly: System.Reflection.AssemblyVersion("1.1.0.0")]

namespace LocalVoiceBridgeLauncher
{
    internal static class Program
    {
        private const string AppTitle = "Local Voice Bridge";
        private const int EnvironmentValidationTimeoutMs = 15000;
        private const uint SEM_FAILCRITICALERRORS = 0x0001;
        private const uint SEM_NOGPFAULTERRORBOX = 0x0002;

        [DllImport("kernel32.dll")]
        private static extern uint SetErrorMode(uint uMode);

        [STAThread]
        private static int Main(string[] args)
        {
            bool selfTest = HasArgument(args, "--self-test");
            bool setup = HasArgument(args, "--setup");
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string localApi = Path.Combine(root, "local-api");
            string python = Path.Combine(localApi, ".venv", "Scripts", "python.exe");
            string pythonw = Path.Combine(localApi, ".venv", "Scripts", "pythonw.exe");
            string controller = Path.Combine(localApi, "tray_controller.py");
            string setupGui = Path.Combine(root, "scripts", "setup", "setup-gui.ps1");

            if (setup)
            {
                return LaunchSetup(setupGui, root, selfTest);
            }

            string validationError = ValidateEnvironment(python, pythonw, controller, localApi);
            if (!String.IsNullOrEmpty(validationError))
            {
                if (!selfTest && ShowSetupPrompt(validationError))
                {
                    return LaunchSetup(setupGui, root, false);
                }
                return 2;
            }

            if (selfTest)
            {
                return File.Exists(setupGui) ? 0 : 4;
            }

            try
            {
                ProcessStartInfo startInfo = new ProcessStartInfo
                {
                    FileName = pythonw,
                    Arguments = Quote(controller),
                    WorkingDirectory = localApi,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                Process.Start(startInfo);
                return 0;
            }
            catch (Exception ex)
            {
                ShowError("起動に失敗しました。\n\n" + ex.Message);
                return 3;
            }
        }

        private static int LaunchSetup(string setupGui, string root, bool selfTest)
        {
            if (!File.Exists(setupGui))
            {
                if (!selfTest)
                {
                    ShowError("セットアップ画面が見つかりません。\n\n" + setupGui);
                }
                return 4;
            }

            if (selfTest)
            {
                return 0;
            }

            try
            {
                string powershell = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Windows),
                    "System32",
                    "WindowsPowerShell",
                    "v1.0",
                    "powershell.exe"
                );
                if (!File.Exists(powershell))
                {
                    powershell = "powershell.exe";
                }

                ProcessStartInfo startInfo = new ProcessStartInfo
                {
                    FileName = powershell,
                    Arguments = "-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File " + Quote(setupGui),
                    WorkingDirectory = root,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                Process.Start(startInfo);
                return 0;
            }
            catch (Exception ex)
            {
                ShowError("セットアップ画面を起動できませんでした。\n\n" + ex.Message);
                return 5;
            }
        }

        private static string ValidateEnvironment(string python, string pythonw, string controller, string workingDirectory)
        {
            if (!File.Exists(python) || !File.Exists(pythonw))
            {
                return "音声環境がありません。";
            }

            if (VenvPythonWasReplacedWithBaseInterpreter(python))
            {
                return "Python仮想環境が壊れています。セットアップで修復できます。";
            }

            if (!File.Exists(controller))
            {
                return "local-api\\tray_controller.py が見つかりません。";
            }

            try
            {
                uint previousErrorMode = SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
                try
                {
                    int versionExitCode = RunEnvironmentCheck(python, "--version", workingDirectory);
                    if (versionExitCode == -2)
                    {
                        return "Python環境の確認がタイムアウトしました。もう一度起動してください。";
                    }
                    if (versionExitCode == -1)
                    {
                        return "Python環境を確認できませんでした。";
                    }
                    if (versionExitCode != 0)
                    {
                        return "Python仮想環境が壊れています。セットアップで修復できます。";
                    }

                    int qtExitCode = RunEnvironmentCheck(python, "-c \"from PySide6 import QtWidgets, QtSvg\"", workingDirectory);
                    if (qtExitCode == -2)
                    {
                        return "Python環境の確認がタイムアウトしました。もう一度起動してください。";
                    }
                    if (qtExitCode == -1)
                    {
                        return "Python環境を確認できませんでした。";
                    }
                    if (qtExitCode != 0)
                    {
                        return "Windows小窓に必要なPySide6が見つかりません。";
                    }
                }
                finally
                {
                    SetErrorMode(previousErrorMode);
                }
            }
            catch (Exception ex)
            {
                return "Python環境を確認できませんでした。\n\n" + ex.Message;
            }

            return null;
        }

        private static int RunEnvironmentCheck(string python, string arguments, string workingDirectory)
        {
            ProcessStartInfo checkInfo = new ProcessStartInfo
            {
                FileName = python,
                Arguments = arguments,
                WorkingDirectory = workingDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };

            using (Process check = Process.Start(checkInfo))
            {
                if (check == null)
                {
                    return -1;
                }
                if (!check.WaitForExit(EnvironmentValidationTimeoutMs))
                {
                    try
                    {
                        check.Kill();
                    }
                    catch (Exception)
                    {
                        // Best effort only. The launcher must not block forever on validation cleanup.
                    }
                    return -2;
                }
                return check.ExitCode;
            }
        }

        private static bool VenvPythonWasReplacedWithBaseInterpreter(string python)
        {
            try
            {
                DirectoryInfo scriptsDirectory = Directory.GetParent(python);
                if (scriptsDirectory == null || scriptsDirectory.Parent == null)
                {
                    return false;
                }

                string cfgPath = Path.Combine(scriptsDirectory.Parent.FullName, "pyvenv.cfg");
                if (!File.Exists(cfgPath))
                {
                    return false;
                }

                string home = null;
                foreach (string line in File.ReadAllLines(cfgPath))
                {
                    if (line.StartsWith("home = ", StringComparison.OrdinalIgnoreCase))
                    {
                        home = line.Substring("home = ".Length).Trim();
                        break;
                    }
                }
                if (String.IsNullOrEmpty(home))
                {
                    return false;
                }

                string basePython = Path.Combine(home, "python.exe");
                if (!File.Exists(basePython))
                {
                    return false;
                }

                FileInfo venvInfo = new FileInfo(python);
                FileInfo baseInfo = new FileInfo(basePython);
                if (venvInfo.Length != baseInfo.Length)
                {
                    return false;
                }
                return FilesHaveSameBytes(python, basePython);
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static bool FilesHaveSameBytes(string firstPath, string secondPath)
        {
            byte[] first = File.ReadAllBytes(firstPath);
            byte[] second = File.ReadAllBytes(secondPath);
            if (first.Length != second.Length)
            {
                return false;
            }
            for (int i = 0; i < first.Length; i++)
            {
                if (first[i] != second[i])
                {
                    return false;
                }
            }
            return true;
        }

        private static bool ShowSetupPrompt(string message)
        {
            Application.EnableVisualStyles();
            DialogResult result = MessageBox.Show(
                message + "\n\nセットアップ画面を開きますか？",
                AppTitle,
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Information
            );
            return result == DialogResult.Yes;
        }

        private static bool HasArgument(string[] args, string expected)
        {
            if (args == null)
            {
                return false;
            }

            foreach (string arg in args)
            {
                if (String.Equals(arg, expected, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }

            return false;
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static void ShowError(string message)
        {
            Application.EnableVisualStyles();
            MessageBox.Show(message, AppTitle, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
