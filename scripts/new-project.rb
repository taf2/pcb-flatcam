#!/usr/bin/env ruby
# frozen_string_literal: true

require "fileutils"
require "optparse"
require "rbconfig"
require "shellwords"

$stdout.sync = true
$stderr.sync = true

ROOT = File.expand_path("..", __dir__)
PYTHON = ENV["PCB_CAM_PYTHON"] || File.join(ROOT, ".venv", "Scripts", "python.exe")
LEGACY_NEW_PROJECT = ENV["PCB_CAM_LEGACY_NEW_PROJECT"] || begin
  candidates = [
    File.expand_path("~/Desktop/project/scripts/new-project.rb"),
    "/cygdrive/c/Users/taf2/Desktop/project/scripts/new-project.rb",
    "C:/Users/taf2/Desktop/project/scripts/new-project.rb"
  ]
  candidates.find { |path| File.file?(path) }
end
POWERSHELL = ENV["PCB_CAM_POWERSHELL"] || begin
  candidates = [
    "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "C:/Program Files/PowerShell/7/pwsh.exe"
  ]
  candidates.find { |path| File.file?(path) } || "powershell.exe"
end
# The legacy prep scripts name the coordinate being reflected, not the
# physical mirror line. Reflecting Y coordinates is a physical X-axis flip.
BOTTOM_REFLECTED_COORDINATE = "y"

def abort_usage
  warn "Usage: ruby scripts/new-project.rb [--carvera-upload] [--carvera-host HOST] [--carvera-port PORT] [--carvera-folder FOLDER] PROJECT_PATH GERBER_ZIP"
  warn "Debug: ruby scripts/new-project.rb flatprj PROJECT_PATH [--output PATH]"
  exit 1
end

def truthy?(value)
  %w[1 true yes on].include?(value.to_s.downcase)
end

def require_python!
  return if File.exist?(PYTHON)

  warn "Python runtime not found: #{PYTHON}"
  warn "Create it with: python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt"
  exit 1
end

def run!(*command, chdir: nil)
  display = chdir ? "(cd #{Shellwords.escape(chdir)} && #{Shellwords.join(command)})" : Shellwords.join(command)
  puts display
  ok = chdir ? system(*command, chdir: chdir) : system(*command)
  abort "Command failed: #{display}" unless ok
end

def project_slug(zip_path)
  File.basename(zip_path, ".zip")
      .sub(/\AGerber_/, "")
      .sub(/_PCB_.*\z/, "")
      .gsub(/[^A-Za-z0-9_-]+/, "_")
      .gsub(/\A_+|_+\z/, "")
end

def refresh_existing_project(project_path, zip_path)
  prepare = File.join(project_path, "prepare.rb")
  abort "Existing project is missing prepare.rb: #{project_path}" unless File.file?(prepare)

  target_zip = File.join(project_path, File.basename(zip_path))
  FileUtils.cp(zip_path, target_zip) unless File.expand_path(zip_path) == File.expand_path(target_zip)
  run!(
    RbConfig.ruby,
    "prepare.rb",
    File.basename(target_zip),
    "100",
    "70",
    BOTTOM_REFLECTED_COORDINATE,
    chdir: project_path
  )
end

def scaffold_new_project(project_path, zip_path)
  abort "Legacy project starter not found. Set PCB_CAM_LEGACY_NEW_PROJECT to scripts/new-project.rb." unless LEGACY_NEW_PROJECT

  run!(
    RbConfig.ruby,
    LEGACY_NEW_PROJECT,
    "--no-flatcam-project",
    "--mirror-axis",
    BOTTOM_REFLECTED_COORDINATE,
    project_path,
    zip_path
  )
end

if ARGV.first == "flatprj"
  require_python!
  exec PYTHON, "-m", "pcb_cam", *ARGV
end

options = {
  carvera_upload: truthy?(ENV["PCB_CAM_CARVERA_UPLOAD"]),
  carvera_host: ENV.fetch("PCB_CAM_CARVERA_HOST", "192.168.1.27"),
  carvera_port: Integer(ENV.fetch("PCB_CAM_CARVERA_PORT", "2222")),
  carvera_folder: ENV["PCB_CAM_CARVERA_FOLDER"]
}

OptionParser.new do |parser|
  parser.on("--carvera-upload", "Create a Carvera work folder and upload cut/*.nc files") { options[:carvera_upload] = true }
  parser.on("--carvera-host HOST", "Carvera IP or hostname") { |value| options[:carvera_host] = value }
  parser.on("--carvera-port PORT", Integer, "Carvera Controller TCP port") { |value| options[:carvera_port] = value }
  parser.on("--carvera-folder FOLDER", "Folder below /sd on the Carvera") { |value| options[:carvera_folder] = value }
end.parse!

require_python!

abort_usage unless ARGV.length == 2

project_path = File.expand_path(ARGV[0])
zip_path = File.expand_path(ARGV[1])

abort "Gerber zip not found: #{zip_path}" unless File.file?(zip_path)
abort "Expected a .zip Gerber export: #{zip_path}" unless File.extname(zip_path).downcase == ".zip"

if File.directory?(project_path)
  refresh_existing_project(project_path, zip_path)
else
  scaffold_new_project(project_path, zip_path)
end

flatcam_project = File.join(project_path, "Project_#{project_slug(zip_path)}_start.FlatPrj")
run!(PYTHON, "-m", "pcb_cam", "flatprj", project_path, "--output", flatcam_project)

gen_all = File.join(project_path, "scripts", "gen.all.ps1")
abort "Generated CNC build script not found: #{gen_all}" unless File.file?(gen_all)

run!(
  POWERSHELL,
  "-NoLogo",
  "-NoProfile",
  "-NonInteractive",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  "scripts/gen.all.ps1",
  chdir: project_path
)

if options[:carvera_upload]
  upload_command = [
    PYTHON,
    "-m",
    "pcb_cam",
    "carvera-upload",
    project_path,
    "--host",
    options[:carvera_host],
    "--port",
    options[:carvera_port].to_s
  ]
  upload_command += ["--remote-folder", options[:carvera_folder]] if options[:carvera_folder]
  run!(*upload_command)
end

puts "FlatCAM project: #{flatcam_project}"
puts "Carvera NC files: #{File.join(project_path, 'cut')}"
puts "Carvera upload: complete" if options[:carvera_upload]
puts "Board flip after step 4: rotate across the physical X axis (top edge swaps with bottom edge)."
