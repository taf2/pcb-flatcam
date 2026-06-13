# pcb-cam

Headless PCB CAM experiments for the EasyEDA -> FlatCAM-style -> NC workflow.

The first milestone is a project starter that uses the existing PCB template
flow, then uses FlatCAM Beta source parsers directly to write FlatCAM's
compressed JSON `.FlatPrj` format without launching the FlatCAM GUI.

```bash
ruby scripts/new-project.rb ~/Desktop/light/switch-board ~/Downloads/Gerber_light-night_switchboard_2026-06-12.zip
```

The command takes exactly two normal arguments:

1. The project directory to create or refresh.
2. The EasyEDA Gerber zip download.

For a new directory it delegates to `~/Desktop/project/scripts/new-project.rb`
with GUI FlatCAM project creation disabled, then writes:

```text
Project_<gerber-zip-name>_start.FlatPrj
```

For an existing project directory it reruns that project's `prepare.rb` with the
zip, then rewrites the starter FlatCAM project.

The lower-level FlatCAM project writer is still available for debugging:

```bash
ruby scripts/new-project.rb flatprj ~/Desktop/light/power-board
```
