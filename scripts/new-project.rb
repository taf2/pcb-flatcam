#!/usr/bin/env ruby
# frozen_string_literal: true

require "fileutils"
require "rbconfig"
require "shellwords"

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

def abort_usage
  warn "Usage: ruby scripts/new-project.rb PROJECT_PATH GERBER_ZIP"
  warn "Debug: ruby scripts/new-project.rb flatprj PROJECT_PATH [--output PATH]"
  exit 1
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
  run!(RbConfig.ruby, "prepare.rb", File.basename(target_zip), "100", "70", "y", chdir: project_path)
end

def scaffold_new_project(project_path, zip_path)
  abort "Legacy project starter not found. Set PCB_CAM_LEGACY_NEW_PROJECT to scripts/new-project.rb." unless LEGACY_NEW_PROJECT

  run!(RbConfig.ruby, LEGACY_NEW_PROJECT, "--no-flatcam-project", project_path, zip_path)
end

require_python!

if ARGV.first == "flatprj"
  exec PYTHON, "-m", "pcb_cam", *ARGV
end

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

puts "FlatCAM project: #{flatcam_project}"
