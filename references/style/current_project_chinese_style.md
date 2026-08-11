# Current-project Python routing

This repository no longer ships a repo-local Python quality gate or Python
dispatcher surface.

When `.py` files under `readable-hls-generator` need to be created, modified,
or deleted, route that work to `readable-python-generator` and use its
`current-project` style workflow.

Repo-local HLS gates remain limited to HLS C/C++ sources, headers, HLS Tcl/cfg,
pragma or interface inputs, synthesis or cosim artifacts, and related HLS
deliverables. They must not be used as Python governance evidence.
