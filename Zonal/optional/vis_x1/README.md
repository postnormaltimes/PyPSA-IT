# Saved-result visualization toolkit

This optional toolkit renders network maps and operational figures from saved networks and analytical tables. It never constructs or optimizes the model. Core execution and reporting do not require it.

The visualization modules and adapters retain their accepted source bytes. HANDOFF_MANIFEST.json preserves the original package identity and source hashes; its historical documentation and example inventory are not a list of required installed files. The model bridge verifies the toolkit_file_hashes subset before importing it. This installation includes that complete subset, adapters and the spatial support needed by the current figures.

Use scripts/bootstrap_presentation.ps1 to install the separately supplied NUTS2 geography. Configuration is in config/vis_x1.yaml. Current result figures are requested through the final model report command with --presentation, after solve verification. Historical example networks are not current production results.
