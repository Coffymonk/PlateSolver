# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Core of PlateSolver.

The core never imports Qt. It defines the plugin interfaces, the shared data
models, the settings store, the plugin registry and the processing pipeline.
Everything that does real work (reading files, solving, catalogue lookups,
drawing overlays) lives in plugins.
"""
